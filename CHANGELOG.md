# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each entry corresponds to a git tag (`v*`) and a GitHub Release.

## [Unreleased]

## [0.1.0] - 2026-10-03

### Added

- Unified-metric benchmark core: TTFT, TPOT, ITL, E2E latency, throughput,
  steady-state window throughput, goodput (SLO attainment) — all formulas
  normatively defined in `docs/metrics.md`
- OpenAI-compatible streaming adapter covering vLLM / SGLang / llama.cpp
  server / Ollama / cloud APIs, with SSE edge-case semantics (role-only,
  usage-only, reasoning chunks)
- asyncio load engine: closed-loop worker pool, count/duration modes,
  Poisson arrivals, warmup, per-request timeout and failure isolation
- Reports: JSON raw records (auditable), rich terminal table, single-file
  HTML with inline SVG charts
- CLI: `faster bench` and `faster sweep` (concurrency ladder with
  comparison table and curves)
- Thinking-model support: reasoning tokens counted as decode work
- Demo endpoint server (`scripts/demo_server.py`)
- Engineering: strict mypy, ruff, 33 tests, CI (3.11/3.12/3.13),
  tag-triggered PyPI publishing
