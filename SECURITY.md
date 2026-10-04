# Security Policy / 安全策略

## Supported Versions / 支持版本

| Version | Supported |
| ------- | --------- |
| 0.1.x   | ✅        |

We backport security fixes to the latest minor release line only.

## Reporting a Vulnerability / 报告漏洞

**请勿通过公开 GitHub Issue 报告安全漏洞。**

Email: **2874932278@qq.com**

- Please include: affected version / commit hash, reproduction steps or PoC,
  expected vs actual behavior, and your assessment of impact.
- 请附上：受影响版本或 commit、复现步骤或 PoC、预期与实际行为、影响评估。
- 中文或英文报告均可。
- You will receive an initial response within **72 hours**; we aim to publish
  a fix within 30 days for confirmed issues, coordinated with you for credit
  if desired.

If you do not receive a response within 72 hours, follow up on the same email
thread before using any public channel.

## Scope / 适用范围

**In scope:**

- This repository's source code: the `faster` SDK, CLI, report generation
  (JSON/HTML), and bundled scripts (`scripts/`)
- Supply-chain issues in declared dependencies (`pyproject.toml`) and the
  GitHub Actions workflows
- The published `faster` package on PyPI (e.g. malicious versions,
  repository/config mismatch)

**Out of scope:**

- The endpoints you benchmark with faster; faster is a *client* and takes
  arbitrary user-provided URLs — issues in the benchmarked service itself
  belong to that service's maintainers
- Reports requiring a malicious or compromised endpoint to exploit the
  machine running faster (the tool is designed to point at untrusted
  endpoints; treat sandboxing as the user's responsibility)
- Volumetric benchmarking of third-party services (using faster against
  endpoints you are not authorized to load-test)

## Design Notes Relevant to Security / 与安全相关的设计说明

- faster sends your `--api-key` only to the `--base-url` you specify; it is
  never written to JSON/HTML reports (the report contains only endpoint URL,
  model name and timings)
- HTML reports are generated data, not templates of user input — they escape
  interpolated strings and embed no external scripts
- If you benchmark untrusted endpoints, run faster in a container or
  dedicated user; a malicious HTTP endpoint can send arbitrary bytes to the
  client parser by design

## Preferred Languages / 沟通语言

中文 / English
