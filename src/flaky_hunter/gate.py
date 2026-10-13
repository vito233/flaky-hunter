"""CI 门禁入口（TODOLIST 第 5 周）—— 把 flaky-hunter 挂到真实仓库的 GitHub Actions 上。

大白话：PR 一来，把整个测试套装按三种姿势各扫一遍：

    探头 ①  默认顺序整跑一遍           抓"单独过、整套挂"的顺序受害者（OD-Vic）
    探头 ②  反向顺序整跑一遍           抓"换个顺序就挂"的顺序依赖（OD）
    探头 ③  每个测试文件同进程重复 N 遍  抓"进程内状态污染"型 NIO（yamicache 教训：
            换进程 100 遍全过、同进程 1 过 99 挂 —— 只有 --count 探头能看见）

谁挂了，再**单独跑一遍**验证定性（三条件验证的简化版）：
    单独跑能过  = flaky 嫌疑 → 报警（附触发探头与证据）
    单独跑也挂  = 坏测试 → 不冤枉，单独列出不计报警

为什么第一版是"报告模式"（检出也不让 CI 变红）：
    门禁上线的工程惯例 —— 先 shadow 观察误报率，数字站住了再转硬拦截。
    `--strict` 一开就是硬拦截（alert → exit 1），留给第二版。

覆盖范围声明（写进报告，防误读）：
    这是**快速门禁**（分钟级），不是 100 遍全量复核（小时级）。
    三探头全绿 ≠ 证明没有 flaky —— 0 次翻转 ≠ 稳定（规范 §2.2）。

用法（本地预演 / Actions 里同一套命令）：
    python -m flaky_hunter.gate --src /w --deps /deps --testdir tests \
        --count 20 --per-test-timeout 30 \
        --image docker.io/library/python:3.10-slim \
        --pip-mirror https://pypi.org/simple \
        --no-volumes --json-out gate.json --md-out gate.md
"""

from __future__ import annotations

import argparse
import contextlib
import logging
import re
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from flaky_hunter.runner import (
    PIP_MIRROR,
    DockerRunner,
    mirror_image,
)

log = logging.getLogger("gate")

GATE_SCHEMA_VERSION = "1.0"

# 嫌疑的定性：抓到它的探头决定它叫什么
FindingKind = Literal["order_victim", "order_flap", "inprocess_nio"]
Verdict = Literal["pass", "alert", "inconclusive"]

# 这几个探头失败 = 扫描根本没完成，门禁必须给 inconclusive 而不是 pass
_CORE_PROBES = ("setup", "collect", "seq_default", "seq_reverse")


# ---------------------------------------------------------------- 数据模型


class GateFinding(BaseModel):
    """一条 flaky 嫌疑：谁挂的、怎么挂的、单独跑验证过没有。"""

    test: str
    kind: FindingKind
    evidence: str = ""  # 触发探头 + 表现（落盘留痕用）
    single_run: str = "pass"  # 单独跑验证的结果（嫌疑成立的必要条件）


class GateProbe(BaseModel):
    """一个探头的执行情况（耗时进 CI 报告，失败决定 verdict 走向）。"""

    name: str
    ok: bool
    seconds: float = 0.0
    note: str = ""


class GateReport(BaseModel):
    """门禁汇总报告（JSON 落盘 + Markdown 进 Job Summary）。"""

    schema_version: str = GATE_SCHEMA_VERSION
    verdict: Verdict
    reason: str = ""
    nodes_total: int = 0
    files_total: int = 0
    findings: list[GateFinding] = Field(default_factory=list)
    bad_tests: list[str] = Field(default_factory=list)
    probes: list[GateProbe] = Field(default_factory=list)
    image: str = ""
    count: int = 0
    generated_utc: datetime

    @property
    def total_seconds(self) -> float:
        return round(sum(p.seconds for p in self.probes), 1)


# ---------------------------------------------------------------- 纯函数


def _sh_quote(text: str) -> str:
    """单引号包裹 + 内置单引号转义。与 runner._sh_quote 同族（模块边界各留一份）。"""
    return "'" + text.replace("'", "'\\''") + "'"


def group_by_file(nodes: list[str]) -> OrderedDict[str, list[str]]:
    """节点 → 所属文件分组（保持收集序）—— 文件级 count 探头的分组依据。"""
    files: OrderedDict[str, list[str]] = OrderedDict()
    for node in nodes:
        files.setdefault(node.split("::")[0], []).append(node)
    return files


