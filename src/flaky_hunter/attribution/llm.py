"""LLM 调用封装 + 结构化解析 + 解析失败率统计（C2 地板，规范 §3.2）。

设计：模型只负责吐原始文本，解析统一在 harness 层做 —— 解析失败不吞掉，
逐条留痕进失败率统计（DoD：10 样本结构化结果 + 解析失败率）。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import pydantic

from flaky_hunter.attribution.models import AttributionInput, AttributionOutput
from flaky_hunter.attribution.prompt import PROMPT_VERSION


class LLMClient(Protocol):
    """真 / 假模型共同遵守的最小接口：吃输入，吐原始文本。"""

    model_id: str

    def attribute(self, inp: AttributionInput) -> str: ...


@dataclass(frozen=True)
class AttributionRecord:
    """一条样本的归因全过程留痕（output=None 即解析失败，error 记原因）。"""

    inp: AttributionInput
    raw: str
    output: AttributionOutput | None
    error: str | None = None


def _strip_code_fence(raw: str) -> str:
    """剥掉模型常见的 markdown 栅栏（```json ... ```）再解析。

    解析器行为变化（2026-10-13）：接真模型后新增，不影响 PROMPT_VERSION。
    """
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip().endswith("```"):
            lines = lines[1:-1]
        text = "\n".join(lines).strip()
    return text


def parse_output(raw: str, *, model_id: str, prompt_version: str) -> AttributionOutput:
    """原始文本 → AttributionOutput。要求 JSON：{"label": ..., "rationale": ...}。

    标签不在裁剪后标签集内 / JSON 坏 / 缺字段 → 抛异常（由调用方计入解析失败）。
    """
    data = json.loads(_strip_code_fence(raw))
    if not isinstance(data, dict):
        msg = f"期望 JSON 对象，得到 {type(data).__name__}"
        raise ValueError(msg)
    return AttributionOutput.model_validate(
        {**data, "model_id": model_id, "prompt_version": prompt_version}
    )


def run_attribution(
    samples: Sequence[AttributionInput],
    client: LLMClient,
) -> list[AttributionRecord]:
    """批量归因：调 client → 逐条解析 → 失败留痕（不让单条坏输出炸掉整批）。"""
    records: list[AttributionRecord] = []
    for inp in samples:
        raw = client.attribute(inp)
        try:
            out = parse_output(
                raw, model_id=client.model_id, prompt_version=PROMPT_VERSION
            )
        except (json.JSONDecodeError, ValueError, pydantic.ValidationError) as exc:
            records.append(
                AttributionRecord(inp=inp, raw=raw, output=None, error=str(exc))
            )
        else:
            records.append(AttributionRecord(inp=inp, raw=raw, output=out))
    return records


def parse_failure_rate(records: Iterable[AttributionRecord]) -> float:
    """解析失败率 = 解析失败条数 / 总条数（空集返回 0.0）。"""
    recs = list(records)
    if not recs:
        return 0.0
    return sum(1 for r in recs if r.output is None) / len(recs)
