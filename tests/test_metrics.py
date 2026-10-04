"""Metric formula correctness tests.

Hand-crafted RequestRecords with numbers chosen so every expected value can be
verified by hand against docs/metrics.md.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from faster.events import ErrorKind, RequestRecord
from faster.metrics import SloSpec, compute_summary, e2e, itls, tpot, ttft
from faster.metrics.stats import percentile

_UNSET: Any = object()

# Relative timeline: first content token at +100ms, then +50ms per token, done at +300ms.
_REL_TOKEN_TIMES = (0.1, 0.15, 0.2, 0.25, 0.3)


def make_rec(
    request_id: int = 0,
    t_start: float = 0.0,
    *,
    t_first_token: Any = _UNSET,
    token_times: Any = _UNSET,
    t_end: Any = _UNSET,
    output_tokens: int = 5,
    prompt_tokens: int | None = 10,
    output_tokens_client: int | None = None,
    error_kind: ErrorKind | None = None,
    error: str | None = None,
) -> RequestRecord:
    """Build a record on a relative timeline anchored at t_start (defaults)."""
    if t_first_token is _UNSET:
        t_first_token = t_start + 0.1
    if token_times is _UNSET:
        token_times = tuple(t_start + v for v in _REL_TOKEN_TIMES)
    if t_end is _UNSET:
        t_end = t_start + 0.3
    if output_tokens_client is None:
        output_tokens_client = len(token_times) if token_times else 0
    return RequestRecord(
        request_id=request_id,
        t_start=t_start,
        t_end=t_end,
        t_first_token=t_first_token,
        token_times=token_times,
        prompt_tokens=prompt_tokens,
        output_tokens=output_tokens,
        output_tokens_client=output_tokens_client,
        error_kind=error_kind,
        error=error,
    )


class TestPerRecordMetrics:
    def test_ttft_e2e_tpot_itl_exact(self):
        r = make_rec()
        assert ttft(r) == pytest.approx(0.1)
        assert e2e(r) == pytest.approx(0.3)
        # TPOT = (E2E - TTFT) / (output_tokens - 1) = (0.3 - 0.1) / 4
        assert tpot(r) == pytest.approx(0.05)
        # ITL = adjacent diffs of content chunks, first token excluded
        assert itls(r) == pytest.approx([0.05, 0.05, 0.05, 0.05])

    def test_tpot_single_token_is_nan(self):
        r = make_rec(token_times=(0.1,), t_end=0.1, output_tokens=1)
        assert math.isnan(tpot(r))

    def test_tpot_uses_server_token_count_not_chunk_count(self):
        # Coalesced chunks: 10 tokens delivered in 5 chunks (e.g. speculative decoding)
        r = make_rec(token_times=(0.1, 0.2, 0.3, 0.4, 0.5), t_end=0.5, output_tokens=10)
        assert tpot(r) == pytest.approx((0.5 - 0.1) / 9)
        assert len(itls(r)) == 4  # chunk-granular; coalescing flagged in summary

    def test_failed_record_metrics_are_nan(self):
        r = make_rec(
            t_first_token=None,
            token_times=(),
            t_end=0.0,
            output_tokens=0,
            error_kind=ErrorKind.TIMEOUT,
        )
        assert math.isnan(ttft(r))
        assert math.isnan(e2e(r))
        assert math.isnan(tpot(r))
        assert itls(r) == []


class TestPercentile:
    def test_matches_numpy_linear_interpolation(self):
        vals = [float(i) for i in range(1, 101)]
        # numpy.percentile(vals, 90) with linear interpolation = 90.1
        assert percentile(vals, 90) == pytest.approx(90.1)
        assert percentile(vals, 50) == pytest.approx(50.5)
        assert percentile([7.0], 99) == 7.0

    def test_empty_is_nan_never_zero(self):
        assert math.isnan(percentile([], 99))


class TestSummary:
    def test_success_and_error_accounting(self):
        ok1 = make_rec(request_id=0)
        ok2 = make_rec(request_id=1, t_start=1.0)
        bad = make_rec(
            request_id=2,
            t_start=2.0,
            t_first_token=None,
            token_times=(),
            t_end=0.0,
            output_tokens=0,
            error_kind=ErrorKind.TIMEOUT,
        )
        summary = compute_summary([ok1, ok2, bad], concurrency=2)
        assert summary.num_requests == 3
        assert summary.num_success == 2
        assert summary.num_failed == 1
        assert summary.success_rate == pytest.approx(2 / 3)
        assert summary.error_counts["timeout"] == 1
        # failed record must not pollute latency stats: mean TTFT = 0.1 s = 100 ms
        assert summary.ttft.mean == pytest.approx(100.0)
        assert summary.e2e.mean == pytest.approx(300.0)

    def test_all_failed_yields_nan_not_zero(self):
        bad = make_rec(
            t_first_token=None,
            token_times=(),
            t_end=0.0,
            output_tokens=0,
            error_kind=ErrorKind.CONNECTION,
        )
        s = compute_summary([bad], concurrency=1)
        assert math.isnan(s.ttft.mean)
        assert math.isnan(s.ttft.percentiles["p99"])
        assert math.isnan(s.output_throughput)
        assert s.success_rate == 0.0

    def test_throughput_over_wall_clock(self):
        ok1 = make_rec(request_id=0, output_tokens=5, prompt_tokens=10)
        ok2 = make_rec(request_id=1, t_start=1.0, output_tokens=15, prompt_tokens=10)
        s = compute_summary([ok1, ok2], concurrency=2)
        # wall = 1.3 s (first send -> last completion); output tokens = 20
        assert s.wall_duration_s == pytest.approx(1.3)
        assert s.output_throughput == pytest.approx(20 / 1.3)
        assert s.total_throughput == pytest.approx((20 + 20) / 1.3)
        assert s.request_throughput == pytest.approx(2 / 1.3)

    def test_goodput_slo(self):
        fast = make_rec(request_id=0)  # TTFT = 100 ms
        slow = make_rec(
            request_id=1,
            t_start=10.0,
            t_first_token=10.4,  # TTFT = 400 ms
            token_times=(10.4, 10.45, 10.5),
            t_end=10.5,
            output_tokens=3,
        )
        slo = SloSpec(ttft_ms=150.0)
        s = compute_summary([fast, slow], concurrency=2, slo=slo)
        assert s.goodput == pytest.approx(0.5)
        assert compute_summary([fast, slow], concurrency=2).goodput is None

    def test_steady_state_window(self):
        # Ramp-up request spans buckets 0-1; 4 saturated requests span buckets 2-5.
        ramp = make_rec(
            request_id=0,
            t_start=0.0,
            t_end=2.0,
            t_first_token=0.1,
            token_times=(0.1,),
            output_tokens=1,
        )
        saturated = [
            make_rec(
                request_id=i + 1,
                t_start=2.0,
                t_end=6.0,
                # 4 tokens each, one per second inside the steady window
                token_times=(2.5 + i * 0.01, 3.5 + i * 0.01, 4.5 + i * 0.01, 5.5 + i * 0.01),
                t_first_token=2.5 + i * 0.01,
                output_tokens=4,
            )
            for i in range(4)
        ]
        s = compute_summary([ramp, *saturated], concurrency=4)
        # threshold = ceil(0.9*4) = 4 -> steady window = buckets [2, 6) = 4 s
        assert s.steady_window_s == pytest.approx(4.0)
        assert s.steady_output_throughput == pytest.approx(16 / 4)

    def test_coalescing_ratio(self):
        r = make_rec(output_tokens=10, output_tokens_client=5)
        s = compute_summary([r], concurrency=1)
        assert s.chunk_coalescing_ratio == pytest.approx(0.5)

    def test_warmup_excluded(self):
        warm = make_rec(request_id=99, t_start=-100.0)
        object.__setattr__(warm, "warmup", True)  # frozen dataclass: test setup only
        ok = make_rec(request_id=0)
        s = compute_summary([warm, ok], concurrency=1)
        assert s.num_requests == 1

    def test_summary_as_dict(self):
        s = compute_summary([make_rec()], concurrency=1)
        d = s.as_dict()
        assert isinstance(d, dict)
        assert d["ttft_ms"]["mean"] == pytest.approx(100.0)
        assert d["output_throughput"] == pytest.approx(5 / 0.3)
