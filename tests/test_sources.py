"""sources.py 的单元测试：codeload 命名、解压校验、测试路径解析。

不联网 —— 下载路径用本地文件假装缓存命中；解压用 tmp_path 里现造的 tar.gz。
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from flaky_hunter.sources import (
    SourceError,
    ensure_source,
    resolve_test_path,
    tarball_path,
)

SHA = "1109d08b18be94bdee55e113309f7be29f25d840"


def _make_tarball(path: Path, top: str) -> None:
    """造一个顶层目录为 top 的 tar.gz。"""
    src = path.parent / "stage"
    (src / top / "tests").mkdir(parents=True)
    (src / top / "tests" / "test_x.py").write_text(
        "def test_x(): pass\n", encoding="utf-8"
    )
    with tarfile.open(path, "w:gz") as t:
        t.add(src / top, arcname=top)


def test_tarball_path_uses_repo_and_sha(tmp_path: Path) -> None:
    p = tarball_path(tmp_path, "mtik00/yamicache", SHA)
    assert p.name == f"yamicache-{SHA}.tar.gz"
    assert p.parent == tmp_path / "tarballs"


def test_ensure_source_reads_top_dir_from_archive(tmp_path: Path) -> None:
    """顶层目录名从包内读，不按 repo 名拼（改名仓库的教训）。"""
    tgz = tarball_path(tmp_path, "thundra-io/thundra-lambda-agent-python", SHA)
    tgz.parent.mkdir(parents=True)
    _make_tarball(tgz, f"thundra-agent-python-{SHA}")

    src = ensure_source("thundra-io/thundra-lambda-agent-python", SHA, tmp_path)
    assert src.name == f"thundra-agent-python-{SHA}"
    assert (src / "tests" / "test_x.py").exists()

    # 再调一次走解压缓存，不重新解压
    assert ensure_source("thundra-io/thundra-lambda-agent-python", SHA, tmp_path) == src


def test_ensure_source_rejects_foreign_sha(tmp_path: Path) -> None:
    """顶层目录不含目标 SHA 前缀 → 疑似拿错包，拒收。"""
    tgz = tarball_path(tmp_path, "a/b", SHA)
    tgz.parent.mkdir(parents=True)
    _make_tarball(tgz, "b-deadbeefdeadbeef")
    with pytest.raises(SourceError, match="不含 SHA"):
        ensure_source("a/b", SHA, tmp_path)


def test_resolve_test_path_finds_missing_prefix(tmp_path: Path) -> None:
    """IDoFT 路径缺 tests/ 前缀时按文件名搜（Pyro5 实例）。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_pyro4compat.py").write_text("", encoding="utf-8")
    resolved = resolve_test_path(tmp_path, "test_pyro4compat.py::test_compat_config")
    assert resolved == "tests/test_pyro4compat.py::test_compat_config"


def test_resolve_test_path_keeps_existing(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    assert (
        resolve_test_path(tmp_path, "tests/test_a.py::test_b")
        == "tests/test_a.py::test_b"
    )


def test_resolve_test_path_returns_original_when_missing(tmp_path: Path) -> None:
    """搜不到就原样返回 —— 让收集阶段如实报 NOT_FOUND，不猜。"""
    assert resolve_test_path(tmp_path, "nope.py::t") == "nope.py::t"
