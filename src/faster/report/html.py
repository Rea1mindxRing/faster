"""Single-file HTML report with inline SVG charts - zero external dependencies.

Charts are hand-rolled SVG (histograms + a sweep line chart), so the report
is fully offline and under ~50 KB regardless of sample count.
"""

from __future__ import annotations

import html
import math
from pathlib import Path

from faster.config import EndpointConfig, WorkloadConfig
from faster.engine.runner import RunResult
from faster.metrics import MetricStats
from faster.metrics.stats import DEFAULT_PERCENTILES

_STAT_FIELDS = ("mean", "median", "std", "min", "max")


def _histogram_svg(
    values: list[float],
    *,
    width: int = 640,
    height: int = 180,
    bins: int = 30,
    color: str = "#4f8cff",
    unit: str = "ms",
) -> str:
    vals = sorted(v for v in values if not math.isnan(v))
    if not vals:
        return '<div class="empty">no data</div>'
    lo, hi = vals[0], vals[-1]
    span = (hi - lo) or 1.0
    counts = [0] * bins
    for v in vals:
        idx = min(bins - 1, int((v - lo) / span * bins))
        counts[idx] += 1
    peak = max(counts) or 1
    bar_w = width / bins
    bars = []
    for i, c in enumerate(counts):
        h = (c / peak) * (height - 30)
        x = i * bar_w
        if c:
            bars.append(
                f'<rect x="{x:.1f}" y="{height - 24 - h:.1f}" width="{bar_w - 1:.1f}" '
                f'height="{h:.1f}" fill="{color}"><title>{c} samples</title></rect>'
            )
    p99 = vals[int(len(vals) * 0.99) - 1] if len(vals) > 1 else vals[0]
    p99_x = min(width - 2, max(0.0, (p99 - lo) / span * width))
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img">'
        + "".join(bars)
        + f'<line x1="{p99_x:.1f}" y1="0" x2="{p99_x:.1f}" y2="{height - 24}" '
        'stroke="#ff6b6b" stroke-dasharray="4 3"/>'
        f'<text x="{p99_x + 4:.1f}" y="12" font-size="11" fill="#ff6b6b">p99 '
        f"{p99:.1f}{unit}</text>"
        f'<text x="2" y="{height - 6}" font-size="11" fill="#888">{lo:.1f}{unit}</text>'
        f'<text x="{width - 60}" y="{height - 6}" font-size="11" fill="#888">'
        f"{hi:.1f}{unit}</text></svg>"
    )


def _line_chart_svg(
    points: list[tuple[float, float]],
    *,
    width: int = 640,
    height: int = 200,
) -> str:
    if len(points) < 2:
        return '<div class="empty">not enough data</div>'
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    xspan, yspan = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    pad = 30
    coords = [
        (
            pad + (x - x0) / xspan * (width - 2 * pad),
            height - pad - (y - y0) / yspan * (height - 2 * pad),
        )
        for x, y in points
    ]
    path = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    dots = "".join(
        f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#4f8cff"/>'
        f'<text x="{x:.1f}" y="{y - 8:.1f}" font-size="10" fill="#888" '
        f'text-anchor="middle">{p[1]:.0f}</text>'
        for (x, y), p in zip(coords, points, strict=True)
    )
    xlabels = "".join(
        f'<text x="{pad + (p[0] - x0) / xspan * (width - 2 * pad):.1f}" '
        f'y="{height - 8}" font-size="11" fill="#888" text-anchor="middle">'
        f"c={p[0]:g}</text>"
        for p in points
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img"><path d="{path}" '
        f'fill="none" stroke="#4f8cff" stroke-width="2"/>' + dots + xlabels + "</svg>"
    )


def _cards(result: RunResult) -> str:
    s = result.summary
    rows = [
        ("Requests", f"{s.num_success} ok / {s.num_failed} failed"),
        ("Output throughput", f"{_n(s.output_throughput)} tok/s"),
        ("Steady-state throughput", f"{_n(s.steady_output_throughput)} tok/s"),
        ("Request throughput", f"{_n(s.request_throughput)} req/s"),
        ("Total tokens (in/out)", f"{s.total_input_tokens} / {s.total_output_tokens}"),
        ("Chunk coalescing ratio", _n(s.chunk_coalescing_ratio)),
        ("Goodput (SLO)", "-" if s.goodput is None else f"{_n(s.goodput * 100)}%"),
    ]
    trs = "".join(f"<tr><td>{html.escape(k)}</td><td><b>{v}</b></td></tr>" for k, v in rows)
    return f"<table>{trs}</table>"


def _n(value: float, decimals: int = 1) -> str:
    return "n/a" if value != value else f"{value:.{decimals}f}"


def render_html(
    result: RunResult,
    endpoint: EndpointConfig,
    workload: WorkloadConfig,
) -> str:
    s = result.summary
    ok_records = [r for r in result.records if r.ok]
    ttft_vals = [r.t_first_token - r.t_start for r in ok_records if r.t_first_token is not None]
    e2e_vals = [r.t_end - r.t_start for r in ok_records if r.t_end > 0]

    def metric_row(name: str, stats: MetricStats) -> str:
        return (
            f"<tr><td>{name}</td>"
            + "".join(f"<td>{_n(getattr(stats, k, math.nan))}</td>" for k in _STAT_FIELDS)
            + "".join(
                f"<td>{_n(stats.percentiles.get(f'p{p:g}', math.nan))}</td>"
                for p in DEFAULT_PERCENTILES
            )
            + "</tr>"
        )

    header = (
        "<tr><th>metric (ms)</th><th>mean</th><th>median</th><th>std</th>"
        "<th>min</th><th>max</th><th>p50</th><th>p90</th><th>p95</th><th>p99</th></tr>"
    )

    body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>faster report - {html.escape(endpoint.model)}</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:760px;color:#1c1c1c}}
 h1{{font-size:1.4rem}}
 h2{{font-size:1.05rem;margin-top:2rem;border-bottom:1px solid #eee;padding-bottom:4px}}
 table{{border-collapse:collapse;font-size:.88rem;margin:.6rem 0}}
 td,th{{border:1px solid #e3e3e3;padding:4px 10px;text-align:right}}
 td:first-child,th:first-child{{text-align:left}}
 svg{{width:100%;height:auto;background:#fafafa;border:1px solid #eee;border-radius:6px}}
 .meta{{color:#666;font-size:.85rem}} .empty{{color:#999;padding:1rem}}
</style></head><body>
<h1>faster benchmark report</h1>
<p class="meta">{html.escape(endpoint.base_url)} &middot; model <b>{html.escape(endpoint.model)}</b>
 &middot; concurrency {workload.concurrency} &middot; max_tokens {workload.max_tokens}
 &middot; wall {_n(result.wall_duration_s, 2)}s</p>
{_cards(result)}
<h2>Latency metrics</h2>
<table>{header}
{metric_row("TTFT", s.ttft)}
{metric_row("TPOT", s.tpot)}
{metric_row("ITL", s.itl)}
{metric_row("E2E", s.e2e)}
</table>
<h2>TTFT distribution</h2>{_histogram_svg(ttft_vals)}
<h2>E2E distribution</h2>{_histogram_svg(e2e_vals, color="#7c5cff")}
<p class="meta">Metric definitions: docs/metrics.md &middot; generated by faster</p>
</body></html>"""
    return body


def write_html(
    result: RunResult,
    endpoint: EndpointConfig,
    workload: WorkloadConfig,
    path: str | Path,
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(result, endpoint, workload), encoding="utf-8")
    return out
