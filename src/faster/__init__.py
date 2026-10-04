"""faster - unified-metric LLM inference benchmark SDK.

Metric semantics are normative in docs/metrics.md.
"""

from __future__ import annotations

from faster.config import EndpointConfig, WorkloadConfig
from faster.engine.runner import BenchRunner, ProgressCallback, RunResult
from faster.events import ErrorKind, RequestRecord
from faster.metrics import BenchSummary, SloSpec
from faster.provider.base import Provider

__version__ = "0.1.0"

__all__ = [
    "BenchRunner",
    "BenchSummary",
    "EndpointConfig",
    "ErrorKind",
    "ProgressCallback",
    "Provider",
    "RequestRecord",
    "RunResult",
    "SloSpec",
    "WorkloadConfig",
    "__version__",
    "bench",
]


async def bench(
    endpoint: EndpointConfig,
    workload: WorkloadConfig,
    *,
    slo: SloSpec | None = None,
    provider: Provider | None = None,
    on_progress: ProgressCallback | None = None,
) -> RunResult:
    """Run one benchmark against an OpenAI-compatible endpoint.

    This is the SDK entry point; the CLI wraps it. When ``provider`` is
    omitted, an OpenAI-compatible adapter is created (and closed) for you.
    """
    runner = BenchRunner(
        endpoint=endpoint,
        workload=workload,
        slo=slo,
        provider=provider,
        on_progress=on_progress,
    )
    return await runner.run()
