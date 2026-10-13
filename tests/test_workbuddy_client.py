"""WorkBuddyLLM 测试：SSE 聚合 / 错误分支 / 配置校验（HTTP 全部 mock，不联网）。"""

import json

import pytest

from flaky_hunter.attribution.llm import parse_output
from flaky_hunter.attribution.models import AttributionInput
from flaky_hunter.attribution.workbuddy_client import TransportError, WorkBuddyLLM


class FakeResponse:
    """requests.Response 的最小替身（SSE 行流）。"""

    def __init__(
        self,
        lines: list[str] | None = None,
        status_code: int = 200,
        text: str = "",
    ) -> None:
        self._lines = lines or []
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}

    def iter_lines(self, decode_unicode: bool = True) -> list[str]:
        return self._lines


def _sse_lines(*chunks: str) -> list[str]:
    lines = []
    for i, chunk in enumerate(chunks):
        frame = {
            "choices": [
                {"delta": {"content": chunk}, "index": i, "finish_reason": None}
            ]
        }
        lines.append(f"data: {json.dumps(frame, ensure_ascii=False)}")
    lines.append("data: [DONE]")
    return lines


def _client(transport) -> WorkBuddyLLM:
    return WorkBuddyLLM(
        model_id="test-model",
        endpoint="https://example.test",
        publishable_key="wbpk_test",
        transport=transport,
    )


def _inp() -> AttributionInput:
    return AttributionInput(test_name="t::x", failure_log="log")


def test_sse_chunks_aggregated() -> None:
    captured: dict[str, object] = {}

    def transport(headers: dict[str, str], body: dict) -> FakeResponse:
        captured["headers"] = dict(headers)
        captured["body"] = dict(body)
        return FakeResponse(_sse_lines('{"label": ', '"NIO"}'))

    client = _client(transport)
    raw = client.attribute(_inp())
    assert raw == '{"label": "NIO"}'
    sent_headers = captured["headers"]
    assert sent_headers["x-wb-webapp-access-key"] == "wbpk_test"  # type: ignore[index]
    assert sent_headers["Accept"] == "text/event-stream"  # type: ignore[index]
    sent_body = captured["body"]
    assert sent_body["stream"] is True  # type: ignore[index]
    assert sent_body["model"] == "test-model"  # type: ignore[index]


def test_http_400_raises_non_retryable_immediately() -> None:
    calls: list[int] = []

    def transport(headers: dict[str, str], body: dict) -> FakeResponse:
        calls.append(1)
        return FakeResponse(status_code=400, text="bad request")

    client = _client(transport)
    with pytest.raises(TransportError, match="HTTP 400"):
        client.attribute(_inp())
    assert len(calls) == 1  # 不可重试错误不重试


def test_sse_error_frame_raises() -> None:
    def transport(headers: dict[str, str], body: dict) -> FakeResponse:
        err_frame = "data: " + json.dumps({"error": {"message": "quota"}})
        return FakeResponse([err_frame, "data: [DONE]"])

    client = _client(transport)
    with pytest.raises(TransportError, match="quota"):
        client.attribute(_inp())


def test_missing_done_raises() -> None:
    def transport(headers: dict[str, str], body: dict) -> FakeResponse:
        return FakeResponse(_sse_lines("partial")[:-1])  # 去掉 [DONE]

    client = _client(transport)
    with pytest.raises(TransportError, match="流中断"):
        client.attribute(_inp())


def test_key_prefix_validation() -> None:
    with pytest.raises(ValueError, match="wbpk_"):
        WorkBuddyLLM(
            model_id="m",
            endpoint="https://example.test",
            publishable_key="not-wbpk",
        )


def test_missing_config_raises() -> None:
    with pytest.raises(ValueError, match="缺少 WorkBuddy 云服务配置"):
        WorkBuddyLLM(model_id="m")


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLAKY_WB_ENDPOINT", "https://env.example.test/")
    monkeypatch.setenv("FLAKY_WB_PUBLISHABLE_KEY", "wbpk_env")

    def transport(headers: dict[str, str], body: dict) -> FakeResponse:
        return FakeResponse(_sse_lines("{}"))

    client = WorkBuddyLLM(model_id="m", transport=transport)
    assert client.endpoint == "https://env.example.test"  # 尾部斜杠已剥
    client.attribute(_inp())  # 不抛 = 环境变量注入生效


def test_parse_output_strips_code_fence() -> None:
    """真模型常见输出：```json 包裹。栅栏剥离后正常解析。"""
    fenced = '```json\n{"label": "NIO", "rationale": "第二次挂"}\n```'
    out = parse_output(fenced, model_id="m", prompt_version="v0")
    assert out.label.value == "NIO"
