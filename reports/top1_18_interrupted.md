# 冻结准确率实验中断记录

运行：`artifacts/otel_demo_rca_v1/20261009T075236Z-8e135c`。状态 **INTERRUPTED / NOT ACCEPTED**。

2026-10-09 18:58:09（Asia/Shanghai）在 `memory-growth/repeat-02` 的恢复查询阶段失败，程序以 `Trial or recovery incomplete; stop instead of contaminating the next pair` 安全终止。没有启动新一轮实验，也没有补跑未执行的最后一个生命周期。

| 模式 | 已评分故障 Top1 | 已评分 Normal |
| --- | --- | --- |
| Full adaptive | 14/17 | 3/3 |
| Fixed full | 15/17 | 3/3 |
| Adaptive without L2 | 12/17 | 3/3 |

这些是中断前的部分评分，不能作为 18/18 完整验收。计划 21 个生命周期、63 次诊断；实际写入 20 个生命周期索引、60 次诊断。最后一个已写入生命周期的 `recovery_queries_success=false`、`symptom_recovery_confirmed=null`；索引中单独的 `status=completed` 不代表恢复通过。尚未执行 `memory-growth/repeat-03`。

没有 `final_verification.json`，也没有完整发布的独立验收通过记录。源代码及配置在 2026-10-10 人工合入新实现前逐文件 SHA256 核验仍与冻结 manifest 一致。新的三终态实现未参加该实验，不能混合两者的验证结论。

自动收尾脚本因实验中断退出，自动合入脚本按保护条件停止。原始数据保留，实验状态保持 interrupted；没有修改评分器、Ground Truth 或注入参数。用户于 2026-10-10 要求将已完成代码和当前记录合入并上传；没有继续调参或运行新实验。

源码快照、配置、逐例报告、分数、调用统计及恢复检查随本次更新归档。原始 telemetry 与 ToolResult 留在本地，未包含 `.env`、凭证或虚拟环境。
