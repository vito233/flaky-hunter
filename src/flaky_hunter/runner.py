"""Docker 执行封装（规范 §2.5：目标项目运行环境一律 Docker，默认 --network none）。

## 为什么是"两段式"

    setup()  带网络的容器：把依赖装进 /deps（宿主机挂载目录，按项目缓存）
    replay() --network none 的容器：PYTHONPATH=/deps，目标测试**换进程**重跑 N 次

断网是铁律 —— 它自动过滤"依赖外部服务"的脏数据（一个连不上就挂的测试，
在有网环境里会随机翻转，在无网环境里稳定挂，后者才是可解释的信号）。
但装依赖不得不联网，所以拆成两个容器；依赖装一次缓存住，
后面 100 遍重跑不再重装（阶段 A 实测：单项目 pip 安装 40–90 秒）。

## 为什么重跑必须在容器内循环，而不是宿主起 N 个容器

容器启动 ~1–2 秒，N=100 就是 100–200 秒纯启动开销。
容器内循环：启动 1 次，里面跑 100 个**独立 pytest 进程** ——
每个进程哈希种子重新随机（修正 C：`--count=N` 同进程循环对哈希序相关的
flaky 完全无效，只能当快速初筛），又不用重复付容器启动的钱。

## 输出协议（宿主 ↔ 容器）

容器脚本往 stdout 打标记行，宿主解析：
    @@TOOLS_RC=0@@          测试工具链安装退出码
    @@PROJECT_RC=0@@        项目自身安装退出码（失败不阻塞，见 setup docstring）
    @@PROJECT_SKIPPED@@     项目装不上，按零依赖项目裸跑
    @@RUN 3 1@@             第 3 次重跑，pytest 退出码 1（0=过 5=没收集到 124=超时）
    @@FIRSTFAIL@@           首个非 0/5 退出的完整日志尾部（证据留存）
"""

from __future__ import annotations

import contextlib
import re
import subprocess
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

# 镜像按 commit 年份选（规范 §2.5）：老项目是老 Python 时代的东西
IMAGE_PY37 = "python:3.7-slim"
IMAGE_PY39 = "python:3.9-slim"
IMAGE_PY310 = "python:3.10-slim"

# 本机镜像仓库走国内 mirror（2026-10-12 实测）：
# 直连 Docker Hub 会被代理破坏 —— 层下载残缺，报
# `commit failed: ... failed size validation: 7975 != 7636`。
# 和 pip 必须走清华源是同一个病：官方源过代理会被动手脚。
REGISTRY_MIRROR = "docker.m.daocloud.io"


def mirror_image(name: str) -> str:
    """`python:3.9-slim` → `docker.m.daocloud.io/library/python:3.9-slim`。

    已带仓库前缀的不动。判定用 Docker 引用惯例：第一段含 `.`/`:` 或
    是 `localhost` → 已经是仓库地址。
    """
    head, sep, _ = name.partition("/")
    if sep and ("." in head or ":" in head or head == "localhost"):
        return name
    if sep:
        return f"{REGISTRY_MIRROR}/{name}"
    return f"{REGISTRY_MIRROR}/library/{name}"


def docker_prefix(bin_spec: str | tuple[str, ...]) -> list[str]:
    """docker 命令前缀。

    字符串 = 单命令（"docker"）；元组 = 多段前缀，用于跨层转发 ——
    gate 预演在 Windows 跑、Docker 在 WSL 里，走 `("wsl", "-d", ...,
    "-e", "docker")`。**-e（--exec）不可省**：wsl.exe 不带 -e 时会把
    参数拼成一条命令行再交给默认 shell 解释，多行脚本里的引号会被
    二次改写（2026-10-13 实测：bash 报 unexpected EOF，多行脚本全碎）。
    """
    if isinstance(bin_spec, str):
        return [bin_spec]
    return list(bin_spec)


# pip 走国内源（阶段 A 实测：官方源会被代理破坏，报 size validation 失败）
PIP_MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
# CI 门禁用（第 5 周）：GitHub Actions 的 runner 在海外，直连官方源更快
PIP_OFFICIAL = "https://pypi.org/simple"

# 默认镜像：3.9-slim 走 mirror（阶段 A 实测 7 个项目全部可跑；
# 方法论按年份选镜像是通则，见 image_for_year）
DEFAULT_IMAGE = mirror_image(IMAGE_PY39)

# 测试工具链：工具必装（失败才算环境没建起来）；setuptools 必须钉 <81 ——
# setuptools >= 81 删掉了 pkg_resources，老项目 `import pkg_resources` 会挂
# （2026-10-11 实测踩到，setuptools 82.0.1）。
DEFAULT_TOOLS = (
    "setuptools<81",
    "wheel",
    "pytest",
    "pytest-repeat",
    "pytest-randomly",
    "pytest-cov",
    "mock",
    "six",
    "nose2",
    "pytz",
)

