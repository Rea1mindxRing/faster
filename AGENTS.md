# AGENTS.md — AI 编码代理指南

本文件为在本仓库工作的 AI 编码代理（Claude Code、Cursor、Copilot 等）提供项目背景与硬性规则。

## 项目是什么

**faster** 是一个统一指标口径、跨引擎/跨云商结果可比的 LLM 推理性能基准 SDK（Python 库 + CLI）。
核心差异化是 `docs/metrics.md` —— 一份**规范性指标口径文档**。

## 硬性规则（违反 = PR 必须被拒绝）

1. **指标口径变更必须同 PR 修改 `docs/metrics.md`**。任何 TTFT/TPOT/ITL/吞吐/稳态窗口/goodput 的公式、
   计时边界、并发语义改动，文档与实现必须原子性同步。
2. **指标计算必须是纯函数**。`src/faster/metrics/` 内禁止 I/O、禁止读取时钟、禁止全局状态；
   唯一输入是 `RequestRecord`（见 `src/faster/events.py`）。
3. **失败请求绝不进入延迟/吞吐分布**，只计入错误统计；空数据集必须返回 `NaN`，**永远不许返回 0**。
4. **单请求失败/超时绝不允许中断整场压测**（失败隔离是引擎的合同，见 `docs/metrics.md` §3.4）。
5. 热路径（事件采集）只用 `time.perf_counter()`；不要在该路径引入日志、pydantic 校验或其他开销。

## 常用命令

```bash
uv sync --all-extras        # 安装依赖（含 dev/cli extras）
uv run pytest -q            # 全量测试（约 3 秒，无网络依赖）
uv run ruff check .         # lint
uv run ruff format .        # 格式化
uv run mypy                 # 类型检查（strict 模式，仅 src/）
uv build                    # 打包验证
```

CI 会跑 lint + mypy + pytest（Python 3.11/3.12/3.13 矩阵）。提交前本地三条命令必须全绿。

## 架构速览

```
CLI/SDK (cli.py, __init__.py bench())
  → config.py (pydantic: EndpointConfig, WorkloadConfig)
  → engine/runner.py (asyncio worker 池, 闭环队列 maxsize=concurrency)
      → provider/base.py (Provider 协议: stream_complete → StreamEvent 流)
      → provider/openai_compat.py (OpenAI 兼容适配器, SSE 解析)
  → events.py (RequestRecord: 逐请求原始时间戳, 所有指标的唯一数据源)
  → metrics/ (纯函数聚合 → BenchSummary)
  → report/ (json_out / terminal / html)
```

关键不变量：

- 引擎到端点只通过 `Provider` 协议解耦；新增协议（如 Anthropic 原生）只需实现该协议
- `engine/runner.py` 的队列是闭环的（maxsize=并发数）：无 `request_rate` 时不得预生成排队请求
- SSE 边界语义（role-only/usage-only/reasoning chunk）在 `openai_compat.py` 模块注释与
  `docs/metrics.md` §1 中成对定义，改动必须两边同步

## 测试约定

- 指标测试用手工构造的 `RequestRecord`，期望值必须可手算验证（用 `pytest.approx` 处理浮点）
- 并发/终止逻辑用 `MockProvider`（脚本化行为：ok / fail / hang），不依赖网络
- SSE 语义用 `tests/test_provider.py` 的 `SSEServer`（真实 HTTP/1.1 chunked + 脚本化延迟）
- 涉及随机性（Poisson 到达）的测试必须注入固定 seed 的 `random.Random`（`BenchRunner(rng=...)`），
  禁止裸阈值断言
- 有超时风险的测试保持秒级耗时；全量测试套件不得依赖外部端点

## 提交规范

- Conventional Commits 风格：`feat: ...` / `fix: ...` / `docs: ...` / `test: ...`
- 版本号遵循 SemVer；发布流程见 `.github/workflows/publish.yml`（tag `v*` 触发）
