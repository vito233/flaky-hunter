"""标签集裁剪规则测试（规范 §3.2，2026-10-13 定稿 = 6 类）。"""

import pytest

from flaky_hunter.attribution.labels import AttributionLabel, map_category


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 官方 12 类：≥20 样本的 5 类保留
        ("OD", AttributionLabel.OD),
        ("OD-Brit", AttributionLabel.OD_BRIT),
        ("NIO", AttributionLabel.NIO),
        ("NOD", AttributionLabel.NOD),
        ("OD-Vic", AttributionLabel.OD_VIC),
        # < 20 样本：ID 3 条 / UD 1 条必须并入 OTHER（评审附录 C）
        ("ID", AttributionLabel.OTHER),
        ("ID-HtF", AttributionLabel.OTHER),
        ("UD", AttributionLabel.OTHER),
        ("OSD", AttributionLabel.OTHER),
        ("TZD", AttributionLabel.OTHER),
        ("NDOD", AttributionLabel.OTHER),
        ("NDOI", AttributionLabel.OTHER),
        # 未知非空值 → OTHER 兜底
        ("SOMETHING_NEW", AttributionLabel.OTHER),
    ],
)
def test_map_category(raw: str, expected: AttributionLabel) -> None:
    assert map_category(raw) == expected


def test_map_category_empty_is_none() -> None:
    """空标签排除（全量 39 条）：调用方应剔除该样本，不能硬塞进 OTHER。"""
    assert map_category("") is None
    assert map_category("   ") is None
    assert map_category(None) is None


def test_map_category_multilabel_takes_first_alphabetical() -> None:
    """多标签取字母序第一个为主标签（规范 §3.2）。"""
    assert map_category("NIO;OD-Vic") == AttributionLabel.NIO
    assert map_category("OD-Vic;NIO") == AttributionLabel.NIO


def test_label_set_is_six_classes() -> None:
    """定稿标签集 = 6 类，改动等于改契约（须同步规范 §3.2 与 data-dictionary）。"""
    assert {lb.value for lb in AttributionLabel} == {
        "OD",
        "OD-Brit",
        "NIO",
        "NOD",
        "OD-Vic",
        "OTHER",
    }