# pytest 退出码 → 判定（与阶段 A rerun_probe.py 一致）
_RC_PASS = 0
_RC_NO_TESTS = 5
_RC_TIMEOUT = 124  # GNU coreutils timeout 的超时退出码


def image_for_year(year: int) -> str:
    """按 commit 年份选镜像（规范 §2.5）：2018–19 → 3.7，2020–21 → 3.9，更新 → 3.10。"""
    if year <= 2019:
        return IMAGE_PY37
    if year <= 2021:
        return IMAGE_PY39
    return IMAGE_PY310


def image_python(image: str) -> str:
    """`python:3.9-slim` → `3.9`（镜像标签版本，记进 env 备查）。"""
    tag = image.rsplit(":", 1)[-1] if ":" in image else ""
    return tag.split("-")[0] if tag else "unknown"


# ---------------------------------------------------------------- 脚本生成（纯函数）


def build_setup_script(
    tools: tuple[str, ...] = DEFAULT_TOOLS, pip_mirror: str = PIP_MIRROR
) -> str:
    """生成依赖安装脚本。

    两步分离（阶段 A 2026-10-12 定稿）：
      ① 工具必装：装不上 → 环境没建起来，后面全免谈
      ② 项目本身尽力装：标准装 → --no-build-isolation 兜底 → 跳过。
         零依赖项目从源码目录直接跑就行（PyGraph 实测：不装反而全收集到），
         所以项目装不上**不阻塞**。

    pip_mirror 可配（第 5 周 CI 门禁需要）：国内走清华源；GitHub Actions
    的 runner 在海外，直连官方源更快 —— 传 PIP_OFFICIAL 即可。
    """
    tools_list = " ".join(f"'{t}'" for t in tools)
    return (
        f"python -m pip install -q -i {pip_mirror} --target=/deps {tools_list}; "
        f"echo @@TOOLS_RC=$?@@; "
        f"python -m pip install -q -i {pip_mirror} --target=/deps . "
        f"|| python -m pip install -q -i {pip_mirror} "
        f"--no-build-isolation --target=/deps . "
        f"|| echo @@PROJECT_SKIPPED@@"
    )


def build_replay_script(target: str, runs: int, per_run_timeout: int) -> str:
    """生成"换进程重跑 N 次"的容器内脚本。

    三个关键细节（都是阶段 A 踩出来的）：
      - `unset PYTHONHASHSEED`：让每个进程拿到不同的哈希种子
      - `timeout <n>`：单次跑的熔断；挂死的测试记 124，不拖垮整个循环
      - 首个失败日志留尾：证据要能复盘，不能只报个数
    """
    return (
        "unset PYTHONHASHSEED\n"
        "first_fail=''\n"
        f"for i in $(seq 1 {runs}); do\n"
        f'  timeout {per_run_timeout} python -m pytest "$TARGET" -q --no-header '
        f"--no-cov -p no:cacheprovider > /tmp/r$i.log 2>&1\n"
        "  rc=$?\n"
        '  echo "@@RUN $i $rc@@"\n'
        '  if [ "$rc" != "0" ] && [ "$rc" != "5" ] && [ -z "$first_fail" ]; then\n'
        "    first_fail=/tmp/r$i.log\n"
        "  fi\n"
        "done\n"
        'if [ -n "$first_fail" ]; then\n'
        "  echo '@@FIRSTFAIL@@'; tail -40 \"$first_fail\"\n"
        "fi"
    )


def build_collect_script(testdir: str) -> str:
    """收集测试目录下全部测试节点 ID（负样本构造 / 顺序扫描用）。

    `-p no:randomly` 是顺序扫描的地基（2026-10-13 实测）：deps 里装了
    pytest-randomly，它会在 pytest_collection_modifyitems 里**随机重排**
    —— 不禁掉，收集顺序和后面的运行顺序都是随机的，"默认顺序"不可复现。
    """
    return (
        f'python -m pytest "{testdir}" --collect-only -q --no-header --no-cov '
        f"-p no:randomly -p no:cacheprovider 2>&1"
    )


