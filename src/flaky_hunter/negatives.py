"""负样本构造（规范 §2.3 口径 N1）。

IDoFT 是"被发现过的 flaky"的并集 —— **正样本管够，负样本一条都没有，必须自己造**。

N1（默认口径）：同项目、同 SHA 下 IDoFT **全库**（py-data.csv，1618 条）未记录的测试。
    ⚠ 对照范围铁律（2026-10-13 审计）：`filter_recorded` 的 `recorded` 参数
    必须来自 `flaky_hunter.idoft.load_idoft_index(data/raw/py-data.csv)`，
    **不许**用真值表 / 评测集等裁剪子集 —— 曾因对照 20 条子集，让 7 条
    IDoFT 已记录的 flaky 混进负样本池（1 条进 C1 TN 实为 FN、6 条进 N3）。
    偏差方向：检测器抓到真 flaky 而 IDoFT 漏记时只能落进假阳性列
    → 精确率被系统性低估（下界）。这是常规做法，FlakyCat / FlakyXbert
    也是自己补的 non-flaky 组。N2/N3 口径（CI 历史复核 / 抽样跑 K≥30 遍）
    见 docs/methodology.md，本项目第一版先用 N1。
"""

from __future__ import annotations

import random


def normalize_node(node: str) -> str:
    """统一测试节点 ID 写法：去 `./` 前缀、反斜杠转正、去首尾空白。"""
    return node.strip().replace("\\", "/").removeprefix("./")


def filter_recorded(candidates: list[str], recorded: set[str]) -> list[str]:
    """从收集到的测试里剔掉 IDoFT 已记录的。

    两条规则：
    1. 节点 ID 完全相同的，剔
    2. **文件级节点**（没有 `::`）但同文件里有已记录测试的，也要剔 ——
       跑整个文件会把已知 flaky 的测试一起跑，负样本run 就脏了
    """
    recorded_norm = {normalize_node(r) for r in recorded}
    recorded_files = {r.split("::")[0] for r in recorded_norm}
    kept: list[str] = []
    for cand in candidates:
        norm = normalize_node(cand)
        if norm in recorded_norm:
            continue
        if "::" not in norm and norm in recorded_files:
            continue
        kept.append(cand)
    return kept


def select_negatives(candidates: list[str], per_project: int, seed: int) -> list[str]:
    """按固定种子抽样（可复现）：先排序再 sample，结果与收集顺序无关。"""
    ordered = sorted({normalize_node(c) for c in candidates})
    if per_project >= len(ordered):
        return ordered
    rng = random.Random(seed)
    return sorted(rng.sample(ordered, per_project))
