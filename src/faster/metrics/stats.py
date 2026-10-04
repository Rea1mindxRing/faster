"""Distribution statistics helpers.

Empty inputs yield ``float('nan')`` - never 0 - per docs/metrics.md: a metric
with no data must be visible as "no data", not silently read as "fast".
Percentiles use linear interpolation, matching ``numpy.percentile`` defaults
(and therefore vLLM/SGLang reference numbers).
"""

from __future__ import annotations

import math

DEFAULT_PERCENTILES: tuple[float, ...] = (50.0, 90.0, 95.0, 99.0)

NAN = float("nan")


def mean(values: list[float]) -> float:
    if not values:
        return NAN
    return math.fsum(values) / len(values)


def std(values: list[float]) -> float:
    """Population standard deviation (ddof=0, matching numpy defaults)."""
    if not values:
        return NAN
    if len(values) == 1:
        return 0.0
    m = mean(values)
    return math.sqrt(math.fsum((v - m) ** 2 for v in values) / len(values))


def percentile(values: list[float], p: float) -> float:
    if not values:
        return NAN
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentile must be in [0, 100], got {p}")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def median(values: list[float]) -> float:
    return percentile(values, 50.0)


def describe(
    values: list[float],
    percentiles: tuple[float, ...] = DEFAULT_PERCENTILES,
    scale: float = 1.0,
) -> dict[str, float]:
    """Aggregate summary; ``scale`` converts units (e.g. seconds -> milliseconds)."""
    out: dict[str, float] = {
        "mean": mean(values) * scale,
        "median": median(values) * scale,
        "std": std(values) * scale,
        "min": min(values) * scale if values else NAN,
        "max": max(values) * scale if values else NAN,
    }
    for p in percentiles:
        out[f"p{p:g}"] = percentile(values, p) * scale
    return out
