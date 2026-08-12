# DeepRCA-Agent 冒烟测试教程

> 本文档随每个里程碑完成追加更新。当前版本对应 M2（PRD-02 通用分析 Agent）完成。

## 1. 环境准备

### 1.1 系统要求

| 项目 | 最低版本 | 推荐 |
|------|----------|------|
| Python | 3.10+ | 3.11+ |
| pip | 23.0+ | 最新 |
| Git | 2.40+ | 最新 |

> **Mac 用户注意**: 推荐使用 Homebrew 安装 Python 3.11+：`brew install python@3.11`

### 1.2 克隆仓库

```bash
git clone <仓库地址>
cd DeepRCA-Agent

# 切换到 PRD-02 分支
git checkout feature/xianhuimeng/20260714/general-analyzer
```

### 1.3 创建虚拟环境并安装依赖

```bash
# 创建虚拟环境
python3 -m venv venv

# Mac/Linux
source venv/bin/activate
# Windows
venv\Scripts\activate

# 安装依赖（开发模式）
pip install -e ".[dev]"

# 如果网络较慢，使用国内镜像
pip install -e ".[dev]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 1.4 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，关键配置项：

```ini
# Mock 环境开关（PRD-02 阶段不需要 Mock API，工具调用失败会自动降级）
MOCK_ENV_ENABLED=false

# LLM 配置（留空则自动降级为 fallback 模式，不影响冒烟测试）
LLM_API_KEY=
LLM_MODEL=gpt-4o-mini

# 服务配置
APP_HOST=0.0.0.0
APP_PORT=8000
```

> **冒烟测试不需要 LLM API Key**：根因定位节点会自动降级为规则引擎模式，报告节点会生成降级建议。

## 2. 测试用例说明

### 2.1 单元测试（tests/unit/）— 18 个用例

| 测试文件 | 用例数 | 验证内容 |
|----------|--------|----------|
| `test_graph.py` | 18 | LangGraph 6 节点：intake 时间窗口推导 / planner 维度映射 / dispatcher 并发+降级 / collector 双键排序 / reporter suggestions / root_cause 降级 |

### 2.2 冒烟测试（tests/smoke/）— 17 个用例

端到端验证 LangGraph 图从告警输入到报告生成的完整流程，使用 4 种告警类型的内联 Mock 数据。

| 测试用例 | 告警类型 | 验证点 |
|----------|----------|--------|
| `test_graph_compiles` | — | LangGraph 图成功编译 |
| `test_dimension_count_by_type[timeout]` | timeout | 6 维度 |
| `test_dimension_count_by_type[error_rate]` | error_rate | 6 维度 |
| `test_dimension_count_by_type[resource]` | resource | 4 维度 |
| `test_dimension_count_by_type[custom]` | custom | 6 维度 |
| `test_timeout_scenario_e2e` | timeout | status / task_plan=6 / report 非空 |
| `test_error_rate_scenario_e2e` | error_rate | status / task_plan=6 / report 非空 |
| `test_resource_scenario_e2e` | resource | status / task_plan=4 |
| `test_custom_scenario_e2e` | custom | status / task_plan=6 / report 非空 |
| `test_report_valid_json` | timeout | report 为合法 JSON 且含 trace_id |
| `test_report_has_suggestions` | timeout | report 包含 suggestions 列表 |
| `test_report_has_satisfaction_url` | timeout | report 包含 satisfaction_url |
| `test_time_window_derivation[timeout-30]` | timeout | 窗口前推 30 分钟 |
| `test_time_window_derivation[error_rate-15]` | error_rate | 窗口前推 15 分钟 |
| `test_time_window_derivation[resource-60]` | resource | 窗口前推 60 分钟 |
| `test_related_services_extracted` | timeout | 提取 order-service + payment-service |
| `test_degraded_mode_flag` | timeout | degraded_mode 为 bool 类型 |

## 3. 运行测试

### 3.1 运行全部测试

```bash
# 设置 PYTHONPATH（Mac/Linux）
export PYTHONPATH=src
python -m pytest tests/ -v --tb=short

