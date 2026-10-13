"""IDoFT 全库对照单测 —— 把 2026-10-13 审计发现钉成防漂移断言。

核心断言：negatives_recheck.csv 的 56 条"负样本"对照 py-data.csv 全库，
必须恰好撞出 6 条 IDoFT 已记录（1 条 yamicache NIO + 2 条 yamicache OD-Vic
+ 1 条 yamicache NIO + 2 条 freezegun OD-Vic）—— 数字变了 = 数据或口径变了，
先查原因再改断言。
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from flaky_hunter.idoft import load_idoft_index, owner_repo

REPO_ROOT = Path(__file__).resolve().parents[1]
PY_DATA = REPO_ROOT / "data" / "raw" / "py-data.csv"
RECHECK = REPO_ROOT / "experiments" / "negatives_recheck.csv"


def test_owner_repo_extraction():
    assert owner_repo("https://github.com/mtik00/yamicache") == "mtik00/yamicache"
    assert owner_repo("https://github.com/a/b/") == "a/b"
    assert owner_repo("https://github.com/a/b.git") == "a/b"


def test_idoft_index_loads_full_dataset():
    index = load_idoft_index(PY_DATA)
    assert len(index) >= 300  # 去重 332 项目（审计实测），留余量防漂移
    total = sum(len(v) for v in index.values())
    assert total >= 1500  # Python 子集 1618 条记录量级
    # 已知条目必须在索引里（审计撞库的 6 条之一）
    assert "tests/test_gc.py::test_deco_timeout" in index["mtik00/yamicache"]


def test_recheck_negatives_contain_exactly_six_recorded():
    """审计钉子：56 条复核负样本对照全库 = 恰好 6 条 IDoFT 已记录。

    修正后这 6 条会被 load_idoft_index 对照剔除 —— 本断言保证
    「未来的重跑」与「本轮审计结论」可对账。
    """
    index = load_idoft_index(PY_DATA)
    with open(RECHECK, encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 56
    hits = sorted(
        (r["project"], r["test_name"])
        for r in rows
        if r["test_name"] in index.get(r["project"], set())
    )
    assert len(hits) == 6, hits
    projects = {p for p, _ in hits}
    assert projects == {"mtik00/yamicache", "spulec/freezegun", "chaosmail/python-fs"}


def test_filter_with_full_index_removes_recorded():
    """用全库索引喂 filter_recorded：已记录的被剔，未记录的保留。"""
    from flaky_hunter.negatives import filter_recorded

    index = load_idoft_index(PY_DATA)
    recorded = index["mtik00/yamicache"]
    candidates = [
        "tests/test_gc.py::test_deco_timeout",  # IDoFT 已记录（NOD）→ 剔
        "tests/test_noclass.py::test_main",  # IDoFT 已记录 → 剔
        "tests/test_something_new.py::test_new",  # 未记录 → 留
    ]
    kept = filter_recorded(candidates, recorded)
    assert kept == ["tests/test_something_new.py::test_new"]


def test_missing_pydata_raises(tmp_path):
    with pytest.raises(OSError):
        load_idoft_index(tmp_path / "nope.csv")
