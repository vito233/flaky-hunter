"""history 模式检测器 —— 模式 B（TODOLIST 第 3 周）：吃现成 CI 历史，不重跑。

大白话：CI 每次跑完都会留下"体检报告"（JUnit XML：哪个测试过、哪个挂）。
把一个项目历史上所有的报告按时间排好，盯着每个测试看 —— 它的结果是
"过挂过挂"地翻，还是一直过、一直挂？

    相邻两次结果不一样  → flaky（真翻面）
    一直过（次数够多）  → stable（带口径声明）
    一直挂              → inconclusive（全挂 ≠ 翻转：测试可能坏了、
                          或者"坏了然后被修好"—— FFFPPP 失败率 50% 却不是
                          flaky；PFPFP 才是 flaky 的签名。这个洞察与
                          flaky-detector（golikovichev）一致）
    次数太少            → inconclusive（0 次翻转 ≠ 稳定，规范 §2.2）

与模式 A（replay）的关系：看的都是"跨 run 非确定性"，但模式 B 不花机器
时间重跑 —— 代价是**必须先有历史**，且历史 run 可能跨 commit / 跨环境
（这是模式 B 的固有前提，stable 判定因此封顶 medium，除非另行校验同 SHA）。
顺序依赖（OD 家族）在固定顺序下不翻 —— 模式 B 和模式 A 一样抓不到，
那是 detect_order.py 的活。

翻转的两个口径（都给，别混用）：
    flip_transitions  相邻 P/F 不同的次数 —— 历史模式主口径（时间维度，
                      FFFPPP = 1 次，PFPF = 3 次）
    flip_min_count    少数派计数 —— 与 replay 的 flip_count 同口径，
                      跨模式可比（FFFPPP = 2，PFPF = 2）

时间序 = 文件名典序（调用方用可排序命名，如 0001.xml 或 ISO 时间戳前缀）。
不读 XML 内 timestamp 做排序 —— 时区/精度坑多，且文件名可控。

用法（本地）：
    python -m flaky_hunter.detect_history \
        --xml-dir data/processed/history/mdutils-master-30 \
        --project didix21/mdutils \
        --json-out history.json --md-out history.md
"""

from __future__ import annotations

import argparse
import itertools
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from flaky_hunter.baselines import RERUN_FOR_95_CONFIDENCE
from flaky_hunter.schema import (
    HISTORY_SCHEMA_VERSION,
    Confidence,
    HistoryReport,
    HistoryResult,
    HistorySummary,
)

# 全部通过时宣称 stable 的最低历史长度 —— 与 replay 的 MIN_RUNS_FOR_STABLE
# 同一条二项检验线：真实失败率 ≥5% 的测试连过 60 次的概率 0.95^60 ≈ 4.6% < 5%
MIN_RUNS_FOR_STABLE = 60

# 翻转判定的置信分档线 —— 与 replay 的 FLIP_MEDIUM_CONFIDENCE_RUNS 同值
FLIP_MEDIUM_CONFIDENCE_RUNS = 20


