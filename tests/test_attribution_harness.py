"""attribution harness 测试：schema / prompt 版本化 / FakeLLM 确定性 / 解析失败率。

DoD（TODOLIST 原第 4 周）：10 样本跑出结构化结果 + 解析失败率统计。
"""

import csv
import json
from pathlib import Path

import pydantic
import pytest

from flaky_hunter.attribution.fake import FakeLLM
from flaky_hunter.attribution.labels import AttributionLabel
from flaky_hunter.attribution.llm import (
    parse_failure_rate,
    parse_output,
    run_attribution,
)
from flaky_hunter.attribution.models import AttributionInput
from flaky_hunter.attribution.prompt import PROMPT_VERSION, FewShotExample, build_prompt
from flaky_hunter.baselines import EVAL_MAJORITY_CLASS_BASELINE

GROUND_TRUTH_CSV = (
    Path(__file__).resolve().parents[1] / "experiments" / "ground_truth_phase_a.csv"
)


def _load_first_eligible(n: int) -> list[AttributionInput]:
    """从真值表取前 n 条 eligible 样本当 harness 输入（只取身份字段，不臆造）。"""
    with GROUND_TRUTH_CSV.open(encoding="utf-8-sig", newline="") as fh:
        rows = [
            r
            for r in csv.DictReader(fh)
            if r["eval_eligible"].strip().lower() == "true"
        ]
    return [
        AttributionInput(
            test_name=r["test_name"],
            failure_log=(
                f"FAILED {r['test_name']} (来自 {r['project']}，harness 冒烟输入)"
            ),
            code_diff="",
            project=r["project"],
        )
        for r in rows[:n]
    ]


def test_fake_llm_is_deterministic() -> None:
    inp = AttributionInput(test_name="t::x", failure_log="OrderError: victim failed")
    client = FakeLLM()
    assert client.attribute(inp) == client.attribute(inp)


def test_fake_llm_keyword_rules() -> None:
    def attr(log: str) -> AttributionLabel:
        raw = FakeLLM().attribute(AttributionInput(test_name="t", failure_log=log))
        return json.loads(raw)["label"]

    assert attr("boom OrderError while tearing down") == AttributionLabel.OD_VIC.value
    assert attr("second call returned stale cache") == AttributionLabel.NIO.value
    assert attr("random failure in hash bucket") == AttributionLabel.NOD.value
    assert attr("nothing matches here") == AttributionLabel.OTHER.value


def test_build_prompt_contains_version_and_labels() -> None:
    inp = AttributionInput(test_name="t::x", failure_log="log line")
    prompt = build_prompt(inp)
    assert PROMPT_VERSION.startswith("attribution-prompt-")
    assert "OD-Vic" in prompt and "OTHER" in prompt
    assert "JSON" in prompt  # 输出格式契约写死在模板里


def test_build_prompt_renders_few_shot() -> None:
    example = FewShotExample(
        project="synthetic/demo",
        test_name="tests/test_a.py::test_demo",
        failure_log="AssertionError on first run",
        label=AttributionLabel.NIO,
    )
    prompt = build_prompt(
        AttributionInput(test_name="t::x", failure_log="log"),
        few_shot=[example],
    )
    assert "示例 1（项目 synthetic/demo）" in prompt
    assert example.label.value in prompt


def test_parse_output_ok_and_bad_label() -> None:
    out = parse_output(
        json.dumps({"label": "OD-Vic", "rationale": "r"}),
        model_id="m",
        prompt_version=PROMPT_VERSION,
    )
    assert out.label == AttributionLabel.OD_VIC
    assert out.model_id == "m" and out.prompt_version == PROMPT_VERSION

    with pytest.raises(pydantic.ValidationError):
        parse_output(
            json.dumps({"label": "NOT_A_LABEL"}),
            model_id="m",
            prompt_version=PROMPT_VERSION,
        )


def test_parse_output_rejects_non_object() -> None:
    with pytest.raises(ValueError, match="期望 JSON 对象"):
        parse_output("[1, 2]", model_id="m", prompt_version=PROMPT_VERSION)


def test_run_attribution_counts_parse_failures() -> None:
    """坏输出逐条留痕，不炸整批；失败率 = 1/2。"""

    class HalfBroken:
        model_id = "half-broken-v0"

        def attribute(self, inp: AttributionInput) -> str:
            if "good" in inp.failure_log:
                return json.dumps({"label": "NIO", "rationale": "ok"})
            return "这不是 JSON"

    samples = [
        AttributionInput(test_name="t1", failure_log="good log"),
        AttributionInput(test_name="t2", failure_log="bad log"),
    ]
    records = run_attribution(samples, HalfBroken())
    assert len(records) == 2
    assert records[0].output is not None and records[1].output is None
    assert records[1].error is not None
    assert parse_failure_rate(records) == pytest.approx(0.5)


def test_ten_samples_structured_results_dod() -> None:
    """DoD：10 样本跑出结构化结果，FakeLLM 下解析失败率 0%。

    注意：这里只验证 harness 通、结果结构化；FakeLLM 的准确率无意义
    （见 fake.py docstring），对外报数字前必须换真模型。
    """
    samples = _load_first_eligible(10)
    assert len(samples) == 10
    records = run_attribution(samples, FakeLLM())
    assert len(records) == 10
    assert all(r.output is not None for r in records)
    assert all(r.error is None for r in records)
    assert parse_failure_rate(records) == 0.0
    # 输出必须带模型与 prompt 版本（可追溯性契约）
    assert all(r.output.model_id == "fake-deterministic-v0" for r in records)
    assert all(r.output.prompt_version == PROMPT_VERSION for r in records)
    # 标签必须落在裁剪后标签集内
    assert all(isinstance(r.output.label, AttributionLabel) for r in records)
    # 真值表 10 条样本至少覆盖 2 个项目，防止"取了 10 条同一项目"的假覆盖
    assert len({s.project for s in samples}) >= 2
    # 本测试顺带自检基线常量在合法范围（铁律 3：数字旁边有基线）
    assert 0.0 < EVAL_MAJORITY_CLASS_BASELINE < 1.0
