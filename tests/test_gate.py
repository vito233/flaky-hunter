"""gate（CI 门禁）测试：纯函数判定 / 解析 / 渲染 / 编排（fake runner，不碰 Docker）。

DoD（TODOLIST 第 5 周）：判定规则可完整单测；probe 失败必须 inconclusive；
嫌疑必须经"单独跑验证"才报警 —— 坏测试不进 findings。
"""

import json

import pytest
from pydantic import ValidationError

from flaky_hunter.gate import (
    GateFinding,
    GateProbe,
    GateReport,
    build_file_count_script,
    gate_verdict,
    group_by_file,
    parse_file_count_output,
    render_markdown,
    run_gate,
)
from flaky_hunter.runner import (
    CollectOutcome,
    SequenceOutcome,
    SetupOutcome,
)

NODES = [
    "tests/test_a.py::test_x",
    "tests/test_a.py::test_z",
    "tests/test_b.py::test_y",
]


class FakeGateRunner:
    """run_gate 的鸭子类型替身：按调用顺序弹预设结果，记录调用轨迹。"""

    def __init__(self, seq_results, script_results, tests=NODES):
        self.image = "test-image:latest"
        self._setup = SetupOutcome(tools_ok=True, project_installed=True, log_tail="")
        self._collect = CollectOutcome(ok=True, tests=list(tests), reason="")
        self._seq_results = list(seq_results)
        self._script_results = list(script_results)
        self.seq_calls: list[list[str]] = []
        self.script_calls: list[str] = []

    def setup(self, src_dir, deps_dir, timeout=900):
        return self._setup

    def collect(self, src_dir, deps_dir, testdir="tests", timeout=300):
        return self._collect

    def run_sequence(self, src_dir, deps_dir, tests, per_run_timeout=120, timeout=None):
        self.seq_calls.append(list(tests))
        return self._seq_results.pop(0)

    def run_script(self, script, src_dir, deps_dir=None, timeout=900):
        self.script_calls.append(script)
        return self._script_results.pop(0)


def _all_pass(nodes):
    return SequenceOutcome(ok=True, outcomes=dict.fromkeys(nodes, "pass"), rc=0)


# ---------------------------------------------------------------- 纯函数


def test_group_by_file_keeps_order():
    grouped = group_by_file(NODES)
    assert list(grouped) == ["tests/test_a.py", "tests/test_b.py"]
    assert grouped["tests/test_a.py"] == [
        "tests/test_a.py::test_x",
        "tests/test_a.py::test_z",
    ]


def test_build_file_count_script_quotes_and_disables_randomly():
    script = build_file_count_script(
        "tests/weird name.py", count=7, per_file_timeout=30
    )
    assert "'tests/weird name.py'" in script  # 空格路径被单引号包裹
    assert "--count=7" in script
    assert "-p no:randomly" in script  # 顺序必须可控（C1 实测教训）
    assert "budget=270" in script  # 30 × 7 + 60


def test_parse_file_count_output_rc_and_parametrized_dedup():
    out = (
        "some noise\n"
        "@@FILE_RC=1@@\n"
        "FAILED tests/test_a.py::test_x[10-20] - assert 12 == 1\n"
        "FAILED tests/test_a.py::test_x[11-20] - assert 13 == 1\n"
        "FAILED tests/test_a.py::test_z - boom\n"
    )
    rc, failed = parse_file_count_output(out)
    assert rc == 1
    # 参数化展开去重到基节点；挂多遍只记一条
    assert failed == ["tests/test_a.py::test_x", "tests/test_a.py::test_z"]


def test_parse_file_count_output_clean_and_no_marker():
    rc, failed = parse_file_count_output("@@FILE_RC=0@@\n")
    assert (rc, failed) == (0, [])
    rc, failed = parse_file_count_output("nothing here")
    assert (rc, failed) == (0, [])  # 无标记不炸（rc 默认 0，交给 rc 探头判定）


def _probe(name, ok=True):
    return GateProbe(name=name, ok=ok, seconds=1.0)