def build_file_count_script(test_file: str, count: int, per_file_timeout: int) -> str:
    """生成"一个测试文件同进程重复 count 遍"的容器内脚本（探头 ③）。

    与 runner.build_count_script 的区别：target 是**整个文件**（进程内污染
    可以发生在文件内任意两个测试之间，逐节点跑反而会漏）。判"挂没挂"看
    退出码 + FAILED 行就够，单节点的精确计数是单独跑验证的活。

    budget = per_file_timeout × count + 60：文件单遍跑满超时的最坏预算，
    兜住慢文件（真实开销通常是它的百分之一）。
    """
    budget = per_file_timeout * count + 60
    return (
        f"budget={budget}\n"
        f"timeout $budget python -m pytest {_sh_quote(test_file)} "
        f"--count={count} -q --no-header --no-cov "
        f"-p no:randomly -p no:cacheprovider > /tmp/fc.log 2>&1\n"
        'echo "@@FILE_RC=$?@@"\n'
        "grep '^FAILED' /tmp/fc.log 2>/dev/null | head -20\n"
    )


_FILE_RC_RE = re.compile(r"^@@FILE_RC=(\d+)@@$")


def parse_file_count_output(output: str) -> tuple[int, list[str]]:
    """文件 count 输出 → (退出码, FAILED 基节点列表)。

    pytest 的 FAILED 行带参数化展开（`test_x[10-20]`），而 collect 给的是
    基节点（`test_x`）—— 去掉尾部参数化段，两边才能对上；同一测试挂多遍
    去重成一条。
    """
    rc = 0
    for line in output.splitlines():
        m = _FILE_RC_RE.match(line.strip())
        if m:
            rc = int(m.group(1))
    failed: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("FAILED "):
            continue
        node = line[len("FAILED ") :].split(" - ")[0].strip()
        if node.endswith("]") and "[" in node:
            node = node.rsplit("[", 1)[0]  # 参数化展开 → 基节点
        failed.add(node)
    return rc, sorted(failed)


def gate_verdict(
    findings: list[GateFinding],
    bad_tests: list[str],
    probes: list[GateProbe],
) -> tuple[Verdict, str]:
    """(verdict, reason) —— 判定规则全在这一个纯函数里，可完整单测。

    核心探头（setup / collect / 两个序扫描）失败 = 扫描没完成 → inconclusive
    （门禁不许在瞎的时候给 pass）。文件 count 探头单文件失败不致命：
    挂本身可能就是信号，交给单独跑验证去定性。
    """
    by_name = {p.name: p for p in probes}
    for name in _CORE_PROBES:
        p = by_name.get(name)
        if p is not None and not p.ok:
            return "inconclusive", f"探头 {name} 失败，扫描没有完成 —— 门禁不给结论"
    if findings:
        return "alert", f"检出 {len(findings)} 条 flaky 嫌疑（报告模式，不阻断合并）"
    reason = "三探头全绿 —— 快速门禁未观察到翻转；0 次翻转 ≠ 证明稳定"
    if bad_tests:
        reason += f"；另有 {len(bad_tests)} 条坏测试（单独跑也挂，不算 flaky）"
    return "pass", reason


