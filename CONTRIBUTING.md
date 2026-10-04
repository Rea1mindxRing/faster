# Contributing to faster / 贡献指南

Thank you for considering contributing! This document keeps our benchmark's
metrics trustworthy — the metric spec is the product, so contributions are
reviewed with that in mind.

感谢你愿意贡献！本项目的核心资产是指标口径的可信度，评审以此为准绳。

## Quick start / 快速开始

```bash
git clone https://github.com/Rea1mindxRing/faster.git
cd faster
uv sync --all-extras      # Python 3.11+ and uv required
uv run pytest -q          # should pass in ~3s, no network needed
```

## The rules / 硬性规则

1. **Any change to a metric's computation MUST update `docs/metrics.md` in the
   same PR.** The normative doc and the code are reviewed as one unit.
2. Metrics are **pure functions** over `RequestRecord` — no I/O, no clock
   reads, no global state in `src/faster/metrics/`.
3. Empty datasets yield `NaN`, never `0`. Failed requests never enter latency
   distributions.
4. One request failing must never abort a benchmark run (failure isolation is
   a contract).
5. Hot path uses `time.perf_counter()` only; no logging in the collection path.
6. Tests involving randomness (Poisson arrivals) must inject a seeded
   `random.Random` via `BenchRunner(rng=...)` — no bare thresholds.

详见 `AGENTS.md`（中英混合，规则一致）。

## Before opening a PR / 提交前检查

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest -q
```

CI runs the same three checks on Python 3.11/3.12/3.13. All green is required
before merge.

## Commit messages / 提交信息

Conventional Commits: `feat:` / `fix:` / `docs:` / `test:` / `refactor:` /
`chore:`. One logical change per commit; imperative mood ("add", not "added").

Examples:

```
feat: add ShareGPT prompt dataset loader
fix(metrics): steady-state window off-by-one on short runs
docs(metrics): clarify TPOT denominator for thinking models
```

## Workflow / 工作流

1. Open or comment on an **Issue** first for anything non-trivial — discuss
   before building. (任何非琐碎改动先开 Issue 讨论)
2. Branch from `main`: `git checkout -b feat/your-topic`
3. Keep PRs small and focused; one feature or one fix per PR
4. CI must pass; a maintainer reviews
5. Squash-merge is preferred for multi-commit PRs

## Metric changes deserve extra scrutiny / 指标改动从严

If your PR changes what a number *means*:

- Update `docs/metrics.md` (formulas, timing boundaries, SSE edge cases)
- Update/extend the hand-verifiable tests in `tests/test_metrics.py`
- Bump the minor version — published numbers will change, users must know
- Explain the motivation in the PR description

## Reporting security issues / 安全问题

Do **not** open public issues for security vulnerabilities — see
`SECURITY.md` (email: 2874932278@qq.com).

## Where to ask / 有疑问

Open a GitHub Issue with the `question` label, or start a Discussion if your
topic is exploratory.
