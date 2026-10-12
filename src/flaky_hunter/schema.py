"""检测结果统一 schema（规范 §3.1）—— 所有模块共用，禁止手写 dict。

代码即契约：改这里 = 改接口契约，必须同步 docs/data-dictionary.md。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from flaky_hunter.baselines import RERUN_FOR_95_CONFIDENCE

SCHEMA_VERSION = "1.0"

Mode = Literal["replay", "order", "history"]
Verdict = Literal["flaky", "stable", "inconclusive"]
ConfidenceLevel = Literal["low", "medium", "high"]

# 目标项目环境状态（规范 §2.5 / AGENTS.md：只许这五个值，不许自创）
TargetStatus = Literal["OK", "DEPS_FAIL", "TEST_NOT_FOUND", "SHA_NOT_FOUND", "TIMEOUT"]


class RunEnv(BaseModel):
    """目标项目运行环境。默认断网（--network none，规范 §2.5）。"""

    image: str
    python: str
    network: str = "none"


class RunStats(BaseModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    flip_count: int = Field(ge=0)


class OrderAnalysis(BaseModel):
    """顺序变换分支的结果（iDFlakies 流程：随机序 → 失败序 → 二分最小化）。"""

    permutations_tried: int = Field(ge=0)
    failing_orders: int = Field(ge=0)
    victim: str
    polluters: list[str] = Field(default_factory=list)
    minimized: bool = False


class Confidence(BaseModel):
    """报"稳定"必须附带依据 —— 0 次翻转 ≠ 稳定（规范 §2.2）。"""

    level: ConfidenceLevel
    basis: str
    note: str = ""


class DetectionResult(BaseModel):
    """检测器 / 归因的统一输出（规范 §3.1）。"""

    schema_version: str = SCHEMA_VERSION
    project: str  # owner/repo
    sha: str  # 40 位检出 SHA（铁律 2：按 SHA Detected 检出）
    test_name: str
    mode: Mode
    env: RunEnv
    runs: RunStats | None = None  # order 模式无重跑语义时可为空
    verdict: Verdict
    order_analysis: OrderAnalysis | None = None
    confidence: Confidence
    timestamp_utc: datetime  # 必须带时区（DTZ）
    raw_log_path: str = ""
    status: TargetStatus = "OK"

    @model_validator(mode="after")
    def stable_requires_evidence(self) -> DetectionResult:
        """铁律 3 / 规范 §2.2 的技术兜底：

        宣称 stable 却没有 170 遍级别的重跑支撑、又拿不出 high 置信依据 —— 拒收。
        """
        if (
            self.verdict == "stable"
            and (self.runs is None or self.runs.total < RERUN_FOR_95_CONFIDENCE)
            and self.confidence.level != "high"
        ):
            raise ValueError(
                f"verdict=stable 需要 runs.total >= {RERUN_FOR_95_CONFIDENCE} "
                f"或 confidence.level=high（0 次翻转 ≠ 稳定，规范 §2.2）；"
                f"实际 runs={self.runs.total if self.runs else None}, "
                f"confidence={self.confidence.level}"
            )
        return self

    def with_utc_now(self) -> DetectionResult:
        """便捷方法：盖 UTC 时间戳（产出结果时调用）。"""
        self.timestamp_utc = datetime.now(UTC)
        return self
