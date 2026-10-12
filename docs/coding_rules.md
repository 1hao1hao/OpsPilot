# OpsPilot 开发规则

更新：2026-10-12。旧迁移规则保存在 [历史档案](history/20261012/coding_rules.md)。当前架构见 [architecture.md](architecture.md)。

## 技术与分层

Python 3.11+、src layout、Pydantic v2、FastAPI、SQLAlchemy/Alembic、pytest 和 Ruff。当前调查循环由 AdaptiveInvestigator 驱动；graph/workflow.py 是离线包装，不新增平行 LangGraph 诊断链。

API → Runtime → Investigation / Agents → Tools / Evidence / RCA；Runtime → Persistence / Queue。公开 models 不依赖 ORM 或具体工具。业务调查不直接操作数据库与队列；外部观测经过 provider、Registry 和 Executor。新代码放入 src/opspilot，src/deeprca 只保留 Mock 相关能力。

## 契约与结论

跨模块数据使用显式 schema；区分输入、输出、ORM 与公开响应。枚举或持久化结构变更要核对 API、旧报告、Checkpoint 与 artifact reader，必要时新增迁移。

Evidence 描述观测并保留来源与支持/反驳引用，排名不能替代最终确认。成功无数据与工具失败分开；缺数据、缺基线和预算耗尽不得作为健康证据。Verdict 由确定性代码决定，LLM 只规划或解释，不能改变排名、标签或确认状态。

Report schema 3.0；历史报告缺少确认字段时保留未确认。Checkpoint schema/graph 版本不匹配拒绝读取；同版本缺少新字段的调查状态通过默认值恢复并重新判断。临时 Top-K 与 confirmed_root_cause 的含义不能混用。

## 工具、执行与恢复

所有注册工具只读且 side_effect=false，统一 schema 校验、timeout、有限 retry 和错误归一。稳定 tool_call_id 与持久化成功结果支持重试复用，request_id 防止重复创建 Run。General 与 Domain 动作权限经 ActionValidator 校验，领域调用经 Expert，禁止绕过白名单或预算。

PostgreSQL 是事实源，Redis 只传 run_id。Step 与 Checkpoint 的持久化边界保持一致；恢复不序列化连接或协程，不丢失诊断决策历史。路由不等待完整 RCA。时间使用带时区 UTC，数值单位明确，不将 secret 放入工具参数、事件或提交工件。

## 验证与维护

默认测试不访问付费 LLM 或自动注入真实故障；优先使用 fixtures、已有 ToolResult 与必要快回归。真实部署、故障生命周期和付费实验按明确任务执行，未授权不启动新轮。frozen dataset、答案和评分器不为结果好看而修改，不删除失败分母、不手填 predictions。

历史实验、离线回放、完整真实发布、中断部分评分与生产环境分别说明。统计原始 Top1、确认率、错误确认率、拒判率与 NO_FAULT 误判；零确认时的条件错误率为未定义。目标与已验证能力分开，并保留可追溯工件。

只提交当前任务涉及的文件，保留无关工作区修改；不用 reset --hard 或 clean 清理现场。历史规划文本不作为当前实现事实，README、架构、工具、API 与验收范围需同步维护。
