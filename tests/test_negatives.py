"""negatives.py 的单元测试：N1 口径过滤 + 定种子抽样。"""

from flaky_hunter.negatives import filter_recorded, normalize_node, select_negatives


def test_normalize_node_variants() -> None:
    assert normalize_node("./tests/test_a.py::t") == "tests/test_a.py::t"
    assert normalize_node("tests\\test_a.py::t") == "tests/test_a.py::t"
    assert normalize_node("  tests/test_a.py::t  ") == "tests/test_a.py::t"


def test_filter_recorded_drops_exact_matches() -> None:
    cands = [
        "tests/test_a.py::test_one",
        "tests/test_a.py::test_two",
        "tests/test_b.py::test_three",
    ]
    kept = filter_recorded(cands, {"tests/test_a.py::test_one"})
    assert kept == ["tests/test_a.py::test_two", "tests/test_b.py::test_three"]


def test_filter_recorded_drops_file_level_node_with_recorded_inside() -> None:
    """文件级节点会把已知 flaky 一起跑，负样本 run 就脏了 —— 必须剔。"""
    cands = ["tests/test_a.py", "tests/test_b.py"]
    kept = filter_recorded(cands, {"tests/test_a.py::test_one"})
    assert kept == ["tests/test_b.py"]


def test_filter_recorded_normalizes_before_comparing() -> None:
    kept = filter_recorded(
        ["./tests/test_a.py::test_one"], {"tests/test_a.py::test_one"}
    )
    assert kept == []


def test_select_negatives_is_deterministic() -> None:
    cands = [f"tests/test_{i}.py::t" for i in range(20)]
    a = select_negatives(cands, per_project=3, seed=1729)
    b = select_negatives(list(reversed(cands)), per_project=3, seed=1729)
    assert a == b  # 与收集顺序无关
    assert len(a) == 3


def test_select_negatives_dedupes_and_caps() -> None:
    cands = ["tests/test_a.py::t", "./tests/test_a.py::t", "tests/test_b.py::t"]
    assert select_negatives(cands, per_project=10, seed=1) == [
        "tests/test_a.py::t",
        "tests/test_b.py::t",
    ]  # 去重后不足 per_project 就全给
