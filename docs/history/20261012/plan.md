```
请基于当前 OpsPilot 仓库，设计并执行一次“Fixed Investigation vs Adaptive Investigation”的公平对照实验。

目标：
验证 Evidence-Guided Adaptive Investigation 是否在保持/提升 RCA 质量的同时，减少无效调查开销。

重点先检查：
- benchmarks/configs/fixed_planner.yaml
- benchmarks/configs/adaptive_planner.yaml
- benchmarks/configs/adaptive_without_dynamic_l2.yaml
- benchmarks/configs/full_adaptive_rca.yaml
- benchmarks/datasets/rca/v1
- src/opspilot/evaluation/
- 现有 artifacts/evaluations/

第一步：
先检查仓库是否已经存在足够的对应实验 artifact。
如果已有可直接比较的、同版本、同 dataset split 的结果，优先分析现有结果，不要无意义重复跑实验。

如果需要重新运行：
1. 使用完全相同的数据集和 split。
2. 至少比较：
   A. fixed_planner
   B. full_adaptive_rca
3. 如果现有实现支持，再加入：
   C. adaptive_planner
   D. adaptive_without_dynamic_l2
   用来区分 Adaptive Planner 和 Dynamic L2 各自的贡献。
4. 不调用真实 DeepSeek API，先评估系统调查策略本身，避免外部模型随机性和网络延迟干扰公平性。
5. dev 和 frozen-test 分开报告，不要用 frozen-test 调参。

需要统计：

【Effectiveness / 诊断质量】
- Hit@1
- Hit@3
- Evidence Recall
- FPR
- 每个 case 的预测结果

【Efficiency / 调查开销】
- Avg Tools
- Avg Experts
- Avg Rounds
- 每个 case 的 Tool Calls
- 每个 case 的 Expert Calls
- 如果现有框架有可靠 latency 数据，则统计平均/P95 investigation latency；如果没有，不要虚构。

额外分析：
1. Adaptive 相比 Fixed：
   - Tool 调用减少多少百分比
   - Expert 调用减少多少百分比
   - Round 是否增加
   - RCA 指标有没有下降
2. 按 root cause 类型拆分，看看哪些 case Adaptive 更省，哪些 case 反而调查更多。
3. 检查“减少 Tool”是否只是因为提前预算耗尽，而不是证据更充分。
4. 输出典型案例：
   - 一个 Adaptive 明显少查工具但仍正确的 case
   - 一个 Adaptive 需要继续深挖才能正确诊断的 case
   - 一个失败/退化 case（如果存在）

最终输出一个 Markdown 报告，不要修改 README 和简历。

报告最后给出非常严格的结论：
- 如果 Adaptive 在质量不降的前提下明显降低 Avg Tools / Avg Experts，可以建议简历使用什么量化表述。
- 如果只提升质量但没有降低调查量，就明确说不能宣传“节省成本”，应该宣传“证据驱动的动态调查/减少无关领域调用”。
- 如果两者都没有明显改善，也直接说明，不要包装。

不要为了得到漂亮数字修改 benchmark、threshold 或 evaluation 逻辑。    
```