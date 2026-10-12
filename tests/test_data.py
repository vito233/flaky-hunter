"""data.py 的单元测试（TODOLIST W1 DoD：表头校验 / 空值处理 / 多标签拆分）。"""

import pytest

from flaky_hunter.data import IdoftParseError, IdoftRecord, parse_idoft

SAMPLE = "\n".join(
    [
        # 表头（7 列）
        "Project URL,SHA Detected,Pytest Test Name,Category,Status,PR Link,Notes",
        # 正常行
        "https://github.com/mtik00/yamicache,1109d08b18be94bdee55e113309f7be29f25d840,"
        "tests/test_noclass.py::test_main,NIO,DeveloperFixed,,",
        # Status 尾随空格（已观测脏数据）
        "https://github.com/goodmami/penman,7770dfe14b3d0d197cedc6640f3ff7e3bd695726,"
        "tests/test_layout.py::test_rearrange,OD,Opened ,https://github.com/goodmami/penman/pull/102,",
        # Category 误落 Status 列（已观测脏数据，评审实算 3 条）
        "https://github.com/bad/row,,tests/test_x.py::test_a,,OD-Vic,,",
        # 多标签 Category
        "https://github.com/some/repo,abc,tests/test_multi.py::test_m,NOD;NIO,Accepted,,",
        # Category 为空
        "https://github.com/x/y,deadbeef,tests/test_e.py::test_e,,Rejected,,",
    ]
)


def test_parse_row_count_and_project() -> None:
    records = parse_idoft(SAMPLE)
    assert len(records) == 5
    first = records[0]
    assert isinstance(first, IdoftRecord)
    assert first.project == "mtik00/yamicache"
    assert first.sha == "1109d08b18be94bdee55e113309f7be29f25d840"
    assert first.status == "DeveloperFixed"


def test_header_mismatch_raises() -> None:
    with pytest.raises(IdoftParseError, match="表头不符"):
        parse_idoft("A,B,C\n1,2,3")
    with pytest.raises(IdoftParseError, match="空输入"):
        parse_idoft("")


def test_status_trailing_space_cleaned() -> None:
    penman = parse_idoft(SAMPLE)[1]
    assert penman.status == "Opened"  # "Opened " -> "Opened"
    assert not any("枚举外" in w for w in penman.warnings)


def test_category_misplaced_in_status_is_relocated() -> None:
    bad = parse_idoft(SAMPLE)[2]
    assert bad.category == ["OD-Vic"]
    assert bad.primary_category == "OD-Vic"
    assert bad.status == ""
    assert any("已归位" in w for w in bad.warnings)  # 清洗留痕


def test_multi_label_split_and_primary() -> None:
    multi = parse_idoft(SAMPLE)[3]
    assert multi.category == ["NOD", "NIO"]
    assert multi.primary_category == "NIO"  # 字母序第一个（§3.2）


def test_empty_category() -> None:
    empty = parse_idoft(SAMPLE)[4]
    assert empty.category == []
    assert empty.primary_category == ""
    assert empty.status == "Rejected"


def test_missing_sha_is_tolerated() -> None:
    bad = parse_idoft(SAMPLE)[2]
    assert bad.sha == ""  # 解析层宽松；业务校验交给下游


def test_owner_repo_from_nested_url() -> None:
    from flaky_hunter.data import _parse_project

    assert _parse_project("https://github.com/owner/repo/") == "owner/repo"
    assert _parse_project("https://github.com/owner/repo/tree/main") == "owner/repo"
    with pytest.raises(ValueError, match="owner/repo"):
        _parse_project("not-a-url")


def test_real_header_with_parenthetical_accepted() -> None:
    """真实 IDoFT 文件第三列表头带括号说明（2026-10-12 用 data/raw/py-data.csv 核验）。

    文档里"已核验"的简化写法是错的 —— 不兼容这个，第一次真实解析就会炸。
    """
    real_header = (
        "Project URL,SHA Detected,"
        "Pytest Test Name (PathToFile::TestClass::TestMethod "
        "or PathToFile::TestMethod),"
        "Category,Status,PR Link,Notes"
    )
    text = real_header + "\n" + SAMPLE.split("\n", 1)[1]
    records = parse_idoft(text)
    assert len(records) == 5
    assert records[0].test_name == "tests/test_noclass.py::test_main"


def test_bom_stripped() -> None:
    """真实文件可能带 UTF-8 BOM（phase-a 的 py-data.csv 就带）。"""
    records = parse_idoft("\ufeff" + SAMPLE)
    assert records[0].project == "mtik00/yamicache"


def test_real_py_data_file_parses() -> None:
    """对库里的真实原始数据跑一遍：1618 条，一条不丢。"""
    from pathlib import Path

    raw = Path(__file__).resolve().parents[1] / "data" / "raw" / "py-data.csv"
    if not raw.exists():
        pytest.skip("data/raw/py-data.csv 不在仓库里")
    records = parse_idoft(raw.read_text(encoding="utf-8-sig"))
    assert len(records) == 1618  # data-dictionary.md 的硬数字
    projects = {r.project for r in records}
    # 332 个去重仓库（343 是去重 URL 数 —— 有 11 个 URL 带 /issues、/tree
    # 子路径，和裸 URL 指向同一个仓库。2026-10-12 实测纠正）
    assert len(projects) == 332
