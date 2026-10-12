"""detect_order.py 的单元测试：顺序生成 / 二分 / ddmin / 判定 + FakeRunner 接线。

不碰 Docker —— runner 用假实现，模拟"polluter 在 victim 前面跑 → victim 挂"
的世界（mdutils 机制的同款抽象：CWD 被删，之后全挂）。
"""

from __future__ import annotations

from pathlib import Path

from flaky_hunter.detect_order import (
    DEFAULT_MINIMIZE_BUDGET,
    MIN_ALONE_RUNS,
    classify_order,
    make_orders,
    minimize_polluters,
    scan_project_order,
    shrink_prefix,
)
from flaky_hunter.runner import CollectOutcome, ReplayOutcome, SequenceOutcome

# ---------------------------------------------------------------- 顺序生成


def test_make_orders_default_and_reverse_first() -> None:
    tests = [f"t{i}" for i in range(10)]
    orders = make_orders(tests, 4, seed=1729)
    assert len(orders) == 4
    assert orders[0] == ("default", tests)
    assert orders[1] == ("reverse", list(reversed(tests)))
    assert all(name.startswith("random-seed1729") for name, _ in orders[2:])


def test_make_orders_deterministic_by_seed() -> None:
    tests = [f"t{i}" for i in range(20)]
    a = make_orders(tests, 4, seed=1729)
    b = make_orders(tests, 4, seed=1729)
    assert a == b
    c = make_orders(tests, 4, seed=7)
    assert [s for _, s in a[2:]] != [s for _, s in c[2:]]


def test_make_orders_random_orders_are_permutations() -> None:
    tests = [f"t{i}" for i in range(30)]
    for _, seq in make_orders(tests, 4, seed=1729)[2:]:
        assert sorted(seq) == sorted(tests)
        assert seq != tests  # 撞车会重摇，不会原样返回


def test_make_orders_count_one_is_default_only() -> None:
    assert make_orders(["a", "b"], 1, seed=1) == [("default", ["a", "b"])]


# ---------------------------------------------------------------- 前缀二分


def test_shrink_prefix_polluter_at_end_keeps_full_prefix() -> None:
    """polluter 在前缀末尾 → 最短失败前缀就是整个前缀（删掉它就复现不了）。"""
    prefix = ["a", "b", "c", "P"]
    calls: list[list[str]] = []

    def fails(sub: list[str]) -> bool:
        calls.append(sub)
        return "P" in sub

    short, runs = shrink_prefix(fails, prefix)
    assert short == prefix
    assert runs == 2  # 二分：mid=2 否、mid=3 否 → lo=hi=4


def test_shrink_prefix_polluter_at_front_collapses() -> None:
    prefix = ["P", "a", "b", "c", "d"]
    short, _ = shrink_prefix(lambda sub: "P" in sub, prefix)
    assert short == ["P"]


def test_shrink_prefix_empty_prefix() -> None:
    assert shrink_prefix(lambda sub: True, []) == ([], 0)


# ---------------------------------------------------------------- ddmin


def test_ddmin_single_polluter_converges_fast() -> None:
    """mdutils 实例的同款形状：1 条 polluter，~log2(n) 次收敛。"""
    seq = [f"t{i}" for i in range(100)] + ["P"]
    short, runs, converged = minimize_polluters(lambda sub: "P" in sub, seq)
    assert short == ["P"]
    assert converged is True
    assert runs <= 12  # 远小于 100


def test_ddmin_two_polluters_preserves_order() -> None:
    seq = ["x1", "P1", "x2", "P2", "x3"]

    def fails(sub: list[str]) -> bool:
        return "P1" in sub and "P2" in sub

    short, _, converged = minimize_polluters(fails, seq)
    assert converged is True
    assert short == ["P1", "P2"]  # 相对顺序保持


def test_ddmin_already_minimal_no_calls() -> None:
    short, runs, converged = minimize_polluters(lambda sub: True, ["P"])
    assert (short, runs, converged) == (["P"], 0, True)


