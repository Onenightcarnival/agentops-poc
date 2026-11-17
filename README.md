# AgentOps POC

DeepAgents **0.6.2** + FastAPI + SQLite checkpoint；依赖使用 uv，格式化使用 Ruff。
服务实现只有 `app/main.py`，没有 AgentService、应用层 middleware、技能执行器或遥测封装。

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

服务里只使用 `get_client()`、官方 `CallbackHandler` 和一处 `with propagate_attributes(...)`。
没有手工 span.update、create_event、评分、ContextVar 或自定义 trace 生命周期。
根 Agent 输入输出、模型用量、原生工具调用及错误均由 callback 自动记录。
`get_client()` 用于 SDK 生命周期管理及本地 flush 辅助接口。

用户和会话经 propagate_attributes 传播。checkpoint thread_id 使用 `[user_id, session_id]` 的 JSON，避免跨用户复用。
持久化使用原生 AsyncSqliteSaver，`durability="sync"`。这是单 worker POC；同一会话请串行请求。

## 原生 SKILL

通过 `create_deep_agent(skills=["/skills/"])` 启用 DeepAgents 自带 SkillsMiddleware。
技能内容位于 `agent_workspace/skills/*/SKILL.md`。框架发现元数据，模型按需调用原生 read_file 加载指令并遵循工作流。
没有自定义 run_skill、技能加载器或注册表。
两个 demo 是纯指令工作流；金额与文本计算不是 Python 程序级确定性验证。

为保持服务无侵入，**不再额外上报 SKILL EVENT**。独立只读脚本从 callback 的根输出 skills_metadata
和 read_file 工具输入匹配技能，统计每个技能文档的读取调用次数：

```powershell
uv run python -m scripts.skill_usage --manifest artifacts/smoke-demo-001.json
```

结果保存为 `artifacts/skill-usage-smoke-demo-001.json`。重复读取计多次，复用会话中的指令不额外计数；
只统计触发，不统计技能执行耗时或成功率。
看板展示原生工具调用次数，read_file 也可能包含非技能文件，不能直接视为 SKILL 次数。
本地 Langfuse 当前指标视图无法直接按工具 input 中的技能路径分组，因此按技能名统计保留在独立查询报告。
旧的自定义 SKILL 卡片已从看板移除，历史数据仍保留。

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
