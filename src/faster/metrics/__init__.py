"""Metric computation - pure functions over raw RequestRecords.

Normative formulas live in docs/metrics.md; this module is their executable
form. Nothing here performs I/O or touches wall clocks, so every aggregate is
deterministic and unit-testable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from faster.events import ErrorKind, RequestRecord
from faster.metrics import stats as st

__all__ = [
    "BenchSummary",
    "MetricStats",
    "SloSpec",
    "compute_summary",
    "e2e",
    "itls",
    "tpot",
    "ttft",
]


def ttft(record: RequestRecord) -> float:
    """Time to first *content* token (seconds). NaN if the request never got one."""
    if record.t_first_token is None:
        return st.NAN
    return record.t_first_token - record.t_start


def e2e(record: RequestRecord) -> float:
    """End-to-end latency (seconds): stream close minus request send."""
    if record.t_end <= 0:
        return st.NAN
    return record.t_end - record.t_start


def tpot(record: RequestRecord) -> float:
    """Time per output token (seconds): decode-phase average, denominator excludes
    the first token (already counted in TTFT). NaN when there is no decode phase."""
    first = record.t_first_token
    if first is None or record.output_tokens <= 1 or record.t_end <= 0:
        return st.NAN
    return (record.t_end - first) / (record.output_tokens - 1)


def itls(record: RequestRecord) -> list[float]:
    """Inter-token latencies (seconds): adjacent diffs of content-chunk timestamps."""
    times = record.token_times
    return [times[i] - times[i - 1] for i in range(1, len(times))]


@dataclass(frozen=True, slots=True)
class SloSpec:
    """SLO constraints for goodput. Fields are None = not constrained.

    Units: milliseconds for latency fields.
    """

    ttft_ms: float | None = None
    tpot_ms: float | None = None
    e2e_ms: float | None = None

    def meets(self, record: RequestRecord) -> bool:
        if self.ttft_ms is not None:
            v = ttft(record)
            if math.isnan(v) or v * 1000.0 > self.ttft_ms:
                return False
        if self.tpot_ms is not None:
            v = tpot(record)
            if math.isnan(v) or v * 1000.0 > self.tpot_ms:
                return False
        if self.e2e_ms is not None:
            v = e2e(record)
            if math.isnan(v) or v * 1000.0 > self.e2e_ms:
                return False
        return True


@dataclass(frozen=True, slots=True)
class MetricStats:
    """Aggregate statistics for one latency metric (values in milliseconds)."""

    mean: float
    median: float
    std: float
    min: float
    max: float
    percentiles: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict[str, float]:
        return {
            "mean": self.mean,
            "median": self.median,
            "std": self.std,
            "min": self.min,
            "max": self.max,
            **self.percentiles,
        }

    @classmethod
    def from_values(
        cls,
        values: list[float],
        percentiles: tuple[float, ...] = st.DEFAULT_PERCENTILES,
    ) -> MetricStats:
        d = st.describe(values, percentiles=percentiles, scale=1000.0)
        return cls(
            mean=d["mean"],
            median=d["median"],
            std=d["std"],
            min=d["min"],
            max=d["max"],
            percentiles={k: v for k, v in d.items() if k.startswith("p")},
        )


@dataclass(frozen=True, slots=True)
class BenchSummary:
    """Aggregate result of one benchmark run. See docs/metrics.md for formulas."""

    num_requests: int
    num_success: int
    num_failed: int
    success_rate: float
    error_counts: dict[str, int]
    ttft: MetricStats
    tpot: MetricStats
    itl: MetricStats
    e2e: MetricStats
    wall_duration_s: float
    request_throughput: float
    output_throughput: float
    total_throughput: float
    steady_output_throughput: float
    steady_window_s: float
    goodput: float | None
    chunk_coalescing_ratio: float
    total_input_tokens: int
    total_output_tokens: int

    def as_dict(self) -> dict[str, object]:
        return {
            "num_requests": self.num_requests,
            "num_success": self.num_success,
            "num_failed": self.num_failed,
            "success_rate": self.success_rate,
            "error_counts": self.error_counts,
            "ttft_ms": self.ttft.as_dict(),
            "tpot_ms": self.tpot.as_dict(),
            "itl_ms": self.itl.as_dict(),
            "e2e_ms": self.e2e.as_dict(),
            "wall_duration_s": self.wall_duration_s,
            "request_throughput": self.request_throughput,
            "output_throughput": self.output_throughput,
            "total_throughput": self.total_throughput,
            "steady_output_throughput": self.steady_output_throughput,
            "steady_window_s": self.steady_window_s,
            "goodput": self.goodput,
            "chunk_coalescing_ratio": self.chunk_coalescing_ratio,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
        }


def _steady_state(
    records: list[RequestRecord],
    concurrency: int,
    t_wall_start: float,
    t_wall_end: float,
) -> tuple[float, float]:
    """Steady-state window throughput (docs/metrics.md 2.4).

    Buckets wall time into 1 s intervals; picks the longest contiguous run of
    buckets whose average in-flight request count reaches ceil(0.9 * concurrency);
    falls back to the whole run when no bucket qualifies.
    Returns (throughput_tokens_per_s, window_seconds).
    """
    if t_wall_end <= t_wall_start:
        return st.NAN, 0.0

    threshold = max(1, math.ceil(0.9 * concurrency)) if concurrency > 0 else 1
    n_buckets = max(math.ceil(t_wall_end - t_wall_start), 1)
    active = [0] * n_buckets
    tokens = [0] * n_buckets

    for rec in records:
        if rec.t_end <= 0:
            continue
        i0 = max(0, int(rec.t_start - t_wall_start))
        i1 = min(n_buckets - 1, max(0, math.ceil(rec.t_end - t_wall_start) - 1))
        for i in range(i0, i1 + 1):
            active[i] += 1
        for ts in rec.token_times:
            i = int(ts - t_wall_start)
            if 0 <= i < n_buckets:
                tokens[i] += 1

    best_len, best_start = 0, 0
    cur_len, cur_start = 0, 0
    for i, a in enumerate(active):
        if a >= threshold:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len = 0

    if best_len == 0:
        window = t_wall_end - t_wall_start
        return math.fsum(tokens) / window, window
    win_tokens = math.fsum(tokens[best_start : best_start + best_len])
    return win_tokens / best_len, float(best_len)


def compute_summary(
    records: list[RequestRecord],
    concurrency: int,
    slo: SloSpec | None = None,
    percentiles: tuple[float, ...] = st.DEFAULT_PERCENTILES,
) -> BenchSummary:
    """Aggregate raw records into a BenchSummary (pure function, no I/O).

    ``records`` must exclude warmup requests. Wall-clock duration is derived
    from the records themselves (first send -> last completion), matching
    docs/metrics.md 2.4.
    """
    done = [r for r in records if not r.warmup]
    ok = [r for r in done if r.ok]
    failed = [r for r in done if not r.ok]

    error_counts = {kind.value: 0 for kind in ErrorKind}
    for r in failed:
        error_counts[r.error_kind.value if r.error_kind else ErrorKind.OTHER.value] += 1

    ttft_vals = [ttft(r) for r in ok]
    tpot_vals = [tpot(r) for r in ok]
    tpot_vals = [v for v in tpot_vals if not math.isnan(v)]
    e2e_vals = [e2e(r) for r in ok]
    itl_vals: list[float] = []
    for r in ok:
        itl_vals.extend(itls(r))

    wall_start = min((r.t_start for r in done), default=0.0)
    wall_end = max((r.t_end for r in done), default=wall_start)
    dur = max(wall_end - wall_start, 0.0)

    total_input = sum(r.prompt_tokens or 0 for r in ok)
    total_output = sum(r.output_tokens for r in ok)

    steady_tp, steady_win = _steady_state(ok, concurrency, wall_start, wall_end)

    goodput: float | None = None
    if slo is not None and ok:
        goodput = sum(1 for r in ok if slo.meets(r)) / len(ok)

    ratios = [r.output_tokens_client / r.output_tokens for r in ok if r.output_tokens > 0]

    return BenchSummary(
        num_requests=len(done),
        num_success=len(ok),
        num_failed=len(failed),
        success_rate=(len(ok) / len(done)) if done else st.NAN,
        error_counts=error_counts,
        ttft=MetricStats.from_values(ttft_vals, percentiles),
        tpot=MetricStats.from_values(tpot_vals, percentiles),
        itl=MetricStats.from_values(itl_vals, percentiles),
        e2e=MetricStats.from_values(e2e_vals, percentiles),
        wall_duration_s=dur,
        request_throughput=(len(ok) / dur) if dur > 0 else st.NAN,
        output_throughput=(total_output / dur) if dur > 0 else st.NAN,
        total_throughput=((total_input + total_output) / dur) if dur > 0 else st.NAN,
        steady_output_throughput=steady_tp,
        steady_window_s=steady_win,
        goodput=goodput,
        chunk_coalescing_ratio=st.mean(ratios) if ratios else st.NAN,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
    )
