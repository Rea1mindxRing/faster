"""OpenAI-compatible adapter: one implementation covering vLLM, SGLang,
llama.cpp server, Ollama, LM Studio and every major cloud API.

SSE edge-case semantics (normative in docs/metrics.md section 1):
- role-only chunks (delta without content) are NOT tokens
- usage-only chunks (choices == []) update token counts but are NOT tokens
- reasoning chunks (``delta.reasoning`` / ``delta.reasoning_content``) ARE
  tokens: thinking is decode work and is included in the server-side
  ``usage.completion_tokens`` that our TPOT denominator relies on. Counting
  them keeps timing and token accounting consistent for thinking models.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from faster.config import EndpointConfig
from faster.events import ErrorKind
from faster.provider.base import (
    CompletionRequest,
    ProviderError,
    StreamEvent,
    StreamKind,
)


class OpenAICompatProvider:
    def __init__(self, endpoint: EndpointConfig) -> None:
        self._endpoint = endpoint
        headers = {"Authorization": f"Bearer {endpoint.api_key}"} if endpoint.api_key else {}
        timeout = httpx.Timeout(endpoint.timeout_s)
        self._client = httpx.AsyncClient(
            base_url=endpoint.base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        )

    async def stream_complete(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "stream": True,
            "max_tokens": request.max_tokens,
            # Ask servers to send a final usage-only chunk when supported;
            # harmless if ignored. Override via extra_body when needed.
            "stream_options": {"include_usage": True},
            **request.extra_body,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature

        try:
            async with self._client.stream("POST", "/chat/completions", json=body) as response:
                if response.status_code >= 400:
                    await response.aread()
                    kind = (
                        ErrorKind.HTTP_CLIENT
                        if response.status_code < 500
                        else ErrorKind.HTTP_SERVER
                    )
                    raise ProviderError(
                        kind,
                        f"HTTP {response.status_code} from {response.request.url}",
                    )
                yield StreamEvent(kind=StreamKind.CONNECTED, ts=time.perf_counter())
                async for event in self._iter_sse(response):
                    yield event
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError(ErrorKind.TIMEOUT, f"request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(ErrorKind.CONNECTION, f"connection error: {exc}") from exc

    async def _iter_sse(self, response: httpx.Response) -> AsyncIterator[StreamEvent]:
        """Parse the SSE byte stream into StreamEvents (see module docstring)."""
        usage: dict[str, int] | None = None
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if not payload:
                continue
            if payload == "[DONE]":
                break
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    ErrorKind.SSE_PROTOCOL, f"malformed SSE JSON: {payload[:120]}"
                ) from exc

            chunk_usage = data.get("usage")
            if isinstance(chunk_usage, dict):
                usage = {
                    "prompt_tokens": int(chunk_usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(chunk_usage.get("completion_tokens") or 0),
                }

            choices = data.get("choices") or []
            if not choices:
                continue  # usage-only chunk: counted above, not a token
            delta = choices[0].get("delta") or {}
            content = delta.get("content")
            # reasoning deltas count as tokens too (see module docstring);
            # a chunk with both fields is still exactly one arrival
            is_token = (isinstance(content, str) and content) or (
                isinstance(delta.get("reasoning"), str) and delta["reasoning"]
            ) or (
                isinstance(delta.get("reasoning_content"), str) and delta["reasoning_content"]
            )
            if is_token:
                text = content if isinstance(content, str) and content else ""
                yield StreamEvent(kind=StreamKind.TOKEN, ts=time.perf_counter(), text=text)

        ts = time.perf_counter()
        if usage is not None:
            yield StreamEvent(
                kind=StreamKind.DONE,
                ts=ts,
                prompt_tokens=usage["prompt_tokens"] or None,
                completion_tokens=usage["completion_tokens"] or None,
            )
        else:
            yield StreamEvent(kind=StreamKind.DONE, ts=ts)

    async def aclose(self) -> None:
        await self._client.aclose()
