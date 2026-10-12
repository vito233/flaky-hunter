"""顺序变换检测器 —— 模式 B（规范 §2.1 路径分流）：OD 家族的猎手。

大白话（为什么需要它）：
    replay 模式把单个测试反复跑。顺序依赖的测试在固定顺序下结果是
    **确定的** —— 跑一百遍全一样，会被误判成"很稳定"。第一版指标里
    8 条 FN 全是这么漏的（mdutils：单独跑 100 遍全过，整套跑才挂）。

    顺序变换反着来：**把整个测试套装在一起，按不同顺序跑**。
    找出"单独跑稳稳过、套装里却挂"的测试 —— 这就是 victim
    （受害者，IDoFT 的 OD-Vic）。然后回头做两件事把证据钉死：
      二分  在"出问题的那个顺序"里，对 victim 前面的测试做二分，
            收敛出最短的、仍能让 victim 挂掉的前缀
      ddmin 在前缀里保序删除无关测试，收敛出最小 polluter
            （污染者）集合 —— 就是这几个测试把 victim 弄挂的
    最后三条件验证，全过才报警：
      ① polluter 集合 + victim → 挂（可复现）
      ② victim 单独跑 N 遍 → 全过（不是坏测试）
      ③ 每个 polluter 单独跑 → 过（不是坏测试）

流程与 iDFlakies 一致（随机序 → 失败序 → 二分收敛最小 polluter 集合，
docs/methodology.md）—— 不自己发明算法，只做工程复现。

关键实现事实（2026-10-13 实测）：
    - pytest **按命令行节点 ID 顺序执行**（含同文件内多个节点）——
      顺序直接用参数控制，不用写收集插件
    - deps 里的 pytest-randomly 会随机重排，必须 `-p no:randomly`
      禁掉（runner.build_sequence_script 已处理）
    - mdutils 实例：102 条测试的前缀二分 + ddmin → 1 条 polluter
      （test_fileutils 里 chdir 进临时目录、目录被删，进程 CWD 没了）
"""

from __future__ import annotations

import logging
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from flaky_hunter.runner import (
    DockerRunner,
    ReplayOutcome,
    SequenceOutcome,
    image_python,
)
from flaky_hunter.schema import (
    Confidence,
    DetectionResult,
    OrderAnalysis,
    RunEnv,
    RunStats,
)

log = logging.getLogger("detect_order")

# 扫描时跑多少个顺序：默认（收集序）+ 反向 + 2 个定种子随机序。
# 4 个不是魔法数字 —— 顺序依赖的污染通常与顺序无关（polluter 只要在
# victim 前面就生效），多跑顺序主要是为了"换个顺序能过"的阴性证据。
DEFAULT_ORDERS = 4

# victim "单独跑稳稳过" 的证据轮数（换进程，与 replay 模式同款正典）。
# 10 遍是成本与证据的折中：replay 模式的 100 遍留给它的 stable 判定，
# 这里要证的只是"不是坏测试"。
DEFAULT_ALONE_RUNS = 10

# polluter 单独跑的轮数：只证"不是坏测试"，1 挂就存疑，3 遍够。
DEFAULT_POLLUTER_ALONE_RUNS = 3

# 单个 victim 的"二分 + ddmin"总测试次数上限。撞上限 → polluter 集合
# 是上界不是最小，minimized=False，明说不假装。
DEFAULT_MINIMIZE_BUDGET = 40

# 少于这么多次单独通过，"不是坏测试"的证据打折（置信度降档）。
MIN_ALONE_RUNS = 5

# 默认随机种子：与 experiments/run_eval.py 的 DEFAULT_SEED 一致（可复现）。
DEFAULT_SEED = 1729

# 判定为"挂"的 outcome 值（fail / error 都算；skipped 不算）。
_BAD = ("fail", "error")


# ---------------------------------------------------------------- 纯函数：顺序生成


def make_orders(tests: list[str], count: int, seed: int) -> list[tuple[str, list[str]]]:
    """生成要跑的顺序清单：默认（收集序）、反向、定种子随机。

    随机部分用 random.Random(seed) —— 种子写死，整个扫描可复现。
    随机结果若与已有顺序重复（撞车）就重摇，不白跑一遍。
    """
    orders: list[tuple[str, list[str]]] = [("default", list(tests))]
    if count >= 2:
        orders.append(("reverse", list(reversed(tests))))
    rng = random.Random(seed)
    i = 0
    seen = {tuple(seq) for _, seq in orders}
    while len(orders) < count:
        shuffled = list(tests)
        rng.shuffle(shuffled)
        i += 1
        key = tuple(shuffled)
        if key in seen:
            continue  # 撞车重摇（不计数，多摇一次而已）
        seen.add(key)
        orders.append((f"random-seed{seed}-{i}", shuffled))
    return orders[:count]


