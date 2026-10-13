"""确定性假模型：harness 可复现、零成本、不依赖外部 API（C2 地板，2026-10-13）。

目的：不接真模型就能测通整条 harness（prompt 渲染 → 调用 → 结构化解析 →
解析失败率统计）。规则简单到可以心算，同输入必同输出。
**它的准确率没有任何意义** —— 对外报数字前必须换真模型（模型来源三岔路
见项目现状附 4：假模型先行 / 真 API / WorkBuddy 免密钥）。
"""

from __future__ import annotations

import json

from flaky_hunter.attribution.labels import AttributionLabel
from flaky_hunter.attribution.models import AttributionInput

Rule = tuple[tuple[str, ...], AttributionLabel]


class FakeLLM:
    """按失败日志关键词规则命中的确定性假模型。先命中先得，全不命中 → OTHER。"""

    model_id = "fake-deterministic-v0"

    _RULES: tuple[Rule, ...] = (
        (("OrderError", "polluter", "order"), AttributionLabel.OD_VIC),
        (("second call", "already exists", "second time"), AttributionLabel.NIO),
        (("EOF", "random", "hash"), AttributionLabel.NOD),
    )

    def attribute(self, inp: AttributionInput) -> str:
        """返回与真模型同构的原始 JSON 文本（解析统一在 harness 层做）。"""
        haystack = f"{inp.failure_log}\n{inp.code_diff}"
        label = AttributionLabel.OTHER
        for keywords, candidate in self._RULES:
            if any(k in haystack for k in keywords):
                label = candidate
                break
        payload = {
            "label": label.value,
            "rationale": f"FakeLLM 关键词规则命中（{self.model_id}）",
        }
        return json.dumps(payload, ensure_ascii=False)
