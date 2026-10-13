"""Prompt 模板（规范 §3.2：模板带 VERSION，实验结果必须记版本号）。"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field

from flaky_hunter.attribution.labels import AttributionLabel
from flaky_hunter.attribution.models import AttributionInput

PROMPT_VERSION = "attribution-prompt-v0.1.0"

_LABELS_TEXT = " / ".join(label.value for label in AttributionLabel)

_LABEL_MEANINGS = """\
- OD-Vic：顺序依赖受害者 —— 单独跑能过，套装里排在某些测试之后才挂
- OD-Brit：顺序依赖 brittle 型 —— 自己先跑会弄坏排在后面的测试
- OD：顺序依赖（官方未细分 victim / brittle）
- NIO：第一次跑过、重复跑挂（进程内状态没清干净）
- NOD：非确定性 —— 同样条件重跑结果不稳定（随机数 / 哈希序 / 时间）
- OTHER：以上都不是（实现依赖、环境依赖、未知依赖等）"""


class FewShotExample(BaseModel):
    """few-shot 示例。``project`` 用于泄漏检查：示例不得来自评测项目。"""

    project: str = Field(
        min_length=1, description="示例来源项目 owner/repo（或 synthetic/...）"
    )
    test_name: str = Field(min_length=1)
    failure_log: str = Field(min_length=1)
    code_diff: str = ""
    label: AttributionLabel


def build_prompt(
    inp: AttributionInput,
    few_shot: Sequence[FewShotExample] = (),
) -> str:
    """渲染归因 prompt。few-shot 示例按序渲染，泄漏检查由 leakage 模块负责。"""
    sections: list[str] = [
        "你是 flaky 测试归因助手。给定一个“时好时坏”测试的失败日志与相关代码 diff，"
        "判断它的 flaky 类别。",
        f"标签集（只能输出其中一个）：{_LABELS_TEXT}",
        f"各类别含义：\n{_LABEL_MEANINGS}",
    ]
    for i, ex in enumerate(few_shot, start=1):
        sections.append(
            f"### 示例 {i}（项目 {ex.project}）\n"
            f"测试：{ex.test_name}\n失败日志（节选）：{ex.failure_log}\n"
            f"代码 diff：{ex.code_diff or '（无）'}\n正确标签：{ex.label.value}"
        )
    sections.append(
        "## 待归因样本\n"
        f"测试：{inp.test_name}\n"
        f"失败日志：\n{inp.failure_log}\n\n"
        f"代码 diff：\n{inp.code_diff or '（无）'}"
    )
    sections.append(
        "只输出一个 JSON 对象，不要输出其它内容："
        '{"label": "<标签>", "rationale": "<一句话依据>"}'
    )
    return "\n\n".join(sections)
