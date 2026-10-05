# OpenTelemetry 官方近真实环境 RCA v1 正式消融

OpsPilot 没有企业生产数据。所有表格读取本次真实实验 artifact；负结果、API 失败和 fallback 均保留。

运行 `20261005T032959Z-d57607`，状态 `interrupted`；保存的实际 RCA 12 次，其中完整恢复后纳入正式配对统计 9 次。失败/重试工件不删除，重复同一场景/模式/编号时采用最近完整 trial。工件 `artifacts/otel_demo_rca_v1/20261005T032959Z-d57607`。

## Environment / 冻结配置

Upstream `7ea09b865a9b9d411b59459b15133d84f8c2784a`；Demo 3.1.0，Collector 0.160.0。OpsPilot HEAD `fcc74f5c857d7a52f702a542f43b4ce3bb646c06` 加不可变 source_manifest/source archive；未提交源码纳入快照，没有覆盖用户工作区。
Dataset `otel-demo-rca@1.0.1`；experiment `otel-demo-rca-release@1.0.0`；固定官方 Locust 5 用户、spawn=1。release 所有时间参数保存在 config/dataset/各 trial 的 scenario.json。
真实 Planner 模型 `deepseek-v4-flash`，temperature=0，max_tokens=500，attempts=1；原始 response usage 已采集，未配置可靠单价，Estimated Cost=N/A。

## Fault Dataset / Modes

6 faults + Normal，Ground Truth 仅 Controller/scorer；Engine 只收到随机 ID symptom Alert 与脱敏真实工具数据。changes.query 不读取 flags。
Full Adaptive：现有 Coordinator/L1/Gate + 真实 LLM Planner，记录未触发 Planner 的 Gate 提前结束和 fallback；Fixed Full：串行实际执行全部 13 工具、同一 Evidence/Ranker/Gate，无 LLM、无 Expert 调用；No-L2：同一真实 Planner，Expert budget=0。所有模式使用相同确定性排序和摘要，不使用 LLM 改写候选。
配对设计：每次注入/恢复周期，三个模式分别对同一个固定 Alert 时间窗发起真实 backend queries，顺序按重复号轮换。21 生命周期对应 63 次真实 RCA；不是把一个结果复制三份。工具请求次数、底层 retries、API calls 与语义 Tool calls 分开保存。Controller 为测量完整遥测另做的全工具采集不计入任何模式的 RCA 调用成本。

## Overall results

| Mode | Scored | Fault Top1 | Fault Top3 | Normal | Gate | Budget | Degraded | Avg Tool | Avg Expert | Avg rounds | Avg LLM | P50 ms | P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| full_adaptive | 3 | N/A | N/A | 1/3 | 1/3 | 2/3 | 0/3 | 5.33 | 0.67 | 3.33 | 2.33 | 10045.57 | 11422.02 |
| fixed_full | 3 | N/A | N/A | 0/3 | 1/3 | 0/3 | 0/3 | 13.00 | 0.00 | 1.00 | 0.00 | 13691.07 | 16060.28 |
| adaptive_no_l2 | 3 | N/A | N/A | 1/3 | 1/3 | 2/3 | 0/3 | 5.33 | 0.00 | 3.33 | 2.33 | 7961.16 | 7978.70 |

Fixed 的 Budget=0 表示没有自适应预算中止；执行 13 工具超出 Adaptive 的 8 工具预算是本 baseline 定义，不能声称相同成本约束。Gate pass 与 Top1 正确是独立指标。

## Per-scenario results

| Scenario | Mode | Fault observed | Top1 | Top3 | Normal | Gate | Budget | Degraded | Tool/Expert/Rounds/LLM avg | P50/P95 ms |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| normal | full_adaptive | 0 | N/A | N/A | 1/3 | 1/3 | 2/3 | 0/3 | 5.33/0.67/3.33/2.33 | 10045.57/11422.02 |
| normal | fixed_full | 0 | N/A | N/A | 0/3 | 1/3 | 0/3 | 0/3 | 13.00/0.00/1.00/0.00 | 13691.07/16060.28 |
| normal | adaptive_no_l2 | 0 | N/A | N/A | 1/3 | 1/3 | 2/3 | 0/3 | 5.33/0.00/3.33/2.33 | 7961.16/7978.70 |