def build_count_script(target: str, count: int, per_run_timeout: int) -> str:
    """生成同进程 `--count=N` 探头脚本（抓进程内状态污染型 NIO）。

    为什么留 JUnit XML：100 遍重跑的文本输出又长又乱，XML 里每次迭代是一条
    testcase，失败带 <failure> 子节点 —— 精确数"目标测试自己"挂了几次，
    不用去 FAILED 行里玩正则匹配（pingouin 假阳性教训）。

    为什么先"量体温"：慢测试（yamicache 的 test_deco_timeout 每轮约 2s）
    ×100 轮 = 200s，会超过默认单轮熔断被无声杀掉 —— XML 根本没写出来，
    表现成"无产出"。先跑一次计时，预算 = 实测 × 次数 × 2 倍余量，
    且不超过 count × per_run_timeout（保证每轮都有熔断上限）。
    """
    return (
        "unset PYTHONHASHSEED\n"
        f't0=$(date +%s); timeout {per_run_timeout} python -m pytest "$TARGET" '
        f"-q --no-header --no-cov -p no:cacheprovider > /tmp/warm.log 2>&1; "
        "t1=$(date +%s)\n"
        f"budget=$(( (t1 - t0 + 2) * {count} * 2 + 60 ))\n"
        f"if [ $budget -gt $(( {per_run_timeout} * {count} )) ]; then "
        f"budget=$(( {per_run_timeout} * {count} )); fi\n"
        'echo "@@WARM secs=$((t1-t0)) budget=$budget@@"\n'
        f'timeout $budget python -m pytest "$TARGET" --count={count} '
        f"-q --no-header --no-cov -p no:cacheprovider "
        f"--junit-xml=/tmp/count.xml > /tmp/count.log 2>&1\n"
        "echo @@COUNT_RC=$?@@\n"
        "echo @@XML_BEGIN@@\n"
        "cat /tmp/count.xml 2>/dev/null\n"
        "echo @@XML_END@@\n"
        "echo @@FIRSTFAIL@@\n"
        "grep -m1 '^FAILED' /tmp/count.log 2>/dev/null\n"
        "echo @@LOGTAIL@@\n"
        "tail -15 /tmp/count.log\n"
    )


def _sh_quote(text: str) -> str:
    """单引号包裹 + 内置单引号转义（`'` → `'\\''`）。

    测试节点 ID 里可能有空格（参数化 ID）、`::`、`[]`；单引号是 bash 里
    唯一不需要猜语义的写法。走环境变量也行，但顺序变换一次要传几十上百个
    节点 ID，命令行更直接。
    """
    return "'" + text.replace("'", "'\\''") + "'"


def build_sequence_script(tests: list[str], per_run_timeout: int) -> str:
    """生成"按给定顺序跑一组测试节点 ID"的容器内脚本（单进程）。

    顺序可控的三个前提（2026-10-13 实测钉住）：
      1. `-p no:randomly` —— deps 里装着 pytest-randomly，它会在
         pytest_collection_modifyitems 里随机重排；不禁掉，我们传什么
         顺序都被搅乱，顺序变换无从谈起
      2. pytest **按命令行顺序执行**节点 ID（含同文件内的多个节点）——
         实测验证过，不需要自己写收集插件
      3. JUnit XML 逐测试计数 —— 不玩 FAILED 行正则（pingouin 教训）

    整体熔断：`timeout $budget` 杀掉整个 pytest → XML 没写出来 →
    调用方看到 SEQ_TIMEOUT，不许把"没跑完"当成"都过了"。
    """
    args = " ".join(_sh_quote(t) for t in tests)
    budget = per_run_timeout * len(tests) + 60
    return (
        f"budget={budget}\n"
        f"timeout $budget python -m pytest {args} -q --no-header --no-cov "
        f"-p no:randomly -p no:cacheprovider --junit-xml=/tmp/seq.xml "
        f"--tb=line > /tmp/seq.log 2>&1\n"
        'echo "@@SEQ_RC=$?@@"\n'
        "echo @@XML_BEGIN@@\n"
        "cat /tmp/seq.xml 2>/dev/null\n"
        "echo @@XML_END@@\n"
        "echo @@LOGTAIL@@\n"
        "tail -15 /tmp/seq.log\n"
    )


# ---------------------------------------------------------------- 输出解析（纯函数）


@dataclass
class SetupOutcome:
    """依赖安装结果。tools_ok=False 表示环境没建起来（后面不用跑了）。"""

    tools_ok: bool
    project_installed: bool
    log_tail: str = ""


@dataclass
class ReplayOutcome:
    """重跑结果：sequence 里每个元素是一次独立进程的判定。"""

    sequence: list[str] = field(
        default_factory=list
    )  # pass / fail / timeout / notfound
    first_failure_log: str = ""
    docker_error: str = ""  # 非空 = 容器本身没跑起来（我们的问题，不是项目的问题）


@dataclass
class CollectOutcome:
    """测试收集结果。ok=False 时 tests=[]，reason 说明为什么收集不到。"""

    ok: bool
    tests: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class CountOutcome:
    """同进程 `--count=N` 探头的 parsed 结果。

    这个探头**只能作阳性证据**（修正 C）：观察到翻转 → 确实是 flaky；
    没观察到 → 不能证明稳定（PYTHONHASHSEED / 全局 random 不重置）。

    short=True：实际迭代数 < 请求数。已知成因：unittest.TestCase 风格的
    测试不适用 pytest-repeat 的参数化重复（mdutils 28 条全是，实测只跑 1 次）。
    """

    ok: bool
    passed: int = 0
    failed: int = 0
    total: int = 0
    reason: str = ""
    log_tail: str = ""
    expected: int = 0
    warm_secs: float = 0.0
    budget_secs: int = 0
    first_fail_line: str = ""  # 首个 FAILED 行（翻转证据，落盘留痕用）

    @property
    def short(self) -> bool:
        """请求了 N 次却只跑了更少 —— 阳性证据不足，且"没翻"的结论要打折看。"""
        return self.ok and self.expected > 0 and self.total < self.expected


