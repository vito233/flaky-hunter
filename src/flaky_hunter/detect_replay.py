"""replay 模式检测器 —— 模式 A 的**非 OD 分支**（规范 §2.1 路径分流）。

大白话：把目标测试反复跑，看结果会不会翻。**两个探头，缺一不可**：

    探头 1  同进程 --count=N   一个进程里跑 N 遍
    探头 2  换进程重跑 N 遍     每次一个新进程

为什么两个都要（2026-10-12 试点实测的教训）：
    yamicache 的 NIO 是**进程内状态污染**（Cache 对象在同进程第二次
    调用时还活着）—— 探头 1 稳定复现"1过/9挂"，探头 2 跑 100 遍全过。
    只上换进程探头，这类 NIO 系统性漏检。
    而修正 C 说 --count 不能作"不 flaky"的证据（PYTHONHASHSEED 不重置）——
    所以探头 1 只当**阳性证据**：翻了 → flaky；没翻 → 不算数，看探头 2。

    探头 1 翻            → flaky（进程内 NIO）
    探头 2 翻            → flaky（NOD：哈希序/随机数，跨进程才现）
    两个都翻             → flaky（两种机制并存）
    探头 2 全过且次数够  → stable（带统计依据，0 次翻转 ≠ 稳定）
    探头 2 全过次数不够  → inconclusive（证据不足，不许下结论）
    探头 2 全挂          → inconclusive（不是"翻转"：顺序依赖在固定顺序下
                           会稳定失败，测试也可能已坏 —— 判不了 flaky，
                           在指标里计"未检出"）

OD 家族（IDoFT 里占 72.7%）**不走这条路** —— 它们在固定顺序下结果是确定的，
重跑一百遍全一样，会被误判成"很稳定"。那是 detect_order.py 的活（顺序变换 +
二分最小化，阶段 C1 后续）。本模块的指标因此是**保守下界**，见
docs/limitations.md 第 1 条。
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from flaky_hunter.baselines import RERUN_FOR_95_CONFIDENCE
from flaky_hunter.runner import CountOutcome, DockerRunner, ReplayOutcome
from flaky_hunter.schema import Confidence, DetectionResult, RunEnv, RunStats

# 默认重跑次数：**100，不是 20** —— 20 遍置信度不足（规范 §2.2：
# 95% 把握确认不 flaky 平均要 170 遍；20 遍连"失败率 5% 级别"的翻转都排不掉）。
DEFAULT_RERUN_COUNT = 100

# 全部通过时宣称 stable 的最低次数（二检验据）：真实失败率 ≥5% 的测试，
# 连过 60 次的概率 0.95^60 ≈ 4.6% < 5% —— 不到 60 遍不许下 stable 结论。
MIN_RUNS_FOR_STABLE = 60

# 翻转判定的置信度分档：少于这么多次观察到的翻转只算低置信
FLIP_MEDIUM_CONFIDENCE_RUNS = 20


def _image_python(image: str) -> str:
    """`python:3.9-slim` → `3.9`（镜像标签版本，记进 env 备查）。"""
    tag = image.rsplit(":", 1)[-1] if ":" in image else ""
    return tag.split("-")[0] if tag else "unknown"


def classify_counts(
    counts: Counter[str],
    count_outcome: CountOutcome | None = None,
    *,
    runs: int,
    per_run_timeout: int,
) -> tuple[str, str, Confidence]:
    """双探头联合判定：从计数推出 (verdict, status, confidence)。

    纯函数 —— 判定规则的全部逻辑都在这里，不碰 Docker，可完整单测。
    counts 是换进程探头的 pass/fail/timeout/notfound 计数；
    count_outcome 是同进程 --count 探头的结果（None = 没跑）。
    """
    passed = counts["pass"]
    failed = counts["fail"]
    timeouts = counts["timeout"]
    notfounds = counts["notfound"]
    total = passed + failed

    # 同进程探头：翻转 = 阳性证据（进程内 NIO）
    count_flip = (
        count_outcome is not None
        and count_outcome.ok
        and count_outcome.passed > 0
        and count_outcome.failed > 0
    )
    cross_flip = passed > 0 and failed > 0

    # ---- 环境类：换进程探头一次有效结果都没有 ----
    if total == 0 and not count_flip:
        if notfounds > 0 and timeouts == 0:
            return (
                "inconclusive",
                "TEST_NOT_FOUND",
                Confidence(
                    level="high",
                    basis=f"{notfounds} 次均未收集到目标测试（pytest 退出码 5）",
                    note="目标测试路径或名字不对；修复路径后重跑",
                ),
            )
        if timeouts > 0:
            return (
                "inconclusive",
                "TIMEOUT",
                Confidence(
                    level="high",
                    basis=f"{timeouts} 次全部触发单次熔断（{per_run_timeout}s）",
                    note="测试挂死或极慢；换进程重跑无意义，先查性能",
                ),
            )
        return (
            "inconclusive",
            "TIMEOUT",
            Confidence(
                level="low",
                basis="容器内一次有效结果都没有",
                note="既没过也没挂也没超时 —— 容器脚本异常，看原始日志",
            ),
        )

    # ---- 观察到翻转 → flaky ----
    if cross_flip or count_flip:
        level = "medium" if total >= FLIP_MEDIUM_CONFIDENCE_RUNS else "low"
        basis_parts: list[str] = []
        if cross_flip:
            basis_parts.append(
                f"换进程 ×{total}：{passed} 过 / {failed} 挂，"
                f"失败率 {failed / total:.1%}"
            )
        if count_flip and count_outcome is not None:
            c = count_outcome
            basis_parts.append(
                f"同进程 --count ×{c.total}：{c.passed} 过 / {c.failed} 挂，"
                f"失败率 {c.failed / c.total:.1%}"
            )
        mechanism = {
            (True, True): "两种机制并存（跨进程非确定性 + 进程内状态污染）",
            (True, False): "跨进程非确定性（哈希序/随机数相关）",
            (False, True): "进程内状态污染（换进程重跑看不到，只有同进程循环现形）",
        }[(cross_flip, count_flip)]
        notes: list[str] = []
        if notfounds or timeouts:
            notes.append(
                f"另有 {notfounds} 次未收集到、{timeouts} 次超时（不计入 total）"
            )
        if count_flip and count_outcome is not None and count_outcome.short:
            notes.append(
                f"同进程探头只完成 {count_outcome.total}/"
                f"{count_outcome.expected} 次（unittest.TestCase 风格"
                f"不适用 pytest-repeat 的参数化重复）"
            )
        return (
            "flaky",
            "OK",
            Confidence(
                level=level,
                basis="；".join(basis_parts) + f" —— {mechanism}",
                note="；".join(notes),
            ),
        )

    # ---- 换进程全部通过 → 看次数够不够 ----
    if failed == 0:
        if total >= RERUN_FOR_95_CONFIDENCE:
            return (
                "stable",
                "OK",
                Confidence(
                    level="high",
                    basis=(
                        f"{total} 遍换进程全部通过（≥{RERUN_FOR_95_CONFIDENCE}："
                        f"95% 置信确认不 flaky 的外部基准，arXiv 2101.09077）"
                    ),
                    note=_count_probe_note(count_outcome),
                ),
            )
        if total >= MIN_RUNS_FOR_STABLE:
            survive = 0.95**total
            return (
                "stable",
                "OK",
                Confidence(
                    level="high",
                    basis=(
                        f"{total} 遍全部通过；二项检验：若真实失败率≥5%，"
                        f"连过概率仅 {survive:.1%}（<5%）→ 95% 置信排除"
                        f"失败率≥5%的翻转；未达 {RERUN_FOR_95_CONFIDENCE} 遍，"
                        f"属快速复核口径"
                    ),
                    note=_count_probe_note(count_outcome),
                ),
            )
        return (
            "inconclusive",
            "OK",
            Confidence(
                level="medium",
                basis=(
                    f"{total} 遍全部通过，但不足 {MIN_RUNS_FOR_STABLE} 遍"
                    f"（二检验据线）也不足 {RERUN_FOR_95_CONFIDENCE} 遍"
                    f"（外部基准线）—— 0 次翻转 ≠ 稳定（规范 §2.2）"
                ),
                note=_count_probe_note(count_outcome),
            ),
        )

    # ---- 换进程全部挂 → 不是翻转 ----
    return (
        "inconclusive",
        "OK",
        Confidence(
            level="medium",
            basis=(
                f"{total} 遍全部挂：无翻转。顺序依赖在固定顺序下会稳定失败，"
                f"也可能是测试已坏或环境不符 —— 无法判定 flaky（计未检出）"
            ),
            note=f"单次熔断 {per_run_timeout}s；共请求 {runs} 次",
        ),
    )


def _count_probe_note(count_outcome: CountOutcome | None) -> str:
    """同进程探头的备注：没翻要明说"只跑了同进程，不能替稳定背书"；
    次数不足（unittest 风格）或探头本身失败，都必须可见。"""
    if count_outcome is None:
        return ""
    if not count_outcome.ok:
        return f"同进程 --count 探头未产出有效结果（{count_outcome.reason}）"
    if count_outcome.failed > 0:
        extra = ""
        if count_outcome.short:
            extra = (
                f"；注意只完成 {count_outcome.total}/{count_outcome.expected} 次"
                f"（unittest.TestCase 风格不适用 pytest-repeat 的参数化重复）"
            )
        return (
            f"同进程 --count：{count_outcome.passed} 过 / "
            f"{count_outcome.failed} 挂{extra}"
        )
    if count_outcome.short:
        return (
            f"同进程 --count 只完成 {count_outcome.total}/"
            f"{count_outcome.expected} 次（unittest.TestCase 风格不适用 "
            f"pytest-repeat 的参数化重复）—— 阳性证据不足，"
            f"'没翻'的结论以换进程探头为准"
        )
    return (
        f"同进程 --count ×{count_outcome.total} 也未翻转"
        f"（该探头只能作阳性证据，不作为稳定背书 —— 修正 C）"
    )


def detect_replay(
    *,
    project: str,
    sha: str,
    test_name: str,
    runner: DockerRunner,
    src_dir: Path,
    deps_dir: Path,
    runs: int = DEFAULT_RERUN_COUNT,
    count_runs: int = DEFAULT_RERUN_COUNT,
    per_run_timeout: int = 120,
    raw_log_dir: Path | None = None,
) -> DetectionResult:
    """对单个 (项目, SHA, 测试) 跑 replay 检测（双探头），产出统一 schema 结果。

    runner 需要提供 `.replay(...)` 和 `.count_replay(...)` 方法
    （DockerRunner 即此类；测试里可换假实现，不碰 Docker）。
    raw_log_dir 非空时，把首个失败日志落盘留证，路径记进 result.raw_log_path。
    count_runs=0 可关掉同进程探头（不推荐 —— 会漏掉进程内 NIO）。
    """
    # 探头 1：同进程 --count（进程内 NIO 的阳性证据）
    count_outcome: CountOutcome | None = None
    if count_runs > 0:
        count_outcome = runner.count_replay(
            src_dir,
            deps_dir,
            test_name,
            count=count_runs,
            per_run_timeout=per_run_timeout,
        )

    # 探头 2：换进程重跑（NOD + stable 判定的正典）
    outcome: ReplayOutcome = runner.replay(
        src_dir, deps_dir, test_name, runs=runs, per_run_timeout=per_run_timeout
    )
    if outcome.docker_error:
        # 容器没跑起来 = 我们的基础设施问题，不是项目的问题
        # → 抛出，让调用方记 FAIL，不冒充"没翻转"
        raise RuntimeError(
            f"runner 失败（{project} {test_name}）: {outcome.docker_error}"
        )

    raw_log_path = ""
    if raw_log_dir is not None:
        # 证据优先级：换进程探头的完整失败日志 > 同进程探头的翻转证据
        # （11 条 TP 全是同进程探头抓的，它的失败签名是双探头设计最硬的证据，
        #   比如 yamicache 的 `1 = len(Cache object)`）
        evidence = outcome.first_failure_log
        evidence_kind = "cross"
        if (
            not evidence
            and count_outcome is not None
            and count_outcome.ok
            and count_outcome.failed > 0
        ):
            c = count_outcome
            evidence = (
                "# 同进程 --count 探头翻转证据（换进程探头未观察到翻转）\n"
                f"# {c.passed} 过 / {c.failed} 挂，共 {c.total} 次迭代"
                f"（请求 {c.expected} 次；预热 {c.warm_secs:.0f}s，"
                f"预算 {c.budget_secs}s）\n"
                f"# 首个 FAILED 行：\n{c.first_fail_line}\n\n"
                f"# 日志尾部：\n{c.log_tail}\n"
            )
            evidence_kind = "count"
        if evidence:
            raw_log_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(
                r"[^\w.-]+", "_", f"{project.replace('/', '__')}__{test_name}"
            )
            path = raw_log_dir / f"{safe}__{evidence_kind}.log"
            path.write_text(evidence, encoding="utf-8")
            raw_log_path = str(path)

    counts: Counter[str] = Counter(outcome.sequence)
    verdict, status, confidence = classify_counts(
        counts, count_outcome, runs=runs, per_run_timeout=per_run_timeout
    )

    return DetectionResult(
        project=project,
        sha=sha,
        test_name=test_name,
        mode="replay",
        env=RunEnv(image=runner.image, python=_image_python(runner.image)),
        runs=RunStats(
            total=counts["pass"] + counts["fail"],
            passed=counts["pass"],
            failed=counts["fail"],
            # 翻转次数 = 少数派结果数（直观度量：90 挂 10 过 → 翻转 10 次）
            flip_count=min(counts["pass"], counts["fail"]),
        ),
        count_runs=(
            RunStats(
                total=count_outcome.total,
                passed=count_outcome.passed,
                failed=count_outcome.failed,
                flip_count=min(count_outcome.passed, count_outcome.failed),
            )
            if count_outcome is not None and count_outcome.ok
            else None
        ),
        verdict=verdict,
        confidence=confidence,
        timestamp_utc=datetime.now(UTC),
        raw_log_path=raw_log_path,
        status=status,
    )
