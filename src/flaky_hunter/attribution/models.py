"""归因评测的输入 / 输出 schema（pydantic；规范 §3.1：禁止手写 dict 校验）。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from flaky_hunter.attribution.labels import AttributionLabel


class AttributionInput(BaseModel):
    """归因任务输入（规范 §3.2：失败日志 + 代码 diff +（可选）通过日志）。

    ``project`` 只用于评测分组与泄漏检查，不会渲染进 prompt。
    """

    test_name: str = Field(min_length=1, description="目标测试节点 ID")
    failure_log: str = Field(min_length=1, description="一次 fail run 的完整输出")
    code_diff: str = Field(default="", description="相关代码 diff")
    pass_log: str | None = Field(
        default=None, description="一次 pass run 的输出（可选）"
    )
    project: str | None = Field(default=None, description="owner/repo，仅评测分组用")


class AttributionOutput(BaseModel):
    """模型结构化输出：标签限定在裁剪后的标签集内（6 类）。"""

    label: AttributionLabel
    rationale: str = Field(default="", max_length=2000)
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
