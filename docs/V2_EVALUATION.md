# OpsPilot V2 评测记录

本记录对应 2026-11-15 本机代码。V1 与五种 V2 消融使用同一 `benchmarks/datasets/rca/v1` 旧 Mock snapshot 数据集和相同调查轮数/工具/Expert 上限。`dev` 25 例（故障 21），冻结 `test` 12 例（故障 10）。模型关闭，因而所有变体的模型调用和 token 都为 0；这些数字不能代表 LLM 开启后的成本或生产遥测效果。原冻结 test 曾在较早实现上运行一次；此表是在功能修复后重新运行的最终代码结果，未用 test 集调参。

| 配置 | dev Top1 | test Top1 | test 确认覆盖 | test 拒判 | test 平均工具数 | test p95 毫秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| V1 baseline | 6/21 | 2/10 | 0/10 | 12/12 | 5.67 | 1.67 |
| V2 dynamic（单任务，无 Skill/native） | 7/21 | 3/10 | 0/10 | 12/12 | 5.67 | 9.03 |
| V2 multi-agent（并行，无 Skill/native） | 8/21 | 3/10 | 0/10 | 12/12 | 6.00 | 9.10 |
| V2 + Skills（并行/native） | 7/21 | 3/10 | 0/10 | 12/12 | 6.00 | 9.57 |
| V2 + MCP（并行，无 Skill） | 8/21 | 3/10 | 0/10 | 12/12 | 6.00 | 458.05 |
| V2 full（并行、Skill、MCP） | 7/21 | 3/10 | 0/10 | 12/12 | 6.00 | 460.97 |

结果目录分别为 `artifacts/evaluations/v2_ablation/20261114T161759Z-opspilot_hybrid-dev` 至 `20261114T161817Z-opspilot_v2_full-test`，每组保存 manifest、逐例 predictions、failures、metrics 和报告。`test` 的错误确认率分母为 0（没有任何确认），不能解释成 0% 错误确认。任务完成率在 test 上为 0，需求覆盖也为 0；旧 snapshot 缺少多项范围/健康参考，三终态安全规则因此全部拒判。V2 在此数据上的 Top1 只多 1 个故障命中，却增加工具调用与延迟；Skill 没有带来命中收益。MCP 进程内协议调用增加明显开销，不能宣传性能收益；当前 MCP 只覆盖通用观测工具，领域工具仍为原生调用。p95 是进程内评测延迟，不含队列、外部网络和生产负载。

复现命令：

```powershell
$names = @('baseline','dynamic','multi_agent','skills','mcp','full')
foreach ($split in @('dev','test')) {
  foreach ($name in $names) {
    .\.venv\Scripts\python.exe -m opspilot.evaluation.cli run --config "benchmarks/configs/v2_$name.yaml" --split $split
  }
}
```

另外，`scripts/v2_demo.py` 的三例是确定性合成情景，验证任务分派、DB 正常反证、证据板和计划变化，不参与准确率表。实际 OpenTelemetry Astronomy Shop 启动后，`tests/integration/test_otel_demo_live.py` 为 **1 passed**；`scripts/v2_live_probe.py` 在真实只读遥测上得到 metrics/traces/changes/logs/rpc 结果、两条 Evidence 和 `INCONCLUSIVE`，未注入故障，没有可评分 Ground Truth。可选 DeepSeek 演示真实执行过两次 Specialist 决策，服务端记录 681 tokens；该单次合成调用不是模型准确率实验。

最终全量自动化测试在真实 PG/Redis 测试容器可用时为 **383 passed、1 skipped**，其中 V2 真实 API→Redis→Worker→PG 结果链路与崩溃恢复测试为 **2 passed**。通过次数不等于生产高可用保证。2026-11-21 本机 V2 Compose 构建并启动成功，PostgreSQL、Redis、Mock、API 健康，Worker 运行并处理了 Run；实际 API 提交 Run `run-c590103d7ab84e4fba4631283883129b` 后状态为 `SUCCEEDED`，报告为 `INCONCLUSIVE`，4 轮、7 次工具调用，容器设置为 V2 graph version 7 / checkpoint schema 4.0。Windows 宿主机 5432 无法绑定，使用 `OPSPILOT_POSTGRES_HOST_PORT=15433`；镜像健康检查使用 Python 标准库，避免构建依赖 Debian apt 软件源。当前仍需改善有充分证据时的确认覆盖，并进一步验证远程 MCP、真实故障注入、多 Worker 长时间负载与模型成本。
