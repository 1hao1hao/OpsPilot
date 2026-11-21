# OpsPilot V2 开发与演示

## 入口

- `src/opspilot/graph/workflow.py`：离线 Workflow 选择 V1/V2。
- `src/opspilot/runtime/execution.py`：Worker 使用相同选择，并将 V2 图状态写入原 PG Checkpoint。
- `src/opspilot/investigation/v2_graph.py`：LangGraph 节点、并行派发、状态快照和 Evidence Board。
- `src/opspilot/investigation/tasks.py`：动态 Task Graph 与任务历史。
- `src/opspilot/investigation/specialist.py`：局部调查、可选结构化 LLM、受限工具选择。
- `src/opspilot/skills/`、`src/opspilot/tools/mcp_bridge.py`：按需 Skill 和官方 MCP SDK 桥接。

## 本机运行

Python 3.11+ 安装 `pip install -e '.[dev]'`。默认 V1；新 Run 使用 V2 时，在 API 与 Worker 的相同环境中设置：

```text
OPSPILOT_INVESTIGATION_MODE=v2
OPSPILOT_GRAPH_VERSION=opspilot-runtime-v7-v2-graph
OPSPILOT_CHECKPOINT_SCHEMA_VERSION=4.0
OPSPILOT_SKILLS_ENABLED=true
OPSPILOT_TOOL_TRANSPORT=native
```

需要 MCP 时将 `OPSPILOT_TOOL_TRANSPORT=mcp`。`OPSPILOT_LLM_ENABLED=false` 可复现确定性调查；启用 LLM 时在本地 `.env` 设置密钥，不能提交。并发和预算参见 `.env.example`。V1 回滚时将模式、graph version、checkpoint schema 一起改回 V1 的值；先排空正在运行的 V2 Run，避免新旧 Worker 读取不同版本状态。运行 `docker compose --profile full up --build -d --wait` 启动 PG、Redis、Mock、API 和 Worker；Compose 使用同一 `.env` 配置。若宿主机 5432 不可绑定，设置 `OPSPILOT_POSTGRES_HOST_PORT=15433` 等空闲端口；容器内仍用 5432。

## 可复现演示

```powershell
.\.venv\Scripts\python.exe scripts\v2_demo.py --output artifacts\v2_demo\demo.json
.\.venv\Scripts\python.exe -m pytest -q tests\contract\test_v2_graph.py tests\contract\test_v2_task_planning.py tests\integration\test_v2_demo.py
```

该脚本使用**合成** ScenarioProvider，不是实时遥测。`two_dependencies` 显示 trace 触发 MySQL 与 Redis 的并行任务；`db_refuted` 显示正常 DB 观测更新计划；`healthy_reference` 演示健康参考。输出的 `events`、`task_graph`、`agent_results` 和 `evidence_board` 可逐步查看。可选 `--llm-enabled` 会调用配置的远端模型并产生费用，确定性演示无需该选项。

真实只读数据源需先部署 Astronomy Shop，再运行 `scripts/v2_live_probe.py`；它查询 Prometheus、Jaeger、OpenSearch，不注入故障，也不提供 Ground Truth。`tests/integration/test_otel_demo_live.py` 可验证数据源连通与范围。PG/Redis 恢复测试使用 `OPSPILOT_TEST_DATABASE_URL` 和 `OPSPILOT_TEST_REDIS_URL`，执行 `pytest -q tests/integration/test_v2_runtime_real.py`。未设置 URL 时对应测试跳过，不能当作真实服务验收。

排查时先看 `/api/v1/runs/{run_id}/events` 中工具完成、Gate 与 Checkpoint 事件，再看报告中的临时候选和 `decision`。`INCONCLUSIVE` 可含 Top-K，不代表根因确认。MCP 模式的延迟包含协议桥接；生产环境应另行评估远程服务认证和超时。