@dataclass
class SequenceOutcome:
    """按序跑一组测试的结果。

    outcomes 的键是**调用方传进去的节点 ID**（不是 XML 里的名字）——
    逐个映射回去，调用方不用猜哪个失败对应哪个输入。
    值域：pass / fail / error / skipped / missing。
        missing = XML 里没有这个节点的任何记录（没收集到，路径或名字不对）
    ok=False 时看 reason：SEQ_TIMEOUT（整体熔断）/ NO_XML /
    XML_PARSE_ERROR / EMPTY_SEQUENCE。
    """

    ok: bool
    outcomes: dict[str, str] = field(default_factory=dict)
    log_tail: str = ""
    docker_error: str = ""  # 非空 = 容器本身没跑起来（我们的问题）
    reason: str = ""
    rc: int = -1


_RUN_RE = re.compile(r"^@@RUN (\d+) (\d+)@@$")


def parse_setup_output(output: str) -> SetupOutcome:
    """解析安装输出。认不出 TOOLS_RC 标记 → 视为环境没建起来（不猜）。"""
    m = re.search(r"@@TOOLS_RC=(\d+)@@", output)
    if not m:
        return SetupOutcome(
            tools_ok=False, project_installed=False, log_tail=_tail(output)
        )
    tools_ok = m.group(1) == "0"
    project_installed = "@@PROJECT_SKIPPED@@" not in output
    return SetupOutcome(
        tools_ok=tools_ok, project_installed=project_installed, log_tail=_tail(output)
    )


def parse_replay_output(output: str) -> ReplayOutcome:
    """解析重跑输出：退出码 → pass/fail/timeout/notfound。"""
    outcome = ReplayOutcome()
    for line in output.splitlines():
        m = _RUN_RE.match(line.strip())
        if not m:
            continue
        rc = int(m.group(2))
        if rc == _RC_PASS:
            outcome.sequence.append("pass")
        elif rc == _RC_NO_TESTS:
            outcome.sequence.append("notfound")
        elif rc == _RC_TIMEOUT:
            outcome.sequence.append("timeout")
        else:
            outcome.sequence.append("fail")
    if "@@FIRSTFAIL@@" in output:
        outcome.first_failure_log = output.split("@@FIRSTFAIL@@", 1)[1].strip()
    return outcome


_NODE_RE = re.compile(r"^[\w./\-]+\.py(::.+)?$")


def _count_target_matches(classname: str, name: str, target: str) -> bool:
    """JUnit XML 的一条 testcase 是不是目标测试的某次迭代。

    pytest-repeat 会给 name 加 `[i-N]` 后缀（实测：`test_main[2-3]`）；
    classname 是点号路径，类方法还会带类名（`tests.test_b.TestC`）。
    """
    path, _, node = target.partition("::")
    file_key = path.replace("/", ".").removesuffix(".py")
    node_parts = node.split("::") if node else []
    base_name = name.split("[", 1)[0]  # 砍掉 pytest-repeat 的 [i-N] 后缀
    if node_parts and base_name != node_parts[-1]:
        return False
    if not node_parts and base_name != path.rsplit("/", 1)[-1].removesuffix(".py"):
        return False
    expected_cls = ".".join([file_key, *node_parts[:-1]])
    return (
        classname in (file_key, expected_cls)
        or classname.endswith(f".{file_key}")
        or classname.endswith(f".{expected_cls}")
    )


