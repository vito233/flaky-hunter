"""目标项目源码获取（codeload + 本地缓存）与测试路径解析。

铁律 1 的相关部分：本模块只往 `cache/` 里写，绝不碰 `data/raw/`。

为什么是 codeload 而不是 git clone（阶段 A 实测，2026-10-12）：
    - `github.com:443`（git 协议）本机不通；`codeload.github.com` 通
    - codeload 直接拿**指定 SHA** 的源码包，天然满足铁律 2（按 SHA Detected 检出），
      连 `git checkout` 都省了；解压出的顶层目录名就是 SHA，天然校验

⚠️ 顶层目录名必须从压缩包里面读，不能按 repo 名拼（阶段 A 血泪）：
    thundra-io/thundra-lambda-agent-python 已改名，包内目录是
    `thundra-agent-python-<sha>`，按名字拼会全卡在 FETCH_FAIL。
"""

from __future__ import annotations

import shutil
import tarfile
import urllib.request
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_fixed

CODELOAD_URL = "https://codeload.github.com/{project}/tar.gz/{sha}"

# 下载超时（秒）与重试：codeload 偶尔抽风，重试 3 次每次隔 2 秒
FETCH_TIMEOUT = 120
FETCH_ATTEMPTS = 3


class SourceError(Exception):
    """源码获取/解压失败。调用方按项目记 FAIL 换下一个，不恋战（规范 §2.5）。"""


def tarball_path(cache_dir: Path, project: str, sha: str) -> Path:
    """`{repo}-{sha}.tar.gz` —— 与阶段 A 的命名一致，方便直接沿用已下载的包。"""
    repo = project.split("/")[-1]
    return cache_dir / "tarballs" / f"{repo}-{sha}.tar.gz"


@retry(stop=stop_after_attempt(FETCH_ATTEMPTS), wait=wait_fixed(2), reraise=True)
def _download(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "flaky-hunter"})
    with (
        urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp,
        open(dest, "wb") as f,
    ):
        shutil.copyfileobj(resp, f)


def fetch_tarball(project: str, sha: str, cache_dir: Path) -> Path:
    """拿到指定 SHA 的源码包（已缓存则跳过下载）。失败抛 SourceError。"""
    dest = tarball_path(cache_dir, project, sha)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _download(CODELOAD_URL.format(project=project, sha=sha), dest)
    except Exception as ex:
        raise SourceError(
            f"下载失败 {project}@{sha[:12]}: {type(ex).__name__}: {ex}"
        ) from ex
    return dest


def ensure_source(project: str, sha: str, cache_dir: Path) -> Path:
    """解压源码到 `cache/src/{顶层目录名}/`，返回源码目录。已解压则直接复用。

    顶层目录名从包内读（见模块 docstring 的改名教训）；
    解压后校验目录名里含 SHA 前缀（codeload 的命名约定 `<repo>-<sha>`）。
    """
    src_root = cache_dir / "src"
    tgz = fetch_tarball(project, sha, cache_dir)

    with tarfile.open(tgz) as t:
        tops = {n.split("/")[0] for n in t.getnames() if "/" in n}
        if len(tops) != 1:
            raise SourceError(
                f"压缩包顶层目录不唯一（{len(tops)} 个），拒绝猜: {project}@{sha[:12]}"
            )
        top = tops.pop()
        dest = src_root / top
        if dest.is_dir():
            return dest  # 已解压过
        try:
            # filter="data"：挡住绝对路径/穿越路径的恶意条目（3.12+ 支持）
            t.extractall(src_root, filter="data")
        except TypeError:  # 老 Python 没有 filter 参数
            t.extractall(src_root)

    if not dest.is_dir():
        raise SourceError(f"解压后找不到源码目录: {dest}")
    if sha[:12] not in top:
        raise SourceError(f"顶层目录 {top} 不含 SHA {sha[:12]}，疑似拿错包")
    return dest


def resolve_test_path(src_dir: Path, target: str) -> str:
    """IDoFT 记的测试路径有时缺目录前缀，按文件名在仓库里搜一遍。

    实例（阶段 A 实测）：Pyro5 记的是 `test_pyro4compat.py::test_compat_config`，
    而文件实际在 `tests/test_pyro4compat.py`。
    """
    path, sep, node = target.partition("::")
    if (src_dir / path).exists():
        return target
    base = path.rsplit("/", 1)[-1]
    for cand in sorted(src_dir.rglob(base)):
        if not cand.is_file():
            continue
        rel = cand.relative_to(src_dir).as_posix()
        return f"{rel}{sep}{node}" if node else rel
    return target  # 搜不到就原样返回，让收集阶段如实报 NOT_FOUND
