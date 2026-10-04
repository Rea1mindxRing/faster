# faster

**Unified-metric LLM inference benchmark SDK.** One ruler for every engine and every provider.

[中文](#中文说明) | English

## Why

Every serious LLM deployment starts with the same question: *how fast is it, really?* But the benchmark
tools that exist today disagree with each other — TTFT/TPOT formulas differ (LLMPerf's TPOT is
systematically lower because its denominator doesn't exclude the first token), "concurrency" means
different things in different tools, and empty datasets silently report 0 ms.

**faster fixes this with a documented, normative metric spec** ([docs/metrics.md](docs/metrics.md)):

- Every metric's formula, timing boundary, and edge-case semantics are written down and enforced by tests
- Identical semantics on **any OpenAI-compatible endpoint**: vLLM, SGLang, llama.cpp server, Ollama,
  LM Studio, and every major cloud API
- Raw per-request records are always exported (JSON), so any aggregate can be recomputed and audited

## Features

| | |
|---|---|
| **Unified metrics** | TTFT, ITL, TPOT, E2E latency, throughput, steady-state throughput, goodput (SLO attainment) |
| **One adapter** | OpenAI-compatible chat/completions with streaming SSE — covers ~all engines & providers |
| **Load engine** | asyncio worker pool; fixed concurrency, count/duration modes, Poisson arrival, warmup, per-request timeout & failure isolation |
| **Reports** | JSON (raw records), rich terminal table, single-file HTML (inline SVG charts, zero JS dependencies) |
| **Dual entry** | `faster bench` CLI, or embed the SDK in your own evaluation pipeline |

## Quick start

```bash
pip install "faster[cli]"

# No endpoint handy? Try it against the bundled demo server first:
python scripts/demo_server.py &
faster bench --base-url http://127.0.0.1:8098/v1 --model demo \
    --num-requests 20 --concurrency 4 --json result.json --html report.html

# Benchmark a local engine
faster bench --base-url http://localhost:11434/v1 --model qwen2.5:7b \
    --prompt "Explain how transformer attention works." --max-tokens 256 \
    --concurrency 4 --num-requests 50

# Find the concurrency sweet spot
faster sweep --base-url http://localhost:8000/v1 --model meta-llama/Llama-3-8B \
    --concurrency 1,2,4,8,16 --num-requests 200

# Enforce an SLO
faster bench ... --slo-ttft-ms 800 --slo-tpot-ms 100
```

As a library:

```python
from faster import bench, EndpointConfig, WorkloadConfig

report = await bench(
    endpoint=EndpointConfig(base_url="http://localhost:8000/v1", model="my-model"),
    workload=WorkloadConfig(num_requests=100, concurrency=8, max_tokens=256),
)
print(report.summary.output_throughput)  # tok/s
report.to_json("result.json")  # raw records, recompute anything
```

## Metric cheat sheet

Full normative definitions (formulas, timing boundaries, SSE edge cases) live in
[docs/metrics.md](docs/metrics.md). Quick reference:

| Metric | Formula | Note |
|---|---|---|
| TTFT | `t_first_output_token − t_start` | role-only chunks don't count; thinking tokens do |
| TPOT | `(E2E − TTFT) / (output_tokens − 1)` | decode-phase only, matches vLLM/GenAI-Perf |
| ITL | per-token inter-arrival distribution | coalescing flagged in reports |
| Throughput | tokens / wall-clock duration | plus steady-state window variant |
| Goodput | share of requests meeting all SLO constraints | — |

## Roadmap

- [x] v0.1 — single-endpoint benchmark, JSON/terminal reports, unified metric core
- [ ] v0.2 — concurrency sweep + HTML report, connection-phase (TCP/TLS) attribution
- [ ] v0.3 — real-prompt datasets (ShareGPT), trace replay, more protocol adapters
- [ ] v1.0 — community reference benchmarks and a public results board

## Contributing

The metric spec is the product. If you change how a metric is computed, you must update
`docs/metrics.md` in the same PR. Run `uv run pytest && uv run ruff check . && uv run mypy` before submitting.

## License

Apache-2.0

---

# 中文说明

## 为什么做这个

每个 LLM 部署者的第一个问题都是"到底多快？"——但现有压测工具彼此对不上：TTFT/TPOT 公式不一致
（LLMPerf 的 TPOT 分母不减 1，数值系统性偏低）、"并发数"各家含义不同、空数据时静默显示 0ms。

**faster 用一份文档化的规范性口径解决它**（[docs/metrics.md](docs/metrics.md)）：

- 每个指标的公式、计时边界、边界情况语义全部白纸黑字并有测试兜底（含思考模型：reasoning token 计入解码指标）
- 对**任何 OpenAI 兼容端点**语义一致：vLLM、SGLang、llama.cpp server、Ollama、主流云 API
- 永远导出逐请求原始记录（JSON），任何聚合结果都可重算、可审计

## 快速开始

```bash
pip install "faster[cli]"

# 没有现成端点？先用自带的 demo 服务器体验：
python scripts/demo_server.py &
faster bench --base-url http://127.0.0.1:8098/v1 --model demo \
    --num-requests 20 --concurrency 4 --json result.json --html report.html

faster bench --base-url http://localhost:11434/v1 --model qwen2.5:7b \
    --prompt "..." --max-tokens 256 --concurrency 4 --num-requests 50

faster sweep --base-url http://localhost:8000/v1 --model my-model \
    --concurrency 1,2,4,8,16 --num-requests 200
```

## 路线图

- [x] v0.1 — 单端点基准、JSON/终端报告、统一指标核心
- [ ] v0.2 — 并发扫描 + HTML 报告、连接阶段（TCP/TLS）归因
- [ ] v0.3 — 真实 prompt 数据集（ShareGPT）、trace 回放、更多协议适配
- [ ] v1.0 — 社区参考基准与公开结果榜

## 许可证

Apache-2.0
