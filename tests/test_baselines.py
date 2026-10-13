"""基线常量断言（铁律 3 的技术兜底：基线错了立刻报错）。

数字来源：评审附录 C（全量 Category 分布实算）、ground_truth_phase_a.csv
（本评测集实算）与 arXiv 2101.09077。
"""

import csv
from pathlib import Path

import pytest

from flaky_hunter.attribution.labels import AttributionLabel, map_category
from flaky_hunter.baselines import (
    EVAL_MAJORITY_CLASS_BASELINE,
    EVAL_OD_BINARY_BASELINE,
    MAJORITY_CLASS_BASELINE,
    OD_BINARY_BASELINE,
    RERUN_FOR_95_CONFIDENCE,
)

GROUND_TRUTH_CSV = (
    Path(__file__).resolve().parents[1] / "experiments" / "ground_truth_phase_a.csv"
)


def test_majority_class_baseline() -> None:
    assert MAJORITY_CLASS_BASELINE == 0.495


def test_od_binary_baseline() -> None:
    assert OD_BINARY_BASELINE == 0.727


def test_rerun_count_for_confidence() -> None:
    assert RERUN_FOR_95_CONFIDENCE == 170


def test_eval_majority_class_baseline() -> None:
    """本评测集口径：19 条里 OD-Vic 12 条 → 12/19。"""
    assert pytest.approx(12 / 19) == EVAL_MAJORITY_CLASS_BASELINE


def test_eval_od_binary_baseline() -> None:
    """本评测集口径：OD 家族（OD-Vic 12 + OD 1）→ 13/19。"""
    assert pytest.approx(13 / 19) == EVAL_OD_BINARY_BASELINE


def test_eval_baselines_recomputed_from_ground_truth() -> None:
    """防漂移：从真值表实算（走 map_category 同一裁剪逻辑），必须与常量一致。"""
    with GROUND_TRUTH_CSV.open(encoding="utf-8-sig", newline="") as fh:
        rows = [
            r
            for r in csv.DictReader(fh)
            if r["eval_eligible"].strip().lower() == "true"
        ]
    labels = [map_category(r["category"]) for r in rows]
    assert None not in labels  # 本评测集不允许出现缺标签样本
    n = len(labels)
    majority = sum(1 for lb in labels if lb == AttributionLabel.OD_VIC) / n
    od_family = (
        sum(1 for lb in labels if lb in {AttributionLabel.OD_VIC, AttributionLabel.OD})
        / n
    )
    assert pytest.approx(majority) == EVAL_MAJORITY_CLASS_BASELINE
    assert pytest.approx(od_family) == EVAL_OD_BINARY_BASELINE
