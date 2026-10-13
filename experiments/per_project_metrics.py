"""每项目（within-project）指标表 —— TODOLIST 原第 2 周 DoD：
"同时给出 within-project 和 cross-project 两套数字，并解释差距"。

数据源：experiments/results.csv（replay 模式）+ results_order.csv（顺序模式）。
合并判定 = 任一探头报警即报警（与 metrics_combined.json 的合并口径一致 ——
脚本自带防漂移自检：总体数字必须与 metrics_combined.json 对上，对不上就报错）。

cross-project 的说明（写在输出 note 里，也是面试答案）：
    本工具是**规则型检测器**（固定阈值 + 双模式判定），没有训练过程、
    没有可调参数 —— 不存在"在一个项目上调参、到另一个项目上失效"的
    train/test 泄漏问题，所以 cross-project 口径与总体口径相同。
    差距体现为**每项目召回的天花板不同**：样本量小的项目（如 Pyro5
    只有 1 条）召回数字波动大；负样本是每项目抽的，FP 列本身是
    IDoFT 漏记的 reservoir（N1 口径噪声），这就是精确率只能报下界的原因。

用法：
    uv run python experiments/per_project_metrics.py
"""

from __future__ import annotations

import csv
import json
import logging
from collections import defaultdict
from pathlib import Path

EXP = Path(__file__).resolve().parent
log = logging.getLogger("per_project")


def load_preds(name: str) -> dict[tuple[str, str], tuple[int, int, bool]]:
    """读一份 results CSV → {(project, test): (y_true, y_pred, excluded)}。"""
    out: dict[tuple[str, str], tuple[int, int, bool]] = {}
    with open(EXP / name, encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            key = (r["project"], r["test_name"])
            out[key] = (int(r["y_true"]), int(r["y_pred"]), r["excluded"] == "True")
    return out


def prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None]:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return precision, recall


def cell_of(y_true: int, y_pred: int) -> str:
    """混淆矩阵格子名（tp / fp / fn / tn）。"""
    if y_true == 1 and y_pred == 1:
        return "tp"
    if y_true == 0 and y_pred == 1:
        return "fp"
    if y_true == 1 and y_pred == 0:
        return "fn"
    return "tn"


def main() -> int:
    replay = load_preds("results.csv")
    order = load_preds("results_order.csv")
    keys = sorted(set(replay) | set(order))

    merged: dict[tuple[str, str], tuple[int, int, bool]] = {}
    for key in keys:
        r = replay.get(key)
        o = order.get(key)
        if r is None or o is None:
            have = f"replay={r is not None}, order={o is not None}"
            raise ValueError(f"两份 results 的键不齐：{key}（{have}）")
        if r[0] != o[0]:
            raise ValueError(f"y_true 不一致：{key}: {r[0]} vs {o[0]}")
        merged[key] = (r[0], max(r[1], o[1]), r[2] or o[2])

    by_project: dict[str, dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    )
    total = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for (proj, _test), (y_true, y_pred, excluded) in merged.items():
        if excluded:
            continue  # 排除行不进指标（与 metrics.py 同规则）
        by_project[proj][cell_of(y_true, y_pred)] += 1
        total[cell_of(y_true, y_pred)] += 1

    # 防漂移自检：总体必须与 metrics_combined.json 一致
    combined = json.loads((EXP / "metrics_combined.json").read_text(encoding="utf-8"))
    cm = combined["confusion_matrix"]
    for k in ("tp", "fp", "fn", "tn"):
        if total[k] != cm[k]:
            raise SystemExit(
                "防漂移自检失败："
                f"{k} 本脚本 {total[k]} != metrics_combined.json {cm[k]}"
            )

    table = []
    for proj in sorted(by_project):
        c = by_project[proj]
        precision, recall = prf(c["tp"], c["fp"], c["fn"])
        table.append(
            {
                "project": proj,
                "positives": c["tp"] + c["fn"],
                "negatives": c["fp"] + c["tn"],
                "tp": c["tp"],
                "fp": c["fp"],
                "fn": c["fn"],
                "tn": c["tn"],
                "precision_lower_bound": precision,
                "recall_at_idoft": recall,
            }
        )
    precision, recall = prf(total["tp"], total["fp"], total["fn"])
    overall = {
        "project": "OVERALL（= cross-project，无调参无泄漏）",
        "positives": total["tp"] + total["fn"],
        "negatives": total["fp"] + total["tn"],
        "tp": total["tp"],
        "fp": total["fp"],
        "fn": total["fn"],
        "tn": total["tn"],
        "precision_lower_bound": precision,
        "recall_at_idoft": recall,
    }
    table.append(overall)

    out = {
        "schema_version": "1.0",
        "mode": "replay+order 合并（任一探头报警即报警）",
        "note": (
            "within-project = 每项目内部算 P/R；cross-project = 总体口径 —— "
            "本工具是规则型检测器，无训练无调参，不存在 train/test 泄漏，"
            "两套口径相同是**结论**不是缺陷。每项目差异来自：① 样本量"
            "（Pyro5 仅 1 条正样本，数字波动大）② 负样本 N1 口径噪声 "
            "（IDoFT 漏记 reservoir），精确率只能报下界"
        ),
        "per_project": table,
        "drift_check": "与 metrics_combined.json 的 confusion_matrix 逐项一致",
    }
    (EXP / "metrics_per_project.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    header = (
        f"{'project':32} {'pos':>4} {'neg':>4} {'tp':>3} {'fp':>3}"
        f" {'fn':>3} {'tn':>3}  {'P(下界)':>8} {'Recall':>8}"
    )
    log.info(header)
    log.info("-" * len(header))
    for row in table:
        p = row["precision_lower_bound"]
        r = row["recall_at_idoft"]
        log.info(
            f"{row['project']:32} {row['positives']:>4} {row['negatives']:>4}"
            f" {row['tp']:>3} {row['fp']:>3} {row['fn']:>3} {row['tn']:>3}"
            f"  {'—' if p is None else f'{p:.3f}':>8}"
            f" {'—' if r is None else f'{r:.3f}':>8}"
        )
    log.info("→ experiments/metrics_per_project.json")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
