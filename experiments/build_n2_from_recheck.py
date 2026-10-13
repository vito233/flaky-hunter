"""N2 口径落档 —— 用 detect_history 走完整管道，把 N3 复核数据升级为 N2 表。

大白话：
    N1 = "IDoFT 没说它 flaky"的测试（弱负样本，可能藏着没被记录的）
    N2 = N1 + **历史无翻转**（强一点：翻了 30 遍也没翻过面）
    N3 = N1 + 抽样 30 遍实测复核（最强）

数据来源的诚实声明（必须随数字走）：
    本脚本的"历史"= negatives_recheck.csv 的 30 遍换进程运行记录
    （同 SHA、同容器环境、2026-10-13 实跑）—— **不是目标项目的公开
    CI 历史**。公开 CI 历史路线（fork 上攒 Actions 产物）另行执行。
    N2 门槛 = valid_runs ≥ 10 且 flip_transitions == 0 且全过
    （全挂是 env_fail，三分类口径排除并单独报数）。

预期对账：recheck 的 stable_ok 55 条应全部进 N2；env_fail 1 条排除；
noise 0 条。对不上 = 管道有问题，先修管道再落档。

用法：
    uv run python experiments/build_n2_from_recheck.py
    uv run python experiments/build_n2_from_recheck.py --skip-pipeline
        # --skip-pipeline：不走 XML→detect_history 管道，直接从 CSV 聚合
        # （仅用于快速对账，不作为 N2 表的正式来源）
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from flaky_hunter.detect_history import detect_history

EXPERIMENTS_DIR = Path(__file__).resolve().parent
RECHECK_CSV = EXPERIMENTS_DIR / "negatives_recheck.csv"
N2_JSON = EXPERIMENTS_DIR / "negatives_n2.json"
N2_CSV = EXPERIMENTS_DIR / "negatives_n2.csv"

# N2 门槛（见模块 docstring 的口径声明）
MIN_HISTORY_FOR_N2 = 10


def load_recheck_rows() -> list[dict[str, str]]:
    with RECHECK_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-pipeline", action="store_true")
    args = parser.parse_args(argv)

    rows = load_recheck_rows()
    # 三分类口径：env_fail（全挂 = 环境失败）排除并单独报数
    candidates = [r for r in rows if r["env_fail"] != "True"]
    env_fail = [r for r in rows if r["env_fail"] == "True"]

    if args.skip_pipeline:
        # 快速对账路径：直接按计数列聚合（不作为正式来源）
        n2 = [
            r
            for r in candidates
            if r["flipped"] == "False"
            and int(r["runs"]) >= MIN_HISTORY_FOR_N2
            and int(r["failed"]) == 0
        ]
        report_summary = {"tests_total": len(rows), "via": "csv-aggregate"}
    else:
        # 正式路径：把 30 遍运行记录转成 JUnit XML 历史，走 detect_history 管道
        tmp = Path(tempfile.mkdtemp(prefix="fh-history-n2-"))
        try:
            runs_total = {int(r["runs"]) for r in candidates}
            if len(runs_total) != 1:
                raise ValueError(f"复核数据 runs 不一致：{sorted(runs_total)}")
            n_runs = runs_total.pop()
            for i in range(1, n_runs + 1):
                cases = "".join(
                    f'<testcase name="{r["test_name"]}" time="0.01"/>'
                    for r in candidates
                )
                (tmp / f"{i:04d}.xml").write_text(
                    '<?xml version="1.0" encoding="utf-8"?>'
                    f'<testsuites><testsuite name="recheck" tests="{len(candidates)}">'
                    f"{cases}</testsuite></testsuites>",
                    encoding="utf-8",
                )
            report = detect_history(
                project="negatives-recheck(30x cross-process, local Docker)",
                xml_dir=tmp,
            )
            by_id = {r.test_id: r for r in report.results}
            # 对账 1：管道看到的测试数 = 候选数
            if len(by_id) != len(candidates):
                raise ValueError(f"管道测试数 {len(by_id)} != 候选数 {len(candidates)}")
            n2 = []
            for r in candidates:
                hr = by_id[r["test_name"]]
                # 对账 2：序列全 P 且计数与 CSV 一致
                if hr.passed != int(r["passed"]) or hr.failed != int(r["failed"]):
                    raise ValueError(f"计数不一致：{r['test_name']}")
                if (
                    hr.flip_transitions == 0
                    and hr.valid_runs >= MIN_HISTORY_FOR_N2
                    and hr.failed == 0
                ):
                    n2.append(r)
            report_summary = {
                "tests_total": report.summary.tests_total,
                "flaky": report.summary.flaky,
                "stable": report.summary.stable,
                "inconclusive": report.summary.inconclusive,
                "files_parsed": report.files_parsed,
                "via": "junit-xml-pipeline",
            }
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    payload = {
        "schema_version": "1.0",
        "generated_utc": datetime.now(UTC).isoformat(),
        "definition": "N2 = N1 + 历史无翻转（valid_runs>=10, flip=0, 全过；"
        "全挂=env_fail 排除并单独报数）",
        "provenance_disclaimer": "历史 = negatives_recheck.csv 的 30 遍换进程"
        "运行记录（同 SHA 同容器，2026-10-13 实跑）；非目标项目公开 CI 历史",
        "min_history_for_n2": MIN_HISTORY_FOR_N2,
        "pipeline": report_summary,
        "counts": {
            "recheck_rows": len(rows),
            "env_fail_excluded": len(env_fail),
            "env_fail_tests": [r["test_name"] for r in env_fail],
            "n2_total": len(n2),
            "projects": len({r["project"] for r in n2}),
        },
        "n2_tests": [
            {
                "project": r["project"],
                "sha": r["sha"],
                "test_name": r["test_name"],
                "history_runs": int(r["runs"]),
                "flips": 0,
            }
            for r in n2
        ],
    }
    N2_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with N2_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["project", "sha", "test_name", "history_runs", "flips"])
        for r in n2:
            w.writerow([r["project"], r["sha"], r["test_name"], r["runs"], 0])

    c = payload["counts"]
    sys.stdout.write(
        f"[N2] recheck {c['recheck_rows']} 条 → env_fail 排除 {c['env_fail_excluded']}"
        f" → N2 {c['n2_total']} 条 / {c['projects']} 项目"
        f"（口径：≥{MIN_HISTORY_FOR_N2} 遍无翻转全过；"
        f"管道：{report_summary['via']}）\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
