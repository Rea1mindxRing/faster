<!--
  Thanks for contributing! Please fill in the sections below.
  感谢贡献！请填写以下内容。
-->

## What does this PR change? / 这个 PR 改了什么

<!-- One or two sentences. 一两句话。 -->

## Motivation / 动机

<!-- Link the issue: "Closes #123", or explain why. 关联 Issue 或说明原因。 -->

Closes #

## Checklist / 检查清单

- [ ] `uv run ruff check . && uv run ruff format --check .` passes
- [ ] `uv run mypy` passes
- [ ] `uv run pytest -q` passes (no network required)
- [ ] Tests added/updated for the change
- [ ] **If a metric's computation changed:** `docs/metrics.md` updated in this
      same PR, and version bumped (minor for behavior changes)
- [ ] No new dependencies without discussion (keep the core lean: httpx + pydantic)
