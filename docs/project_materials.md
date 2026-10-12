# OpsPilot 项目说明与证据口径

更新：2026-10-12。本文面向项目介绍与技术交流；当前运行说明见 [README](../README.md)，旧材料保存在 [历史档案](history/20261012/project_materials.md)。

## 项目介绍

OpsPilot 把“调查什么”“证据支持什么”“任务能否可靠执行”分开：LLM 在允许动作和预算内选择工具；确定性分析生成证据、排名和三终态；独立 Worker 持久化任务并支持中断恢复。

一次请求从 Run API 进入 PostgreSQL 和 Redis 队列，Worker 调用 AdaptiveInvestigator。Coordinator 选择 Seed；工具结果进入同一 Evidence/异常检测管线；每轮更新候选、存在性、需求覆盖和矛盾，再决定继续测量或返回 Verdict。最终报告由共享构造函数生成，执行细节保留在事件中。

## 可说明的设计亮点

| 设计 | 代码与证据 | 实际限制 |
| --- | --- | --- |
| 受约束调查规划 | Planner、ActionValidator、只读 Registry；需求反馈与 Fallback | 可选 LLM，有预算限制，不能保证调查覆盖所有根因 |
| 证据与结论分离 | collector、AnomalyDetector、Ranker、decision.py；三终态报告 | 高拒判率反映缺观测，不能转换成准确率改善宣传 |
| 可恢复执行 | TaskManager、Worker、Repository、Checkpoint、稳定 ToolCall ID | schema/graph 版本不兼容时拒绝恢复；不声明任意故障下 exactly-once |
| 可追溯评测 | 固定场景、逐例分数、冻结源码、真实请求与隔离标签 | Mock、Demo、离线回放与生产数据的结论不能混用 |

## 数字证据

| 记录 | 可使用的表述 |
| --- | --- |
| [完整 Demo 发布](../reports/top1_18_iteration1.md) | Full Top1 16/18，Normal 3/3；21 个恢复通过的生命周期、63 次诊断；比 Fixed 少 57.88% 工具调用，未达到 18/18 |
| [中断发布](../reports/top1_18_interrupted.md) | 60 次诊断；Full 部分 14/17；恢复失败，未通过最终验收 |
| [三终态离线验收](../reports/task_10.9.md) | 126 次已有工件回放；最新一批 Full 确认 3/21、拒判 18/21、错误确认 0/21；原始 Top1 不变 |
| [人工合入验证](../artifacts/tri_state/manual_integration.json) | 2026-10-10 针对性测试 68 passed，Ruff 通过；不是新的 live 实验 |

旧三路 DeepSeek frozen-test、受控并发与 Runtime Reliability 数字只描述当时版本及固定测试条件；原始工件继续保留，不能写成当前自适应版本的新实验结果。旧 Mock 6/21 覆盖率也未被本次三终态工作修复。

介绍时可以说“实现动态证据需求、三终态决策与可恢复调查，并保留逐例验证记录”；不能说“生产准确率 100%”“最新达到 18/18”“零错误确认代表整体全命中”“自动修复生产故障”，也不能把缓存回放写成新真实部署验收。