_CORE = ("setup", "collect", "seq_default", "seq_reverse")


def _probes(*names):
    return [_probe(n) for n in names]


def test_verdict_pass_with_all_green():
    verdict, reason = gate_verdict([], [], _probes(*_CORE, "count:f"))
    assert verdict == "pass"
    assert "0 次翻转" in reason


def test_verdict_alert_on_findings():
    f = [GateFinding(test="a", kind="order_victim")]
    verdict, reason = gate_verdict(f, [], _probes(*_CORE))
    assert verdict == "alert"
    assert "1 条" in reason


@pytest.mark.parametrize("broken", ["setup", "collect", "seq_default", "seq_reverse"])
def test_verdict_inconclusive_when_core_probe_fails(broken):
    probes = _probes(*_CORE)
    for p in probes:
        if p.name == broken:
            p.ok = False
    verdict, reason = gate_verdict([], [], probes)
    assert verdict == "inconclusive"
    assert broken in reason


def test_verdict_count_probe_failure_not_fatal():
    probes = [
        _probe("setup"),
        _probe("collect"),
        _probe("seq_default"),
        _probe("seq_reverse"),
        _probe("count:tests/test_a.py", ok=False),  # 单文件 count 失败不致命
    ]
    verdict, _ = gate_verdict([], [], probes)
    assert verdict == "pass"


def test_verdict_pass_reason_mentions_bad_tests():
    probes = _probes(*_CORE)
    verdict, reason = gate_verdict([], ["tests/test_a.py::test_x"], probes)
    assert verdict == "pass"
    assert "1 条坏测试" in reason


def test_render_markdown_contains_verdict_and_disclaimer():
    report = GateReport(
        verdict="alert",
        reason="检出 1 条 flaky 嫌疑（报告模式，不阻断合并）",
        nodes_total=3,
        files_total=2,
        findings=[GateFinding(test="t::x", kind="order_victim", evidence="默认序挂")],
        bad_tests=["t::bad"],
        probes=[_probe("setup")],
        image="img",
        count=20,
        generated_utc="2026-10-13T10:00:00Z",
    )
    md = render_markdown(report)
    assert "alert" in md
    assert "3 条测试" in md
    assert "t::x" in md and "order_victim" in md
    assert "t::bad" in md
    assert "0 次翻转 ≠ 稳定" in md  # 覆盖范围免责声明必须在场


def test_gate_report_json_roundtrip():
    report = GateReport(
        verdict="pass",
        nodes_total=1,
        probes=[_probe("setup")],
        generated_utc="2026-10-13T10:00:00Z",
    )
    restored = GateReport.model_validate_json(report.model_dump_json())
    assert restored.verdict == "pass"
    assert restored.probes[0].name == "setup"


def test_gate_report_rejects_bad_verdict():
    with pytest.raises(ValidationError):
        GateReport(verdict="maybe", generated_utc="2026-10-13T10:00:00Z")


# ---------------------------------------------------------------- 编排（fake runner）


def test_run_gate_happy_path_all_green():
    runner = FakeGateRunner(
        seq_results=[_all_pass(NODES), _all_pass(NODES)],
        script_results=[
            ("@@FILE_RC=0@@", "@@FILE_RC=0@@\n"),
            ("@@FILE_RC=0@@", "@@FILE_RC=0@@\n"),
        ],
    )
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "pass"
    assert report.findings == []
    assert report.bad_tests == []
    assert report.nodes_total == 3
    assert report.files_total == 2
    assert {p.name for p in report.probes} >= set(_CORE)
    json.loads(report.model_dump_json())  # 序列化不炸