def render_markdown(report: GateReport) -> str:
    """渲染 Job Summary 用 Markdown（Actions 页面直接可读）。"""
    icon = {"pass": "✅", "alert": "⚠️", "inconclusive": "❔"}[report.verdict]
    lines = [
        f"## {icon} Flaky 门禁判定：{report.verdict}",
        "",
        f"- **理由**：{report.reason}",
        f"- **扫描覆盖**：{report.nodes_total} 条测试 / {report.files_total} 个文件，"
        f"总耗时 {report.total_seconds}s（镜像 {report.image or '默认'}）",
        "",
        "| 探头 | 状态 | 耗时s | 说明 |",
        "|---|---|---|---|",
    ]
    for p in report.probes:
        mark = "✅" if p.ok else "❌"
        note = p.note.replace("|", "\\|")[:120]
        lines.append(f"| {p.name} | {mark} | {p.seconds:.1f} | {note} |")
    if report.findings:
        lines += [
            "",
            "### ⚠️ flaky 嫌疑（单独跑能过、套餐/重复跑挂）",
            "",
            "| 测试 | 类型 | 证据 |",
            "|---|---|---|",
        ]
        for f in report.findings:
            ev = f.evidence.replace("|", "\\|")[:160]
            lines.append(f"| `{f.test}` | {f.kind} | {ev} |")
    if report.bad_tests:
        lines += [
            "",
            f"### 📎 另有 {len(report.bad_tests)} 条坏测试（单独跑也挂，不计 flaky）",
            "",
        ]
        lines += [f"- `{t}`" for t in report.bad_tests]
    lines += [
        "",
        "---",
        "_快速门禁（报告模式）：三探头全绿 ≠ 证明没有 flaky（0 次翻转 ≠ 稳定）。"
        "全量复核走 flaky-hunter 的 100 遍重跑 + 顺序扫描。_",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 编排


def _single_run_pass(
    runner: DockerRunner,
    src_dir: Path,
    deps_dir: Path,
    node: str,
    per_test_timeout: int,
) -> str:
    """单独跑一个节点。

    返回 pass/fail/error/missing/unknown（unknown = 探头自己失败）。
    """
    outcome = runner.run_sequence(
        src_dir, deps_dir, [node], per_run_timeout=per_test_timeout
    )
    if not outcome.ok:
        return "unknown"
    return outcome.outcomes.get(node, "missing")


def _short_circuit(probes: list[GateProbe]) -> GateReport:
    """核心探头失败时立即收场：让 gate_verdict 给 inconclusive，不伪装成功。"""
    verdict, reason = gate_verdict([], [], probes)
    failed = [p for p in probes if not p.ok and p.note]
    if failed:
        reason += "（" + "；".join(f"{p.name}: {p.note}" for p in failed) + "）"
    return GateReport(
        verdict=verdict,
        reason=reason,
        probes=probes,
        generated_utc=datetime.now(UTC),
    )


def run_gate(
    runner: DockerRunner,
    src_dir: Path,
    deps_dir: Path,
    testdir: str = "tests",
    count: int = 20,
    per_test_timeout: int = 30,
    files: list[str] | None = None,
) -> GateReport:
    """门禁主流程：setup → collect → 双序扫描 → 单独验证 → 文件 count → 判定。"""
    src_dir = Path(src_dir)
    deps_dir = Path(deps_dir)
    # POSIX 风格路径（WSL/CI 场景，挂载点在另一侧文件系统）：宿主侧不建
    # 目录，挂载点由 Docker 兜底自动创建
    with contextlib.suppress(OSError):
        deps_dir.mkdir(parents=True, exist_ok=True)
    probes: list[GateProbe] = []
    bad_tests: list[str] = []
    findings: list[GateFinding] = []

    def _probe(name: str, ok: bool, t0: float, note: str = "") -> None:
        probes.append(
            GateProbe(
                name=name,
                ok=ok,
                seconds=round(time.monotonic() - t0, 1),
                note=note,
            )
        )

    # ① 环境与收集 —— 任一失败就短路：环境/清单都没有，后面全是白跑
    t0 = time.monotonic()
    setup = runner.setup(src_dir, deps_dir)
    _probe(
        "setup",
        setup.tools_ok,
        t0,
        "工具链就绪" if setup.tools_ok else f"安装失败: {setup.log_tail[-80:]}",
    )
    if not setup.tools_ok:
        return _short_circuit(probes)

    t0 = time.monotonic()
    coll = runner.collect(src_dir, deps_dir, testdir)
    _probe(
        "collect",
        coll.ok,
        t0,
        f"{len(coll.tests)} 条" if coll.ok else coll.reason,
    )
    if not coll.ok:
        return _short_circuit(probes)

    nodes = coll.tests
    file_map = group_by_file(nodes) if coll.ok else OrderedDict()

    suspect: dict[str, str] = {}  # node -> 触发探头描述

    # ② 双序扫描（探头 1 / 2）
    for name, seq in (("seq_default", nodes), ("seq_reverse", list(reversed(nodes)))):
        t0 = time.monotonic()
        outcome = runner.run_sequence(
            src_dir, deps_dir, seq, per_run_timeout=per_test_timeout
        )
        if outcome.ok:
            hit = [n for n, v in outcome.outcomes.items() if v in ("fail", "error")]
            for n in hit:
                suspect.setdefault(
                    n, "默认序挂" if name == "seq_default" else "反向序挂"
                )
            _probe(name, True, t0, f"{len(hit)} 挂")
        else:
            _probe(name, False, t0, outcome.reason or outcome.docker_error)

    # ③ 嫌疑定性：单独跑一遍（单独能过 = 嫌疑成立；也挂 = 坏测试）
    for node, how in suspect.items():
        single = _single_run_pass(runner, src_dir, deps_dir, node, per_test_timeout)
        if single == "pass":
            findings.append(
                GateFinding(
                    test=node,
                    kind="order_victim" if how == "默认序挂" else "order_flap",
                    evidence=f"{how}（{per_test_timeout}s 级快速扫描）",
                    single_run=single,
                )
            )
        else:
            bad_tests.append(node)
            log.info("坏测试（单独跑 %s）：%s", single, node)

    # ④ 文件级 count 探头（探头 3）：只扫还没进嫌疑池的文件（省预算）
    if coll.ok:
        targets = files if files is not None else list(file_map)
        for test_file in targets:
            t0 = time.monotonic()
            rc, out = runner.run_script(
                build_file_count_script(test_file, count, per_test_timeout),
                src_dir,
                deps_dir=deps_dir,
                timeout=per_test_timeout * count + 300,
            )
            if rc == "TIMEOUT" or rc == "NOBIN":
                _probe(
                    f"count:{test_file}",
                    False,
                    t0,
                    f"容器异常 rc={rc}",
                )
                continue
            frc, failed_nodes = parse_file_count_output(out)
            _probe(f"count:{test_file}", True, t0, f"rc={frc}，{len(failed_nodes)} 挂")
            for node in failed_nodes:
                if node in suspect:
                    continue  # 已经定性过了
                single = _single_run_pass(
                    runner, src_dir, deps_dir, node, per_test_timeout
                )
                if single == "pass":
                    findings.append(
                        GateFinding(
                            test=node,
                            kind="inprocess_nio",
                            evidence=f"同进程重复 {count} 遍挂（rc={frc}）",
                            single_run=single,
                        )
                    )
                else:
                    bad_tests.append(node)

    # ⑤ 判定
    verdict, reason = gate_verdict(findings, bad_tests, probes)
    report = GateReport(
        verdict=verdict,
        reason=reason,
        nodes_total=len(nodes),
        files_total=len(file_map),
        findings=findings,
        bad_tests=bad_tests,
        probes=probes,
        image=runner.image,
        count=count if coll.ok else 0,
        generated_utc=datetime.now(UTC),
    )
    return report


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        prog="flaky-hunter-gate", description="flaky-hunter CI 门禁（报告模式）"
    )
    parser.add_argument("--src", required=True, help="项目源码目录（挂进容器 /w）")
    parser.add_argument(
        "--deps", required=True, help="依赖缓存目录（pip --target 落点）"
    )
    parser.add_argument("--testdir", default="tests")
    parser.add_argument(
        "--count", type=int, default=20, help="同进程重复遍数（探头 3）"
    )
    parser.add_argument("--per-test-timeout", type=int, default=30)
    parser.add_argument(
        "--image",
        default=None,
        help="默认 python:3.9-slim（走 daocloud mirror）；CI 上传官方全名直连",
    )
    parser.add_argument("--pip-mirror", default=PIP_MIRROR)
    parser.add_argument(
        "--docker-bin",
        default="docker",
        help=(
            "docker 命令前缀；多段用 ; 分隔（本机预演跨 WSL 时传 "
            "wsl;-d;Ubuntu-22.04;-e;docker，-e 必须带上）"
        ),
    )
    parser.add_argument("--tools", nargs="*", default=None, help="覆盖默认工具链")
    parser.add_argument(
        "--files", nargs="*", default=None, help="count 探头只扫这些文件（调试用）"
    )
    parser.add_argument(
        "--no-volumes",
        action="store_true",
        help="不用 Docker 卷缓存（CI 每次全新环境）",
    )
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--md-out", default=None)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="硬门禁：alert → exit 1（默认报告模式恒 0）",
    )
    args = parser.parse_args(argv)

    runner = DockerRunner()
    _parts = tuple(s for s in args.docker_bin.split(";") if s)
    runner.docker_bin = _parts[0] if len(_parts) == 1 else _parts
    if args.image:
        runner.image = mirror_image(args.image)
    runner.pip_mirror = args.pip_mirror
    if args.no_volumes:
        runner.use_volumes = False
    if args.tools:
        runner.tools = tuple(args.tools)

    report = run_gate(
        runner,
        Path(args.src),
        Path(args.deps),
        testdir=args.testdir,
        count=args.count,
        per_test_timeout=args.per_test_timeout,
        files=args.files,
    )

    md = render_markdown(report)
    if args.json_out:
        Path(args.json_out).write_text(
            report.model_dump_json(indent=2), encoding="utf-8"
        )
    if args.md_out:
        Path(args.md_out).write_text(md, encoding="utf-8")
    log.info("%s", md)
    return 1 if (args.strict and report.verdict == "alert") else 0


if __name__ == "__main__":
    raise SystemExit(main())