def parse_junit_file(path: Path) -> dict[str, str]:
    """解析一份 JUnit XML → {test_id: "P"|"F"|"S"}。

    兼容两种版式：xunit2 的 <testsuites><testsuite>… 和老式的顶层
    <testsuite> —— 一律 root.iter("testcase") 全捞。
    pytest 的 <error>（fixture 崩了等）按失败计 —— 对"这个测试这次没过"
    的历史语义而言，failure 和 error 是一回事。
    同一文件里同 test_id 出现多次（脏数据）：取首个，后面的忽略。
    坏 XML 直接抛 ValueError —— 静默跳过会伪造"无翻转"证据，宁可炸。
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"JUnit XML 解析失败：{path}（{exc}）") from exc

    outcomes: dict[str, str] = {}
    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        classname = tc.get("classname", "")
        # classname 缺省时 test_id 直接用 name —— 便于自攒历史沿用
        # replay 的路径形式（tests/test_x.py::test_y）而不硬凑点号路径
        test_id = f"{classname}::{name}" if classname else name
        if test_id in outcomes:
            continue
        if tc.find("failure") is not None or tc.find("error") is not None:
            outcomes[test_id] = "F"
        elif tc.find("skipped") is not None:
            outcomes[test_id] = "S"
        else:
            outcomes[test_id] = "P"
    return outcomes


def classify_history(sequence: str) -> tuple[str, Confidence]:
    """从 P/F/S 串推出 (verdict, confidence)。纯函数，可完整单测。

    判定分母是有效序列（P+F；S 不算有效观察）。
    """
    passed = sequence.count("P")
    failed = sequence.count("F")
    skipped = sequence.count("S")
    valid = passed + failed
    transitions = sum(
        1 for a, b in itertools.pairwise(sequence) if a in "PF" and b in "PF" and a != b
    )

    skip_note = f"；另有 {skipped} 次跳过（不计入有效观察）" if skipped else ""

    if valid == 0:
        return (
            "inconclusive",
            Confidence(
                level="medium",
                basis="历史里没有一次有效观察（全跳过/全缺失）",
                note="没有证据就没有结论" + skip_note,
            ),
        )

    if transitions > 0:
        level = "medium" if valid >= FLIP_MEDIUM_CONFIDENCE_RUNS else "low"
        return (
            "flaky",
            Confidence(
                level=level,
                basis=(
                    f"历史 ×{valid}：{passed} 过 / {failed} 挂，"
                    f"相邻翻转 {transitions} 次（少数派口径 "
                    f"{min(passed, failed)}）—— flaky 签名"
                ),
                note=skip_note.lstrip("；"),
            ),
        )

    if failed == 0:
        if valid >= MIN_RUNS_FOR_STABLE:
            survive = 0.95**valid
            return (
                "stable",
                Confidence(
                    level="medium",
                    basis=(
                        f"历史 ×{valid} 全部通过；二项检验：若真实失败率≥5%，"
                        f"连过概率仅 {survive:.1%}（<5%）→ 排除失败率≥5%的翻转。"
                        f"历史口径 stable 封顶 medium —— 历史 run 可能跨 "
                        f"commit/环境，同 SHA 校验后方可升 high"
                    ),
                    note=(
                        f"0 次翻转 ≠ 稳定的外部基准线为 {RERUN_FOR_95_CONFIDENCE} 遍"
                        f"（历史长度不足时只看二项线）" + skip_note
                    ),
                ),
            )
        return (
            "inconclusive",
            Confidence(
                level="medium",
                basis=(
                    f"历史 ×{valid} 全部通过，但不足 {MIN_RUNS_FOR_STABLE} 条"
                    f"（二项检验线）—— 0 次翻转 ≠ 稳定（规范 §2.2）"
                ),
                note=skip_note.lstrip("；"),
            ),
        )

    # passed == 0：全挂
    return (
        "inconclusive",
        Confidence(
            level="medium",
            basis=(
                f"历史 ×{valid} 全部挂：无翻转。全挂 ≠ flaky —— 可能是测试"
                f"已坏、或'坏了后被修好'（FFFPPP 失败率 50% 却不是 flaky）；"
                f"也可能是环境类失败（同 replay 三分类口径，排除并单独报数）"
            ),
            note=skip_note.lstrip("；"),
        ),
    )


def detect_history(
    *,
    project: str,
    xml_dir: Path,
) -> HistoryReport:
    """吃一个 JUnit XML 目录（每文件 = 一次 CI run，文件名典序 = 时间序），
    产出整份模式 B 报告。"""
    files = sorted(xml_dir.glob("*.xml"))
    if not files:
        raise ValueError(f"目录里没有 JUnit XML 文件：{xml_dir}")

    per_test: dict[str, list[str]] = {}
    for path in files:
        outcomes = parse_junit_file(path)
        for test_id, outcome in outcomes.items():
            per_test.setdefault(test_id, []).append(outcome)

    results: list[HistoryResult] = []
    for test_id, outcomes in sorted(per_test.items()):
        passed = outcomes.count("P")
        failed = outcomes.count("F")
        skipped = outcomes.count("S")
        runs_total = len(outcomes)
        sequence = "".join(outcomes)
        verdict, confidence = classify_history(sequence)
        transitions = sum(
            1
            for a, b in itertools.pairwise(sequence)
            if a in "PF" and b in "PF" and a != b
        )
        results.append(
            HistoryResult(
                test_id=test_id,
                sequence=sequence,
                runs_total=runs_total,
                valid_runs=passed + failed,
                passed=passed,
                failed=failed,
                skipped=skipped,
                missing_files=len(files) - runs_total,
                flip_transitions=transitions,
                flip_min_count=min(passed, failed),
                verdict=verdict,
                confidence=confidence,
            )
        )

    verdicts = Counter(r.verdict for r in results)
    return HistoryReport(
        schema_version=HISTORY_SCHEMA_VERSION,
        project=project,
        source_dir=str(xml_dir),
        files_parsed=len(files),
        ordering="filename-lexicographic（文件名典序 = 时间序；"
        "调用方负责用可排序命名，如 0001.xml 或 ISO 时间戳前缀）",
        results=results,
        summary=HistorySummary(
            flaky=verdicts["flaky"],
            stable=verdicts["stable"],
            inconclusive=verdicts["inconclusive"],
            tests_total=len(results),
        ),
        timestamp_utc=datetime.now(UTC),
    )


def report_markdown(report: HistoryReport) -> str:
    """报告的 Markdown 视图：汇总 + flaky 明细（附序列证据）。"""
    lines = [
        f"# 模式 B 历史翻转报告 —— {report.project}",
        "",
        f"- 历史 run 数：{report.files_parsed}（时间序：{report.ordering}）",
        f"- 测试总数：{report.summary.tests_total}"
        f" ｜ flaky {report.summary.flaky}"
        f" ｜ stable {report.summary.stable}"
        f" ｜ inconclusive {report.summary.inconclusive}",
        "",
        "## flaky 明细",
        "",
    ]
    flaky = [r for r in report.results if r.verdict == "flaky"]
    if not flaky:
        lines.append("（无）")
    for r in flaky:
        lines.append(
            f"- `{r.test_id}`：{r.confidence.basis}"
            f"（置信 {r.confidence.level}；序列 `{r.sequence}`）"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m flaky_hunter.detect_history",
        description="模式 B：吃 JUnit XML 历史目录，数翻转次数，判 flaky/stable",
    )
    parser.add_argument(
        "--xml-dir", required=True, help="JUnit XML 目录（文件名典序 = 时间序）"
    )
    parser.add_argument("--project", required=True, help="owner/repo 或历史来源标注")
    parser.add_argument("--json-out", help="报告 JSON 输出路径")
    parser.add_argument("--md-out", help="报告 Markdown 输出路径")
    args = parser.parse_args(argv)

    report = detect_history(project=args.project, xml_dir=Path(args.xml_dir))

    payload = report.model_dump_json(indent=2)
    if args.json_out:
        Path(args.json_out).write_text(payload + "\n", encoding="utf-8")
    if args.md_out:
        Path(args.md_out).write_text(report_markdown(report), encoding="utf-8")
    if not args.json_out and not args.md_out:
        sys.stdout.write(payload + "\n")
    else:
        s = report.summary
        sys.stdout.write(
            f"[detect_history] {args.project}: {s.tests_total} 测试 = "
            f"flaky {s.flaky} / stable {s.stable} / inconclusive {s.inconclusive}"
            f"（{report.files_parsed} 个历史 run）\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