def test_run_gate_order_victim_alerts_after_single_run_pass():
    seq_default = SequenceOutcome(
        ok=True,
        outcomes={
            "tests/test_a.py::test_x": "fail",
            "tests/test_a.py::test_z": "pass",
            "tests/test_b.py::test_y": "pass",
        },
        rc=1,
    )
    seq_reverse = _all_pass(NODES)
    single = SequenceOutcome(
        ok=True, outcomes={"tests/test_a.py::test_x": "pass"}, rc=0
    )
    runner = FakeGateRunner(
        seq_results=[seq_default, seq_reverse, single],  # 第 3 次 = 单独跑验证
        script_results=[("@@FILE_RC=0@@", "@@FILE_RC=0@@\n") for _ in range(2)],
    )
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "alert"
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.test == "tests/test_a.py::test_x"
    assert f.kind == "order_victim"
    assert f.single_run == "pass"
    # 单独跑验证必须真的发生过（调用轨迹第 3 位 = [node]）
    assert runner.seq_calls[2] == ["tests/test_a.py::test_x"]


def test_run_gate_always_failing_is_bad_test_not_flaky():
    seq_default = SequenceOutcome(
        ok=True,
        outcomes={
            "tests/test_a.py::test_x": "fail",
            "tests/test_a.py::test_z": "pass",
            "tests/test_b.py::test_y": "pass",
        },
        rc=1,
    )
    seq_reverse = _all_pass(NODES)
    single_fail = SequenceOutcome(
        ok=True, outcomes={"tests/test_a.py::test_x": "fail"}, rc=1
    )
    runner = FakeGateRunner(
        seq_results=[seq_default, seq_reverse, single_fail],
        script_results=[("@@FILE_RC=0@@", "@@FILE_RC=0@@\n") for _ in range(2)],
    )
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "pass"  # 坏测试不算 flaky
    assert report.findings == []
    assert report.bad_tests == ["tests/test_a.py::test_x"]


def test_run_gate_count_probe_catches_inprocess_nio():
    runner = FakeGateRunner(
        seq_results=[
            _all_pass(NODES),
            _all_pass(NODES),
            _all_pass(["tests/test_a.py::test_x"]),
        ],
        script_results=[
            (
                1,
                "@@FILE_RC=1@@\nFAILED tests/test_a.py::test_x[3-20] - assert 4 == 1\n",
            ),
            ("@@FILE_RC=0@@", "@@FILE_RC=0@@\n"),
        ],
    )
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "alert"
    assert report.findings[0].kind == "inprocess_nio"
    assert report.findings[0].test == "tests/test_a.py::test_x"
    # test_a.py 的 count 脚本真的跑过（且只跑了未定性的文件）
    assert len(runner.script_calls) == 2


def test_run_gate_setup_failure_short_circuits():
    runner = FakeGateRunner(seq_results=[], script_results=[])
    runner._setup = SetupOutcome(
        tools_ok=False, project_installed=False, log_tail="pip boom"
    )
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "inconclusive"
    # setup 失败后不许再跑任何探头（环境都没有，跑了也是白跑）
    assert runner.seq_calls == []


def test_run_gate_collect_failure_short_circuits():
    runner = FakeGateRunner(seq_results=[], script_results=[])
    runner._collect = CollectOutcome(ok=False, tests=[], reason="IMPORT_ERROR")
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "inconclusive"
    assert "IMPORT_ERROR" in report.reason
    assert runner.seq_calls == []


def test_run_gate_reverse_order_flap():
    seq_default = _all_pass(NODES)
    seq_reverse = SequenceOutcome(
        ok=True,
        outcomes={
            "tests/test_a.py::test_x": "error",
            "tests/test_a.py::test_z": "pass",
            "tests/test_b.py::test_y": "pass",
        },
        rc=1,
    )
    single = SequenceOutcome(
        ok=True, outcomes={"tests/test_a.py::test_x": "pass"}, rc=0
    )
    runner = FakeGateRunner(
        seq_results=[seq_default, seq_reverse, single],
        script_results=[("@@FILE_RC=0@@", "@@FILE_RC=0@@\n") for _ in range(2)],
    )
    report = run_gate(runner, src_dir=".", deps_dir="/tmp/d")
    assert report.verdict == "alert"
    assert report.findings[0].kind == "order_flap"
