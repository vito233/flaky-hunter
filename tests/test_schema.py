"""schema.py 的单元测试：统一输出 schema + "stable 需要证据"的防线。"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from flaky_hunter.baselines import RERUN_FOR_95_CONFIDENCE
from flaky_hunter.schema import (
    SCHEMA_VERSION,
    Confidence,
    DetectionResult,
    RunEnv,
    RunStats,
)


def make_result(**overrides) -> DetectionResult:
    base: dict = {
        "project": "mtik00/yamicache",
        "sha": "a" * 40,
        "test_name": "tests/test_noclass.py::test_main",
        "mode": "replay",
        "env": RunEnv(image="python:3.9-slim", python="3.9.18"),
        "runs": RunStats(total=10, passed=1, failed=9, flip_count=9),
        "verdict": "flaky",
        "confidence": Confidence(level="medium", basis="runs=10"),
        "timestamp_utc": datetime(2026, 10, 12, tzinfo=UTC),
    }
    base.update(overrides)
    return DetectionResult(**base)


def test_roundtrip_json() -> None:
    result = make_result()
    restored = DetectionResult.model_validate_json(result.model_dump_json())
    assert restored == result
    assert restored.schema_version == SCHEMA_VERSION


def test_stable_without_evidence_rejected() -> None:
    # 0 次翻转 ≠ 稳定（规范 §2.2）：20 遍 + low 置信 不许宣称 stable
    with pytest.raises(ValidationError, match="0 次翻转"):
        make_result(
            verdict="stable",
            runs=RunStats(total=20, passed=20, failed=0, flip_count=0),
            confidence=Confidence(level="low", basis="runs=20"),
        )


def test_stable_with_170_runs_accepted() -> None:
    result = make_result(
        verdict="stable",
        runs=RunStats(
            total=RERUN_FOR_95_CONFIDENCE, passed=170, failed=0, flip_count=0
        ),
        confidence=Confidence(level="medium", basis="runs=170"),
    )
    assert result.verdict == "stable"


def test_stable_with_high_confidence_accepted() -> None:
    result = make_result(
        verdict="stable",
        runs=RunStats(total=30, passed=30, failed=0, flip_count=0),
        confidence=Confidence(level="high", basis="CI 历史 30 次复核"),
    )
    assert result.verdict == "stable"


def test_target_status_enum_only() -> None:
    with pytest.raises(ValidationError):
        make_result(status="SOMETHING_ELSE")  # 只许五值，不许自创
