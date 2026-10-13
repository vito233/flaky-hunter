"""归因标签集与裁剪规则（项目规范 §3.2，2026-10-13 定稿）。

裁剪规则：官方 12 类枚举中，IDoFT Python 子集样本数 < 20 的类别合并为
OTHER；Category 为空的记录排除（缺标签无法评测）。
定稿依据：评审附录 C 全量分布（1618 条）+ ground_truth_phase_a.csv
本评测集实算（19 条 / 7 项目）。
"""

from __future__ import annotations

from enum import StrEnum


class AttributionLabel(StrEnum):
    """裁剪后标签集（规范 §3.2 定稿 = 6 类）。"""

    OD = "OD"
    OD_BRIT = "OD-Brit"
    NIO = "NIO"
    NOD = "NOD"
    OD_VIC = "OD-Vic"
    OTHER = "OTHER"


# Python 子集样本数 < 20 的官方类别 → OTHER（评审附录 C 全量分布实算：
# ID 3 条 / UD 1 条；NDOD / NDOI / OSD / TZD 在 Python 子集为 0 条，一并兜底）
_MERGED_INTO_OTHER: frozenset[str] = frozenset(
    {"ID", "ID-HtF", "UD", "OSD", "TZD", "NDOD", "NDOI"}
)


def map_category(raw: str | None) -> AttributionLabel | None:
    """官方 Category 原值 → 裁剪后标签。

    - 空白（None / 纯空格）→ None：调用方应排除该样本（缺标签无法评测）
    - 多标签（``;`` 分隔）→ 取字母序第一个为主标签（规范 §3.2）
    - 裁剪后标签集之外的任何非空值 → OTHER 兜底
    """
    if raw is None:
        return None
    parts = sorted(p.strip() for p in raw.split(";") if p.strip())
    if not parts:
        return None
    head = parts[0]
    if head in _MERGED_INTO_OTHER:
        return AttributionLabel.OTHER
    try:
        return AttributionLabel(head)
    except ValueError:
        return AttributionLabel.OTHER