def parse_count_output(output: str, target: str, expected: int = 0) -> CountOutcome:
    """解析 --count 探头输出（XML 精确计数，日志尾部留证）。

    expected = 请求的迭代数；实际跑出来的少于它 → short=True（打标，不静默）。
    rc=124（预算耗尽）→ COUNT_TIMEOUT，不许伪装成"没翻转"。
    """
    if "@@XML_BEGIN@@" not in output or "@@XML_END@@" not in output:
        return CountOutcome(
            ok=False, reason="NO_XML", expected=expected, log_tail=_tail(output)
        )
    xml = output.split("@@XML_BEGIN@@", 1)[1].split("@@XML_END@@", 1)[0]
    after_xml = output.split("@@XML_END@@", 1)[1]
    first_fail_line = ""
    if "@@FIRSTFAIL@@" in after_xml:
        segment = after_xml.split("@@FIRSTFAIL@@", 1)[1]
        first_fail_line = segment.split("@@LOGTAIL@@", 1)[0].strip()
    log_tail = (
        after_xml.split("@@LOGTAIL@@", 1)[1] if "@@LOGTAIL@@" in after_xml else ""
    )

    warm_secs = 0.0
    budget_secs = 0
    m = re.search(r"@@WARM secs=(\d+) budget=(\d+)@@", output)
    if m:
        warm_secs = float(m.group(1))
        budget_secs = int(m.group(2))

    # 预算耗尽被 timeout 杀掉 → XML 要么没有要么不完整，给明确理由
    rc_m = re.search(r"@@COUNT_RC=(\d+)@@", output)
    if rc_m and rc_m.group(1) == "124":
        return CountOutcome(
            ok=False,
            reason="COUNT_TIMEOUT",
            expected=expected,
            log_tail=log_tail,
            warm_secs=warm_secs,
            budget_secs=budget_secs,
            first_fail_line=first_fail_line,
        )

    try:
        # strip：echo 标记行会在 XML 前留一个换行，而 <?xml 声明必须在实体开头
        root = ElementTree.fromstring(xml.strip())
    except ElementTree.ParseError:
        return CountOutcome(
            ok=False,
            reason="XML_PARSE_ERROR",
            expected=expected,
            log_tail=log_tail,
            warm_secs=warm_secs,
            budget_secs=budget_secs,
            first_fail_line=first_fail_line,
        )

    passed = failed = 0
    for tc in root.iter("testcase"):
        if not _count_target_matches(
            tc.get("classname", ""), tc.get("name", ""), target
        ):
            continue
        if tc.find("failure") is not None or tc.find("error") is not None:
            failed += 1
        else:
            passed += 1
    if passed + failed == 0:
        # XML 里没有目标测试的任何迭代 → 没收集到（路径/测试名不对）
        return CountOutcome(
            ok=False,
            reason="NO_MATCH",
            expected=expected,
            log_tail=log_tail,
            warm_secs=warm_secs,
            budget_secs=budget_secs,
            first_fail_line=first_fail_line,
        )
    return CountOutcome(
        ok=True,
        passed=passed,
        failed=failed,
        total=passed + failed,
        expected=expected,
        log_tail=log_tail,
        warm_secs=warm_secs,
        budget_secs=budget_secs,
        first_fail_line=first_fail_line,
    )


def parse_collect_output(output: str) -> CollectOutcome:
    """解析 --collect-only 输出：只认长得像测试节点 ID 的行。

    汇总行（"23 tests collected in 0.01s"）、警告行都不匹配，天然被过滤。
    """
    tests = [
        line.strip()
        for line in output.splitlines()
        if _NODE_RE.match(line.strip()) and not line.strip().startswith("=")
    ]
    if not tests:
        low = output.lower()
        if "modulenotfounderror" in low or "importerror" in low:
            reason = "IMPORT_ERROR"
        elif "error" in low:
            reason = "COLLECT_ERROR"
        else:
            reason = "NO_RESULT"
        return CollectOutcome(ok=False, tests=[], reason=reason)
    return CollectOutcome(ok=True, tests=tests)


# ---------------------------------------------------------------- 顺序执行解析

# 严重度排序：同一节点的多次迭代（参数化）取最坏的一次
_SEVERITY = {"pass": 0, "skipped": 1, "fail": 2, "error": 3}


def _node_matches(classname: str, name: str, node: str) -> bool:
    """JUnit XML 的一条 testcase 是不是输入节点 `node` 的某次执行。

    XML 只有 classname（点号路径，类方法还带类名）和 name，没有 file 属性
    （2026-10-13 实测 pytest 8.4.2）—— 按输入节点反推期望的 classname/name
    再比对。规则（与 parse_count_output 的 _count_target_matches 同族）：
      - name 精确相等，或 name 是参数化展开（`test_p[1-2]` vs 节点 `test_p`）
      - classname 等于 `模块点号路径[.类名]`，或以其结尾（容忍前缀差异）
      - 文件级节点（无 `::`）：该文件下任何测试都算它
    """
    path, _, rest = node.partition("::")
    module = path.replace("/", ".").removesuffix(".py")
    if not rest:
        return classname == module or classname.startswith(f"{module}.")
    parts = rest.split("::")
    want_name = parts[-1]
    want_cls = ".".join(parts[:-1])
    expected_cls = f"{module}.{want_cls}" if want_cls else module
    if name != want_name and not name.startswith(f"{want_name}["):
        return False
    return classname == expected_cls or classname.endswith(f".{expected_cls}")


