"""数据泄漏防线测试（TODOLIST 原第 4 周 DoD：断言脚本跑起来能证明没有泄漏）。

机制：few-shot 示例按项目登记，断言池中项目与评测项目（ground_truth_phase_a.csv
eval_eligible=true 的 7 个项目）无交集。真模型阶段往池子里填示例时，这条防线自动生效。
"""

from pathlib import Path

import pytest

from flaky_hunter.attribution.labels import AttributionLabel
from flaky_hunter.attribution.leakage import (
    assert_no_leakage,
    find_leakage,
    load_eval_projects,
)
from flaky_hunter.attribution.prompt import FewShotExample

GROUND_TRUTH_CSV = (
    Path(__file__).resolve().parents[1] / "experiments" / "ground_truth_phase_a.csv"
)


def _example(project: str) -> FewShotExample:
    return FewShotExample(
        project=project,
        test_name="tests/test_demo.py::test_demo",
        failure_log="AssertionError: expected 1 got 2",
        label=AttributionLabel.NIO,
    )


def test_load_eval_projects_matches_ground_truth() -> None:
    projects = load_eval_projects(GROUND_TRUTH_CSV)
    assert len(projects) == 7  # 19 条 / 7 项目（项目现状附 4 实算）
    assert "didix21/mdutils" in projects
    assert "mtik00/yamicache" in projects


def test_empty_pool_has_no_leakage() -> None:
    eval_projects = load_eval_projects(GROUND_TRUTH_CSV)
    assert find_leakage([], eval_projects) == set()
    assert_no_leakage([], eval_projects)  # 不抛 = 地板阶段默认池合法


def test_eval_project_in_pool_raises() -> None:
    eval_projects = load_eval_projects(GROUND_TRUTH_CSV)
    assert "didix21/mdutils" in eval_projects
    with pytest.raises(AssertionError, match="didix21/mdutils"):
        assert_no_leakage([_example("didix21/mdutils")], eval_projects)


def test_non_eval_project_passes() -> None:
    """非评测项目（如人工构造的 synthetic）可以进池子。"""
    eval_projects = load_eval_projects(GROUND_TRUTH_CSV)
    assert_no_leakage([_example("synthetic/demo")], eval_projects)
    assert find_leakage([_example("some/other-project")], eval_projects) == set()
