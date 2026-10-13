"""数据泄漏防线：few-shot 示例不得来自评测项目（TODOLIST 原第 4 周 DoD）。

机制：few-shot 池按项目登记 → 断言池中项目与评测项目集合无交集。
评测项目集合从 ground_truth_phase_a.csv（eval_eligible=true）程序生成，
不许手抄。填池子（真模型阶段）必须过这条断言。
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from flaky_hunter.attribution.prompt import FewShotExample

GROUND_TRUTH_CSV = (
    Path(__file__).resolve().parents[3] / "experiments" / "ground_truth_phase_a.csv"
)


def load_eval_projects(csv_path: Path = GROUND_TRUTH_CSV) -> set[str]:
    """评测项目集合 = ground_truth_phase_a.csv 中 eval_eligible=true 的 project。"""
    projects: set[str] = set()
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if row["eval_eligible"].strip().lower() == "true":
                projects.add(row["project"].strip())
    return projects


def find_leakage(
    few_shot: Iterable[FewShotExample],
    eval_projects: set[str],
) -> set[str]:
    """返回出现在 few-shot 池里的评测项目（空集 = 无泄漏）。"""
    return {ex.project for ex in few_shot if ex.project in eval_projects}


def assert_no_leakage(
    few_shot: Iterable[FewShotExample],
    eval_projects: set[str],
) -> None:
    """断言 few-shot 池不含任何评测项目；有泄漏直接抛 AssertionError。"""
    leaked = find_leakage(few_shot, eval_projects)
    if leaked:
        msg = f"few-shot 示例泄漏评测项目: {sorted(leaked)}"
        raise AssertionError(msg)
