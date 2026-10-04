"""Provider protocol: decouples the load engine from transport details.

A Provider turns a :class:`CompletionRequest` into a stream of timestamped
:class:`StreamEvent` items. All metric computation happens downstream from
these events, so adding a new protocol (e.g. Anthropic native) never touches
engine or metrics code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from faster.events import ErrorKind


class ProviderError(Exception):
    """Transport/protocol failure carrying the ErrorKind taxonomy."""

    def __init__(self, kind: ErrorKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class StreamKind(StrEnum):
    CONNECTED = "connected"  # response headers received
    TOKEN = "token"  # a content chunk arrived (text may hold >1 token)
    DONE = "done"  # stream closed normally
    ERROR = "error"  # stream aborted; ts + text describe the failure


@dataclass(frozen=True, slots=True)
class StreamEvent:
    kind: StreamKind
    ts: float  # perf_counter() at event observation
    text: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    prompt: str
    max_tokens: int
    model: str
    temperature: float | None = None
    extra_body: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    """Transport adapter for one endpoint. Implementations must be safe for
    concurrent use from multiple asyncio tasks sharing one instance."""

    def stream_complete(self, request: CompletionRequest) -> AsyncIterator[StreamEvent]:
        """Yield StreamEvents: one CONNECTED, one or more TOKEN, then DONE
        (or raise ProviderError). TOKEN events are emitted only for chunks
        carrying content; role-only and usage-only chunks are not TOKENs.
        Implementations are async generators (``async def ... yield``)."""
        ...

    async def aclose(self) -> None:
        """Release underlying connections."""
        ...
