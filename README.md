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
- [运营看板](http://localhost:18082/project/cmwk7bi2e0006rm07849pgu6a/dashboards/cmwk7cpv80009rm07f8ib2kc9?dateRange=7d)

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
项目的 `SkillTriggerCallbackHandler` 继承 SDK 的 `CallbackHandler`，在 `on_tool_start` 中先调用官方实现创建工具 span，再为带 SKILL 标记的调用写入一次 EVENT。
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
技能作者可以在 SKILL.md 的 YAML 头部填写 `metadata.id` 和 `metadata.version`。
新 EVENT 会将已填写的值分别记录为 `skill_id` 和 `skill_version`。ID 可跨改名保留，版本由技能作者维护。

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
uv run python -m scripts.smoke --run-id demo-001
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

## Langfuse 自动模型评估

三项模型评估已迁移到 Langfuse Evaluators，由 Langfuse worker 异步调用模型、写回分数。
Agent 服务不执行评分，也不需要本地批量调用模型的脚本。

| 分数名称 | 含义 |
| --- | --- |
| `online_task_success` | 当前请求的任务完成度；合理拒绝无效输入也可满分 |
| `online_correctness` | 计算、文本统计及陈述是否有原始输入、历史或工具证据支持 |
| `online_instruction_following` | 是否遵循用户约束和可见技能指令，保留原始输入 |

三个评估器均 Active，100% 采样新的 `agentops.chat` 根 observation，排除 Langfuse 内部环境。
`input` 变量映射 Input，`output` 映射 Output，JsonPath 留空。根输出的原生消息包含历史与工具结果；
提示词要求只评估当前轮最终回答，历史仅供参考，不把读取 SKILL 当作执行成功。
均为 Numeric 0～1 分，中文评分依据写入 score comment，来源为 `EVAL`，关联实际 Agent 根节点。
任务配置、提示词和默认模型均在
[Langfuse Evaluators](http://localhost:18082/project/cmwk7bi2e0006rm07849pgu6a/evals) 管理。

```powershell
# 发送真实请求，Langfuse 在后台自动评分，不再单独运行模型评估脚本
uv run --locked python -m scripts.smoke --run-id demo-002
# 只读：等待三个自动分数，核对根节点、范围、重复记录及看板均值/样本数
uv run --locked python -m scripts.verify_online_evaluation --manifest artifacts/smoke-demo-002.json
uv run --locked python -m scripts.configure_dashboard
```

校验报告保存为 `artifacts/online-evaluation-smoke-demo-002.json`。
看板展示三项自动评估的均分、趋势、各指标样本数，使用三个专用 `online_*` 指标名筛选。
Langfuse 4.16 的统计 API 支持 `source=EVAL`，但 widget 编辑器未提供 Score Source，
保存该条件会出现 Unsupported filters，编辑时还会丢弃条件，因此 widget 不保存它。
`online_*` 名称仅供自动评估使用；`scripts.verify_langfuse` 会检查这些名称下的分数均来自 EVAL，
防止手工/API 同名评分混入。该校验是检测措施，不能阻止其他调用方写入同名分数。
旧版 `llm_*` 分数留在 Langfuse 历史记录中，不参与当前自动评估卡片统计。
原本的本地模型调用脚本、评分实现、专属测试和 `JUDGE_MODEL_*` 示例配置已清理；
独立规则评估、真实请求脚本和只读验证工具仍可使用。

新配置只自动评估启用后的请求，不会自动补评历史 trace。评估错误可在各 Evaluator 的 Logs 查看；
没有有效结果的任务不会伪造成功分数。原本本地 manifest 的可选参考答案不会自动传入在线评估，
需要参考答案时应另建数据集评估并明确变量映射。
同模型执行和评分只是 POC，需用人工标注或独立 judge 模型继续校准。

## Langfuse 内置评估的 JSON 适配服务

`json_adapter.py` 是独立 FastAPI 服务，Agent 的 `app/main.py` 不依赖它。
调用链是：Langfuse → `18081` JSON 适配服务 → `23333` 原模型网关。
适配服务复用 `.env` 中的 `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME`，
入口也使用同一 API Key 鉴权；Agent 继续直接访问原模型网关。

```powershell
uv sync --locked
.\scripts\adapter.ps1 start
.\scripts\adapter.ps1 status
.\scripts\adapter.ps1 stop
# 或前台启动
uv run --locked python -m uvicorn json_adapter:app --host 127.0.0.1 --port 18081
```

Langfuse LLM Connections 中的 `local-debug-model` 使用 openai adapter，Base URL 为
`http://host.docker.internal:18081/v1`，模型为 `openai-codex:gpt-6-astra`，关闭 Responses API。
Docker 内的 Langfuse web 和 worker 均需允许 `host.docker.internal`：
`LANGFUSE_LLM_CONNECTION_WHITELISTED_HOST=host.docker.internal`。
当前机器已配置；适配服务须与 Langfuse 一起运行。日志位于 `logs/adapter.*.log`。

支持 `/v1/models` 和非流式 `/v1/chat/completions`。针对 `response_format.json_schema`，
将 Schema 写入提示词，并使用 jsonschema 严格校验返回值；错误时携带校验信息重试，
总共最多三次。拒绝无效类型、越界、NaN 和远程 Schema 引用，不自动转换分数或补默认值。
仍不合格时返回 502，让 Langfuse 显示评估失败；成功响应的 Token 用量合计所有尝试。
这属于提示词加校验重试，不能保证上游每次都生成合法 JSON，不支持流式或工具调用。

自动评分依赖本服务持续运行。启用后 smoke 命令不要传 `--assert-unscored`。
本次 `native-evals-all-v2` 的 5 个真实请求用于验证三项评估和看板统计。
已验证 15 条自动评分，每项 5 条，均关联实际 Agent 根节点，Metrics API 的均值和样本数一致。
任务完成度另保留试点阶段的 5 条自动评分，因此当前看板该指标总样本数为 10。

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
