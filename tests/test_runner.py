"""runner.py 的单元测试：脚本生成、输出解析、Docker 命令构造。

不跑真 Docker —— subprocess.run 打桩，只验证"该传什么、该解析什么"。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from flaky_hunter.runner import (
    IMAGE_PY37,
    IMAGE_PY39,
    IMAGE_PY310,
    DockerRunner,
    build_collect_script,
    build_count_script,
    build_replay_script,
    build_sequence_script,
    build_setup_script,
    image_for_year,
    image_python,
    mirror_image,
    parse_collect_output,
    parse_count_output,
    parse_replay_output,
    parse_sequence_output,
    parse_setup_output,
)

# ---------------------------------------------------------------- 纯函数：镜像选择


@pytest.mark.parametrize(
    ("year", "expected"),
    [
        (2015, IMAGE_PY37),  # 更早的也归 3.7 档
        (2018, IMAGE_PY37),
        (2019, IMAGE_PY37),
        (2020, IMAGE_PY39),
        (2021, IMAGE_PY39),
        (2022, IMAGE_PY310),
        (2026, IMAGE_PY310),
    ],
)
def test_image_for_year(year: int, expected: str) -> None:
    assert image_for_year(year) == expected


def test_mirror_image_prefixes_official_names() -> None:
    """本机必须走国内 mirror —— 直连官方源会被代理破坏（实测 size validation）。"""
    assert mirror_image("python:3.9-slim") == (
        "docker.m.daocloud.io/library/python:3.9-slim"
    )
    assert mirror_image(IMAGE_PY37).startswith("docker.m.daocloud.io/")


def test_mirror_image_leaves_prefixed_alone() -> None:
    custom = "docker.m.daocloud.io/library/python:3.9-slim"
    assert mirror_image(custom) == custom
    assert mirror_image("myregistry.local:5000/py:3.9") == (
        "myregistry.local:5000/py:3.9"
    )


def test_runner_default_image_uses_mirror() -> None:
    assert DockerRunner().image == mirror_image(IMAGE_PY39)


# ---------------------------------------------------------------- 纯函数：脚本生成


def test_setup_script_pins_setuptools_and_has_markers() -> None:
    script = build_setup_script()
    assert "setuptools<81" in script  # 老代码 import pkg_resources 的坑（实测）
    assert "@@TOOLS_RC=$?@@" in script
    assert "@@PROJECT_SKIPPED@@" in script
    assert "--target=/deps" in script


def test_setup_script_quotes_version_specs() -> None:
    """`setuptools<81` 的 `<` 是 shell 重定向符，不引起来会被 bash 吃掉。

    2026-10-12 试点实测：不引 → `bash: 81: No such file or directory`，
    工具链装假成功，全项目 DEPS_FAIL。
    """
    script = build_setup_script()
    assert "'setuptools<81'" in script
    # 每个包名都单独引住，不给 shell 留解释空间
    for pkg in ("wheel", "pytest", "pytest-randomly", "pytz"):
        assert f"'{pkg}'" in script


def test_replay_script_resets_hash_seed_and_guards_single_run() -> None:
    script = build_replay_script("tests/test_x.py::test_y", runs=5, per_run_timeout=99)
    assert "unset PYTHONHASHSEED" in script  # 修正 C：换进程才有意义
    assert "timeout 99 python -m pytest" in script
    assert '"$TARGET"' in script  # 目标走环境变量，不拼命令行
    assert "seq 1 5" in script
    assert "@@FIRSTFAIL@@" in script


def test_collect_script_uses_collect_only() -> None:
    assert "--collect-only" in build_collect_script("tests")


def test_collect_script_disables_randomly() -> None:
    """`-p no:randomly` 是顺序扫描的地基：不禁掉收集顺序都是随机的，
    "默认顺序"不可复现（2026-10-13 实测 deps 里装着 pytest-randomly）。"""
    assert "-p no:randomly" in build_collect_script("tests")


def test_image_python_extracts_tag_version() -> None:
    assert image_python("python:3.9-slim") == "3.9"
    assert image_python("docker.m.daocloud.io/library/python:3.10-slim") == "3.10"
    assert image_python("weird") == "unknown"


# ---------------------------------------------------------------- 顺序执行


def test_sequence_script_disables_randomly_and_keeps_order() -> None:
    """顺序变换的两个前提：禁 pytest-randomly + 节点 ID 按序进命令行。"""
    script = build_sequence_script(
        ["tests/test_a.py::test_b", "tests/test_a.py::test_a"], 99
    )
    assert "-p no:randomly" in script
    assert (
        "python -m pytest 'tests/test_a.py::test_b' 'tests/test_a.py::test_a'" in script
    )
    assert "--junit-xml=/tmp/seq.xml" in script
    assert "@@SEQ_RC=" in script


def test_sequence_script_quotes_node_ids() -> None:
    """参数化 ID 可能带空格/引号 —— 单引号包裹 + 转义，不给 shell 留解释空间。"""
    script = build_sequence_script(["tests/t.py::test_p[a b]"], 60)
    assert "'tests/t.py::test_p[a b]'" in script
    script = build_sequence_script(["tests/t.py::test_'x'"], 60)
    assert "'tests/t.py::test_'\\''x'\\'''" in script


def test_sequence_script_budget_scales_with_test_count() -> None:
    """每组预算 = per_run_timeout × 条数 + 60；容器内 timeout 先熔断。"""
    script = build_sequence_script(["a", "b", "c"], 100)
    assert "budget=360" in script
    assert "timeout $budget python -m pytest" in script


def test_parse_sequence_maps_xml_back_to_input_nodes() -> None:
    # classname 用真实格式（2026-10-13 容器实测）：完整模块点号路径 + 类名，
    # 目录名与文件名重复时也全带上
    # （tests.test_fileutils.test_fileutils.TestMarkdownFile）
    xml = (
        '<testsuites><testsuite name="pytest" tests="3" failures="1">'
        '<testcase classname="tests.test_mdutils.TestMdUtils" name="test_new_header">'
        "<failure>boom</failure></testcase>"
        '<testcase classname="tests.test_fileutils.test_fileutils.TestMarkdownFile" '
        'name="test_read"/>'
        '<testcase classname="tests.test_tools.test_table_of_contents.'
        'TestTableOfContents" name="test_table_of_contents_with_dot"/>'
        "</testsuite></testsuites>"
    )
    out = f"@@SEQ_RC=1@@\n@@XML_BEGIN@@\n{xml}\n@@XML_END@@\n@@LOGTAIL@@\n1 failed"
    r = parse_sequence_output(
        out,
        [
            "tests/test_mdutils.py::TestMdUtils::test_new_header",
            "tests/test_fileutils/test_fileutils.py::TestMarkdownFile::test_read",
            "tests/test_tools/test_table_of_contents.py"
            "::TestTableOfContents::test_table_of_contents_with_dot",
        ],
    )
    assert r.ok is True
    assert r.outcomes == {
        "tests/test_mdutils.py::TestMdUtils::test_new_header": "fail",
        "tests/test_fileutils/test_fileutils.py::TestMarkdownFile::test_read": "pass",
        "tests/test_tools/test_table_of_contents.py"
        "::TestTableOfContents::test_table_of_contents_with_dot": "pass",
    }


def test_parse_sequence_marks_uncollected_nodes_missing() -> None:
    """输入了但 XML 里没有 → missing（路径/名字不对），不许默认当 pass。"""
    xml = (
        '<testsuites><testsuite name="pytest" tests="1">'
        '<testcase classname="tests.test_a" name="test_one"/></testsuite></testsuites>'
    )
    out = f"@@SEQ_RC=0@@\n@@XML_BEGIN@@\n{xml}\n@@XML_END@@\n@@LOGTAIL@@\nok"
    r = parse_sequence_output(
        out, ["tests/test_a.py::test_one", "tests/test_b.py::test_x"]
    )
    assert r.outcomes["tests/test_a.py::test_one"] == "pass"
    assert r.outcomes["tests/test_b.py::test_x"] == "missing"


def test_parse_sequence_matches_file_level_node() -> None:
    """文件级节点（无 ::）：该文件下任何测试都算它。"""
    xml = (
        '<testsuites><testsuite name="pytest" tests="1">'
        '<testcase classname="tests.test_a.TestC" name="test_one">'
        "<failure>x</failure></testcase></testsuite></testsuites>"
    )
    out = f"@@SEQ_RC=1@@\n@@XML_BEGIN@@\n{xml}\n@@XML_END@@\n@@LOGTAIL@@\nbad"
    r = parse_sequence_output(out, ["tests/test_a.py"])
    assert r.outcomes["tests/test_a.py"] == "fail"
    # 另一个文件的测试不算它
    r2 = parse_sequence_output(out, ["tests/test_other.py"])
    assert r2.outcomes["tests/test_other.py"] == "missing"


def test_parse_sequence_parametrized_names_match() -> None:
    """参数化展开：XML name 带 [后缀]，输入节点不带也能匹配（取最坏）。"""
    xml = (
        '<testsuites><testsuite name="pytest" tests="2" failures="1">'
        '<testcase classname="tests.test_p" name="test_x[1-2]"/>'
        '<testcase classname="tests.test_p" name="test_x[2-2]">'
        "<failure>x</failure></testcase></testsuite></testsuites>"
    )
    out = f"@@SEQ_RC=1@@\n@@XML_BEGIN@@\n{xml}\n@@XML_END@@\n@@LOGTAIL@@\n1 failed"
    r = parse_sequence_output(out, ["tests/test_p.py::test_x"])
    assert r.outcomes["tests/test_p.py::test_x"] == "fail"


def test_parse_sequence_timeout_is_not_silent() -> None:
    """rc=124 = 整体熔断，XML 没写出来 —— 给明确理由，不伪装成"都过了"。"""
    out = "@@SEQ_RC=124@@\nkilled"
    r = parse_sequence_output(out, ["a::b"])
    assert r.ok is False
    assert r.reason == "SEQ_TIMEOUT"


def test_parse_sequence_no_xml_is_not_ok() -> None:
    r = parse_sequence_output("random noise", ["a::b"])
    assert r.ok is False
    assert r.reason == "NO_XML"


def test_parse_sequence_empty_xml_region_is_xml_empty() -> None:
    """标记在、XML 空 —— pytest 自己崩了（实测 freezegun：假时间泄漏进
    pytest 计时器，INTERNALERROR，XML 没写出来）。给独立理由，log_tail
    里应有完整堆栈，不许和"XML 损坏"混为一谈。"""
    out = (
        "@@SEQ_RC=1@@\n@@XML_BEGIN@@\n@@XML_END@@\n@@LOGTAIL@@\n"
        "INTERNALERROR> ... IndexError: list index out of range"
    )
    r = parse_sequence_output(out, ["a::b"])
    assert r.ok is False
    assert r.reason == "XML_EMPTY"
    assert "INTERNALERROR" in r.log_tail


def test_run_sequence_empty_list_is_not_ok(runner: DockerRunner) -> None:
    out = runner.run_sequence(Path("/c/src"), Path("/c/deps"), [])
    assert out.ok is False
    assert out.reason == "EMPTY_SEQUENCE"


def test_run_sequence_reports_docker_error(runner: DockerRunner, monkeypatch) -> None:
    def fake(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    out = runner.run_sequence(Path("/c/src"), Path("/c/deps"), ["a::b"])
    assert out.ok is False
    assert "整轮超时" in out.docker_error


# ---------------------------------------------------------------- 同进程 --count 探头

_COUNT_XML = (
    '<testsuites><testsuite name="pytest" tests="3" failures="2">'
    '<testcase classname="tests.test_noclass" name="test_main[1-3]">'
    '<failure message="assert 1 == 0">boom</failure></testcase>'
    '<testcase classname="tests.test_noclass" name="test_main[2-3]"/>'
    '<testcase classname="tests.test_noclass" name="test_main[3-3]">'
    '<failure message="x">boom</failure></testcase>'
    "</testsuite></testsuites>"
)


def _count_out(xml: str, rc: str = "1") -> str:
    return f"@@COUNT_RC={rc}@@\n@@XML_BEGIN@@\n{xml}\n@@XML_END@@\n@@LOGTAIL@@\nsummary"


def test_count_script_warms_up_before_counting() -> None:
    """慢测试 ×100 会超过默认熔断被无声杀掉（实测 test_deco_timeout 2s/轮）——
    先量体温，预算按实测放大，且不超过 count × per_run_timeout。"""
    script = build_count_script("t::x", count=100, per_run_timeout=120)
    assert "--junit-xml=/tmp/count.xml" in script  # XML 精确计数，不玩正则
    assert "date +%s" in script  # 量体温
    assert "@@WARM" in script
    assert "budget" in script
    assert "if [ $budget -gt" in script  # 上限保护
    assert "timeout $budget python -m pytest" in script
    assert '"$TARGET"' in script


def test_parse_count_reports_timeout_reason() -> None:
    """rc=124 = 预算耗尽被 timeout 杀掉 —— 给明确理由，不伪装成"没翻转"。"""
    out = (
        "@@WARM secs=2 budget=460@@\n@@COUNT_RC=124@@\n@@XML_BEGIN@@\n\n"
        "@@XML_END@@\n@@LOGTAIL@@\nkilled"
    )
    r = parse_count_output(out, "t::x", expected=100)
    assert r.ok is False
    assert r.reason == "COUNT_TIMEOUT"
    assert r.warm_secs == 2.0
    assert r.budget_secs == 460


def test_parse_count_flags_shortfall() -> None:
    """unittest.TestCase 风格：--count=100 实际只跑 1 次（parametrize 不适用）。
    必须打 short 标，不许静默按"100 遍没翻"算。"""
    xml = (
        '<testsuites><testsuite name="pytest" tests="1" failures="0">'
        '<testcase classname="tests.test_noclass" name="test_main[1-1]"/>'
        "</testsuite></testsuites>"
    )
    out = (
        "@@WARM secs=0 budget=160@@\n@@COUNT_RC=0@@\n@@XML_BEGIN@@\n"
        + xml
        + "\n@@XML_END@@\n@@LOGTAIL@@\n1 passed"
    )
    r = parse_count_output(out, "tests/test_noclass.py::test_main", expected=100)
    assert r.ok is True
    assert r.total == 1
    assert r.short is True  # 1 < 100


def test_parse_count_extracts_first_fail_line() -> None:
    """首个 FAILED 行是翻转证据的核心（如 yamicache 的 assert 1 == 0）。"""
    xml = (
        '<testsuites><testsuite name="pytest" tests="2" failures="1">'
        '<testcase classname="tests.test_noclass" name="test_main[1-2]"/>'
        '<testcase classname="tests.test_noclass" name="test_main[2-2]">'
        '<failure message="assert 1 == 0">boom</failure></testcase>'
        "</testsuite></testsuites>"
    )
    out = (
        "@@WARM secs=0 budget=120@@\n@@COUNT_RC=1@@\n@@XML_BEGIN@@\n"
        + xml
        + "\n@@XML_END@@\n@@FIRSTFAIL@@\n"
        "FAILED tests/test_noclass.py::test_main[2-2] - assert 1 == 0\n"
        "@@LOGTAIL@@\n1 failed, 1 passed"
    )
    r = parse_count_output(out, "tests/test_noclass.py::test_main", expected=2)
    assert r.ok is True
    assert (r.passed, r.failed) == (1, 1)
    assert r.first_fail_line == (
        "FAILED tests/test_noclass.py::test_main[2-2] - assert 1 == 0"
    )


def test_parse_count_full_run_is_not_short() -> None:
    xml = "".join(
        f'<testcase classname="tests.test_noclass" name="test_main[{i}-3]"/>'
        for i in (1, 2, 3)
    )
    out = (
        "@@WARM secs=0 budget=120@@\n@@COUNT_RC=0@@\n@@XML_BEGIN@@\n"
        f'<testsuites><testsuite name="pytest" tests="3" failures="0">{xml}'
        "</testsuite></testsuites>\n@@XML_END@@\n@@LOGTAIL@@\n3 passed"
    )
    r = parse_count_output(out, "tests/test_noclass.py::test_main", expected=3)
    assert r.short is False


def test_parse_count_counts_target_iterations() -> None:
    """pytest-repeat 的 [i-N] 后缀要砍掉再匹配（实测 name="test_main[2-3]"）。"""
    r = parse_count_output(_count_out(_COUNT_XML), "tests/test_noclass.py::test_main")
    assert r.ok is True
    assert (r.passed, r.failed, r.total) == (1, 2, 3)


def test_parse_count_ignores_other_tests_failures() -> None:
    """pingouin 教训：别的测试挂了不算目标测试的翻转。"""
    xml = _COUNT_XML.replace(
        'classname="tests.test_noclass"', 'classname="tests.other"'
    )
    r = parse_count_output(_count_out(xml), "tests/test_noclass.py::test_main")
    assert r.ok is False
    assert r.reason == "NO_MATCH"


def test_parse_count_matches_class_based_node() -> None:
    xml = (
        '<testsuites><testsuite name="pytest" tests="1" failures="1">'
        '<testcase classname="tests.test_b.TestC" name="test_two[1-1]">'
        '<failure message="boom">x</failure></testcase>'
        "</testsuite></testsuites>"
    )
    r = parse_count_output(_count_out(xml), "tests/test_b.py::TestC::test_two")
    assert r.ok is True
    assert (r.passed, r.failed) == (0, 1)


def test_parse_count_without_xml_marker_is_not_ok() -> None:
    r = parse_count_output("random noise", "t::x")
    assert r.ok is False
    assert r.reason == "NO_XML"


def test_count_replay_reports_timeout(runner: DockerRunner, monkeypatch) -> None:
    def fake(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    out = runner.count_replay(Path("/c/src"), Path("/c/deps"), "t::x", count=10)
    assert out.ok is False
    assert "TIMEOUT" in out.reason


# ---------------------------------------------------------------- 纯函数：输出解析


def test_parse_setup_ok_and_project_installed() -> None:
    out = "...\n@@TOOLS_RC=0@@\nSuccessfully installed flaky-hunter"
    r = parse_setup_output(out)
    assert r.tools_ok is True
    assert r.project_installed is True


def test_parse_setup_tools_fail_means_env_not_ready() -> None:
    out = "@@TOOLS_RC=1@@\nERROR: could not find a version"
    r = parse_setup_output(out)
    assert r.tools_ok is False
    assert "could not find" in r.log_tail


def test_parse_setup_project_skipped_still_tools_ok() -> None:
    """项目装不上不阻塞（零依赖项目裸跑就行），工具装上了就算环境就绪。"""
    r = parse_setup_output("@@TOOLS_RC=0@@\n@@PROJECT_SKIPPED@@")
    assert r.tools_ok is True
    assert r.project_installed is False


def test_parse_setup_without_marker_is_not_ok() -> None:
    """认不出标记 → 不猜，视为环境没建起来。"""
    assert parse_setup_output("random docker noise").tools_ok is False


def test_parse_replay_maps_exit_codes() -> None:
    out = "\n".join(
        [
            "@@RUN 1 0@@",  # 过
            "@@RUN 2 1@@",  # 挂
            "@@RUN 3 5@@",  # 没收集到
            "@@RUN 4 124@@",  # 单次超时
            "@@RUN 5 2@@",  # 用法错误也算挂
        ]
    )
    r = parse_replay_output(out)
    assert r.sequence == ["pass", "fail", "notfound", "timeout", "fail"]
    assert r.first_failure_log == ""


def test_parse_replay_extracts_first_failure_log() -> None:
    out = "@@RUN 1 0@@\n@@RUN 2 1@@\n@@FIRSTFAIL@@\nFAILED test_x\nassert 1 == 2"
    r = parse_replay_output(out)
    assert r.sequence == ["pass", "fail"]
    assert "assert 1 == 2" in r.first_failure_log


def test_parse_collect_keeps_only_node_ids() -> None:
    out = "\n".join(
        [
            "tests/test_a.py::test_one",
            "tests/test_b.py::TestC::test_two",
            "tests/test_b.py",  # 文件级节点（无 ::）也合法
            "23 tests collected in 0.02s",
            "warning: something",
            "===== short test summary =====",
        ]
    )
    r = parse_collect_output(out)
    assert r.ok is True
    assert r.tests == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::TestC::test_two",
        "tests/test_b.py",
    ]


def test_parse_collect_import_error_reason() -> None:
    r = parse_collect_output("ModuleNotFoundError: No module named 'fs'")
    assert r.ok is False
    assert r.reason == "IMPORT_ERROR"


# ---------------------------------------------------------------- Docker 命令构造


@pytest.fixture
def runner() -> DockerRunner:
    return DockerRunner(image=IMAGE_PY39)


def _fake_run(rc: int = 0, out: str = ""):
    calls: list[list[str]] = []

    def fake(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=out)

    return fake, calls


def test_run_container_mounts_and_network(monkeypatch) -> None:
    """use_volumes=False 时走 9p 挂载（兜底路径）。"""
    runner = DockerRunner(use_volumes=False)
    fake, calls = _fake_run(0, "@@RUN 1 0@@")
    monkeypatch.setattr(subprocess, "run", fake)

    runner._run_container(
        "echo hi", Path("/c/src"), deps_dir=Path("/c/deps"), env={"TARGET": "a::b"}
    )

    cmd = calls[0]
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert "/c/src:/w" in cmd
    assert "/c/deps:/deps" in cmd
    assert cmd[cmd.index("--network") + 1] == "none"  # 默认断网（铁律）
    assert "PYTHONPATH=/deps" in cmd
    assert "TARGET=a::b" in cmd  # 环境变量传目标
    assert cmd[-1].endswith("echo hi 2>&1")  # stderr 必须并入 stdout


def test_run_container_uses_volumes_by_default(monkeypatch) -> None:
    """默认走 Docker 卷：9p 挂载 I/O 慢 12.5s/次，卷内 5.4s/次（实测）。"""
    runner = DockerRunner(use_volumes=True)
    monkeypatch.setattr(runner, "_volume_exists", lambda name: True)
    fake, calls = _fake_run(0, "")
    monkeypatch.setattr(subprocess, "run", fake)

    runner._run_container("echo hi", Path("/c/src"), deps_dir=Path("/c/deps"))

    cmd = calls[-1]  # 最后一次调用才是 docker run（前面是 volume inspect）
    assert "fh-src-src:/w" in cmd
    assert "fh-deps-deps:/deps" in cmd
    assert not any(m == "/c/src:/w" for m in cmd)  # 不走 9p 挂载


def test_volume_failure_falls_back_to_mount(monkeypatch) -> None:
    """卷方案失败 → 退回 9p 挂载，保底能跑（不炸）。"""

    def fake(cmd, **kwargs):
        joined = " ".join(cmd)
        if "volume inspect" in joined:
            rc = 1  # 卷不存在
        elif "volume create" in joined:
            rc = 0
        elif "cp -r" in joined:
            rc = 1  # 种子化失败
        else:
            rc = 0
        # 假 subprocess 也要实现 check=True 语义，否则测不出降级路径
        if kwargs.get("check") and rc != 0:
            raise subprocess.CalledProcessError(rc, cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout="")

    calls: list[list[str]] = []

    def recording(cmd, **kwargs):
        calls.append(cmd)
        return fake(cmd, **kwargs)

    monkeypatch.setattr(subprocess, "run", recording)
    runner = DockerRunner(use_volumes=True)
    runner._run_container("echo hi", Path("/c/src"))
    assert "/c/src:/w" in calls[-1]  # 种子化失败 → 退回挂载


def test_volume_name_sanitizes_key() -> None:
    assert DockerRunner()._volume_name("src", "yamicache-1109d08b18be") == (
        "fh-src-yamicache-1109d08b18be"
    )
    # 非法字符折叠成单个 -，首尾的 - 削掉
    assert (
        DockerRunner()._volume_name("deps", "weird key+sha!") == "fh-deps-weird-key-sha"
    )


def test_setup_uses_bridge_network(runner: DockerRunner, monkeypatch, tmp_path) -> None:
    fake, calls = _fake_run(0, "@@TOOLS_RC=0@@")
    monkeypatch.setattr(subprocess, "run", fake)
    # 必须用 tmp_path：Path("/c/deps") 在 Windows 上是 C:\c\deps，
    # marker 会落到真实文件系统，污染后续轮次（2026-10-12 实测踩到）
    src = tmp_path / "src"
    deps = tmp_path / "deps" / "key"
    src.mkdir()
    out = runner.setup(src, deps)
    assert out.tools_ok is True
    run_cmd = calls[-1]  # docker run（前面可能有 volume inspect）
    assert run_cmd[run_cmd.index("--network") + 1] == "bridge"  # 装依赖要联网


def test_setup_cache_hit_skips_docker(
    runner: DockerRunner, monkeypatch, tmp_path
) -> None:
    deps = tmp_path / "deps" / "key"
    deps.mkdir(parents=True)
    (tmp_path / "deps" / "key.done").write_text("installed", encoding="utf-8")
    fake, calls = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)
    out = runner.setup(tmp_path / "src", deps)
    assert out.tools_ok is True
    assert calls == []  # 标记在，一次 docker 都不起


def test_setup_seeds_host_deps_into_volume(
    runner: DockerRunner, monkeypatch, tmp_path
) -> None:
    """宿主机已有依赖（旧版挂载模式装的）→ 搬进卷，不重装。"""
    deps = tmp_path / "deps" / "key"
    deps.mkdir(parents=True)
    (deps / "pytest").write_text("", encoding="utf-8")
    fake, calls = _fake_run()
    monkeypatch.setattr(subprocess, "run", fake)
    out = runner.setup(tmp_path / "src", deps)
    assert out.tools_ok is True
    assert "搬入卷" in out.log_tail
    assert not any("pip install" in " ".join(c) for c in calls)  # 没跑安装
    assert (tmp_path / "deps" / "key.done").exists()  # 标记落盘


def test_replay_reports_docker_error_on_timeout(
    runner: DockerRunner, monkeypatch
) -> None:
    def fake(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(subprocess, "run", fake)
    out = runner.replay(Path("/c/src"), Path("/c/deps"), "t::x", runs=3)
    assert out.sequence == []
    assert "超时" in out.docker_error


def test_replay_reports_missing_docker(runner: DockerRunner, monkeypatch) -> None:
    def fake(cmd, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", fake)
    out = runner.replay(Path("/c/src"), Path("/c/deps"), "t::x", runs=3)
    assert "找不到 docker" in out.docker_error


def test_replay_flags_empty_output(runner: DockerRunner, monkeypatch) -> None:
    """容器跑完但一个 @@RUN@@ 都没有 → 不是"0 翻转"，是没跑起来。"""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, stdout="noise"),
    )
    out = runner.replay(Path("/c/src"), Path("/c/deps"), "t::x", runs=3)
    assert out.sequence == []
    assert out.docker_error  # 必须留下诊断，不能静悄悄当没翻转