def parse_sequence_output(output: str, tests: list[str]) -> SequenceOutcome:
    """解析顺序执行输出：XML testcase → 输入节点 ID 的判定映射。

    rc=124（整体熔断）→ SEQ_TIMEOUT，给明确理由，不伪装成"都过了"。
    """
    rc_m = re.search(r"@@SEQ_RC=(\d+)@@", output)
    rc = int(rc_m.group(1)) if rc_m else -1
    if "@@XML_BEGIN@@" not in output or "@@XML_END@@" not in output:
        reason = "SEQ_TIMEOUT" if rc == _RC_TIMEOUT else "NO_XML"
        return SequenceOutcome(ok=False, reason=reason, rc=rc, log_tail=_tail(output))
    xml = output.split("@@XML_BEGIN@@", 1)[1].split("@@XML_END@@", 1)[0]
    after_xml = output.split("@@XML_END@@", 1)[1]
    log_tail = (
        after_xml.split("@@LOGTAIL@@", 1)[1] if "@@LOGTAIL@@" in after_xml else ""
    )
    if not xml.strip():
        # 标记在、XML 空 —— pytest 自己崩了（实测 freezegun：假时间泄漏进
        # pytest 计时器，INTERNALERROR，XML 没写出来）。给独立理由，
        # 不和"XML 损坏"混为一谈，log_tail 里有完整堆栈可查。
        return SequenceOutcome(ok=False, reason="XML_EMPTY", rc=rc, log_tail=log_tail)
    try:
        # strip：echo 标记行会在 XML 前留一个换行，声明必须在实体开头
        root = ElementTree.fromstring(xml.strip())
    except ElementTree.ParseError:
        return SequenceOutcome(
            ok=False, reason="XML_PARSE_ERROR", rc=rc, log_tail=log_tail
        )

    verdicts: dict[str, str] = {}
    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        node = next((n for n in tests if _node_matches(classname, name, n)), None)
        if node is None:
            continue  # 不在输入列表里的测试（理论上不会有），忽略
        verdict = "pass"
        if tc.find("failure") is not None:
            verdict = "fail"
        elif tc.find("error") is not None:
            verdict = "error"
        elif tc.find("skipped") is not None:
            verdict = "skipped"
        current = verdicts.get(node)
        # 同节点的多次迭代（参数化）取最坏的一次；pass 也要能落账
        if current is None or _SEVERITY[verdict] > _SEVERITY[current]:
            verdicts[node] = verdict
    outcomes = {t: verdicts.get(t, "missing") for t in tests}
    return SequenceOutcome(ok=True, outcomes=outcomes, rc=rc, log_tail=log_tail)


def _tail(text: str, n: int = 15) -> str:
    lines = text.strip().splitlines()
    return "\n".join(lines[-n:])


# ---------------------------------------------------------------- Docker 执行层


