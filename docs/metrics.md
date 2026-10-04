# faster 指标口径规范 (Metric Definitions)

> 本文档是 faster 的**规范性文档**（normative spec）：所有指标的计算公式、计时边界与并发语义以本文为准。
> 代码中的实现必须与本文一致，任何口径变更必须同时修改本文并通过评审。
> 这是 faster 区别于其他压测工具的核心承诺：**不同端点上测出的数字，语义完全一致、可以横向比较。**

所有时间戳均在客户端用 `time.perf_counter()` 采集（单调时钟，不受系统时间跳变影响）。

---

## 1. 计时基准

每个请求记录以下原始事件时间戳（perf_counter 秒）：

| 事件 | 定义 |
|---|---|
| `t_start` | HTTP 请求**发出**的时刻（请求体写入连接前） |
| `t_first_token` | 第一个**携带文本内容**的 SSE chunk 到达客户端的时刻 |
| `token_times` | 每个 content chunk 到达的时刻序列（含首 token） |
| `t_end` | 流结束的时刻（服务端关闭流或收到 `[DONE]`，取两者中较早被观察到的） |

**边界语义（重要）**：

1. **TTFT 从 `t_start` 起算**，即包含 TCP/TLS 握手（新连接时）与请求排队。这是 vLLM / SGLang / GenAI-Perf 的事实口径，faster 保持一致。连接复用时握手开销为零，不影响口径。
2. **role-only chunk 不算 token**。OpenAI 兼容流的首个 chunk 常常只含 `role` 字段（`delta: {"role": "assistant"}`），不含内容。它不计入 TTFT、不计入 `token_times`、不计入 token 计数。
3. **usage-only chunk 不截断计时**。部分服务在流末尾发送 `choices: []` 的 chunk 仅携带 `usage`。此类 chunk 不计入 token 时间戳；`t_end` 取最后一个 content chunk 之后**流真正关闭**的时刻。
4. **reasoning（思考）token 计入 token 到达**。推理模型（o1/DeepSeek-R1/Qwen3 等）通过 `delta.reasoning_content` 或 `delta.reasoning` 输出思考内容。思考也是解码工作，且包含在服务端 `usage.completion_tokens`（TPOT 分母）中——因此首个思考 chunk 即 TTFT，思考 chunk 计入 `token_times`/ITL，chunk 同时含 `content` 与 `reasoning` 时只算一次到达。这保证了思考模型上分子（解码时长）与分母（含思考的总 token 数）口径一致。**跨模型对比建议关闭思考模式**（如 vLLM 的 `--reasoning-parser` + `chat_template_kwargs`，或换非思考模型），否则 TTFT 会包含思考时间而非用户可感知的响应时间。
5. token 计数优先取服务端返回的 `usage.completion_tokens` / `usage.prompt_tokens`；仅当服务端未提供时才回退到客户端 chunk 计数（并在报告中标注 `token_source`）。

## 2. 指标定义

以下 `N` 为成功请求数；失败请求（网络错误、超时、HTTP ≥ 400）**不进入任何延迟分位数**，只计入错误统计。

### 2.1 TTFT (Time To First Token)

```
TTFT(r) = t_first_token(r) - t_start(r)
```

报告：mean、median、std、P50/P90/P95/P99、min、max（可配置分位列表）。
单位：毫秒（报告时 ×1000）。样本为空（全部失败）时报 `NaN`，**绝不显示 0**。

### 2.2 ITL (Inter-Token Latency) 与 TPOT (Time Per Output Token)

```
ITL(r) = { token_times(r)[i] - token_times(r)[i-1] | i = 2..len(token_times(r)) }
TPOT(r) = (t_end(r) - t_first_token(r)) / (output_tokens(r) - 1)    当 output_tokens > 1
```

- ITL 是逐 token 相邻间隔的**分布**（含所有成功请求的所有间隔），TPOT 是单请求解码阶段的**平均**。
- 两者分母都排除首 token：首 token 的时间已被 TTFT 覆盖，减 1 保证 TPOT/ITL 纯粹刻画**解码阶段**。这与 NVIDIA GenAI-Perf 文档一致。
- 当服务端将多个 token 合并进一个 chunk（coalescing）或使用投机解码时，chunk 数 < token 数。此时 `output_tokens` 取服务端 usage，TPOT 公式不受影响；ITL 分布会变稀疏，faster 在报告中标注 `chunk_coalescing_ratio` 提示精度损失。
- `output_tokens = 1` 的请求 TPOT 记为 NaN（无解码阶段），不进入 TPOT 统计。

