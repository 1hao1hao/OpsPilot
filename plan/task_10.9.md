## OpsPilot：三终态决策机制重构

实施记录：三终态实现、快速回归和既有真实工件离线回放已完成；详见 `reports/task_10.9.md`。没有启动新一轮真实环境实验；线上覆盖率验证仍未执行。

仓库：https://github.com/1hao1hao/OpsPilot

基于当前代码，一次性完成可靠的 `CONFIRMED / NO_FAULT / INCONCLUSIVE` 三终态机制。重点是建立完整的证据驱动决策逻辑，而非简单增加枚举或固定阈值判断。

### 1. 核心设计

**动态证据需求（Evidence Requirements）**

- 根据 Alert 的异常类型、目标服务、已有 Evidence 和 Top-K 候选生成调查所需的证据需求。
- 每轮调查后动态更新：哪些异常已验证、哪些候选仍需区分、哪些关键观测缺失。
- 区分有效观测、正常观测、无数据、工具失败、缺少基线；不能把“没有查到异常”直接等同于正常。
- 复用现有 Coordinator / Planner / ToolRegistry / Evidence Engine，必要时将未满足的证据需求反馈给 Planner，指导后续调查。
- 不要求调用全部工具，也不只凭 Alert 类型固定一份覆盖清单。

**故障存在性判断（Fault Presence）**

独立于根因排名，确定性判断：

- `PRESENT`：有效观测证实告警范围内存在实质异常。
- `ABSENT`：关键观测覆盖充分，具有可信健康参考，且能支持告警所描述的异常未发生。
- `UNKNOWN`：观测缺失、质量不足或证据矛盾，无法判断。

必须支持真实 Metrics/Traces/Logs 的证据关联；不能以 Evidence 为空、Tool 调用成功或低置信 Top1 推断 `ABSENT`。`NO_FAULT` 只针对当前告警及观测窗口，不代表整个系统完全正常。

**根因可信度与最终 Verdict**

- `CONFIRMED`：故障存在，且根因通过 Evidence Gate，关键矛盾已处理。
- `NO_FAULT`：故障存在性被充分证据判定为 `ABSENT`。
- `INCONCLUSIVE`：其他无法可靠下结论的情况，包括预算耗尽、缺少关键观测、故障存在但根因未确认等。

**预算耗尽不等于调查充分；Top1 排名不等于已确认根因。**

### 2. 工程集成

- 将执行状态（completed/failed）与诊断结论（verdict）分离。
- 将证据需求、覆盖状态、Fault Presence、Verdict 及判断理由记录进 Trace/Report。
- `INCONCLUSIVE` 可以保留临时 Top-K，但不能将其标记为确认根因。
- 保持现有 Checkpoint、恢复、API 和报告消费者的兼容性，必要时修改 Schema。
- 最终 Verdict 必须由确定性代码判断，LLM 只参与调查规划。
- 不重构 Runtime，不调整现有根因排序权重，不引入新 Agent。

### 3. 验证要求（严格控制耗时）

**禁止重新运行完整 Astronomy Shop 真实故障生命周期。**

优先复用现有真实 ToolResult、Evidence、Trace 和 Benchmark fixtures 进行离线回放，并补充针对性单元/集成测试，覆盖：

1. 已确认故障 → CONFIRMED
2. 有效观测证实正常 → NO_FAULT
3. 故障存在但根因不确定 → INCONCLUSIVE
4. 数据缺失、工具失败、基线不足、矛盾证据 → INCONCLUSIVE
5. 预算耗尽、Checkpoint 恢复及历史报告兼容性

验证不允许使用 Ground Truth 或故障注入 flag 参与在线决策；不修改 Benchmark 答案及评分规则。

区分原始 Top1 命中、最终确认率、错误确认率与拒判率，避免靠增加 INCONCLUSIVE 虚假提高准确率。

仅运行必要的快速回归测试与 Ruff；无需重新部署 28 个微服务或运行耗时的真实 LLM 消融。若已有工件不足，明确说明，不能伪造验证结果。

### 4. 交付

完成实现和测试后，只汇报：

1. 三终态的最终判定规则
2. 动态证据需求如何更新，以及何时停止调查
3. 改动的核心模块
4. 测试结果和现有真实 Case 的离线回放结果
5. 尚未进行的真实环境验证

自主完成，不需要逐步骤向我确认；不要进行与本任务无关的优化或无限调参。
