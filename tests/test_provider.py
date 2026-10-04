"""OpenAI-compatible adapter tests against a scripted in-process SSE server.

The mock server speaks real HTTP/1.1 with chunked transfer encoding and
scripted inter-chunk delays, so streaming timing and SSE edge-case semantics
are exercised end to end (docs/metrics.md section 1).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest

from faster.config import EndpointConfig
from faster.events import ErrorKind
from faster.provider.base import CompletionRequest, ProviderError, StreamKind
from faster.provider.openai_compat import OpenAICompatProvider


def sse(obj: object) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@dataclass
class SSEServer:
    script: list[tuple[float, str]] = field(default_factory=list)
    status: int = 200
    _server: asyncio.Server | None = None
    port: int = 0

    async def start(self) -> None:
        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b"\r\n\r\n")
                reason = "OK" if self.status == 200 else "Error"
                head = (
                    f"HTTP/1.1 {self.status} {reason}\r\n"
                    "Content-Type: text/event-stream\r\n"
                    "Transfer-Encoding: chunked\r\n\r\n"
                ).encode()
                writer.write(head)
                await writer.drain()
                for delay, payload in self.script:
                    await asyncio.sleep(delay)
                    chunk = payload.encode()
                    writer.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                    await writer.drain()
                writer.write(b"0\r\n\r\n")
                await writer.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                writer.close()

        self._server = await asyncio.start_server(handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


def make_provider(server: SSEServer) -> OpenAICompatProvider:
    return OpenAICompatProvider(EndpointConfig(base_url=server.base_url, model="test-model"))


async def collect(provider: OpenAICompatProvider) -> list:
    events = []
    stream = provider.stream_complete(
        CompletionRequest(prompt="hi", max_tokens=16, model="test-model")
    )
    async for event in stream:
        events.append(event)
    return events


ROLE_ONLY = (0.0, sse({"choices": [{"delta": {"role": "assistant"}}]}))
REASONING = (0.0, sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}))
USAGE_ONLY = (0.0, sse({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 7}}))
DONE = (0.0, "data: [DONE]\n\n")


class TestSSEParsing:
    async def test_role_only_not_token_but_reasoning_is(self):
        server = SSEServer(
            script=[
                ROLE_ONLY,  # role-only chunk must NOT count as first token
                (0.03, sse({"choices": [{"delta": {"content": "Hello"}}]})),
                REASONING,  # thinking counts as a token arrival (docs 1.4)
                (0.0, sse({"choices": [{"delta": {"content": " world"}}]})),
                USAGE_ONLY,
                DONE,
            ]
        )
        await server.start()
        try:
            provider = make_provider(server)
            events = await collect(provider)
            await provider.aclose()
        finally:
            await server.stop()

        assert events[0].kind is StreamKind.CONNECTED
        tokens = [e for e in events if e.kind is StreamKind.TOKEN]
        # reasoning chunk IS a token (text empty); role-only is not
        assert [e.text for e in tokens] == ["Hello", "", " world"]
        done = events[-1]
        assert done.kind is StreamKind.DONE
        assert done.completion_tokens == 7
        assert done.prompt_tokens == 3

    async def test_reasoning_only_stream_counts_tokens(self):
        # thinking model burning all max_tokens on reasoning: no content, but
        # token arrivals must still be recorded (no sse_protocol error)
        server = SSEServer(
            script=[
                (0.03, sse({"choices": [{"delta": {"reasoning": "Okay"}}]})),
                (0.02, sse({"choices": [{"delta": {"reasoning": " then"}}]})),
                (0.0, sse({"choices": [], "usage": {"prompt_tokens": 3, "completion_tokens": 2}})),
                DONE,
            ]
        )
        await server.start()
        try:
            provider = make_provider(server)
            events = await collect(provider)
            await provider.aclose()
        finally:
            await server.stop()
        tokens = [e for e in events if e.kind is StreamKind.TOKEN]
        assert len(tokens) == 2
        done = events[-1]
        assert done.completion_tokens == 2

    async def test_token_timestamps_track_scripted_delays(self):
        server = SSEServer(
            script=[
                ROLE_ONLY,
                (0.05, sse({"choices": [{"delta": {"content": "a"}}]})),
                (0.05, sse({"choices": [{"delta": {"content": "b"}}]})),
                DONE,
            ]
        )
        await server.start()
        try:
            provider = make_provider(server)
            events = await collect(provider)
            await provider.aclose()
        finally:
            await server.stop()

        tokens = [e for e in events if e.kind is StreamKind.TOKEN]
        assert len(tokens) == 2
        assert tokens[1].ts - tokens[0].ts >= 0.04  # ~50ms scripted gap
        assert tokens[0].ts <= tokens[1].ts

    async def test_content_without_usage_falls_back_to_chunk_count(self):
        server = SSEServer(
            script=[
                (0.0, sse({"choices": [{"delta": {"content": "x"}}]})),
                (0.0, sse({"choices": [{"delta": {"content": "y"}}]})),
                DONE,
            ]
        )
        await server.start()
        try:
            provider = make_provider(server)
            events = await collect(provider)
            await provider.aclose()
        finally:
            await server.stop()
        done = events[-1]
        assert done.completion_tokens is None  # engine applies the fallback


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("status", "expected_kind"),
        [(401, ErrorKind.HTTP_CLIENT), (500, ErrorKind.HTTP_SERVER)],
    )
    async def test_http_status_mapping(self, status: int, expected_kind: ErrorKind):
        server = SSEServer(script=[], status=status)
        await server.start()
        try:
            provider = make_provider(server)
            with pytest.raises(ProviderError) as exc_info:
                await collect(provider)
            assert exc_info.value.kind is expected_kind
            await provider.aclose()
        finally:
            await server.stop()

    async def test_malformed_sse_json_is_protocol_error(self):
        server = SSEServer(script=[(0.0, "data: {not valid json\n\n")])
        await server.start()
        try:
            provider = make_provider(server)
            with pytest.raises(ProviderError) as exc_info:
                await collect(provider)
            assert exc_info.value.kind is ErrorKind.SSE_PROTOCOL
            await provider.aclose()
        finally:
            await server.stop()

    async def test_connection_refused_is_connection_error(self):
        provider = OpenAICompatProvider(EndpointConfig(base_url="http://127.0.0.1:1/v1", model="m"))
        with pytest.raises(ProviderError) as exc_info:
            await collect(provider)
        assert exc_info.value.kind is ErrorKind.CONNECTION
        await provider.aclose()


# keep flake happy: AsyncIterator imported for type annotations above
_ = AsyncIterator
