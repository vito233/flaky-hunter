"""审计修正 · 重算主指标（2026-10-13 真值口径修正）。

做什么（一次性审计脚本，可复现）：
    1. 用 IDoFT 全库索引（py-data.csv）对照 results.csv / results_order.csv
       的全部 y_true=0 行 —— 凡 (project, test_name) 命中全库的翻正
       （y_true 0→1，category 换 IDoFT 类别，note 加审计标记）。
       断言恰好命中 1 条（yamicache test_deco_timeout，NOD 同 SHA）——
       命中数变了说明数据变了，先查原因。
    2. 从修正后的 CSV 重算 replay / order / combined 三套 metrics json
       （复用 flaky_hunter.metrics 的聚合函数，保留旧 json 的额外字段）。
    3. 每份 json 加 "audit" 块（修正依据可追溯）。

预期数字（审计推算，脚本输出必须一致）：
    combined: TP=19 FP=0 FN=1 TN=13 → P 100.0%（下界）/ R 95.0% / F1 0.974
    基线也随正样本占比变：always-flaky 精确率 20/33 = 60.6%

用法：
    uv run python experiments/rebuild_metrics_post_audit.py --apply
    # 不加 --apply 只做 dry-run（打印将修正的行，不落盘）
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from flaky_hunter.idoft import load_idoft_index
from flaky_hunter.metrics import (
    Prediction,
    combine_modes,
    summarize,
    write_metrics_json,
)

EXPERIMENTS = Path(__file__).resolve().parent
REPO_ROOT = EXPERIMENTS.parent
PY_DATA = REPO_ROOT / "data" / "raw" / "py-data.csv"
TEST_NAME_COL = (
    "Pytest Test Name (PathToFile::TestClass::TestMethod or PathToFile::TestMethod)"
)

AUDIT_NOTE = (
    "AUDIT-2026-10-13: IDoFT 全库对照发现本测试同 SHA 已记录（NOD）；"
    "我们 replay 100 遍全过 + order 4 顺序全过未检出 → 按 Recall@IDoFT "
    "口径为 FN（原误标 TN）。见 experiments/2026-10-13-groundtruth-audit.md"
)


def load_predictions(path: Path) -> list[Prediction]:
    """通用 Prediction CSV 加载器（含 order 扩展列）。"""
    preds: list[Prediction] = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            preds.append(
                Prediction(
                    project=r["project"],
                    sha=r["sha"],
                    test_name=r["test_name"],
                    category=r["category"],
                    y_true=int(r["y_true"]),
                    y_pred=int(r["y_pred"]),
                    verdict=r["verdict"],
                    status=r["status"],
                    runs_total=int(r["runs_total"]),
                    passed=int(r["passed"]),
                    failed=int(r["failed"]),
                    fail_rate=float(r["fail_rate"]) if r["fail_rate"] else None,
                    count_passed=int(r["count_passed"]),
                    count_failed=int(r["count_failed"]),
                    count_fail_rate=float(r["count_fail_rate"])
                    if r["count_fail_rate"]
                    else None,
                    excluded=r["excluded"] == "True",
                    exclude_reason=r["exclude_reason"],
                    note=r["note"],
                    mode=r.get("mode", "replay"),
                    polluters=r.get("polluters", ""),
                    orders_tried=int(r.get("orders_tried") or 0),
                    minimized=r.get("minimized", "False") == "True",
                )
            )
    return preds


def find_flips(path: Path, index: dict[str, set[str]]) -> list[dict[str, str]]:
    """找出 y_true=0 但 IDoFT 全库已记录的行（应翻正为 FN 候选）。"""
    flips: list[dict[str, str]] = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["y_true"] == "0" and r["test_name"] in index.get(r["project"], set()):
                flips.append(r)
    return flips


def apply_flips(path: Path, flips: list[dict[str, str]], category_of) -> int:
    """原位修正 CSV 行（以 (project, sha, test_name) 为键）。返回修正行数。"""
    keys = {(r["project"], r["sha"], r["test_name"]) for r in flips}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        cols = reader.fieldnames
        rows = list(reader)
    n = 0
    for r in rows:
        key = (r["project"], r["sha"], r["test_name"])
        if key in keys and r["y_true"] == "0":
            r["y_true"] = "1"
            r["category"] = category_of[(r["project"], r["test_name"])]
            r["note"] = (r["note"] + " | " + AUDIT_NOTE) if r["note"] else AUDIT_NOTE
            n += 1
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return n


def rebuild_json(
    preds: list[Prediction],
    out_path: Path,
    *,
    mode: str,
    runs: int,
    negatives_per_project: int,
    extra_keys: dict,
    flip_keys: set[tuple[str, str, str]],
) -> dict:
    """重算指标并保留旧 json 的额外字段 + 加审计块。"""
    summary = summarize(
        preds,
        runs=runs,
        negatives_per_project=negatives_per_project,
        mode=mode,
    )
    old_extra: dict = {}
    if out_path.exists():
        old = json.loads(out_path.read_text(encoding="utf-8"))
        base_keys = {
            "mode",
            "runs",
            "negatives_per_project",
            "confusion_matrix",
            "precision",
            "recall_at_idoft",
            "f1",
            "baselines",
            "by_category",
            "excluded",
            "n_rows",
            "audit",
        }
        old_extra = {k: v for k, v in old.items() if k not in base_keys}
    summary.update(old_extra)
    summary["audit"] = {
        "date": "2026-10-13",
        "reason": (
            "IDoFT 全库对照：负样本剔除曾只对照真值表（20 条）而非 py-data.csv 全库"
        ),
        "corrected_rows": sorted("|".join(k) for k in flip_keys),
        "audit_doc": "experiments/2026-10-13-groundtruth-audit.md",
    }
    write_metrics_json(summary, out_path)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真写（缺省 dry-run）")
    args = ap.parse_args(argv)

    index = load_idoft_index(PY_DATA)
    with open(PY_DATA, encoding="utf-8-sig", newline="") as f:
        category_of = {
            (
                "/".join(r["Project URL"].rstrip("/").split("/")[-2:]),
                r[TEST_NAME_COL].strip(),
            ): r["Category"]
            for r in csv.DictReader(f)
        }

    flips = find_flips(EXPERIMENTS / "results.csv", index)
    flips_order = find_flips(EXPERIMENTS / "results_order.csv", index)
    all_flips = {(r["project"], r["sha"], r["test_name"]) for r in flips + flips_order}
    sys.stdout.write(
        f"[audit] 全库对照命中（y_true=0 → 应为 FN）：{len(all_flips)} 条\n"
    )
    for r in flips:
        cat = category_of[(r["project"], r["test_name"])]
        sys.stdout.write(
            f"  {r['project']} | {r['test_name']}"
            f" | 旧 category={r['category']} → 新 category={cat}\n"
        )

    # 幂等：CSV 已修正过（y_true 已翻正）时命中 0 条 —— 校验审计行状态后继续重算
    already_applied = False
    if not all_flips:
        with open(EXPERIMENTS / "results.csv", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if (
                    r["test_name"] == "tests/test_gc.py::test_deco_timeout"
                    and r["y_true"] == "1"
                    and "AUDIT-2026-10-13" in r["note"]
                ):
                    already_applied = True
    if already_applied:
        all_flips = {
            (
                "mtik00/yamicache",
                "1109d08b18be94bdee55e113309f7be29f25d840",
                "tests/test_gc.py::test_deco_timeout",
            )
        }
        sys.stdout.write("[audit] CSV 已含审计修正（幂等重算模式）\n")
    elif len(all_flips) != 1:
        sys.stdout.write(
            f"[audit] ✗ 命中数 {len(all_flips)} != 1"
            " —— 与审计结论不符，先查原因再跑 --apply\n"
        )
        return 2

    if not args.apply:
        sys.stdout.write("[audit] dry-run 结束（加 --apply 落盘）\n")
        return 0

    n1 = apply_flips(EXPERIMENTS / "results.csv", flips, category_of)
    n2 = apply_flips(EXPERIMENTS / "results_order.csv", flips_order, category_of)
    sys.stdout.write(
        f"[audit] CSV 修正：results.csv {n1} 行 / results_order.csv {n2} 行\n"
    )

    replay = load_predictions(EXPERIMENTS / "results.csv")
    order = load_predictions(EXPERIMENTS / "results_order.csv")

    old_order = json.loads(
        (EXPERIMENTS / "metrics_order.json").read_text(encoding="utf-8")
    )
    s_replay = rebuild_json(
        replay,
        EXPERIMENTS / "metrics.json",
        mode="replay",
        runs=100,
        negatives_per_project=2,
        extra_keys={},
        flip_keys=all_flips,
    )
    s_order = rebuild_json(
        order,
        EXPERIMENTS / "metrics_order.json",
        mode="order",
        runs=old_order.get("runs", 4),
        negatives_per_project=2,
        extra_keys=old_order,
        flip_keys=all_flips,
    )
    combined = combine_modes(replay, order)
    s_combined = rebuild_json(
        combined,
        EXPERIMENTS / "metrics_combined.json",
        mode="replay+order",
        runs=100,
        negatives_per_project=2,
        extra_keys=old_order,
        flip_keys=all_flips,
    )

    for name, s in (("replay", s_replay), ("order", s_order), ("combined", s_combined)):
        cm = s["confusion_matrix"]
        base = s["baselines"]["always_flaky"]
        sys.stdout.write(
            f"[audit] {name:8s}: TP={cm['tp']} FP={cm['fp']}"
            f" FN={cm['fn']} TN={cm['tn']}"
            f" | P {s['precision']:.3f} R {s['recall_at_idoft']:.3f}"
            f" F1 {s['f1']:.3f}"
            f" | 基线 P {base['precision']:.3f} F1 {base['f1']:.3f}\n"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
