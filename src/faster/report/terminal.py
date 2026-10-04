"""Rich terminal summary (requires the ``cli`` extra).

Renders directly to the live console so the table adapts to the actual
terminal width (no double-wrapping). :func:`format_summary` exists for
tests/logs and renders into a fixed-width capture.
"""

from __future__ import annotations

import io
from collections import Counter

from rich.console import Console
from rich.table import Table

from faster.engine.runner import RunResult


def _fmt(value: float, unit: str = "ms", decimals: int | None = None) -> str:
    if value != value:  # NaN
        return "n/a"
    if decimals is None:
        # adaptive precision: small rates need more decimals to stay readable
        decimals = 1 if abs(value) >= 10 else 2
    return f"{value:.{decimals}f}{unit}"


def _summary_renderables(result: RunResult, title: str) -> list[object]:
    s = result.summary

    table = Table(title=title, show_lines=False)
    table.add_column("metric", style="cyan")
    for col in ("mean", "median", "p90", "p95", "p99"):
        table.add_column(col, justify="right")

    for name, stats in (
        ("TTFT", s.ttft),
        ("TPOT", s.tpot),
        ("ITL", s.itl),
        ("E2E", s.e2e),
    ):
        p = stats.percentiles
        table.add_row(
            f"{name} (ms)",
            _fmt(stats.mean),
            _fmt(stats.median),
            _fmt(p.get("p90", float("nan"))),
            _fmt(p.get("p95", float("nan"))),
            _fmt(p.get("p99", float("nan"))),
        )

    lines: list[str] = [
        f"requests: {s.num_success} ok / {s.num_failed} failed"
        f"  (success rate {_fmt(s.success_rate * 100, '%')})",
        f"throughput: {_fmt(s.output_throughput, ' tok/s')} out"
        f" | {_fmt(s.total_throughput, ' tok/s')} total"
        f" | {_fmt(s.request_throughput, ' req/s')}",
        f"steady-state output throughput: {_fmt(s.steady_output_throughput, ' tok/s')}"
        f" over {s.steady_window_s:.1f}s window",
    ]
    if s.goodput is not None:
        lines.append(f"goodput (SLO): {_fmt(s.goodput * 100, '%')}")
    errors = {k: v for k, v in s.error_counts.items() if v}
    if errors:
        lines.append(f"errors: {errors}")
        msgs = Counter(r.error for r in result.records if not r.ok and r.error).most_common(3)
        lines.extend(f"  x{count}: {msg[:120]}" for msg, count in msgs)

    return [table, *lines]


def print_summary(result: RunResult, title: str) -> None:
    """Print the summary to the live terminal (auto-fits its width)."""
    console = Console()
    for renderable in _summary_renderables(result, title):
        console.print(renderable)


def format_summary(result: RunResult, title: str = "faster benchmark") -> str:
    """Render the summary as a plain string (for tests and logs)."""
    buffer = io.StringIO()
    console = Console(file=buffer, width=110, force_terminal=False, no_color=True)
    for renderable in _summary_renderables(result, title):
        console.print(renderable)
    return buffer.getvalue()