# ---------------------------------------------------------------- 纯函数：最小化


def shrink_prefix(fails, prefix: list[str]) -> tuple[list[str], int]:
    """二分最小前缀长度：找最短的 prefix[:L] 仍使 victim 挂。

    fails(seq) = "跑 seq + [victim]，victim 挂了吗"（调用方提供）。
    前提（调用方保证）：fails(prefix) 为真。
    返回 (最短仍失败的前缀, 消耗次数)。

    诚实声明：pollution 不保证对前缀长度单调 —— 可能存在"清洁剂"测试，
    跑一下把状态复位（删掉它反而复现不了）。二分可能给出非最短解；
    但后面的 ddmin 只接受"仍然失败"的删除，组合起来保守不误报。
    """
    if not prefix:
        return [], 0
    runs = 0
    lo, hi = 1, len(prefix)
    while lo < hi:
        mid = (lo + hi) // 2
        runs += 1
        if fails(prefix[:mid]):
            hi = mid
        else:
            lo = mid + 1
    return prefix[:hi], runs


def minimize_polluters(
    fails, seq: list[str], budget: int = DEFAULT_MINIMIZE_BUDGET
) -> tuple[list[str], int, bool]:
    """保序 ddmin：不断尝试"删掉一块仍失败"，收敛最小 polluter 子序列。

    返回 (最小子序列, 消耗次数, 是否在预算内收敛)。
    ddmin 是启发式，不保证全局最小；单 polluter 场景 ~log2(n) 次收敛
    （mdutils 实例：102 条前缀 → 1 条，6 次以内）。
    撞预算上限 → 返回当前集合（仍是能复现失败的集合）+ converged=False，
    明说"是上界不是最小"，不假装最小。
    """
    seq = list(seq)
    runs = 0
    n = 2
    while len(seq) >= 2:
        chunk = max(1, len(seq) // n)
        i = 0
        shrunk = False
        while i < len(seq):
            candidate = seq[:i] + seq[i + chunk :]
            if candidate:
                runs += 1
                if runs > budget:
                    return seq, runs - 1, False
                if fails(candidate):
                    seq = candidate
                    shrunk = True
                    break  # 块边界变了，重新按当前粒度切
            i += chunk
        if shrunk:
            n = max(n - 1, 2)
            continue
        if n >= len(seq):
            break  # 单元素块都删不动了 —— 到此为止
        n = min(len(seq), n * 2)
    return seq, runs, True


# ---------------------------------------------------------------- 纯函数：判定


def classify_order(
    *,
    orders_tried: int,
    failing_orders: list[str],
    alone_runs: int,
    alone_failures: int,
    minimized: bool,
    polluters: list[str],
    polluter_alone_ok: bool,
    incomplete_orders: tuple[str, ...] = (),
) -> tuple[str, Confidence]:
    """顺序模式的联合判定（纯函数，规则全在这里，可完整单测）。

    只有一种情况报警（flaky）：某顺序挂 ∧ 单独跑全过 ∧ 最小 polluter
    集合能复现。其余全是 inconclusive + 明说原因：
      - 单独跑也挂      → 不是顺序依赖（坏测试 / NIO 归 replay 模式）
      - 顺序全过        → 未观察到顺序依赖（K 个顺序 ≠ 全部顺序）
    注意：顺序模式**不判 stable** —— "几个顺序没翻"证明不了稳定性
    （规范 §2.2 的同款纪律），只给阴性观察。
    incomplete_orders：没跑完的顺序（实测 freezegun：它的假时间泄漏进
    pytest 计时器，把 pytest 自己搞崩）—— 必须进备注，"N/M 个顺序挂"
    里的 M 只数**有效观测**。
    """
    if alone_failures > 0:
        return (
            "inconclusive",
            Confidence(
                level="high",
                basis=(
                    f"套装里挂过，但单独跑也挂（{alone_failures}/{alone_runs}）"
                    f"—— 不是顺序依赖受害者；可能是坏测试或 NIO，归 replay 模式"
                ),
                note=_incomplete_note(incomplete_orders),
            ),
        )
    if not failing_orders:
        return (
            "inconclusive",
            Confidence(
                level="medium",
                basis=(
                    f"{orders_tried} 个顺序全部通过 —— 未观察到顺序依赖"
                    f"（{orders_tried} 个顺序 ≠ 全部顺序，阴性观察不等于稳定，"
                    f"规范 §2.2）"
                ),
                note=_incomplete_note(incomplete_orders),
            ),
        )

    # ---- victim 确认：某顺序挂 ∧ 单独跑全过 ----
    notes: list[str] = []
    if not minimized:
        notes.append(
            f"ddmin 撞预算上限（{DEFAULT_MINIMIZE_BUDGET} 次），"
            f"polluter 集合是上界不是最小"
        )
    if not polluter_alone_ok:
        notes.append(
            "有 polluter 单独跑也挂（可能是坏测试）—— victim 判定不变，"
            "但'谁污染的'归因存疑"
        )
    if alone_runs < MIN_ALONE_RUNS:
        notes.append(
            f"单独只跑了 {alone_runs} 遍（<{MIN_ALONE_RUNS}），'不是坏测试'的证据单薄"
        )
    level = (
        "high"
        if minimized and polluter_alone_ok and alone_runs >= MIN_ALONE_RUNS
        else "medium"
    )
    basis = (
        f"{len(failing_orders)}/{orders_tried} 个顺序挂"
        f"（{'、'.join(failing_orders)}），单独跑 {alone_runs} 遍全过；"
        f"最小 polluter 集合 {len(polluters)} 条"
        f"（ddmin {'收敛' if minimized else '未收敛'}），"
        f"验证：polluter 集合 + victim 复现挂"
    )
    note = "；".join([*notes, _incomplete_note(incomplete_orders)])
    return (
        "flaky",
        Confidence(level=level, basis=basis, note=note),
    )


def _incomplete_note(incomplete_orders: tuple[str, ...]) -> str:
    """没跑完的顺序必须可见 —— 不许把"没跑完"混进"跑过了"里充数。"""
    if not incomplete_orders:
        return ""
    return (
        f"另有 {len(incomplete_orders)} 个顺序没跑完"
        f"（{'、'.join(incomplete_orders)}），未计入上述比例"
    )


# ---------------------------------------------------------------- 编排：项目级扫描


@dataclass
class OrderScan:
    """一个项目的顺序变换扫描结果。

    victims 只装"三条件验证全过"的 victim（mode=order 的 DetectionResult）。
    non_victim_reasons 装每个检查过但没判 victim 的测试 + 原因 ——
    数据诚信：不许悄悄消失（与 metrics.status_breakdown 同款纪律）。
    error 非空 = 项目级失败（收集失败 / 容器没跑起来），此时 victims
    可能不完整，调用方按项目记失败，不许拿不完整的扫描当"没 victim"。
    """

    project: str
    sha: str
    error: str = ""
    tests_total: int = 0
    orders_tried: int = 0
    suite_runs: int = 0
    victims: list[DetectionResult] = field(default_factory=list)
    non_victim_reasons: dict[str, str] = field(default_factory=dict)

    @property
    def victim_nodes(self) -> set[str]:
        return {v.test_name for v in self.victims}


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w.-]+", "_", text)


