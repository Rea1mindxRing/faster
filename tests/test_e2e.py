"""End-to-end integration test: real HTTP over loopback, full pipeline.

Scripted SSE server -> OpenAICompatProvider -> BenchRunner -> metrics ->
JSON/HTML/terminal reports. Verifies that scripted timing survives the whole
stack and that exports are usable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from faster import EndpointConfig, WorkloadConfig
from faster.engine.runner import BenchRunner
from faster.metrics import SloSpec
from faster.report.html import render_html
from faster.report.json_out import export_json
from faster.report.terminal import format_summary
from test_provider import SSEServer, sse


@dataclass
class StreamingServer(SSEServer):
    """SSE server whose per-request script mimics a well-behaved engine:

    role-only chunk -> first content token at +30ms -> 3 more tokens every
    +20ms -> usage-only chunk with counts -> [DONE].
    """

    script: list[tuple[float, str]] = field(
        default_factory=lambda: [
            (0.0, sse({"choices": [{"delta": {"role": "assistant"}}]})),
            (0.03, sse({"choices": [{"delta": {"content": "Hello"}}]})),
            (0.02, sse({"choices": [{"delta": {"content": " faster"}}]})),
            (0.02, sse({"choices": [{"delta": {"content": " and"}}]})),
            (0.02, sse({"choices": [{"delta": {"content": " faster!"}}]})),
            (0.0, sse({"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4}})),
            (0.0, "data: [DONE]\n\n"),
        ]
    )


async def run_pipeline(server: StreamingServer, workload: WorkloadConfig):
    await server.start()
    try:
        runner = BenchRunner(
            endpoint=_endpoint(server),
            workload=workload,
            slo=SloSpec(ttft_ms=200.0),
        )
        return await runner.run()
    finally:
        await server.stop()


def _endpoint(server: StreamingServer) -> EndpointConfig:
    return EndpointConfig(base_url=server.base_url, model="e2e-model", timeout_s=10.0)


class TestFullPipeline:
    async def test_scripted_timing_survives_the_stack(self):
        server = StreamingServer()
        result = await run_pipeline(
            server,
            WorkloadConfig(num_requests=6, concurrency=2, warmup_requests=1),
        )
        s = result.summary
        assert s.num_requests == 6
        assert s.num_success == 6
        assert s.total_output_tokens == 24  # server-reported usage: 4 per request
        assert s.total_input_tokens == 54  # 9 per request
        # scripted: TTFT = 30ms (+ loopback overhead), TPOT = 20ms
        assert 25.0 <= s.ttft.mean <= 100.0
        assert 18.0 <= s.tpot.mean <= 60.0
        # SLO ttft <= 200ms met by every request
        assert s.goodput == 1.0
        # ~0.2s run vs 1s sampling interval: usually empty, never required
        assert isinstance(result.resource_samples, list)

    async def test_json_export_is_recomputable(self, tmp_path: Path):
        server = StreamingServer()
        result = await run_pipeline(
            server,
            WorkloadConfig(num_requests=4, concurrency=2, warmup_requests=0),
        )
        export = export_json(result, _endpoint(server), result_summary_workload())
        path = export.write(tmp_path / "out" / "result.json")

        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded["schema_version"] == 1
        assert len(loaded["records"]) == 4
        rec = loaded["records"][0]
        # raw records must contain every timestamp needed to recompute metrics
        assert rec["t_start"] > 0 and rec["t_end"] > rec["t_start"]
        assert len(rec["token_times"]) == 4
        assert rec["output_tokens"] == 4
        assert loaded["summary"]["ttft_ms"]["mean"] > 0


def result_summary_workload() -> WorkloadConfig:
    return WorkloadConfig(num_requests=4, concurrency=2, warmup_requests=0)


class TestReports:
    async def test_html_and_terminal_contain_metrics(self, tmp_path):
        server = StreamingServer()
        result = await run_pipeline(
            server,
            WorkloadConfig(num_requests=2, concurrency=1, warmup_requests=0),
        )
        html = render_html(result, _endpoint(server), result_summary_workload())
        assert "<svg" in html
        assert "TTFT" in html
        assert "faster benchmark report" in html

        text = format_summary(result)
        assert "TTFT" in text
        assert "throughput" in text


def test_streaming_server_script_is_valid_sse():
    for delay, payload in StreamingServer().script:
        assert isinstance(delay, float) and delay >= 0
        assert payload.endswith("\n\n")
