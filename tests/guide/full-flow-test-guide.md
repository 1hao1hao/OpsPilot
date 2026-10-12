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

状态为 QUEUED、RUNNING、RETRYING、SUCCEEDED、FAILED 或 CANCELLED。运行中的 result 请求返回 202。最终报告 schema 3.0 包含 Top-K、Evidence、verdict、decision 和嵌套 InvestigationTrace；工具 attempt、latency、状态与错误码在工具完成事件中。

Run SUCCEEDED 和报告 completed 只表示执行成功。CONFIRMED 才确认根因；NO_FAULT 需要当前告警范围内的充分健康证据；INCONCLUSIVE 可保留临时 Top-K。读取 primary_root_cause_confirmed 与 confirmed_root_cause，不要把兼容字段 primary_root_cause 自动当作确认结果。上例只有一个资源观测，不保证通过默认双来源 Gate。

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

恢复测试覆盖 Worker 崩溃、stale run 重新入队、checkpoint 恢复、成功 ToolCall 不重复执行、重复请求/投递和超时重试。当前 checkpoint schema 是 3.0，graph version 为 opspilot-runtime-v6-compact-report；版本不匹配拒绝恢复，同版本状态缺少新决策字段时兼容读取并重新判断。历史报告缺少新字段时保持未确认。

## 诊断质量

原始 v1 数据中的领域快照不对 Planner 开放。Fallback 优先已有 Evidence 指向的领域，并考虑动态证据需求；缺少观测或健康参考、预算耗尽时保留 INCONCLUSIVE，不能从 Top1=no_fault 推断已排除故障。原始 Top1、确认率、错误确认率和拒判率分别统计；历史对照与最新真实发布、离线回放及中断记录见 [README](../../README.md)。
