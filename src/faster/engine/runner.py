"""Asyncio load engine.

Concurrency semantics (normative in docs/metrics.md section 3):
- concurrency = max in-flight requests, implemented as a fixed worker pool
- count mode: exactly ``num_requests`` requests are sent
- duration mode: requests are sent until ``duration_s`` elapses
- ``request_rate`` paces *arrivals* (Poisson, exponential inter-arrival);
  it composes with the concurrency cap, which limits *in-flight* work
- warmup requests are excluded from metrics; if ALL warmups fail, the run
  aborts immediately (endpoint misconfigured)
- per-request failures/timeouts are isolated: one bad request never aborts
  the run; engine-level timeout is a backstop above the provider's own
"""

from __future__ import annotations

import asyncio
import itertools
import math
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from faster.config import EndpointConfig, WorkloadConfig
from faster.engine.sampler import ResourceSample, ResourceSampler
from faster.events import ErrorKind, RequestRecord, RequestRecordBuilder
from faster.metrics import BenchSummary, SloSpec, compute_summary
from faster.provider.base import (
    CompletionRequest,
    Provider,
    ProviderError,
    StreamKind,
)
from faster.provider.openai_compat import OpenAICompatProvider

T = TypeVar("T")

_WARMUP_MAX_TOKENS = 32  # docs/metrics.md 3.3
_SENTINEL = object()


@dataclass(frozen=True, slots=True)
class RunResult:
    records: list[RequestRecord]
    summary: BenchSummary
    wall_start: float
    wall_end: float
    resource_samples: list[ResourceSample] = field(default_factory=list)

    @property
    def wall_duration_s(self) -> float:
        return self.wall_end - self.wall_start


ProgressCallback = Callable[[int, int | None], None]
ProviderFactory = Callable[[EndpointConfig], Provider]