# Windows PowerShell
$env:PYTHONPATH = "src"
python -m pytest tests/ -v --tb=short
```

### 3.2 仅运行冒烟测试

```bash
export PYTHONPATH=src
python -m pytest tests/smoke/ -v --tb=short
```

### 3.3 仅运行单元测试

```bash
export PYTHONPATH=src
python -m pytest tests/unit/ -v --tb=short
```

### 3.4 运行特定测试

```bash
# 只运行超时场景
export PYTHONPATH=src
python -m pytest tests/smoke/test_smoke.py::TestSmokeEndToEnd::test_timeout_scenario_e2e -v

# 只运行维度数验证
python -m pytest tests/smoke/test_smoke.py::TestSmokeEndToEnd::test_dimension_count_by_type -v
```

### 3.5 显示详细输出

```bash
export PYTHONPATH=src
python -m pytest tests/ -v -s --tb=long
```

## 4. 预期输出

全部测试通过时输出示例：

```
============================== 35 passed, 1 warning in 0.80s ========================
```

- 18 个单元测试 + 17 个冒烟测试 = 35 个测试
- 执行时间约 0.3~1.0 秒（无网络调用，纯本地执行）

## 5. 测试失败排查

| 问题 | 排查步骤 |
|------|----------|
| ModuleNotFoundError: deeprca | 确认 `PYTHONPATH=src` 已设置 |
| Import error: langgraph | 运行 `pip install -e ".[dev]"` 重新安装 |
| async test not running | 确认 `pyproject.toml` 中 `asyncio_mode = "auto"` |
| Python version error | 确认 `python --version` >= 3.10 |
| graph compile error | 检查 `src/deeprca/graph/main_graph.py` 是否完整 |

## 6. Mac 远端测试快速指南

在 Mac 机器上从远端拉取代码并测试的完整步骤：

```bash
# 1. 克隆仓库
git clone <仓库地址>
cd DeepRCA-Agent

# 2. 切换分支
git checkout feature/xianhuimeng/20260714/general-analyzer

# 3. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 4. 安装依赖
pip install -e ".[dev]"

# 5. 配置环境变量
cp .env.example .env
# 编辑 .env，设置 MOCK_ENV_ENABLED=false（冒烟测试不需要 Mock API）

# 6. 运行全部测试
export PYTHONPATH=src
python -m pytest tests/ -v --tb=short

# 7. 预期结果
# 35 passed (18 unit + 17 smoke)
```

## 7. 里程碑完成记录

### M2 完成（PRD-02 通用分析 Agent）— 2026-07-14

**已完成模块：**
- 6 节点工作流：intake → planner → dispatcher → collector → root_cause → reporter
- 6 维度分析工具：metrics / error_logs / recent_changes / trace / related_alerts / topology
- 5 个 API 端点：POST /analyze / GET /status / GET /result / WS /stream / POST /feedback
- 按告警类型推导时间窗口和维度组合
- 全部维度失败降级模式
- 报告 suggestions + satisfaction_url + 推送通知
- 告警格式验证（400）
- COORDINATOR_SYSTEM_PROMPT

**测试覆盖：**
- 18 个单元测试（6 节点功能 + 时间窗口 + 维度映射 + 降级模式）
- 17 个冒烟测试（4 种告警类型端到端 + 报告字段 + 时间窗口 + 关联服务 + 降级模式）
- 全部 35 个测试通过，执行时间 ~0.8s

**已知限制：**
- LLM 降级为 fallback 模式（无 API Key 时使用规则引擎）
- 工具调用会失败（无 Mock API），dispatcher 自动触发降级模式
- `tests/integration/` 目录为空，API 端到端测试待后续 PRD 补充
- Docker 容器化测试待 PRD-06 补充