@dataclass
class DockerRunner:
    """Docker 执行封装。默认断网跑测试；装依赖的阶段单独带网。

    image 默认走国内 mirror（见 mirror_image 的实测注释）——
    直连官方镜像源会被代理破坏。

    use_volumes=True 时，源码/依赖不进 9p 挂载，而是拷进 Docker 卷
    （住在 WSL2 VM 里）。实测（2026-10-12，yamicache 单测试 5 连跑）：
        9p 挂载   62s（12.5s/次，其中 ~10s 是 I/O 等待）
        :cached   67s（没用）
        Docker 卷 27s（5.4s/次，剩下的是 CPU —— 且这是在 3 个评测容器
                   抢核的前提下测的）
    100 遍重跑差出来就是 20 分钟级别，所以默认走卷。
    """

    image: str = mirror_image(IMAGE_PY39)
    pip_mirror: str = PIP_MIRROR
    docker_bin: str | tuple[str, ...] = "docker"
    run_network: str = "none"
    tools: tuple[str, ...] = DEFAULT_TOOLS
    use_volumes: bool = True

    # ---- 底层：跑一个容器 ----

    def _volume_name(self, kind: str, key: str) -> str:
        """卷名：`fh-{kind}-{key}`。key 一般是目录名（仓库名-SHA 前缀），唯一。"""
        safe = re.sub(r"[^0-9a-zA-Z_.-]+", "-", key).strip("-")
        return f"fh-{kind}-{safe}"

    def _volume_exists(self, name: str) -> bool:
        try:
            p = subprocess.run(
                [*docker_prefix(self.docker_bin), "volume", "inspect", name],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=60,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        return p.returncode == 0

    def _ensure_volume(self, name: str, host_dir: Path) -> bool:
        """卷不存在就从 host_dir 种子化（一次性 cp）。

        失败返回 False，让调用方退回 9p 挂载（保底能跑，不炸）。
        """
        if self._volume_exists(name):
            return True
        try:
            subprocess.run(
                [*docker_prefix(self.docker_bin), "volume", "create", name],
                timeout=60,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            win = str(host_dir).replace("\\", "/")
            subprocess.run(
                [
                    *docker_prefix(self.docker_bin),
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "-v",
                    f"{win}:/from",
                    "-v",
                    f"{name}:/to",
                    self.image,
                    "bash",
                    "-c",
                    "cp -r /from/. /to/ 2>/dev/null || true",
                ],
                timeout=600,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        return True

    def _mount_args(self, kind: str, host_dir: Path, target: str) -> list[str]:
        """生成 -v 参数对：卷（默认，快）或 9p 挂载（兜底）。"""
        win = str(host_dir).replace("\\", "/")
        if not self.use_volumes:
            return ["-v", f"{win}:{target}"]
        vol = self._volume_name(kind, host_dir.name)
        if not self._ensure_volume(vol, host_dir):
            return ["-v", f"{win}:{target}"]  # 卷方案失败 → 退回挂载，保底能跑
        return ["-v", f"{vol}:{target}"]

    def _run_container(
        self,
        script: str,
        src_dir: Path,
        deps_dir: Path | None = None,
        network: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 900,
    ) -> tuple[str | int, str]:
        """跑一个 --rm 容器，返回 (退出码或 "TIMEOUT"/"NOBIN", 合并后的输出)。

        stderr 必须并进 stdout（阶段 A 实测）：分开捕获再拼接会把容器里 pip 的
        报错整体挪到输出末尾，诊断信息错位（PyGraph 的 INSTALL_FAIL 因此
        打不出报错细节）。
        """
        cmd = [
            *docker_prefix(self.docker_bin),
            "run",
            "--rm",
            *self._mount_args("src", src_dir, "/w"),
            "-w",
            "/w",
        ]
        if deps_dir is not None:
            cmd += [
                *self._mount_args("deps", deps_dir, "/deps"),
                "-e",
                "PYTHONPATH=/deps",
            ]
        cmd += ["-e", "PYTHONDONTWRITEBYTECODE=1"]
        if env:
            cmd += [item for kv in env.items() for item in ("-e", f"{kv[0]}={kv[1]}")]
        cmd += [
            "--network",
            network or self.run_network,
            self.image,
            "bash",
            "-c",
            script + " 2>&1",
        ]
        try:
            p = subprocess.run(
                cmd,
                timeout=timeout,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            return "TIMEOUT", ""
        except FileNotFoundError as ex:
            return "NOBIN", str(ex)
        return p.returncode, p.stdout or ""

    # ---- 第一段：装依赖（带网） ----

    def run_script(
        self,
        script: str,
        src_dir: Path,
        deps_dir: Path | None = None,
        timeout: int = 900,
    ) -> tuple[int | str, str]:
        """跑一段自定义脚本（默认断网，与测试同一环境）—— 公开薄转发。

        用户：gate 的文件级 --count 探头（第 5 周 CI 门禁）；后续模式 B
        （历史翻转）大概率也走这里。不另起抽象层：参数语义与 _run_container
        完全一致，只是不再要求调用方住在本模块内部。
        """
        return self._run_container(script, src_dir, deps_dir=deps_dir, timeout=timeout)

    def setup(self, src_dir: Path, deps_dir: Path, timeout: int = 900) -> SetupOutcome:
        """把测试工具链 + 项目依赖装好（pip --target=/deps）。

        带网跑（pip 要联网）；装什么、怎么解析见 build_setup_script /
        parse_setup_output。缓存判定（三种情况都不重装）：
          1. 同目录旁的 `.done` 标记在 → 上一轮装好了
          2. 宿主机 deps_dir 非空（比如旧版挂载模式装的）→ 搬进卷，跳过安装
        卷模式下依赖装进 Docker 卷（VM 内 I/O）；标记写在宿主机，跨轮次有效。
        """
        deps_dir = Path(deps_dir)
        # POSIX 风格路径（gate 预演传 WSL 路径）：宿主侧建不了就不建，
        # 挂载点由 Docker 兜底自动创建 —— 不让目录创建挡住安装
        with contextlib.suppress(OSError):
            deps_dir.mkdir(parents=True, exist_ok=True)
        marker = deps_dir.parent / f"{deps_dir.name}.done"
        if marker.exists():
            return SetupOutcome(
                tools_ok=True, project_installed=True, log_tail="(缓存复用，跳过安装)"
            )
        try:
            host_has_deps = any(deps_dir.iterdir())
        except OSError:
            # 宿主侧看不到这个目录（POSIX 风格路径 / 权限）→ 视为无缓存
            host_has_deps = False
        if host_has_deps:
            # 宿主机已有装好的依赖 → 搬进卷即用，不重装
            if not self.use_volumes:
                marker.write_text("host cache", encoding="utf-8")
                return SetupOutcome(
                    tools_ok=True,
                    project_installed=True,
                    log_tail="(宿主机缓存复用，跳过安装)",
                )
            vol = self._volume_name("deps", deps_dir.name)
            if self._ensure_volume(vol, deps_dir):
                marker.write_text("seeded from host", encoding="utf-8")
                return SetupOutcome(
                    tools_ok=True,
                    project_installed=True,
                    log_tail="(依赖从宿主机缓存搬入卷，跳过安装)",
                )
            # 卷失败 → 继续走下面的安装流程（挂载模式兜底）

        rc, out = self._run_container(
            build_setup_script(self.tools, pip_mirror=self.pip_mirror),
            src_dir,
            deps_dir=deps_dir,
            network="bridge",
            timeout=timeout,
        )
        if rc == "TIMEOUT":
            return SetupOutcome(
                tools_ok=False,
                project_installed=False,
                log_tail=f"安装超时（{timeout}s）",
            )
        if rc == "NOBIN":
            return SetupOutcome(
                tools_ok=False,
                project_installed=False,
                log_tail=f"找不到 docker: {out}",
            )
        outcome = parse_setup_output(out)
        if outcome.tools_ok:
            # 宿主侧写不了（如 gate 预演传 POSIX 风格路径）→ 只损失缓存
            # （下轮重装），功能不受影响 —— 不让它炸掉整个 setup
            with contextlib.suppress(OSError):
                marker.write_text("installed", encoding="utf-8")
        return outcome

    # ---- 第二段：跑测试（断网） ----

    def replay(
        self,
        src_dir: Path,
        deps_dir: Path,
        target: str,
        runs: int,
        per_run_timeout: int = 120,
        timeout: int | None = None,
    ) -> ReplayOutcome:
        """目标测试**换进程**重跑 runs 次（--network none）。

        target 走环境变量传进容器（测试名里有 `::` 甚至引号，拼命令行会炸）。
        """
        script = build_replay_script(target, runs, per_run_timeout)
        # 整体超时按"单次超时 × 次数"给，再留 60 秒容器启动余量
        total_timeout = timeout or (per_run_timeout * runs + 60)
        rc, out = self._run_container(
            script,
            src_dir,
            deps_dir=deps_dir,
            env={"TARGET": target},
            timeout=total_timeout,
        )
        if rc == "TIMEOUT":
            return ReplayOutcome(docker_error=f"整轮超时（{total_timeout}s）")
        if rc == "NOBIN":
            return ReplayOutcome(docker_error=f"找不到 docker: {out}")
        outcome = parse_replay_output(out)
        if not outcome.sequence and not outcome.first_failure_log:
            outcome.docker_error = f"容器跑完但没有产出任何 @@RUN@@ 标记（rc={rc}）"
        return outcome

    def collect(
        self, src_dir: Path, deps_dir: Path, testdir: str = "tests", timeout: int = 300
    ) -> CollectOutcome:
        """收集 testdir 下全部测试节点 ID（负样本构造用，--network none）。"""
        rc, out = self._run_container(
            build_collect_script(testdir), src_dir, deps_dir=deps_dir, timeout=timeout
        )
        if rc == "TIMEOUT":
            return CollectOutcome(ok=False, reason="TIMEOUT")
        if rc == "NOBIN":
            return CollectOutcome(ok=False, reason=f"DOCKER_MISSING: {out}")
        return parse_collect_output(out)

    def count_replay(
        self,
        src_dir: Path,
        deps_dir: Path,
        target: str,
        count: int,
        per_run_timeout: int = 120,
        timeout: int | None = None,
    ) -> CountOutcome:
        """同进程 `--count=N` 探头：一个进程里把目标测试跑 N 遍。

        抓的是**进程内状态污染**型 NIO（yamicache 实例：Cache 对象在
        同进程第二次调用时还活着，换进程重跑反而永远看不到）。
        修正 C：只能当阳性证据（翻 → flaky），没翻不能证明稳定。
        """
        script = build_count_script(target, count, per_run_timeout)
        # 容器级超时要罩住"量体温 + count 轮"：最坏每轮跑满 per_run_timeout，
        # 所以给 per_run_timeout × (count+1) + 300；实测常见情况几分钟完事
        total_timeout = timeout or (per_run_timeout * (count + 1) + 300)
        rc, out = self._run_container(
            script,
            src_dir,
            deps_dir=deps_dir,
            env={"TARGET": target},
            timeout=total_timeout,
        )
        if rc == "TIMEOUT":
            return CountOutcome(
                ok=False, reason=f"TIMEOUT({total_timeout}s)", expected=count
            )
        if rc == "NOBIN":
            return CountOutcome(
                ok=False, reason=f"DOCKER_MISSING: {out}", expected=count
            )
        return parse_count_output(out, target, expected=count)

    def run_sequence(
        self,
        src_dir: Path,
        deps_dir: Path,
        tests: list[str],
        per_run_timeout: int = 120,
        timeout: int | None = None,
    ) -> SequenceOutcome:
        """按 tests 的顺序跑这一组（单进程，--network none）。

        顺序变换 / ddmin 的工作马：每次调用 = 一次"这个顺序下谁挂了"的
        观测。per_run_timeout 在这里是**每组**的预算（容器内 timeout =
        per_run_timeout × len(tests) + 60）；整个序列被熔断 → 没有 XML →
        ok=False（SEQ_TIMEOUT）—— 调用方不许把"没跑完"当成"都过了"。
        """
        if not tests:
            return SequenceOutcome(ok=False, reason="EMPTY_SEQUENCE")
        script = build_sequence_script(tests, per_run_timeout)
        budget = per_run_timeout * len(tests) + 60
        total_timeout = timeout or (budget + 300)
        rc, out = self._run_container(
            script, src_dir, deps_dir=deps_dir, timeout=total_timeout
        )
        if rc == "TIMEOUT":
            return SequenceOutcome(
                ok=False,
                reason=f"TIMEOUT({total_timeout}s)",
                docker_error=f"整轮超时（{total_timeout}s）",
            )
        if rc == "NOBIN":
            return SequenceOutcome(
                ok=False, reason="DOCKER_MISSING", docker_error=f"找不到 docker: {out}"
            )
        return parse_sequence_output(out, tests)