def test_ddmin_budget_exceeded_flags_upper_bound() -> None:
    """什么都删不掉（全都要）且序列长 → 撞预算，明说"是上界不是最小"。"""
    seq = [f"t{i}" for i in range(20)]
    short, runs, converged = minimize_polluters(lambda sub: True, seq, budget=3)
    assert converged is False
    assert len(short) < len(seq)  # 确实删掉了一些
    assert runs <= DEFAULT_MINIMIZE_BUDGET


def test_ddmin_never_returns_non_reproducing_sequence() -> None:
    """铁律：任何一次删除被接受，前提是 victim 仍然挂 —— 结果必须能复现。"""
    seq = ["a", "P", "b", "P", "c"]

    def fails(sub: list[str]) -> bool:
        return sub.count("P") >= 2

    short, _, converged = minimize_polluters(fails, seq)
    assert converged is True
    assert fails(short) is True
    assert short.count("P") == 2


# ---------------------------------------------------------------- 判定规则


def test_classify_victim_confirmed_high_confidence() -> None:
    verdict, conf = classify_order(
        orders_tried=4,
        failing_orders=["default"],
        alone_runs=10,
        alone_failures=0,
        minimized=True,
        polluters=["P"],
        polluter_alone_ok=True,
    )
    assert verdict == "flaky"
    assert conf.level == "high"
    assert "1/4 个顺序挂" in conf.basis
    assert "最小 polluter 集合 1 条" in conf.basis


def test_classify_victim_not_minimized_downgrades() -> None:
    verdict, conf = classify_order(
        orders_tried=4,
        failing_orders=["default", "reverse"],
        alone_runs=10,
        alone_failures=0,
        minimized=False,
        polluters=["P", "Q"],
        polluter_alone_ok=True,
    )
    assert verdict == "flaky"
    assert conf.level == "medium"
    assert "上界不是最小" in conf.note


def test_classify_polluter_alone_fail_keeps_victim_flags_attribution() -> None:
    verdict, conf = classify_order(
        orders_tried=4,
        failing_orders=["default"],
        alone_runs=10,
        alone_failures=0,
        minimized=True,
        polluters=["P"],
        polluter_alone_ok=False,
    )
    assert verdict == "flaky"  # victim 判定不变
    assert conf.level == "medium"
    assert "归因存疑" in conf.note


def test_classify_thin_alone_evidence_downgrades() -> None:
    verdict, conf = classify_order(
        orders_tried=4,
        failing_orders=["default"],
        alone_runs=MIN_ALONE_RUNS - 1,
        alone_failures=0,
        minimized=True,
        polluters=["P"],
        polluter_alone_ok=True,
    )
    assert verdict == "flaky"
    assert conf.level == "medium"
    assert "证据单薄" in conf.note


def test_classify_fails_alone_is_not_order_dependent() -> None:
    """单独跑也挂 → 不是顺序依赖（坏测试 / NIO 归 replay 模式）。"""
    verdict, conf = classify_order(
        orders_tried=4,
        failing_orders=["default"],
        alone_runs=10,
        alone_failures=10,
        minimized=True,
        polluters=[],
        polluter_alone_ok=True,
    )
    assert verdict == "inconclusive"
    assert "归 replay 模式" in conf.basis


def test_classify_all_orders_pass_is_negative_observation_only() -> None:
    """顺序全过 ≠ 稳定：只给阴性观察，不许判 stable（规范 §2.2）。"""
    verdict, conf = classify_order(
        orders_tried=4,
        failing_orders=[],
        alone_runs=10,
        alone_failures=0,
        minimized=False,
        polluters=[],
        polluter_alone_ok=True,
    )
    assert verdict == "inconclusive"
    assert "不等于稳定" in conf.basis


def test_classify_incomplete_orders_are_visible_not_counted() -> None:
    """没跑完的顺序（pytest 自己被搞崩）必须进备注，且不充进有效观测数。"""
    verdict, conf = classify_order(
        orders_tried=2,
        failing_orders=["default"],
        alone_runs=10,
        alone_failures=0,
        minimized=True,
        polluters=["P"],
        polluter_alone_ok=True,
        incomplete_orders=("random-seed1729-1:XML_EMPTY",),
    )
    assert verdict == "flaky"
    assert "1/2 个顺序挂" in conf.basis  # 分母只有 2（有效观测）
    assert "1 个顺序没跑完" in conf.note
    assert "XML_EMPTY" in conf.note


