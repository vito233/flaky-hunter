"""IDoFT 全库对照 —— N1 口径的对照范围铁律（2026-10-13 审计教训）。

背景：N1 的定义是「同项目、同 SHA 下 **IDoFT 全库** 未记录的测试」。
2026-10-13 审计发现：负样本剔除曾只对照阶段 A 真值表（约 20 条），
而 IDoFT 全库 py-data.csv 有 1618 条 —— 差两个数量级，导致
「IDoFT 已记录、真值表没有」的测试（7 条）混进了负样本池
（1 条进了 C1 评测 TN 实为 FN，6 条进了 N3 复核）。

铁律：**任何"未记录"判定都必须对数据全集做，不能对裁剪子集做。**
本模块提供全库索引，两个实验脚本（run_eval / run_negatives_recheck）
的 recorded 集合一律从这里取。
"""

from __future__ import annotations

import csv
from pathlib import Path

TEST_NAME_COL = (
    "Pytest Test Name (PathToFile::TestClass::TestMethod or PathToFile::TestMethod)"
)


def owner_repo(project_url: str) -> str:
    """https://github.com/owner/repo(.git) → owner/repo。"""
    url = project_url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return "/".join(url.split("/")[-2:])


def load_idoft_index(py_data_csv: Path) -> dict[str, set[str]]:
    """py-data.csv → {owner/repo: {Pytest Test Name, ...}} 全库索引。

    键 = (Project URL, Pytest Test Name) 的项目维度展开 —— 与
    filter_recorded 的节点 ID 匹配口径一致（同名测试跨 SHA 只记一次：
    IDoFT 规定每 (URL, Test) 三元组唯一行，SHA 取最新）。
    """
    index: dict[str, set[str]] = {}
    with open(py_data_csv, encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            proj = owner_repo(r["Project URL"])
            test = r[TEST_NAME_COL].strip()
            if test:
                index.setdefault(proj, set()).add(test)
    return index
