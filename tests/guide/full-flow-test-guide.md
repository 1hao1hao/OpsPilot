# OpsPilot 全流程验证

## 本地测试

```bash
source .venv/bin/activate
ruff check src tests
pytest -q
```

默认 HTTP 冒烟使用测试应用、SQLite 和内存队列。独立 PostgreSQL/Redis 和 Worker 进程测试需要真实服务；不配置时会明确跳过。

## 部署服务

```bash
docker compose --profile full up --build -d
```

服务包括 API、独立 Worker、PostgreSQL、Redis 和 Mock；migration 在启动时运行。LLM 为可选项，配置方式见根目录 README。

## 提交、状态、结果与事件

```bash
curl -X POST http://localhost:8000/api/v1/runs \
  -H 'Content-Type: application/json' \
  -d '{"request_id":"manual-resource-1","alert":{"alert_id":"manual-resource-1","service_name":"checkout-service","alert_type":"resource","severity":"P1","timestamp":"2026-10-03T00:00:00Z","signals":{"metric":{"cpu_usage":0.99}}}}'
```

从响应取出 `run_id`，替换以下示例中的 `RUN_ID`：

```bash
curl http://localhost:8000/api/v1/runs/RUN_ID
curl http://localhost:8000/api/v1/runs/RUN_ID/result
curl 'http://localhost:8000/api/v1/runs/RUN_ID/events?after=0'
websocat ws://localhost:8000/api/v1/runs/RUN_ID/stream
```

状态为 QUEUED、RUNNING、RETRYING、SUCCEEDED、FAILED 或 CANCELLED。运行中的 result 请求返回 202。最终报告包含 Top-K、Evidence 和嵌套 InvestigationTrace；工具 attempt、latency、状态与错误码在工具完成事件中。

## 测试部署后的 HTTP 服务

```bash
AGENT_URL=http://localhost:8000 MOCK_URL=http://localhost:8001 pytest -q tests/smoke
```

## 真实恢复与幂等验证

```bash
export OPSPILOT_TEST_DATABASE_URL='postgresql+asyncpg://opspilot:opspilot@localhost:5432/opspilot'
export OPSPILOT_TEST_REDIS_URL='redis://localhost:6379/0'
pytest -q tests/integration/test_postgres_redis_runtime.py
python -m opspilot.evaluation.cli reliability --config benchmarks/configs/runtime_faults.yaml
```

恢复测试覆盖 Worker 崩溃、stale run 重新入队、checkpoint 恢复、成功 ToolCall 不重复执行、重复请求/投递和超时重试。当前 checkpoint schema 是 3.0，不读取旧版本状态。

## 诊断质量

原始 v1 数据中的领域快照不对 Planner 开放。关闭 LLM 且 L1 没有领域线索时，fallback 只补查通用工具，可能以 NO_FAULT 和预算耗尽结束；这不能解释为已经排除了全部领域故障。完整观测排名回归和自适应调查覆盖率必须分别查看，当前对照见 README 和 `artifacts/reconstruction_2/comparison.json`。