class BenchRunner:
    def __init__(
        self,
        endpoint: EndpointConfig,
        workload: WorkloadConfig,
        *,
        slo: SloSpec | None = None,
        provider: Provider | None = None,
        on_progress: ProgressCallback | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._workload = workload
        self._slo = slo
        self._provider = provider
        self._owns_provider = provider is None
        self._on_progress = on_progress
        self._rng = rng

    async def run(self) -> RunResult:
        try:
            await self._warmup()
            return await self._run_main()
        finally:
            if self._owns_provider and self._provider is not None:
                await self._provider.aclose()

    # ---- warmup -----------------------------------------------------------

    async def _warmup(self) -> None:
        k = self._workload.warmup_requests
        if k == 0 or self._provider is None:
            return
        provider = self._ensure_provider()
        failures: list[str] = []
        for i in range(k):
            record = await self._one_request(
                request_id=-1 - i,
                prompt=self._workload.prompt_at(i),
                max_tokens=min(_WARMUP_MAX_TOKENS, self._workload.max_tokens),
                warmup=True,
                provider=provider,
            )
            if not record.ok:
                failures.append(record.error or "unknown")
        if len(failures) == k:
            raise RuntimeError(
                f"all {k} warmup requests failed; endpoint is misconfigured. "
                f"First error: {failures[0]}"
            )

    # ---- main phase -------------------------------------------------------

    async def _run_main(self) -> RunResult:
        wl = self._workload
        provider = self._ensure_provider()
        sampler = ResourceSampler()
        sampler.start()

        # Closed-loop arrivals: the queue never holds more than `concurrency`
        # pending items, so without a request_rate the client cannot run ahead
        # of the endpoint (open-loop flooding is only possible with an
        # explicit --request-rate, and even then the cap bounds memory).
        queue: asyncio.Queue[int | None] = asyncio.Queue(maxsize=wl.concurrency)
        ids = itertools.count()
        loop = asyncio.get_running_loop()
        deadline: float | None = None if wl.duration_s is None else loop.time() + wl.duration_s
        rate = wl.request_rate
        rng = self._rng or random.Random()

        records: list[RequestRecord] = []
        completed = 0
        produced = 0
        total: int | None = wl.num_requests

        async def producer() -> None:
            nonlocal produced
            while True:
                # Duration mode: stop arrivals at the deadline.
                if deadline is not None and loop.time() >= deadline:
                    return
                # Count mode: stop after exactly num_requests arrivals.
                if total is not None and produced >= total:
                    return
                if rate is not None:
                    await asyncio.sleep(rng.expovariate(rate))
                await queue.put(next(ids))
                produced += 1

        prod_task: asyncio.Task[None] = asyncio.create_task(producer())

        async def feed_sentinels() -> None:
            """Wait for the producer, then reliably release every worker."""
            try:
                await prod_task
            finally:
                for _ in range(wl.concurrency):
                    await queue.put(None)

        async def worker() -> None:
            nonlocal completed
            while True:
                item = await queue.get()
                if item is None:
                    return
                record = await self._one_request(
                    request_id=item,
                    prompt=wl.prompt_at(item),
                    max_tokens=wl.max_tokens,
                    warmup=False,
                    provider=provider,
                )
                records.append(record)
                completed += 1
                if self._on_progress is not None:
                    self._on_progress(completed, total)

        wall_start = time.perf_counter()
        workers = [asyncio.create_task(worker()) for _ in range(wl.concurrency)]
        try:
            async with asyncio.timeout(
                (self._endpoint.timeout_s + 10.0) * (wl.num_requests or math.inf)
            ):
                await asyncio.gather(feed_sentinels(), *workers)
        except BaseException:
            prod_task.cancel()
            for w in workers:
                w.cancel()
            raise
        finally:
            wall_end = time.perf_counter()
            sampler.stop()

        summary = compute_summary(records, wl.concurrency, slo=self._slo)
        return RunResult(
            records=records,
            summary=summary,
            wall_start=wall_start,
            wall_end=wall_end,
            resource_samples=sampler.samples,
        )

    # ---- single request ---------------------------------------------------

    async def _one_request(
        self,
        request_id: int,
        prompt: str,
        max_tokens: int,
        warmup: bool,
        provider: Provider,
    ) -> RequestRecord:
        wl = self._workload
        t_start = time.perf_counter()
        builder = RequestRecordBuilder(request_id=request_id, t_start=t_start, warmup=warmup)
        request = CompletionRequest(
            prompt=prompt,
            max_tokens=max_tokens,
            model=self._endpoint.model,
            temperature=wl.temperature,
            extra_body=self._endpoint.extra_body,
        )
        try:
            # Backstop above the provider's own transport timeout.
            async with asyncio.timeout(self._endpoint.timeout_s + 1.0):
                async for event in provider.stream_complete(request):
                    if event.kind is StreamKind.TOKEN:
                        builder.add_token(event.ts)
                    elif event.kind is StreamKind.DONE:
                        if event.completion_tokens is not None:
                            builder.output_tokens = event.completion_tokens
                        if event.prompt_tokens is not None:
                            builder.prompt_tokens = event.prompt_tokens
            if builder.output_tokens == 0 and builder.output_tokens_client > 0:
                # Server sent no usage: fall back to client chunk count
                builder.output_tokens = builder.output_tokens_client
            record = builder.finish(time.perf_counter())
            if not warmup and record.t_first_token is None and record.ok:
                record = _rebuild_error(
                    record,
                    ErrorKind.SSE_PROTOCOL,
                    "stream ended without any output token (all chunks empty or role-only)",
                )
            return record
        except TimeoutError:
            return _fail(builder, ErrorKind.TIMEOUT, "engine timeout")
        except ProviderError as exc:
            return _fail(builder, exc.kind, exc.message)
        except Exception as exc:
            return _fail(builder, ErrorKind.OTHER, f"{type(exc).__name__}: {exc}")

    def _ensure_provider(self) -> Provider:
        provider = self._provider
        if provider is None:
            provider = OpenAICompatProvider(self._endpoint)
            self._provider = provider
        return provider


def _fail(builder: RequestRecordBuilder, kind: ErrorKind, message: str) -> RequestRecord:
    builder.error_kind = kind
    builder.error = message
    return builder.finish(time.perf_counter())


def _rebuild_error(record: RequestRecord, kind: ErrorKind, message: str) -> RequestRecord:
    return RequestRecord(
        request_id=record.request_id,
        t_start=record.t_start,
        t_end=record.t_end,
        t_first_token=record.t_first_token,
        token_times=record.token_times,
        prompt_tokens=record.prompt_tokens,
        output_tokens=record.output_tokens,
        output_tokens_client=record.output_tokens_client,
        error_kind=kind,
        error=message,
        warmup=record.warmup,
    )