### 2.3 端到端延迟 (E2E Latency)

```
E2E(r) = t_end(r) - t_start(r)
```

### 2.4 吞吐 (Throughput)

分母为**整场压测墙钟时长**：

```
dur = t_wall_end - t_wall_start          # 第一请求发出 → 最后请求结束
request_throughput = 成功请求数 / dur     # req/s
output_throughput  = Σ output_tokens / dur   # tok/s
total_throughput   = Σ (prompt_tokens + output_tokens) / dur
```

**稳态窗口吞吐 (Steady-state throughput)**（附加指标）：

全程平均吞吐会被启动爬坡与收尾稀释，对短压测失真严重。faster 另外计算：

- 将墙钟时间按秒分桶，统计每秒在途请求数（active）与输出 token 数；
- 取 `active >= ceil(0.9 × concurrency)` 的**最长连续秒桶窗口**（无则退化为全场）；
- `steady_output_throughput = 窗口内输出 token 数 / 窗口时长`。

### 2.5 成功率与错误分布

```
success_rate = 成功请求数 / 总发出请求数
```

错误按类别计数：`timeout` / `http_4xx` / `http_5xx` / `connection` / `sse_protocol` / `other`。

### 2.6 Goodput (SLO 达标率)

用户给定 SLO 约束（如 `TTFT ≤ 800ms`、`TPOT ≤ 100ms`），满足**全部**约束的请求占比：

```
goodput = |{ r : r 满足所有 SLO 约束 }| / 成功请求数
```

分母为成功请求数——失败请求无法评估延迟，但可另行参考 `success_rate`。

## 3. 并发与负载语义

### 3.1 并发数 (Concurrency)

> **并发数 = 同时在途（in-flight）的请求数上限，含排队中的请求。**

faster 以固定数量的 worker 任务实现：每个 worker 同时最多处理一个请求，请求完成后立即从队列取下一个。因此实际瞬时在途数 ≤ concurrency。

### 3.2 负载模式

| 模式 | 参数 | 语义 |
|---|---|---|
| 定量 (count) | `--num-requests N` | 共发出 N 个请求（受并发上限约束），全部结束后停止 |
| 定时 (duration) | `--duration-s S` | 持续发出请求 S 秒，到点后不再发新请求，等在途请求完成 |
| 到达间隔 | `--request-rate R` | 请求按 Poisson 过程发起（间隔服从参数 1/R 的指数分布）；默认 `inf` 即立即发起 |

`--request-rate` 与并发上限可叠加：rate 控制"何时发起"，并发上限控制"最多多少在途"。

**闭环语义**：faster 的到达队列长度以并发数为上限——没有新请求完成时不会预生成排队请求。因此不设 `--request-rate` 时是标准闭环压测（closed-loop）；设了 rate 则是开环到达 + 闭环上限的混合模式。这一语义在所有端点上严格一致。

### 3.3 预热 (Warmup)

正式测量前发出 `--warmup-requests K`（默认 1）个请求，输出截断至 32 token，**结果不计入任何指标**，用于触发 JIT/缓存/连接建立。预热请求**全部失败**时直接终止并报错（参数或端点配置有误）。

### 3.4 失败隔离与超时

- 每个请求有独立的 `--timeout-s`（默认 120s）上限，超时该请求记为 `timeout` 错误，**不影响其他请求**。
- 单请求异常（含 SSE 协议错误）只终止该请求并记录，绝不中断整场压测。

## 4. 与其他工具的口径对照

| 指标 | faster | vLLM/SGLang | GenAI-Perf | LLMPerf |
|---|---|---|---|---|
| TTFT 分母起点 | 请求发出 | 请求发出 | 请求发出 | 请求发出 |
| TPOT 分母 | `output_len - 1` | `output_len - 1` | `output_len - 1` | 不减 1（含首 token） |
| 吞吐分母 | 墙钟时长 | 墙钟时长 | 墙钟时长 | 墙钟时长 |
| role-only chunk | 不计 | 跳过 | 未明确 | 未明确 |
| 空数据展示 | NaN | 0（有误导风险） | — | — |

> 已知差异：LLMPerf 的 TPOT 分母不减 1，因此其 TPOT 数值系统性偏低，不能与 faster/vLLM/GenAI-Perf 直接比较。这正是"统一口径"要解决的问题。
