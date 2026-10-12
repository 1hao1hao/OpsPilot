# 本地与部署冒烟测试

更新：2026-10-12。Python 3.11+，项目安装 `.[dev]`。详细步骤见 [本地指南](README_local.md) 和 [全流程验证](../tests/guide/full-flow-test-guide.md)。旧 M2 教程保存在 [历史档案](history/20261012/smoke-testing-guide.md)。

## 本地测试

```bash
python -m pytest -q tests/smoke tests/contract tests/recovery
```

未配置 AGENT_URL/MOCK_URL 时，HTTP smoke 使用测试应用、SQLite 和内存队列；不能声称已经通过真实容器或 PostgreSQL/Redis 验证。默认不调用付费 LLM。

## 部署接口

```bash
docker compose --profile full up --build -d
AGENT_URL=http://localhost:8000 MOCK_URL=http://localhost:8001 python -m pytest -q tests/smoke
```

首个命令会启动服务。文档维护无需执行它；验证时以 Run API `/api/v1/runs` 为入口，不使用已移除的 `/analyze`。运行状态成功后仍需检查 Verdict，工具失败与不充分观测可以得到 INCONCLUSIVE。

真实恢复测试需明确配置测试 PostgreSQL/Redis URL，见全流程指南。OpenTelemetry live 验证是另一套独立流程，见 [集成指南](../integrations/opentelemetry_demo/README.md)，不能把普通 smoke 写成真实故障生命周期验收。
