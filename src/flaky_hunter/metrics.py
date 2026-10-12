"""第一版指标：precision（下界）/ Recall@IDoFT / F1 + always-flaky 基线。

铁律 3：**任何准确率旁边必须有基线**，裸百分比不许出现。
检测任务的平凡基线 = "全都判 flaky"：召回 100%，精确率 = 正样本占比。
CI 门禁全报警等于没有门禁 —— 检测器要赢的是精确率，这个基线就是标尺。

两个指标都是**下界**（docs/limitations.md 第 1 条）：
    - 精确率低估：检测器抓到真 flaky 而 IDoFT 漏记 → 只能落进假阳性列
    - 召回率低估：重跑不足导致漏检（Recall@IDoFT 的分母只是 IDoFT 记录数，
      不是"世界上所有 flaky"）
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

# 判定口径：verdict == "flaky" 即"报警"。stable / inconclusive 都算未报警 ——
# 门禁只关心报不报，stable 与"证据不足不下结论"在门禁眼里是一回事。
FLAGGED_VERDICT = "flaky"


@dataclass
class Prediction:
    """一条 (项目, SHA, 测试) 的评测行。excluded=True 的不进指标计算。"""

    project: str
    sha: str
    test_name: str
    category: str  # IDoFT 主类别；负样本为 "N1"
    y_true: int  # 1 = IDoFT flaky 真值；0 = 自建负样本
    y_pred: int  # 1 = 检测器报警
    verdict: str
    status: str
    runs_total: int
    passed: int
    failed: int
    fail_rate: float | None
    count_passed: int = 0  # 同进程 --count 探头（进程内 NIO 的阳性证据）
    count_failed: int = 0
    count_fail_rate: float | None = None
    excluded: bool = False
    exclude_reason: str = ""
    note: str = ""


def confusion(preds: list[Prediction]) -> dict[str, int]:
    """混淆矩阵（只统计未排除的行）。"""
    cm = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for p in preds:
        if p.excluded:
            continue
        if p.y_true == 1 and p.y_pred == 1:
            cm["tp"] += 1
        elif p.y_true == 0 and p.y_pred == 1:
            cm["fp"] += 1
        elif p.y_true == 1 and p.y_pred == 0:
            cm["fn"] += 1
        else:
            cm["tn"] += 1
    return cm


def prf(cm: dict[str, int]) -> tuple[float, float, float]:
    """(precision, recall, F1)。分母为 0 时该指标记 0.0（调用方负责说明）。"""
    tp, fp, fn = cm["tp"], cm["fp"], cm["fn"]
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def always_flaky_baseline(cm: dict[str, int]) -> tuple[float, float, float]:
    """平凡基线：每个测试都报警。召回 100%，精确率 = 正样本占比。"""
    n = sum(cm.values())
    positives = cm["tp"] + cm["fn"]
    precision = positives / n if n else 0.0
    f1 = 2 * precision / (precision + 1.0) if precision else 0.0
    return precision, 1.0, f1


def by_category(preds: list[Prediction]) -> dict[str, dict[str, float | int]]:
    """按 IDoFT 主类别拆正样本召回（看检测器的盲区在哪一类）。"""
    out: dict[str, dict[str, float | int]] = {}
    for p in preds:
        if p.excluded or p.y_true != 1:
            continue
        cat = p.category or "(空)"
        row = out.setdefault(cat, {"n": 0, "detected": 0, "recall": 0.0})
        row["n"] = int(row["n"]) + 1
        row["detected"] = int(row["detected"]) + p.y_pred
    for row in out.values():
        row["recall"] = row["detected"] / row["n"] if row["n"] else 0.0
    return dict(sorted(out.items()))


def status_breakdown(preds: list[Prediction]) -> dict[str, int]:
    """被排除行的原因分布（环境失败不进指标，但要报数，不许悄悄消失）。"""
    counts: dict[str, int] = {}
    for p in preds:
        if p.excluded:
            key = p.exclude_reason or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def format_report(
    preds: list[Prediction],
    *,
    runs: int,
    negatives_per_project: int,
) -> list[str]:
    """生成人读报告行（硬格式：每个数字带基线，见 AGENTS.md）。"""
    cm = confusion(preds)
    precision, recall, f1 = prf(cm)
    base_p, base_r, base_f1 = always_flaky_baseline(cm)
    lines = [
        "replay 模式 · 第一版指标"
        f"（换进程重跑 ×{runs}；负样本 N1 ×{negatives_per_project}/项目）",
        f"精确率 {_pct(precision)}（下界；always-flaky 基线 {_pct(base_p)}）",
        f"Recall@IDoFT {_pct(recall)}（always-flaky 基线 {_pct(base_r)}）",
        f"F1 {f1:.3f}（always-flaky 基线 {base_f1:.3f}）",
        f"混淆矩阵 TP={cm['tp']} FP={cm['fp']} FN={cm['fn']} TN={cm['tn']}"
        f"（另有排除 {sum(1 for p in preds if p.excluded)} 条，见 breakdown）",
    ]
    cats = by_category(preds)
    if cats:
        lines.append("按 IDoFT 类别拆正样本召回：")
        for cat, row in cats.items():
            lines.append(
                f"  {cat:8s} {row['detected']}/{row['n']}  {_pct(row['recall'])}"
            )
    excluded = status_breakdown(preds)
    if excluded:
        lines.append(
            "排除（环境类失败，不计入指标）："
            + " · ".join(f"{k} {v}" for k, v in excluded.items())
        )
    return lines


def summarize(
    preds: list[Prediction],
    *,
    runs: int,
    negatives_per_project: int,
) -> dict:
    """整份摘要（写 metrics.json 用；results.csv 是逐行事实，这是聚合视图）。"""
    cm = confusion(preds)
    precision, recall, f1 = prf(cm)
    base_p, base_r, base_f1 = always_flaky_baseline(cm)
    return {
        "mode": "replay",
        "runs": runs,
        "negatives_per_project": negatives_per_project,
        "confusion_matrix": cm,
        "precision": precision,
        "recall_at_idoft": recall,
        "f1": f1,
        "baselines": {
            "always_flaky": {
                "precision": base_p,
                "recall": base_r,
                "f1": base_f1,
            },
        },
        "by_category": by_category(preds),
        "excluded": status_breakdown(preds),
        "n_rows": len(preds),
    }


def write_results_csv(preds: list[Prediction], path: Path) -> None:
    """逐行结果落盘（AGENTS.md：每个数字都要能在 experiments/results.csv 查到）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = [
        "project",
        "sha",
        "test_name",
        "category",
        "y_true",
        "y_pred",
        "verdict",
        "status",
        "runs_total",
        "passed",
        "failed",
        "fail_rate",
        "count_passed",
        "count_failed",
        "count_fail_rate",
        "excluded",
        "exclude_reason",
        "note",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for p in preds:
            w.writerow(asdict(p))


def write_metrics_json(summary: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
