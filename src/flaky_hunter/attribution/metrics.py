"""归因评测指标（三件套）：准确率 / per-class P-R-F1 + macro-F1 / 混淆矩阵。

为什么手写而不用 scikit-learn（AGENTS.md 选型规则要求说明）：19 条样本、6 类
标签的一次性统计，sklearn 是几十 MB 的重依赖——规范 §4.3 精神（实验个位数
不上重装备）。正确性由 tests/test_attribution_metrics.py 的手算用例兜底。

口径：解析失败（label=None）计入分母、按判错算（最严格，主口径）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence


def confusion_matrix(
    y_true: Sequence[str], y_pred: Sequence[str]
) -> dict[str, dict[str, int]]:
    """行=真值，列=预测。预测侧额外含 None（解析失败）列。"""
    labels = sorted(set(y_true))
    pred_labels = sorted({p for p in y_pred if p is not None}, reverse=False)
    columns = pred_labels + ([None] if None in y_pred else [])  # type: ignore[list-item]
    matrix = {t: dict.fromkeys(columns, 0) for t in labels}
    for t, p in zip(y_true, y_pred, strict=True):
        matrix[t][p] += 1  # type: ignore[index]
    return matrix


def accuracy(y_true: Sequence[str], y_pred: Sequence[str | None]) -> float:
    """解析失败（None）计 0 分，计入分母（主口径，最严格）。"""
    if not y_true:
        msg = "y_true 为空"
        raise ValueError(msg)
    correct = sum(
        1 for t, p in zip(y_true, y_pred, strict=True) if p is not None and p == t
    )
    return correct / len(y_true)


def _prf(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def per_class_f1(
    y_true: Sequence[str], y_pred: Sequence[str | None]
) -> dict[str, dict[str, float]]:
    """逐类 precision / recall / f1。解析失败（None）计入各类的 fp。"""
    true_counter = Counter(y_true)
    stats: dict[str, dict[str, float]] = {}
    for label in sorted(true_counter):
        tp = sum(
            1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p == label
        )
        fp = sum(
            1 for t, p in zip(y_true, y_pred, strict=True) if t != label and p == label
        )
        fn = true_counter[label] - tp
        precision, recall, f1 = _prf(tp, fp, fn)
        stats[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": true_counter[label],
        }
    return stats


def macro_f1(y_true: Sequence[str], y_pred: Sequence[str | None]) -> float:
    """macro-F1 = 各真值类 F1 的简单平均（support 不加权）。"""
    stats = per_class_f1(y_true, y_pred)
    return sum(s["f1"] for s in stats.values()) / len(stats)
