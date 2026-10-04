"""faster CLI: `faster bench` and `faster sweep` (requires the ``cli`` extra)."""

from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from typing import Annotated, Any

import typer

from faster.config import EndpointConfig, WorkloadConfig
from faster.engine.runner import RunResult
from faster.metrics import BenchSummary, SloSpec

app = typer.Typer(
    help="Unified-metric LLM inference benchmark (docs/metrics.md defines every formula).",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

PROMPT_HELP = "Prompt text. Repeatable / omit to use a built-in default."


def _collect_prompts(prompt: str | None, prompts_file: Path | None) -> tuple[str, ...]:
    if prompts_file is not None:
        lines = [
            line.strip()
            for line in prompts_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if not lines:
            raise typer.BadParameter(f"{prompts_file} contains no non-empty lines")
        return tuple(lines)
    if prompt:
        return (prompt,)
    return ("Explain how transformer attention works, step by step.",)


def _build_slo(
    ttft_ms: float | None, tpot_ms: float | None, e2e_ms: float | None
) -> SloSpec | None:
    if ttft_ms is None and tpot_ms is None and e2e_ms is None:
        return None
    return SloSpec(ttft_ms=ttft_ms, tpot_ms=tpot_ms, e2e_ms=e2e_ms)


def _print_summary(result: RunResult, title: str) -> None:
    from faster.report.terminal import print_summary

    print_summary(result, title=title)


def _write_outputs(
    result: RunResult,
    endpoint: EndpointConfig,
    workload: WorkloadConfig,
    output_json: Path | None,
    output_html: Path | None,
) -> list[str]:
    written: list[str] = []
    if output_json is not None:
        from faster.report.json_out import export_json

        path = export_json(result, endpoint, workload).write(output_json)
        written.append(str(path))
    if output_html is not None:
        from faster.report.html import write_html

        path = write_html(result, endpoint, workload, output_html)
        written.append(str(path))
    return written


@app.command()
def bench(
    base_url: Annotated[
        str, typer.Option(help="OpenAI-compatible base URL, e.g. http://localhost:11434/v1")
    ],
    model: Annotated[str, typer.Option(help="Model name")],
    api_key: Annotated[
        str | None, typer.Option(envvar="FASTER_API_KEY", help="Bearer token")
    ] = None,
    prompt: Annotated[str | None, typer.Option(help=PROMPT_HELP)] = None,
    prompts_file: Annotated[Path | None, typer.Option(help="File with one prompt per line")] = None,
    max_tokens: Annotated[int, typer.Option(min=1)] = 256,
    num_requests: Annotated[
        int | None, typer.Option("--num-requests", "-n", min=1, help="Count mode")
    ] = None,
    duration_s: Annotated[
        float | None, typer.Option("--duration-s", min=0.001, help="Duration mode")
    ] = None,
    concurrency: Annotated[
        int, typer.Option("-c", "--concurrency", min=1, help="Max in-flight requests")
    ] = 1,
    request_rate: Annotated[
        float | None,
        typer.Option("--request-rate", min=0.000001, help="Poisson arrival rate (req/s)"),
    ] = None,
    warmup: Annotated[int, typer.Option(min=0, help="Warmup requests (excluded from metrics)")] = 1,
    timeout: Annotated[float, typer.Option(min=0.1, help="Per-request timeout (s)")] = 120.0,
    temperature: Annotated[float | None, typer.Option(min=-2.0, max=2.0)] = None,
    extra_body: Annotated[
        str | None, typer.Option(help='Extra JSON body fields, e.g. {"top_k":40}')
    ] = None,
    slo_ttft_ms: Annotated[float | None, typer.Option(min=0)] = None,
    slo_tpot_ms: Annotated[float | None, typer.Option(min=0)] = None,
    slo_e2e_ms: Annotated[float | None, typer.Option(min=0)] = None,
    output_json: Annotated[
        Path | None, typer.Option("--json", help="Write raw-record JSON report")
    ] = None,
    output_html: Annotated[
        Path | None, typer.Option("--html", help="Write single-file HTML report")
    ] = None,
) -> None:
    """Benchmark one endpoint under a fixed concurrency."""
    from faster import bench as run_bench

    endpoint = EndpointConfig(
        base_url=base_url,
        model=model,
        api_key=api_key,
        timeout_s=timeout,
        extra_body=json.loads(extra_body) if extra_body else {},
    )
    workload = WorkloadConfig(
        num_requests=num_requests,
        duration_s=duration_s,
        concurrency=concurrency,
        request_rate=request_rate,
        warmup_requests=warmup,
        prompts=_collect_prompts(prompt, prompts_file),
        max_tokens=max_tokens,
        temperature=temperature,
    )
    result = asyncio.run(
        run_bench(endpoint, workload, slo=_build_slo(slo_ttft_ms, slo_tpot_ms, slo_e2e_ms))
    )
    _print_summary(result, title=f"faster bench - {model} @ c={concurrency}")
    for path in _write_outputs(result, endpoint, workload, output_json, output_html):
        typer.echo(f"written: {path}")


@app.command()
def sweep(
    base_url: Annotated[str, typer.Option(help="OpenAI-compatible base URL")],
    model: Annotated[str, typer.Option(help="Model name")],
    concurrency: Annotated[
        str, typer.Option("-c", "--concurrency", help="Comma-separated levels, e.g. 1,2,4,8")
    ],
    api_key: Annotated[str | None, typer.Option(envvar="FASTER_API_KEY")] = None,
    prompt: Annotated[str | None, typer.Option(help=PROMPT_HELP)] = None,
    prompts_file: Annotated[Path | None, typer.Option()] = None,
    max_tokens: Annotated[int, typer.Option(min=1)] = 256,
    num_requests: Annotated[int | None, typer.Option("--num-requests", "-n", min=1)] = None,
    duration_s: Annotated[float | None, typer.Option("--duration-s", min=0.001)] = None,
    request_rate: Annotated[float | None, typer.Option("--request-rate", min=0.000001)] = None,
    warmup: Annotated[int, typer.Option(min=0)] = 1,
    timeout: Annotated[float, typer.Option(min=0.1)] = 120.0,
    output_json: Annotated[Path | None, typer.Option("--json")] = None,
    output_html: Annotated[Path | None, typer.Option("--html")] = None,
) -> None:
    """Run bench at each concurrency level and compare throughput/latency."""
    from faster import bench as run_bench

    try:
        levels = sorted({int(x) for x in concurrency.split(",")})
    except ValueError as exc:
        raise typer.BadParameter("concurrency must be like '1,2,4,8'") from exc
    if levels[0] < 1:
        raise typer.BadParameter("concurrency levels must be >= 1")

    endpoint = EndpointConfig(base_url=base_url, model=model, api_key=api_key, timeout_s=timeout)
    prompts = _collect_prompts(prompt, prompts_file)
    if (num_requests is None) == (duration_s is None):
        raise typer.BadParameter("exactly one of --num-requests / --duration-s is required")

    rows: list[dict[str, Any]] = []
    results: list[tuple[int, RunResult]] = []
    for level in levels:
        workload = WorkloadConfig(
            num_requests=num_requests,
            duration_s=duration_s,
            concurrency=level,
            request_rate=request_rate,
            warmup_requests=warmup,
            prompts=prompts,
            max_tokens=max_tokens,
        )
        typer.echo(f"== running c={level} ...")
        result = asyncio.run(run_bench(endpoint, workload))
        results.append((level, result))
        _print_summary(result, title=f"faster sweep - c={level}")
        rows.append(_sweep_row(level, result.summary))

    _print_sweep_table(rows)
    if output_json is not None:
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(
            json.dumps({"model": model, "base_url": base_url, "rows": rows}, indent=2),
            encoding="utf-8",
        )
        typer.echo(f"written: {output_json}")
    if output_html is not None:
        _write_sweep_html(results, endpoint, output_html)
        typer.echo(f"written: {output_html}")


def _sweep_row(level: int, s: BenchSummary) -> dict[str, Any]:
    return {
        "concurrency": level,
        "success_rate": None if s.success_rate != s.success_rate else s.success_rate,
        "request_throughput": s.request_throughput,
        "output_throughput": s.output_throughput,
        "steady_output_throughput": s.steady_output_throughput,
        "ttft_mean_ms": s.ttft.mean,
        "ttft_p99_ms": s.ttft.percentiles.get("p99", math.nan),
        "tpot_mean_ms": s.tpot.mean,
        "e2e_p99_ms": s.e2e.percentiles.get("p99", math.nan),
    }


def _print_sweep_table(rows: list[dict[str, Any]]) -> None:
    from rich.console import Console
    from rich.table import Table

    def cell(v: Any, fmt: str = "{:.1f}") -> str:
        return "n/a" if v is None or v != v else fmt.format(v)

    table = Table(title="sweep results (docs/metrics.md semantics)")
    for col in (
        "c",
        "req/s",
        "out tok/s",
        "steady tok/s",
        "TTFT mean",
        "TTFT p99",
        "TPOT mean",
        "E2E p99",
        "ok%",
    ):
        table.add_column(col, justify="right")
    for r in rows:
        table.add_row(
            str(r["concurrency"]),
            cell(r["request_throughput"]),
            cell(r["output_throughput"]),
            cell(r["steady_output_throughput"]),
            cell(r["ttft_mean_ms"]),
            cell(r["ttft_p99_ms"]),
            cell(r["tpot_mean_ms"]),
            cell(r["e2e_p99_ms"]),
            cell(r["success_rate"], "{:.0%}") if r["success_rate"] is not None else "n/a",
        )
    Console().print(table)


def _write_sweep_html(
    results: list[tuple[int, RunResult]],
    endpoint: EndpointConfig,
    path: Path,
) -> None:
    from faster.report.html import _histogram_svg, _line_chart_svg, _n

    points_tp = [(lvl, r.summary.output_throughput) for lvl, r in results]
    points_ttft = [(lvl, r.summary.ttft.percentiles.get("p99", math.nan)) for lvl, r in results]
    charts = (
        "<h2>Output throughput vs concurrency</h2>"
        + _line_chart_svg([p for p in points_tp if p[1] == p[1]])
        + "<h2>TTFT p99 vs concurrency</h2>"
        + _line_chart_svg([p for p in points_ttft if p[1] == p[1]])
        + "<h2>TTFT distribution (highest concurrency)</h2>"
        + _histogram_svg(
            [
                rec.t_first_token - rec.t_start
                for rec in results[-1][1].records
                if rec.ok and rec.t_first_token is not None
            ]
        )
    )
    html_text = f"""<!doctype html><html><head><meta charset="utf-8">
<title>faster sweep - {endpoint.model}</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem auto;max-width:760px}}
h2{{font-size:1.05rem;border-bottom:1px solid #eee;padding-bottom:4px}}
svg{{width:100%;height:auto;background:#fafafa;border:1px solid #eee;border-radius:6px}}
.meta{{color:#666;font-size:.85rem}}</style></head><body>
<h1>faster sweep report</h1>
<p class="meta">{endpoint.base_url} &middot; model <b>{endpoint.model}</b>
 &middot; generated {_n(time.time(), 0)}</p>
{charts}<p class="meta">Metric definitions: docs/metrics.md</p></body></html>"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html_text, encoding="utf-8")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
