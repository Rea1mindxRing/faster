"""Raw per-request event records - the single source of truth for all metrics.

Normative semantics are defined in docs/metrics.md. Every timestamp is a
``time.perf_counter()`` reading (monotonic clock) taken on the client side.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ErrorKind(StrEnum):
    """Error taxonomy for failed requests (see docs/metrics.md section 2.5)."""

    TIMEOUT = "timeout"
    HTTP_CLIENT = "http_4xx"
    HTTP_SERVER = "http_5xx"
    CONNECTION = "connection"
    SSE_PROTOCOL = "sse_protocol"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One request's raw measurement. The only input to metric computation.

    Invariant: a record is either successful (``error_kind is None`` and at
    least one token timestamp present) or failed (``error_kind is not None``).
    Failed records never enter latency/throughput distributions; they only
    contribute to error accounting.
    """

    request_id: int
    t_start: float
    t_end: float = 0.0
    t_first_token: float | None = None
    token_times: tuple[float, ...] = ()
    prompt_tokens: int | None = None
    output_tokens: int = 0
    output_tokens_client: int = 0
    error_kind: ErrorKind | None = None
    error: str | None = None
    warmup: bool = False

    @property
    def ok(self) -> bool:
        return self.error_kind is None


@dataclass(slots=True)
class RequestRecordBuilder:
    """Mutable accumulator used by the provider/engine while streaming.

    Built incrementally per request (cheap appends on the hot path), then
    frozen into an immutable :class:`RequestRecord` exactly once.
    """

    request_id: int
    t_start: float
    t_first_token: float | None = None
    token_times: list[float] = field(default_factory=list)
    prompt_tokens: int | None = None
    output_tokens: int = 0
    output_tokens_client: int = 0
    error_kind: ErrorKind | None = None
    error: str | None = None
    warmup: bool = False

    def add_token(self, ts: float) -> None:
        if self.t_first_token is None:
            self.t_first_token = ts
        self.token_times.append(ts)
        self.output_tokens_client += 1

    def finish(self, t_end: float) -> RequestRecord:
        return RequestRecord(
            request_id=self.request_id,
            t_start=self.t_start,
            t_end=t_end,
            t_first_token=self.t_first_token,
            token_times=tuple(self.token_times),
            prompt_tokens=self.prompt_tokens,
            output_tokens=self.output_tokens,
            output_tokens_client=self.output_tokens_client,
            error_kind=self.error_kind,
            error=self.error,
            warmup=self.warmup,
        )
