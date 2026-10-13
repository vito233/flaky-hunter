"""WorkBuddy 免密钥大模型 client（`LLMClient` 协议实现，模型来源三岔路之 C）。

协议逐行核对自官方 JS SDK（`@tencent-ai/workbuddy-cloud-sdk@dev`，
lib/index.js，2026-10-13），不从记忆猜测：

- ``POST {endpoint}/.cloud/llm/chat/completions``
- header ``x-wb-webapp-access-key: <publishableKey>``（keyless 匿名场景不发
  Authorization；SDK 默认不发 Origin）
- body：OpenAI 风格，``stream`` 必须为 ``true``（网关不支持非流式）
- 响应：SSE —— ``data: {chunk}`` 帧、``choices[0].delta.content`` 增量、
  ``data: [DONE]`` 结束；错误以 ``event: error`` 或 chunk 内嵌 ``error`` 出现

配置来源：构造参数或环境变量 ``FLAKY_WB_ENDPOINT`` /
``FLAKY_WB_PUBLISHABLE_KEY``（激活流程产出的 publicConfig；key 不入仓库、
不入 git、不入日志）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from flaky_hunter.attribution.models import AttributionInput
from flaky_hunter.attribution.prompt import build_prompt

# HTTP 层错误：可重试（网络/网关），与"模型输出解析失败"（逐条留痕）严格区分
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class TransportError(RuntimeError):
    """网络/网关层错误（携带 X-Request-Id 便于诊断）。不可当作解析失败吞掉。"""


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, TransportError) and getattr(exc, "retryable", False)


class WorkBuddyLLM:
    """经 WorkBuddy 云网关调大模型。同输入不保证同输出（真模型），
    归因评测记录 model_id / 温度 / 原始输出以保证可追溯。"""

    def __init__(
        self,
        *,
        model_id: str,
        endpoint: str | None = None,
        publishable_key: str | None = None,
        temperature: float = 0.0,
        timeout: float = 120.0,
        max_retries: int = 2,
        transport: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.model_id = model_id
        env_endpoint = endpoint or os.environ.get("FLAKY_WB_ENDPOINT", "")
        self.endpoint = env_endpoint.rstrip("/")
        self.publishable_key = publishable_key or os.environ.get(
            "FLAKY_WB_PUBLISHABLE_KEY", ""
        )
        if not self.endpoint or not self.publishable_key:
            msg = (
                "缺少 WorkBuddy 云服务配置：需要 endpoint 与 publishableKey"
                "（构造参数或环境变量 FLAKY_WB_ENDPOINT / FLAKY_WB_PUBLISHABLE_KEY）"
            )
            raise ValueError(msg)
        if not self.publishable_key.startswith("wbpk_"):
            msg = "publishableKey 必须以 wbpk_ 开头（SDK 同款校验）"
            raise ValueError(msg)
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        # transport 注入仅用于测试；生产用 requests
        self._transport = transport

    @property
    def _url(self) -> str:
        return f"{self.endpoint}/.cloud/llm/chat/completions"

    @retry(
        retry=retry_if_exception(_is_retryable),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=15),
        reraise=True,
    )
    def _stream_chat(self, payload: dict[str, Any]) -> str:
        """发起流式请求并聚合增量文本。传输错误抛 TransportError。"""
        body = {**payload, "stream": True}
        try:
            response = self._post(body)
        except requests.RequestException as exc:
            raise TransportError(f"网络错误: {exc}") from exc
        if response.status_code >= 400:
            request_id = response.headers.get("x-request-id", "")
            err = TransportError(
                f"HTTP {response.status_code}: {response.text[:300]}"
                + (f"（request-id: {request_id}）" if request_id else "")
            )
            err.retryable = response.status_code in RETRYABLE_STATUS  # type: ignore[attr-defined]
            raise err
        return self._consume_sse(response)

    def _post(self, body: dict[str, Any]) -> Any:
        """执行 POST 并返回 response-like 对象（transport 注入仅用于测试）。"""
        headers = {
            "x-wb-webapp-access-key": self.publishable_key,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if self._transport is not None:
            return self._transport(headers, body)
        return requests.post(
            self._url,
            headers=headers,
            json=body,
            stream=True,
            timeout=self.timeout,
        )

    def _consume_sse(self, response: requests.Response) -> str:
        parts: list[str] = []
        saw_done = False
        # SSE 网关不回 charset；requests 对 text/* 默认按 ISO-8859-1 解码，
        # 中文 content 会变乱码——显式钉 UTF-8（实测 2026-10-13 冒烟抓到）
        response.encoding = "utf-8"
        for raw in response.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data:"):
                continue
            data = raw[len("data:") :].strip()
            if data == "[DONE]":
                saw_done = True
                break
            try:
                frame = json.loads(data)
            except json.JSONDecodeError:
                raise TransportError(f"SSE 帧不是 JSON: {data[:200]}") from None
            error = frame.get("error")
            if error:
                request_id = response.headers.get("x-request-id", "")
                suffix = f"（{request_id}）" if request_id else ""
                raise TransportError(f"网关错误: {error}{suffix}")
            for choice in frame.get("choices", []):
                delta = choice.get("delta", {})
                content = delta.get("content")
                if isinstance(content, str):
                    parts.append(content)
        if not saw_done:
            raise TransportError(
                "流中断：未收到 [DONE]（gateway_stream_interrupted 语义）"
            )
        return "".join(parts)

    def attribute(self, inp: AttributionInput) -> str:
        """返回模型原始文本（JSON 期望由 harness 层统一解析）。"""
        prompt = build_prompt(inp)
        payload = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }
        return self._stream_chat(payload)
