"""C2 · LLM 归因评测（地板阶段）：标签集 / schema / prompt / FakeLLM / 泄漏防线。

地板原则：整条 harness 不依赖任何外部服务与真模型 —— FakeLLM 先行，
真模型来源（假模型接真 / 真 API / WorkBuddy 免密钥）待拍板后即插即用。
"""

from flaky_hunter.attribution.labels import AttributionLabel, map_category
from flaky_hunter.attribution.leakage import (
    assert_no_leakage,
    find_leakage,
    load_eval_projects,
)
from flaky_hunter.attribution.models import AttributionInput, AttributionOutput
from flaky_hunter.attribution.prompt import PROMPT_VERSION, FewShotExample, build_prompt

__all__ = [
    "PROMPT_VERSION",
    "AttributionInput",
    "AttributionLabel",
    "AttributionOutput",
    "FewShotExample",
    "assert_no_leakage",
    "build_prompt",
    "find_leakage",
    "load_eval_projects",
    "map_category",
]