def _alone_summary(outcome: ReplayOutcome) -> tuple[int, int]:
    counts = Counter(outcome.sequence)
    return counts["pass"], counts["fail"]


def scan_project_order(
    *,
    project: str,
    sha: str,
    runner: DockerRunner,
    src_dir: Path,
    deps_dir: Path,
    testdir: str = "tests",
    orders: int = DEFAULT_ORDERS,
    seed: int = DEFAULT_SEED,
    alone_runs: int = DEFAULT_ALONE_RUNS,
    polluter_alone_runs: int = DEFAULT_POLLUTER_ALONE_RUNS,
    minimize_budget: int = DEFAULT_MINIMIZE_BUDGET,
    per_run_timeout: int = 120,
    raw_log_dir: Path | None = None,
) -> OrderScan:
    """对一个项目跑完整顺序变换扫描：多顺序 → victim → 二分 → ddmin → 验证。

    返回 OrderScan（见其 docstring）。任何容器级故障（docker_error）直接
    中断本项目并记 error —— 不拿"没跑起来"冒充"没 victim"。
    """
    scan = OrderScan(project=project, sha=sha)

    collected = runner.collect(src_dir, deps_dir, testdir=testdir)
    if not collected.ok:
        scan.error = f"COLLECT_FAIL:{collected.reason}"
        return scan
    tests = collected.tests
    scan.tests_total = len(tests)
    if not tests:
        scan.error = "COLLECT_FAIL:EMPTY"
        return scan
    log.info("  📦 收集到 %d 个测试，跑 %d 个顺序", len(tests), orders)

    # ---- 1. 多顺序扫描：谁在套装里挂过 ----
    failing: dict[str, list[str]] = {}  # node → 失败顺序名列表
    failing_order: dict[str, list[str]] = {}  # node → 首个失败顺序的完整序列
    incomplete_orders: list[str] = []  # 没跑完的顺序（名:原因）
    for name, seq in make_orders(tests, orders, seed):
        scan.suite_runs += 1
        outcome = runner.run_sequence(
            src_dir, deps_dir, seq, per_run_timeout=per_run_timeout
        )
        if outcome.docker_error:
            scan.error = f"RUNNER_ERROR:{outcome.docker_error}"
            return scan
        if not outcome.ok:
            # 这个顺序没跑完（熔断 / XML 空 / XML 坏）—— 记下来，不冒充"全过"。
            # 实测 freezegun：它的假时间泄漏进 pytest 计时器，pytest 自己
            # INTERNALERROR 崩了，XML 没写出来 —— 这种顺序不能算有效观测。
            log.warning("  ⚠ 顺序 %s 没跑完（%s），跳过", name, outcome.reason)
            incomplete_orders.append(f"{name}:{outcome.reason}")
            scan.non_victim_reasons[f"(order:{name})"] = (
                f"ORDER_INCOMPLETE:{outcome.reason}"
            )
            continue
        # 只数跑完的顺序 —— "N/M 个顺序挂"里的 M 必须是有效观测数
        scan.orders_tried += 1
        for node, verdict in outcome.outcomes.items():
            if verdict in _BAD:
                failing.setdefault(node, []).append(name)
                failing_order.setdefault(node, seq)

    candidates = sorted(failing)
    log.info(
        "  🔍 %d 个顺序跑完，%d 个测试在套装里挂过",
        scan.orders_tried,
        len(candidates),
    )
    for node in tests:
        if node not in failing:
            scan.non_victim_reasons[node] = f"ORDERS_ALL_PASS:{scan.orders_tried}"

    # ---- 2. 逐个候选：单独跑 → 二分 → ddmin → 验证 ----
    for node in candidates:
        alone = runner.replay(
            src_dir, deps_dir, node, runs=alone_runs, per_run_timeout=per_run_timeout
        )
        if alone.docker_error:
            scan.error = f"RUNNER_ERROR:{alone.docker_error}"
            return scan
        alone_counts = Counter(alone.sequence)
        alone_pass = alone_counts["pass"]
        alone_fail = alone_counts["fail"]
        if alone_fail > 0 or alone_pass < alone_runs:
            # 超时/未收集到都算"不干净"—— 证据不足，不当 victim
            other = alone_runs - alone_pass - alone_fail
            scan.non_victim_reasons[node] = (
                f"ALONE_NOT_CLEAN:pass={alone_pass},fail={alone_fail},other={other}"
            )
            log.info(
                "  ⬚ %s 单独跑不干净（%d过/%d挂/%d其他），跳过",
                node,
                alone_pass,
                alone_fail,
                other,
            )
            continue

        seq = failing_order[node]
        prefix = seq[: seq.index(node)]
        if not prefix:
            # 第一个跑还挂、单独跑又过 —— 矛盾，留证不下结论
            scan.non_victim_reasons[node] = "FIRST_IN_ORDER_BUT_FAILED"
            continue

        last_fail_outcome: dict[str, SequenceOutcome] = {}

        def fails(
            sub: list[str],
            _node: str = node,
            _store: dict[str, SequenceOutcome] = last_fail_outcome,
        ) -> bool:
            outcome = runner.run_sequence(
                src_dir, deps_dir, [*sub, _node], per_run_timeout=per_run_timeout
            )
            scan.suite_runs += 1
            if outcome.docker_error or not outcome.ok:
                return False  # 跑不完 → 不能确认失败 → 保守当没挂
            reproduced = outcome.outcomes.get(_node) in _BAD
            if reproduced:
                # 只存"成功复现"的那次 —— 证据要挂在能复现的序列上
                _store["seq"] = outcome
            return reproduced

        if not fails(prefix):
            scan.non_victim_reasons[node] = "PREFIX_NOT_REPRODUCED"
            log.warning("  ⚠ %s 前缀单独复现不了挂，跳过", node)
            continue

        short, prefix_runs = shrink_prefix(fails, prefix)
        polluters, runs_used, converged = minimize_polluters(
            fails, short, minimize_budget
        )

        # 验证 ③：每个 polluter 单独跑（证"不是坏测试"）
        polluter_alone_ok = True
        polluter_notes: list[str] = []
        for pol in polluters:
            pa = runner.replay(
                src_dir,
                deps_dir,
                pol,
                runs=polluter_alone_runs,
                per_run_timeout=per_run_timeout,
            )
            if pa.docker_error:
                scan.error = f"RUNNER_ERROR:{pa.docker_error}"
                return scan
            p_pass, p_fail = _alone_summary(pa)
            if p_fail > 0 or p_pass < polluter_alone_runs:
                polluter_alone_ok = False
                polluter_notes.append(f"{pol} 单独跑 {p_pass}过/{p_fail}挂")

        verdict, confidence = classify_order(
            orders_tried=scan.orders_tried,
            failing_orders=failing[node],
            alone_runs=alone_runs,
            alone_failures=alone_fail,
            minimized=converged,
            polluters=polluters,
            polluter_alone_ok=polluter_alone_ok,
            incomplete_orders=tuple(incomplete_orders),
        )
        if polluter_notes:
            confidence = confidence.model_copy(
                update={
                    "note": "；".join(
                        part for part in (confidence.note, *polluter_notes) if part
                    )
                }
            )

        result = DetectionResult(
            project=project,
            sha=sha,
            test_name=node,
            mode="order",
            env=RunEnv(image=runner.image, python=image_python(runner.image)),
            runs=RunStats(
                total=alone_runs, passed=alone_pass, failed=alone_fail, flip_count=0
            ),
            verdict=verdict,
            order_analysis=OrderAnalysis(
                permutations_tried=scan.orders_tried,
                failing_orders=len(failing[node]),
                victim=node,
                polluters=polluters,
                minimized=converged,
            ),
            confidence=confidence,
            timestamp_utc=datetime.now(UTC),
            status="OK",
        ).with_utc_now()
        scan.victims.append(result)

        if raw_log_dir is not None:
            _write_evidence(
                raw_log_dir,
                result=result,
                failing_orders=failing[node],
                polluters=polluters,
                minimized=converged,
                alone=(alone_pass, alone_fail, alone_runs),
                last_outcome=last_fail_outcome.get("seq"),
                runs_used=prefix_runs + runs_used,
                incomplete_orders=incomplete_orders,
            )
        log.info(
            "  ✅ victim %s ← polluter ×%d（%s）",
            node,
            len(polluters),
            "ddmin 收敛" if converged else "撞预算上限",
        )

    return scan