# ---------------------------------------------------------------- FakeRunner 接线


class FakeOrderRunner:
    """假 runner：polluter 一跑，之后 poison_by 里的测试全挂。

    抽象自 mdutils 实测机制（chdir 进临时目录 → 目录被删 → CWD 没了 →
    之后所有相对路径操作全挂）。支持三种特殊行为：
      always_fail      单独跑也挂（坏测试）
      fail_once        只在第一次进序列时挂（不可复现的翻转）
      fail_seq_calls   第 N 次 run_sequence 没跑完（1 起）—— 模拟
                       freezegun 实测：假时间泄漏进 pytest 计时器，
                       pytest 自己 INTERNALERROR，XML 没写出来
    """

    image = "python:3.9-slim"

    def __init__(
        self,
        tests: list[str],
        *,
        poison_by: dict[str, str] | None = None,
        always_fail: set[str] | None = None,
        fail_once: set[str] | None = None,
        collect_ok: bool = True,
        docker_error: str = "",
        fail_seq_calls: set[int] | None = None,
    ) -> None:
        self.tests = tests
        self.poison_by = poison_by or {}
        self.always_fail = always_fail or set()
        self.fail_once = fail_once or set()
        self.collect_ok = collect_ok
        self.docker_error = docker_error
        self.fail_seq_calls = fail_seq_calls or set()
        self.seq_calls: list[list[str]] = []
        self.replay_calls: list[tuple[str, int]] = []
        self._seen_once: set[str] = set()

    def collect(self, src, deps, testdir="tests"):
        if not self.collect_ok:
            return CollectOutcome(ok=False, reason="IMPORT_ERROR")
        return CollectOutcome(ok=True, tests=list(self.tests))

    def run_sequence(self, src, deps, seq, per_run_timeout=120, timeout=None):
        self.seq_calls.append(list(seq))
        if len(self.seq_calls) in self.fail_seq_calls:
            return SequenceOutcome(
                ok=False, reason="XML_EMPTY", log_tail="INTERNALERROR> boom"
            )
        if self.docker_error:
            return SequenceOutcome(ok=False, docker_error=self.docker_error)
        poisoners = set(self.poison_by.values())
        poisoned = False
        outcomes: dict[str, str] = {}
        for node in seq:
            if node in poisoners:
                poisoned = True
            bad = node in self.always_fail
            if node in self.fail_once and node not in self._seen_once:
                bad = True
            if poisoned and node in self.poison_by:
                bad = True
            self._seen_once.add(node)
            outcomes[node] = "fail" if bad else "pass"
        return SequenceOutcome(ok=True, outcomes=outcomes)

    def replay(self, src, deps, target, runs, per_run_timeout=120):
        self.replay_calls.append((target, runs))
        if target in self.always_fail:
            return ReplayOutcome(sequence=["fail"] * runs)
        return ReplayOutcome(sequence=["pass"] * runs)


def _scan(runner: FakeOrderRunner, **kw):
    kw.setdefault("orders", 4)
    kw.setdefault("alone_runs", 10)
    return scan_project_order(
        project="didix21/mdutils",
        sha="a" * 40,
        runner=runner,
        src_dir=Path("/c/src"),
        deps_dir=Path("/c/deps"),
        **kw,
    )


def test_scan_finds_victim_and_minimizes_polluter() -> None:
    """端到端：套装里挂 ∧ 单独过 → victim；ddmin 收敛到 1 条 polluter。"""
    tests = [f"t{i}" for i in range(20)] + ["V1", "V2", "other"]
    runner = FakeOrderRunner(tests, poison_by={"V1": "t5", "V2": "t5"})
    scan = _scan(runner)

    assert scan.error == ""
    assert scan.victim_nodes == {"V1", "V2"}
    assert scan.orders_tried == 4
    for victim in scan.victims:
        assert victim.verdict == "flaky"
        assert victim.mode == "order"
        assert victim.order_analysis is not None
        assert victim.order_analysis.polluters == ["t5"]
        assert victim.order_analysis.minimized is True
        assert victim.confidence.level == "high"
    # 没挂过的测试有记录，不许悄悄消失
    assert scan.non_victim_reasons["other"].startswith("ORDERS_ALL_PASS")


