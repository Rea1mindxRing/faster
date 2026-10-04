"""Load engine tests with an in-process mock provider.

Verifies concurrency capping, count/duration modes, warmup semantics,
failure isolation, engine-level timeout, and Poisson pacing.
"""

from __future__ import annotations

import asyncio
import random
import time

import pytest

from faster.config import EndpointConfig, WorkloadConfig
from faster.engine.runner import BenchRunner
from faster.events import ErrorKind
from faster.provider.base import ProviderError, StreamEvent, StreamKind


class MockProvider:
    """Scripted streaming provider; tracks in-flight concurrency.

    ``script`` is a list of behaviors cycled per request:
    "ok" | "fail" (connection error) | "hang" (stalls, triggering timeout).
    """

    def __init__(
        self,
        script: list[str] | None = None,
        *,
        n_tokens: int = 3,
        token_delay: float = 0.01,
        first_delay: float = 0.005,
        hang_s: float = 0.5,
    ) -> None:
        self.script = script or []
        self.n_tokens = n_tokens
        self.token_delay = token_delay
        self.first_delay = first_delay
        self.hang_s = hang_s
        self.inflight = 0
        self.max_inflight = 0
        self.calls = 0

    async def stream_complete(self, request):  # type: ignore[no-untyped-def]
        behavior = self.script[self.calls % len(self.script)] if self.script else "ok"
        self.calls += 1
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            await asyncio.sleep(self.first_delay)
            yield StreamEvent(kind=StreamKind.CONNECTED, ts=time.perf_counter())
            if behavior == "fail":
                raise ProviderError(ErrorKind.CONNECTION, "mock failure")
            for _ in range(self.n_tokens):
                await asyncio.sleep(self.token_delay)
                yield StreamEvent(kind=StreamKind.TOKEN, ts=time.perf_counter(), text="x")
            if behavior == "hang":
                await asyncio.sleep(self.hang_s)
            yield StreamEvent(
                kind=StreamKind.DONE,
                ts=time.perf_counter(),
                prompt_tokens=10,
                completion_tokens=self.n_tokens,
            )
        finally:
            self.inflight -= 1

    async def aclose(self) -> None:
        pass


def endpoint(timeout_s: float = 30.0) -> EndpointConfig:
    return EndpointConfig(base_url="http://mock/v1", model="mock-model", timeout_s=timeout_s)


class TestCountMode:
    async def test_exactly_n_requests_and_concurrency_cap(self):
        provider = MockProvider(n_tokens=3, token_delay=0.01)
        progress: list[tuple[int, int | None]] = []
        runner = BenchRunner(
            endpoint=endpoint(),
            workload=WorkloadConfig(num_requests=12, concurrency=3, warmup_requests=0),
            provider=provider,
            on_progress=lambda done, total: progress.append((done, total)),
        )
        result = await runner.run()

        assert len(result.records) == 12
        assert all(r.ok for r in result.records)
        assert provider.max_inflight <= 3
        # Server usage counted: each request has 3 output tokens
        assert result.summary.num_success == 12
        assert result.summary.total_output_tokens == 36
        assert progress[-1] == (12, 12)

    async def test_failure_isolation(self):
        # every 3rd request fails; run must complete with 8/12 success
        provider = MockProvider(script=["ok", "ok", "fail"], n_tokens=2, token_delay=0.005)
        runner = BenchRunner(
            endpoint=endpoint(),
            workload=WorkloadConfig(num_requests=12, concurrency=4, warmup_requests=0),
            provider=provider,
        )
        result = await runner.run()
        assert result.summary.num_requests == 12
        # multiset of behaviors is deterministic: indices 0..11 each assigned once
        assert result.summary.num_success == 8
        assert result.summary.error_counts["connection"] == 4
        assert result.summary.success_rate == pytest.approx(2 / 3)
        # failed records excluded from latency stats
        failed = [r for r in result.records if not r.ok]
        assert len(failed) == 4
        assert all(r.error_kind is ErrorKind.CONNECTION for r in failed)


class TestDurationMode:
    async def test_runs_until_deadline(self):
        provider = MockProvider(n_tokens=2, token_delay=0.02)
        runner = BenchRunner(
            endpoint=endpoint(),
            workload=WorkloadConfig(duration_s=0.3, concurrency=2, warmup_requests=0),
            provider=provider,
        )
        result = await runner.run()
        assert result.summary.num_requests >= 2
        assert result.wall_duration_s >= 0.3
        # no record may complete after the wall end
        assert all(r.t_end <= result.wall_end + 1e-6 for r in result.records)


class TestWarmup:
    async def test_warmup_excluded_from_records(self):
        provider = MockProvider(n_tokens=2, token_delay=0.005)
        runner = BenchRunner(
            endpoint=endpoint(),
            workload=WorkloadConfig(num_requests=5, concurrency=2, warmup_requests=2),
            provider=provider,
        )
        result = await runner.run()
        assert provider.calls == 7  # 2 warmup + 5 measured
        assert len(result.records) == 5
        assert all(not r.warmup for r in result.records)

    async def test_all_warmup_failed_aborts(self):
        provider = MockProvider(script=["fail"], n_tokens=1)
        runner = BenchRunner(
            endpoint=endpoint(),
            workload=WorkloadConfig(num_requests=5, concurrency=2, warmup_requests=2),
            provider=provider,
        )
        with pytest.raises(RuntimeError, match="warmup"):
            await runner.run()


class TestTimeoutAndRate:
    async def test_hanging_request_is_isolated_as_timeout(self):
        # first request hangs, the rest succeed
        provider = MockProvider(
            script=["hang", "ok", "ok"], n_tokens=2, token_delay=0.005, hang_s=5.0
        )
        runner = BenchRunner(
            endpoint=endpoint(timeout_s=0.2),
            workload=WorkloadConfig(num_requests=3, concurrency=1, warmup_requests=0),
            provider=provider,
        )
        result = await runner.run()
        kinds = [r.error_kind for r in result.records]
        assert kinds == [ErrorKind.TIMEOUT, None, None]
        assert result.summary.num_success == 2

    async def test_request_rate_paces_arrivals(self):
        # Seeded rng -> deterministic Poisson intervals -> non-flaky assertion
        seed_rng = random.Random(1234)
        expected_pace = sum(seed_rng.expovariate(50.0) for _ in range(5))

        provider = MockProvider(n_tokens=1, token_delay=0.0)
        runner = BenchRunner(
            endpoint=endpoint(),
            workload=WorkloadConfig(
                num_requests=6, concurrency=6, warmup_requests=0, request_rate=50.0
            ),
            provider=provider,
            rng=random.Random(1234),
        )
        result = await runner.run()
        # arrivals are paced by the scripted intervals (minus scheduling slack)
        assert result.wall_duration_s >= expected_pace * 0.9
        assert len(result.records) == 6