## Adaptive vs Fixed / L2 Expert ablation

- full_adaptive 相对 Fixed：Fault Top1 差值 N/A 个百分点；Tool calls 减少 58.97%；P50/P95 latency 差值 -3645.50/-4638.26ms。
- full_adaptive 真正使用 Planner 的运行 3/3，fallback 运行 0；API 成功/失败=7/0，有效决策=7，fallback 决策=0；已记录 usage 7/7，tokens={'prompt_tokens': 3207, 'completion_tokens': 298, 'total_tokens': 3505}。
- adaptive_no_l2 相对 Fixed：Fault Top1 差值 N/A 个百分点；Tool calls 减少 58.97%；P50/P95 latency 差值 -5729.91/-8081.58ms。
- adaptive_no_l2 真正使用 Planner 的运行 3/3，fallback 运行 0；API 成功/失败=7/0，有效决策=7，fallback 决策=0；已记录 usage 7/7，tokens={'prompt_tokens': 3166, 'completion_tokens': 413, 'total_tokens': 3579}。

本次 Fault Top1 最高：['full_adaptive', 'fixed_full', 'adaptive_no_l2']；平均 Tool calls 最低：['full_adaptive', 'adaptive_no_l2']；P50 latency 最低：adaptive_no_l2。Fixed LLM calls=0；这些成本维度不能未经单价配置合成现金成本。

各场景有价值/更稳定的判断以逐场景 Top1/Top3、调用数和延迟为依据。Expert 是按需实际调用，不能将 Fixed 的直接域工具查询算作 Expert 调用。三个重复仅为初步配对结果，没有统计显著性结论；LLM API 数量降低不等于现金成本降低。

## Failure analysis

错误 Top1 7 次；主要观测条件计数 `{'E': 3, 'unknown': 2, 'G': 2}`；多标签计数 `{'E': 3, 'unknown': 2, 'G': 2}`。完整证据与对应文件见 failure_analysis.json/md。自动分类不调用 LLM，条件共现不证明唯一因果，无法确定的因果标为 unknown。

## Limitations

类别评分，不宣称精确服务定位。paymentUnreachable 的连接/DNS ERROR 不等于 timeout；DB_SLOW_QUERY 不等于锁竞争机制；内存增长不等于 OOM，email 人工恢复重启另记。Controller 的无故障 baseline 与 Engine 包含稳定阶段的历史窗口不同；保持前版 release 窗口冻结，后续单独消融。
部分 deployment/topology/DB/cache 指标缺失；trace/log 窗口限额和稀疏流量影响采样。官方负载还包含未部署可选 agent 的 /prompt 背景请求失败。Normal 仅表示固定负载下的无故障注入状态。三个模式共享同一生命周期但实际发起查询，查询顺序轮换仍不能排除异步采样差异。

## Knowledge Tool decision

INCONCLUSIVE

尚无经核验的历史 incident/runbook 证明增量区分信息；本轮还存在工具选择、排名、Gate、预算、采样/窗口或 taxonomy 限制，不能从错例直接推断需要 RAG。

当前 Evidence/错例逐项见 failure_analysis.json；外部知识候选仅为待核验设想，未证明实时 Evidence 无法区分，也没有把 feature flags 或 benchmark 答案当作知识库。
先修复并冻结 telemetry/window/tool selection/ranking 问题，再核验独立脱敏知识文档；若五项标准满足，单独做 Without/With Knowledge，比较 Top1/Top3、Tool Cost、Latency，知识不能直接决定 RootCause。

本任务没有开发/接入 EvalRAG 或 Knowledge Tool。


## ?????????

{"status": "interrupted", "error_type": "RuntimeError", "error": "Trial or recovery incomplete; stop instead of contaminating the next pair", "at": "2026-10-05T04:00:51.775773+00:00"}

CPU ???????????? utilization gauge ????????????????? run ????????????????? scoped CPU ??????? rate????? gauge/???/JVM/Docker ?????????????????

{"baseline": 0.00820755245314719, "incident": 1.0, "recovery": 0.007702542152529153, "recovery_limit": 0.25615566433986037, "fault_effect": true, "recovered": true, "at": "2026-10-05T04:09:02.176899+00:00"}