def test_scan_writes_evidence_log(tmp_path: Path) -> None:
    tests = ["t0", "V", "other"]
    runner = FakeOrderRunner(tests, poison_by={"V": "t0"})
    scan = _scan(runner, raw_log_dir=tmp_path)
    assert scan.victim_nodes == {"V"}
    logs = list(tmp_path.glob("*__order.log"))
    assert len(logs) == 1
    text = logs[0].read_text(encoding="utf-8")
    assert "最小 polluter 集合（1 条" in text
    assert "验证 ② victim 单独跑 ×10 → 10过/0挂" in text


def test_scan_alone_failure_is_not_victim() -> None:
    """套装里挂但单独跑也挂 → 坏测试/NIO，不归顺序模式。"""
    tests = ["t0", "broken", "other"]
    runner = FakeOrderRunner(tests, poison_by={"broken": "t0"}, always_fail={"broken"})
    scan = _scan(runner)
    assert scan.victims == []
    assert scan.non_victim_reasons["broken"].startswith("ALONE_NOT_CLEAN")


def test_scan_non_reproducible_failure_is_not_victim() -> None:
    """只翻一次、复现不了 → 不报警（PREFIX_NOT_REPRODUCED，留证）。"""
    tests = [f"t{i}" for i in range(10)] + ["flaky_once"]
    runner = FakeOrderRunner(tests, fail_once={"flaky_once"})
    scan = _scan(runner)
    assert scan.victims == []
    assert scan.non_victim_reasons["flaky_once"] == "PREFIX_NOT_REPRODUCED"


def test_scan_docker_error_aborts_project() -> None:
    """容器没跑起来 = 我们的问题 → 记 error 中断，不冒充"没 victim"。"""
    tests = ["t0", "V", "other"]
    runner = FakeOrderRunner(tests, poison_by={"V": "t0"}, docker_error="找不到 docker")
    scan = _scan(runner)
    assert scan.error.startswith("RUNNER_ERROR")
    assert scan.victims == []


def test_scan_collect_failure_aborts_project() -> None:
    runner = FakeOrderRunner(["t0"], collect_ok=False)
    scan = _scan(runner)
    assert scan.error == "COLLECT_FAIL:IMPORT_ERROR"


def test_scan_orders_one_only_default() -> None:
    """orders=1 时只跑默认序；victim 在默认序挂也能抓到（mdutils 场景）。"""
    tests = ["t0", "V", "other"]
    runner = FakeOrderRunner(tests, poison_by={"V": "t0"})
    scan = _scan(runner, orders=1)
    assert scan.orders_tried == 1
    assert scan.victim_nodes == {"V"}
    assert runner.seq_calls  # 只跑了套装序 + 最小化序列


def test_scan_incomplete_orders_not_counted_but_visible() -> None:
    """freezegun 实测复刻：2 个随机序 pytest 自己崩了（XML_EMPTY）。
    必须：① 不计入有效观测 ② victim 仍能从跑完的顺序里抓到
    ③ 崩掉的顺序在 non_victim_reasons 和 victim 备注里都可见。"""
    tests = ["t0", "V", "other"]
    runner = FakeOrderRunner(
        tests,
        poison_by={"V": "t0"},
        fail_seq_calls={3, 4},  # 两个随机序崩
    )
    scan = _scan(runner)
    assert scan.orders_tried == 2  # 只有 default + reverse 算有效观测
    assert scan.victim_nodes == {"V"}
    assert "(order:random-seed1729-1)" in scan.non_victim_reasons
    assert "XML_EMPTY" in scan.non_victim_reasons["(order:random-seed1729-1)"]
    victim = scan.victims[0]
    assert "1/2 个顺序挂" in victim.confidence.basis
    assert "XML_EMPTY" in victim.confidence.note
