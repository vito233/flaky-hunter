"""detect_replay.py 的单元测试：判定规则全表 + FakeRunner 接线。

不碰 Docker —— runner 用假实现，喂预设的 pass/fail 序列。
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from flaky_hunter.baselines import RERUN_FOR_95_CONFIDENCE
from flaky_hunter.detect_replay import (
    DEFAULT_RERUN_COUNT,
    MIN_RUNS_FOR_STABLE,
    classify_counts,
    detect_replay,
)
from flaky_hunter.runner import CountOutcome, ReplayOutcome


class FakeRunner:
    """假 runner：记录调用参数，返回预设结果。image 属性供 env 记录用。"""

    image = "python:3.9-slim"

    def __init__(
        self,
        outcome: ReplayOutcome,
        count_outcome: CountOutcome | None = None,
    ) -> None:
        self.outcome = outcome
        self.count_outcome = count_outcome
        self.calls: list[tuple] = []
        self.count_calls: list[tuple] = []

    def replay(self, src, deps, target, *, runs, per_run_timeout):
        self.calls.append((src, deps, target, runs, per_run_timeout))
        return self.outcome

    def count_replay(self, src, deps, target, *, count, per_run_timeout):
        self.count_calls.append((src, deps, target, count, per_run_timeout))
        if self.count_outcome is None:
            return CountOutcome(ok=False, reason="NOT_RUN")
        return self.count_outcome


def _seq(*pattern: str) -> ReplayOutcome:
    return ReplayOutcome(sequence=list(pattern))


def _detect(outcome: ReplayOutcome, count_outcome: CountOutcome | None = None, **kw):
    runner = FakeRunner(outcome, count_outcome)
    return detect_replay(
        project="mtik00/yamicache",
        sha="a" * 40,
        test_name="tests/test_noclass.py::test_main",
        runner=runner,
        src_dir=Path("/c/src"),
        deps_dir=Path("/c/deps"),
        **kw,
    )


# ---------------------------------------------------------------- 判定规则全表


def test_flip_is_flaky_medium_confidence() -> None:
    """NIO 家族（yamicache 实例）：第 1 次过、后面全挂 → flaky。"""
    verdict, status, conf = classify_counts(
        Counter(["pass"] + ["fail"] * 99), runs=100, per_run_timeout=120
    )
    assert verdict == "flaky"
    assert status == "OK"
    assert conf.level == "medium"  # total=100 ≥ 20
    assert "99.0%" in conf.basis  # 失败率进依据


def test_small_flip_is_low_confidence() -> None:
    verdict, _, conf = classify_counts(
        Counter(["pass", "fail"] * 5), runs=10, per_run_timeout=120
    )
    assert verdict == "flaky"
    assert conf.level == "low"  # total=10 < 20，观察次数太少


def test_all_pass_100_runs_is_stable_high() -> None:
    """100 遍全过 → stable + 二项检验依据（schema 的 stable 防线放行）。"""
    verdict, status, conf = classify_counts(
        Counter(["pass"] * 100), runs=100, per_run_timeout=120
    )
    assert verdict == "stable"
    assert status == "OK"
    assert conf.level == "high"
    assert "二项检验" in conf.basis


def test_all_pass_170_runs_uses_external_baseline() -> None:
    verdict, _, conf = classify_counts(
        Counter(["pass"] * RERUN_FOR_95_CONFIDENCE),
        runs=RERUN_FOR_95_CONFIDENCE,
        per_run_timeout=120,
    )
    assert verdict == "stable"
    assert "170" in conf.basis  # 外部基准线（arXiv 2101.09077）


def test_all_pass_too_few_runs_is_inconclusive() -> None:
    """30 遍全过但不足 60 → 不许宣称稳定（0 次翻转 ≠ 稳定）。"""
    verdict, status, conf = classify_counts(
        Counter(["pass"] * (MIN_RUNS_FOR_STABLE - 30)), runs=30, per_run_timeout=120
    )
    assert verdict == "inconclusive"
    assert status == "OK"
    assert "0 次翻转 ≠ 稳定" in conf.basis


def test_all_fail_is_not_flip() -> None:
    """全挂 ≠ flaky：顺序依赖在固定顺序下稳定失败 / 测试已坏，判不了。"""
    verdict, status, conf = classify_counts(
        Counter(["fail"] * 100), runs=100, per_run_timeout=120
    )
    assert verdict == "inconclusive"
    assert status == "OK"
    assert "无翻转" in conf.basis


def test_notfound_maps_to_target_status() -> None:
    verdict, status, _ = classify_counts(
        Counter(["notfound"] * 3), runs=3, per_run_timeout=120
    )
    assert verdict == "inconclusive"
    assert status == "TEST_NOT_FOUND"


def test_all_timeout_maps_to_timeout_status() -> None:
    verdict, status, conf = classify_counts(
        Counter(["timeout"] * 3), runs=3, per_run_timeout=120
    )
    assert verdict == "inconclusive"
    assert status == "TIMEOUT"
    assert "熔断" in conf.basis


def test_empty_sequence_is_not_silent() -> None:
    """一遍都没跑出来 → 不能静悄悄当"没翻转"，必须留低置信诊断。"""
    verdict, _, conf = classify_counts(Counter(), None, runs=10, per_run_timeout=120)
    assert verdict == "inconclusive"
    assert conf.level == "low"


# ---------------------------------------------------------------- 双探头联合判定


def test_count_probe_flip_alone_is_flaky() -> None:
    """yamicache 实例（2026-10-12 试点实测）：换进程 100 遍全过，
    同进程 1过/99挂 → 仍是 flaky。只上换进程探头会系统性漏掉这类 NIO。"""
    verdict, status, conf = classify_counts(
        Counter(["pass"] * 100),
        CountOutcome(ok=True, passed=1, failed=99, total=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "flaky"
    assert status == "OK"
    assert "进程内状态污染" in conf.basis
    assert "99.0%" in conf.basis


def test_cross_flip_only_is_cross_process_nondeterminism() -> None:
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 50 + ["fail"] * 50),
        CountOutcome(ok=True, passed=100, failed=0, total=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "flaky"
    assert "跨进程非确定性" in conf.basis


def test_both_probes_flip_names_both_mechanisms() -> None:
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 10 + ["fail"] * 90),
        CountOutcome(ok=True, passed=5, failed=95, total=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "flaky"
    assert "两种机制并存" in conf.basis


def test_count_probe_clean_does_not_back_stable_claim() -> None:
    """同进程没翻不能给 stable 背书（修正 C）—— 备注必须写明。"""
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 100),
        CountOutcome(ok=True, passed=100, failed=0, total=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "stable"
    assert "不作为稳定背书" in conf.note


def test_count_probe_failure_does_not_block_cross_verdict() -> None:
    """同进程探头坏了（XML 没了等）不影响换进程探头出结论，但要在备注里说。"""
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 100),
        CountOutcome(ok=False, reason="XML_PARSE_ERROR"),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "stable"
    assert "XML_PARSE_ERROR" in conf.note


def test_count_probe_shortfall_is_visible_in_note() -> None:
    """unittest 风格只跑 1/100 次 —— 备注必须写明，不许静默按"100 遍没翻"算。"""
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 100),
        CountOutcome(ok=True, passed=1, failed=0, total=1, expected=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "stable"
    assert "只完成 1/100 次" in conf.note
    assert "unittest" in conf.note


def test_count_probe_timeout_reason_surfaced() -> None:
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 100),
        CountOutcome(ok=False, reason="COUNT_TIMEOUT", expected=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "stable"
    assert "COUNT_TIMEOUT" in conf.note


def test_short_count_probe_flip_still_counts() -> None:
    """只跑了 2/100 次就观察到翻 → 仍是阳性证据（备注说明次数不足）。"""
    verdict, _, conf = classify_counts(
        Counter(["pass"] * 100),
        CountOutcome(ok=True, passed=1, failed=1, total=2, expected=100),
        runs=100,
        per_run_timeout=120,
    )
    assert verdict == "flaky"
    assert "只完成 2/100 次" in conf.note


# ---------------------------------------------------------------- 接线（FakeRunner）


def test_detect_wires_runner_and_schema() -> None:
    result = _detect(_seq("pass", "fail", "fail"), runs=3)
    assert result.verdict == "flaky"
    assert result.mode == "replay"
    assert result.runs is not None
    assert (result.runs.total, result.runs.passed, result.runs.failed) == (3, 1, 2)
    assert result.runs.flip_count == 1  # 少数派计数
    assert result.env.image == "python:3.9-slim"
    assert result.env.python == "3.9"  # 从镜像标签解析
    assert result.env.network == "none"  # 铁律：运行阶段断网
    assert result.timestamp_utc.tzinfo is not None


def test_detect_passes_runs_and_timeout_through() -> None:
    runner = FakeRunner(_seq("pass"))
    detect_replay(
        project="a/b",
        sha="a" * 40,
        test_name="t::x",
        runner=runner,
        src_dir=Path("/c/src"),
        deps_dir=Path("/c/deps"),
        runs=42,
        per_run_timeout=7,
    )
    src, deps, target, runs, per_run = runner.calls[0]
    assert (src, deps) == (Path("/c/src"), Path("/c/deps"))
    assert target == "t::x"
    assert runs == 42
    assert per_run == 7


def test_detect_default_runs_is_100() -> None:
    """默认 100 遍，不是 20（规范 §2.2）。"""
    assert DEFAULT_RERUN_COUNT == 100
    runner = FakeRunner(_seq("pass"))
    detect_replay(
        project="a/b",
        sha="a" * 40,
        test_name="t::x",
        runner=runner,
        src_dir=Path("/c/src"),
        deps_dir=Path("/c/deps"),
    )
    assert runner.calls[0][3] == 100


def test_detect_raises_on_docker_error() -> None:
    """容器没跑起来 = 我们的问题，抛出让调用方记 FAIL，不冒充"没翻转"。"""
    with pytest.raises(RuntimeError, match="runner 失败"):
        _detect(ReplayOutcome(docker_error="找不到 docker"))


def test_detect_stable_result_passes_schema_validator() -> None:
    """端到端过一遍 schema 的 stable 防线：100 遍 + high 置信 → 放行。"""
    result = _detect(_seq(*(["pass"] * 100)), runs=100)
    assert result.verdict == "stable"  # 没抛 ValidationError 即放行


def test_detect_fills_count_runs_field() -> None:
    """yamicache 型：换进程全过但同进程 1过/9挂 → flaky，两个探头数字都入 schema。"""
    result = _detect(
        _seq(*(["pass"] * 100)),
        CountOutcome(ok=True, passed=1, failed=9, total=10),
        runs=100,
    )
    assert result.verdict == "flaky"
    assert result.runs is not None
    assert result.runs.failed == 0  # 换进程没翻
    assert result.count_runs is not None
    assert result.count_runs.failed == 9  # 同进程翻了 9 次


def test_detect_count_runs_zero_disables_probe() -> None:
    runner = FakeRunner(_seq("pass", "fail"))
    detect_replay(
        project="a/b",
        sha="a" * 40,
        test_name="t::x",
        runner=runner,
        src_dir=Path("/c/src"),
        deps_dir=Path("/c/deps"),
        count_runs=0,
    )
    assert runner.count_calls == []


def test_detect_serializes_to_json() -> None:
    result = _detect(_seq("pass", "fail"), runs=2)
    restored = type(result).model_validate_json(result.model_dump_json())
    assert restored == result


def test_detect_writes_first_failure_log(tmp_path: Path) -> None:
    """首个失败日志落盘留证，路径记进 raw_log_path（AGENTS.md：证据要能复盘）。"""
    outcome = ReplayOutcome(
        sequence=["pass", "fail"],
        first_failure_log="FAILED tests/test_x.py::test_y\nassert 1 == 2",
    )
    runner = FakeRunner(outcome)
    result = detect_replay(
        project="mtik00/yamicache",
        sha="a" * 40,
        test_name="tests/test_x.py::test_y",
        runner=runner,
        src_dir=Path("/c/src"),
        deps_dir=Path("/c/deps"),
        runs=2,
        raw_log_dir=tmp_path,
    )
    assert result.raw_log_path
    saved = Path(result.raw_log_path)
    assert saved.exists()
    assert "assert 1 == 2" in saved.read_text(encoding="utf-8")


def test_detect_no_log_when_all_pass(tmp_path: Path) -> None:
    result = _detect(_seq(*(["pass"] * 100)), runs=100, raw_log_dir=tmp_path)
    assert result.raw_log_path == ""
    assert list(tmp_path.iterdir()) == []
