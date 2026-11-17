# AgentOps POC

DeepAgents **0.6.2** + FastAPI + SQLite checkpoint；依赖使用 uv，格式化使用 Ruff。
服务实现只有 `app/main.py`。额外的函数式 middleware 只记录 SKILL 触发，不实现技能加载或执行。

## 启动

```powershell
uv sync --locked
# 当前机器 .env 已配置，请勿覆盖
uv run --locked python -m uvicorn app.main:app --host 127.0.0.1 --port 18080
```

环境变量见 `.env.example`。后台启动/停止：`scripts/start.ps1`、`scripts/stop.ps1`。
当前机器 uv 也可通过 `& "$env:APPDATA\Python\Scripts\uv.exe"` 调用。

- [接口文档](http://127.0.0.1:18080/docs)
- [运营看板](http://localhost:13000/project/cmwk7bi2e0006rm07849pgu6a/dashboards/cmwk7cpv80009rm07f8ib2kc9?dateRange=7d)

## 原生流式响应

`POST /chat` 请求：

```json
{"user_id":"alice","session_id":"demo-001","message":"请用 order-total 技能计算订单总额……"}
```

`StreamingResponse` 返回 `application/x-ndjson`，每行是一条 `agent.astream()` 原生结果的 JSON 序列化。
使用 `stream_mode=["messages", "updates"]`、`version="v2"`，保留 `type`、`ns`、`data` 原生结构。
例如 `messages` 的 data 为消息块和 metadata，`updates` 为节点状态更新。
不再组装自定义 start/token/final/error 事件，不汇总答案、Token、SKILL 或耗时。
客户端按原生消息/更新消费；HTTP 响应头 `X-Langfuse-Trace-Id` 可关联 Langfuse。
执行异常由框架处理，流可能中断；不会伪造业务完成事件。

由于 astream 产出 Python 对象，而 HTTP body 需要文本或字节，服务只做必要的一行 JSON 编码。
这与旧 SSE 协议不兼容。

## Langfuse 接入

请求链路使用 `get_client()`、Langfuse `CallbackHandler` 和一处 `with propagate_attributes(...)`。
CallbackHandler 只扩展 `on_tool_start`：先调用官方实现创建工具 span，再为带 SKILL 标记的调用写入一次 EVENT。
没有手工 span.update、评分、ContextVar 或自定义 trace 生命周期。
根 Agent 输入输出、模型用量、原生工具调用及错误均由 callback 自动记录。
`get_client()` 用于 SDK 生命周期管理及本地 flush 辅助接口。

用户和会话经 propagate_attributes 传播。checkpoint thread_id 使用 `[user_id, session_id]` 的 JSON，避免跨用户复用。
持久化使用原生 AsyncSqliteSaver，`durability="sync"`。这是单 worker POC；同一会话请串行请求。

## 原生 SKILL

通过 `create_deep_agent(skills=["/skills/"])` 启用 DeepAgents 自带 SkillsMiddleware。
技能内容位于 `agent_workspace/skills/*/SKILL.md`。框架发现元数据，模型按需调用原生 read_file 加载指令并遵循工作流。
没有自定义 run_skill、技能加载器或注册表。
两个 demo 是纯指令工作流；金额与文本计算不是 Python 程序级确定性验证。

`@wrap_tool_call` 定义的 `skill_triggers` 使用框架提供的 `skills_metadata`，匹配原生
`read_file` 的 `file_path`。匹配后只给本次工具调用的 config.metadata 添加 `skill_trigger` 标记，
随后原样传递请求和返回结果。普通文件读取不计入 SKILL。

middleware 入口处尚未创建 read_file span，因此 EVENT 在 `on_tool_start` 中、官方 handler
创建并激活工具 span 后写入，自动成为该 read_file 的子节点。无需读取 SDK 私有字段或手动指定 trace/parent ID。
`tool_call_id` 防止标记被嵌套调用误用，并用于父子关系核对。事件只记录触发时点及识别元数据。

口径是**每次请求读取已发现的技能文档计 1 次**：重复读取计多次，读取失败也计触发，
复用会话中已有指令不额外计数；不统计技能执行耗时或成功率。
看板通过 `EVENT + metadata.kind=skill_trigger + metadata.instrumentation=skill-trigger-v3`
筛选，按 observation name 分组做 Count，展示 Top 20 和时间趋势；旧版实验事件不参与计数。

独立只读脚本仍可从 callback 的根输出和原生工具输入交叉核对：

```powershell
uv run python -m scripts.verify_skill_events --manifest artifacts/smoke-demo-001.json
```

校验原生读取与 EVENT 一一对应、parentObservationId 指向实际 read_file、trace/user/session 关联正确、事件起止时间相同，
并通过 Langfuse Metrics API 验证同批 trace 的看板统计结果。
配置可重复运行：`uv run python -m scripts.configure_dashboard`。

## 请求与独立评估

```powershell
uv run python -m scripts.smoke --run-id demo-001 --assert-unscored
uv run python -m scripts.evaluate --manifest artifacts/smoke-demo-001.json --dry-run
uv run python -m scripts.evaluate --manifest artifacts/smoke-demo-001.json
uv run python -m scripts.verify_evaluation --report artifacts/evaluation-smoke-demo-001.json
```

smoke 测试原生流、checkpoint 记忆、用户隔离、技能加载，并从 Langfuse 验证模型用量。
参考答案仅写入本地 manifest，不传给服务。
评估器读取 callback 根 observation 的原生 messages 输出，并结合原生 read_file 记录进行规则评估。
等待根和本轮模型/工具数据结束；不依赖服务自定义状态或事件 ID，不重跑 Agent。

- response_nonempty：最终答案是否非空。
- task_success：运行未报错、答案非空、预期技能被读取、所有参考文本命中。
- quality：参考文本命中比例；没有参考文本不生成该指标。

这些是规则检查，不是 LLM judge；技能读取不等于执行成功。
评分使用稳定 ID 写回，版本为 offline-rules-v4。同样 manifest 重跑不会重复计分。

## 独立模型评估

`scripts.evaluate_llm` 直接从 Langfuse 读取已完成请求，用模型评估当前轮的用户请求、回答、
历史上下文和原生工具输入输出。manifest 只提供 trace ID 和可选参考要求；不使用其中缓存的回答。
服务代码及请求链路不参与评估，也不重跑 Agent。

```powershell
# 调用模型生成评估报告，暂不写回；也会保存模型结果缓存
uv run python -m scripts.evaluate_llm --manifest artifacts/smoke-demo-001.json --dry-run
# 使用已验证的缓存结果，写回 Agent 根 observation 的 Scores
uv run python -m scripts.evaluate_llm --manifest artifacts/smoke-demo-001.json
uv run python -m scripts.verify_evaluation --report artifacts/llm-evaluation-smoke-demo-001.json --check-metrics
uv run python -m scripts.configure_dashboard
# 也可直接评估现有 trace，无需 manifest 或参考答案
uv run python -m scripts.evaluate_llm --trace-id YOUR_TRACE_ID
```

默认复用 `.env` 的 `MODEL_*`；可通过 `JUDGE_MODEL_NAME`、`JUDGE_MODEL_BASE_URL`、
`JUDGE_MODEL_API_KEY` 分别覆盖。judge 调用不接 Agent 的 Langfuse callback，因此不混入线上模型用量。
评估模型的 Token 用量保存在本地缓存和分数 metadata 中。

| 分数名称 | 含义 |
| --- | --- |
| `llm_task_success` | 是否完成用户实际目标，合理拒绝无效输入也可以满分 |
| `llm_correctness` | 回答及计算是否有输入、历史或工具证据支持 |
| `llm_instruction_following` | 是否遵循用户约束及适用技能指令、保留原始输入 |

均为 0～1 分，越高越好；每项中文评分依据写入 score comment。
评分标准、结构校验和证据提取位于 `evaluators/llm.py`。模型输出缺项、非数字或越界时终止；
全部请求评估通过后才写回。证据超过 `--max-input-chars`（默认 120000）时明确报错，不静默截断。

结果缓存位于 `artifacts/llm-judge-cache`；相同 trace、证据、参考要求、模型及评估标准生成稳定 ID，
重跑复用缓存且不会增加重复评分。`--refresh` 重新调用模型并覆盖同一组分数；修改模型或评估标准会生成
新的评估记录，此时看板同名指标会包含这些记录。缓存和报告不提交 Git。

看板新增模型评估的均分、均分趋势和各指标样本数，单独展示 `llm_*`；原规则评估卡片仍保留。
趋势按 Agent 请求发生时间聚合，样本数只表示已评估请求。模型评分是启发式判断；POC 默认使用
同一个模型执行和评估，后续可替换独立 judge 模型并用人工标注校准。

## 检查

```powershell
uv run ruff format .
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv run python -m scripts.verify_langfuse
```

服务版本 0.6.0；Langfuse 环境为 default，历史版本数据保留。
仅监听 loopback，user_id 为本地调用方提供的标签；SQLite 单 worker；文件后端仅映射 agent_workspace 并禁止写入。
.env、data、logs、测试 JSON 均被 Git 忽略。
