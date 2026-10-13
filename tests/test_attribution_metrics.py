"""归因指标手算用例（不用 sklearn 的正确性兜底）。"""

import pytest

from flaky_hunter.attribution.metrics import (
    accuracy,
    confusion_matrix,
    macro_f1,
    per_class_f1,
)


def test_accuracy_counts_parse_fail_as_wrong() -> None:
    # 4 条：3 对 1 解析失败(None) → 0.75（解析失败计分母、按错算）
    assert accuracy(["A", "A", "B", "B"], ["A", "A", "B", None]) == 0.75


def test_per_class_f1_hand_computed() -> None:
    # y_true=[A,A,B], y_pred=[A,B,B]
    # A: tp=1 fp=0 fn=1 → P=1, R=0.5, F1=2/3
    # B: tp=1 fp=1 fn=0 → P=0.5, R=1, F1=2/3
    stats = per_class_f1(["A", "A", "B"], ["A", "B", "B"])
    assert stats["A"]["precision"] == 1.0
    assert stats["A"]["recall"] == 0.5
    assert stats["A"]["f1"] == pytest.approx(2 / 3)
    assert stats["B"]["precision"] == 0.5
    assert stats["B"]["recall"] == 1.0
    assert stats["B"]["f1"] == pytest.approx(2 / 3)


def test_macro_f1_parse_fail_counts_as_fp() -> None:
    # 解析失败不给"类"，只是相关类的 fp；macro 对真值类平均
    y_true = ["A", "A", "B", "B"]
    y_pred = ["A", "B", "B", None]
    # A: tp=1 fp=1 fn=1 → P=0.5 R=0.5 F1=0.5
    # B: tp=1 fp=0 fn=1 → P=1 R=0.5 F1=2/3
    assert macro_f1(y_true, y_pred) == pytest.approx((0.5 + 2 / 3) / 2)


def test_confusion_matrix_orientation() -> None:
    # 行=真值，列=预测；None 是独立列
    matrix = confusion_matrix(["A", "A", "B"], ["A", "B", None])
    assert matrix["A"] == {"A": 1, "B": 1, None: 0}
    assert matrix["B"] == {"A": 0, "B": 0, None: 1}


def test_empty_raises() -> None:
    with pytest.raises(ValueError, match="y_true 为空"):
        accuracy([], [])
