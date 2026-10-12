"""基线常量断言（铁律 3 的技术兜底：基线错了立刻报错）。

数字来源：评审附录 C（Category 分布实算）与 arXiv 2101.09077。
"""

from flaky_hunter.baselines import (
    MAJORITY_CLASS_BASELINE,
    OD_BINARY_BASELINE,
    RERUN_FOR_95_CONFIDENCE,
)


def test_majority_class_baseline() -> None:
    assert MAJORITY_CLASS_BASELINE == 0.495


def test_od_binary_baseline() -> None:
    assert OD_BINARY_BASELINE == 0.727


def test_rerun_count_for_confidence() -> None:
    assert RERUN_FOR_95_CONFIDENCE == 170