def _write_evidence(
    raw_dir: Path,
    *,
    result: DetectionResult,
    failing_orders: list[str],
    polluters: list[str],
    minimized: bool,
    alone: tuple[int, int, int],
    last_outcome: SequenceOutcome | None,
    runs_used: int,
    incomplete_orders: list[str] | None = None,
) -> None:
    """把 victim 的归因证据落盘（AGENTS.md：证据要能复盘）。

    内容：失败顺序清单、最小 polluter 集合、三条件验证结果、
    没跑完的顺序（如有）、复现序列的 pytest 输出尾部（失败签名 ——
    面试最硬的那行）。
    """
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(f"{result.project.replace('/', '__')}__{result.test_name}")
    path = raw_dir / f"{safe}__order.log"
    alone_pass, alone_fail, alone_runs = alone
    lines = [
        f"# 顺序依赖归因证据：{result.test_name}",
        f"# 项目 {result.project} @ {result.sha[:12]}",
        f"# 失败顺序（{len(failing_orders)}/{result.order_analysis.permutations_tried}"
        f" 个有效顺序挂）：{'、'.join(failing_orders)}",
        f"# 最小 polluter 集合（{len(polluters)} 条，"
        f"ddmin {'收敛' if minimized else '未收敛（预算内）'}，"
        f"二分+ddmin 共消耗 {runs_used} 次）：",
        *(f"#   - {p}" for p in polluters),
        "# 验证 ① polluter 集合 + victim → 挂（可复现）",
        f"# 验证 ② victim 单独跑 ×{alone_runs} → {alone_pass}过/{alone_fail}挂",
    ]
    if incomplete_orders:
        lines.append(
            f"# 没跑完的顺序（未计入有效观测）：{'、'.join(incomplete_orders)}"
        )
    lines += [
        f"# 判定：{result.verdict}（{result.confidence.level} 置信）",
        f"# 依据：{result.confidence.basis}",
    ]
    if result.confidence.note:
        lines.append(f"# 备注：{result.confidence.note}")
    if last_outcome is not None and last_outcome.log_tail:
        lines.append("# ---- 复现序列 pytest 输出尾部（失败签名）----")
        lines.append(last_outcome.log_tail)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
