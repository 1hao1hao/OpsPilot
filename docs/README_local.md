# 本地运行与验证

更新：2026-10-12。完整功能与结论见 [README](../README.md)。[旧本地说明](history/20261012/README_local.md) 保留历史内容。

## Python 环境

Python 3.11+，安装 `.[dev]`。迁移项目时备份旧 `.venv`，在本机新建；不要复用另一台机器的虚拟环境。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src/opspilot tests/contract tests/integration tests/recovery scripts/otel_demo
```

Linux/macOS 使用 `bash scripts/bootstrap_dev_env.sh` 后激活 `.venv/bin/activate`。测试默认不调用付费 LLM，真实基础设施测试未配置时跳过。实际 Python 版本可选本机安装的符合要求版本。

## API 与 Worker

```bash
docker compose --profile full up --build -d
```

API 位于 localhost:8000，Mock 位于 localhost:8001；PostgreSQL 和 Redis 为持久化及队列。首次启动由 migrate 服务执行 Alembic。API 实际入口为 `opspilot.api.app:app`，Worker 为 `python -m opspilot.runtime.worker`。请求示例与恢复测试见 [全流程指南](../tests/guide/full-flow-test-guide.md)。

LLM 默认关闭，可通过 `OPSPILOT_LLM_ENABLED` 和 `DEEPSEEK_API_KEY` 启用。`.env` 保留在本机，不能上传密钥。`alert.signals` 只在 Mock 测试中用于后端快照，真实告警不要携带答案。

## Astronomy Shop

项目曾在 Windows Docker Desktop 上完成部署与 live smoke；这不替代当前 `docker info` 检查，也不要求重复重启。版本固定、启动/停止脚本、端口与后端设置见 [集成指南](../integrations/opentelemetry_demo/README.md)。Linux/WSL Bash 使用 shell 脚本；Windows 真实后端环境可在当前进程加载 `scripts/otel_demo/local_live_env.ps1`。

普通文档维护和开发优先使用快回归、fixtures 与离线回放，不需要重新部署 28 个服务或执行完整故障生命周期。最后一轮冻结实验已中断，详情见 [记录](../reports/top1_18_interrupted.md)；没有自动重跑安排。

## 读取结果

Run `SUCCEEDED` 与 Report `status=completed` 是执行状态；需检查 `verdict` 与确认标记。`primary_root_cause` 可能仍是临时 Top1；`confirmed_root_cause` 为空时不可当作确认根因。缺数据、缺基线和预算耗尽可能返回 `INCONCLUSIVE`，不是服务已健康。
