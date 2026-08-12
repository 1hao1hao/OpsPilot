"""L1 通用分析 Agent — Coordinator 6 节点函数实现。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：intake/planner/dispatcher/collector/root_cause/reporter 6 节点</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3, §3.4.1-3.4.2</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from deeprca.config import get_settings
from deeprca.models import (
    AnalysisReport,
    Evidence,
    EvidenceLevel,
    EvidencePool,
    ParsedAlert,
    RootCauseCandidate,
    RootCauseResult,
    SubAgentResult,
)

__all__ = [
    "intake_node",
    "planner_node",
    "dispatcher_node",
    "collector_node",
    "root_cause_node",
    "reporter_node",
    "check_timeout",
    "COORDINATOR_SYSTEM_PROMPT",
]

logger = logging.getLogger(__name__)

# PRD-02 §3: Coordinator Agent System Prompt
COORDINATOR_SYSTEM_PROMPT = """你是一个故障诊断智能体系统的通用分析 Agent（Coordinator Agent）。

你的职责是：
1. 接收告警事件，理解故障上下文
2. 拆解分析任务，覆盖六个维度：变更、上游流量、下游依赖、集群状态、错误日志、已知问题
3. 并发调度领域专家子 Agent 执行下钻分析
4. 汇聚分析结果，构建证据链
5. 生成结构化分析报告

分析原则：
- 优先检查变更维度：90% 的故障由变更引起
- 并发分析：六个维度同时执行，不串行等待
- 证据驱动：每个结论必须有工具调用结果作为证据
- 置信度量化：所有发现需标注置信度（0.0~1.0）
- 容错优先：单个维度失败不影响整体分析

输出要求：
- 根因结论需包含：结论 + 置信度 + 证据链
- 建议措施需可执行、有优先级
- 报告格式遵循 JSON Schema

当前告警信息：
- 服务: {service_name}
- 告警类型: {alert_type}
- 严重程度: {severity}
- 描述: {description}
- 时间: {timestamp}
"""

# 维度 → 分析函数映射（在模块加载时延迟导入，避免循环依赖）
_DIMENSION_MAP: dict[str, str] = {
    "change": "analyze_change",
    "upstream": "analyze_upstream",
    "downstream": "analyze_downstream",
    "cluster": "analyze_cluster",
    "errorlog": "analyze_errorlog",
    "problem": "analyze_problem",
}

# ThreadPoolExecutor（全局单例，预留供 dispatcher_node 切换为线程池模式使用）
# 当前 dispatcher_node 使用纯 asyncio.gather 模式，_get_executor() 暂未调用
_executor: ThreadPoolExecutor | None = None


def _get_executor() -> ThreadPoolExecutor:
    """获取全局线程池单例（当前未使用，保留供未来扩展）。"""
    global _executor
    if _executor is None:
        settings = get_settings()
        _executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_tasks)
    return _executor


def _now_iso() -> str:
    """当前时间 ISO 8601。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _parse_iso(ts: str) -> datetime:
    """解析 ISO 8601 时间戳。"""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


# ─────────────────────────────────────────────
# B01: intake — 告警解析
# ─────────────────────────────────────────────
def _derive_time_window(alert_type: str, alert_ts: str) -> tuple[str, str]:
    """根据告警类型推导分析时间窗口。

    PRD-02 §2.1: 不同告警类型使用不同的 before/after 窗口。
    """
    windows = {
        "timeout": {"before": 30, "after": 5},
        "error_rate": {"before": 15, "after": 5},
        "resource": {"before": 60, "after": 5},
        "custom": {"before": 30, "after": 5},
    }
    window = windows.get(alert_type, windows["custom"])
    alert_dt = _parse_iso(alert_ts)
    window_start = (alert_dt - timedelta(minutes=window["before"])).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    window_end = (alert_dt + timedelta(minutes=window["after"])).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    return window_start, window_end


def _extract_related_services(alert: dict) -> list[str]:
    """从告警描述和标签中推导关联服务列表。"""
    services = set()
    # 从 labels 提取
    for key in ("app", "application", "related_service", "dependency"):
        val = alert.get("labels", {}).get(key)
        if val:
            services.add(val)
    # 从 description 提取服务名（简单启发式：xxx-service 模式）
    import re
    desc = alert.get("description", "")
    for match in re.findall(r"\b([a-z][a-z0-9-]*-service)\b", desc):
        services.add(match)
    # 告警服务本身
    if alert.get("service_name"):
        services.add(alert["service_name"])
    return sorted(services)


def intake_node(state: DeepRCAState) -> dict:
    """接收告警事件，提取关键字段并标准化。

    输入: state["alert"] = AlertEvent 序列化 dict
    输出: {"alert": ParsedAlert dict, "trace_id": str, "start_time": str, "status": "running"}
    """
    raw_alert = state.get("alert", {})

    # 提取标签
    labels = raw_alert.get("labels", {})
    cluster = labels.get("cluster") or labels.get("cls")
    env = labels.get("env") or labels.get("environment")
    app = labels.get("app") or labels.get("application") or raw_alert.get("service_name")

    # 按告警类型推导时间窗口
    alert_type = raw_alert.get("alert_type", "custom")
    alert_ts = raw_alert.get("timestamp", _now_iso())
    window_start, window_end = _derive_time_window(alert_type, alert_ts)

    # 推导关联服务列表
    related_services = _extract_related_services(raw_alert)

    parsed = ParsedAlert(
        alert_id=raw_alert.get("alert_id", str(uuid.uuid4())),
        service_name=raw_alert.get("service_name", "unknown"),
        alert_type=alert_type,
        severity=raw_alert.get("severity", "P2"),
        timestamp=alert_ts,
        description=raw_alert.get("description", ""),
        labels=labels,
        time_window_start=window_start,
        time_window_end=window_end,
        cluster=cluster,
        env=env,
        app=app,
    )

    # 优先使用 routes.py 已生成的 trace_id，仅在不存在时才生成新的
    trace_id = state.get("trace_id", "") or f"trace-{uuid.uuid4().hex[:12]}"

    # 优先使用 routes.py 已设置的 start_time，仅在不存在时才生成新的
    start_time = state.get("start_time", "") or _now_iso()

    return {
        "alert": parsed.model_dump(),
        "trace_id": trace_id,
        "start_time": start_time,
        "status": "running",
        "related_services": related_services,
        "messages": [f"[intake] 解析告警: service={parsed.service_name}, severity={parsed.severity}"],
    }


# ─────────────────────────────────────────────
# B02: planner — 任务拆解
# ─────────────────────────────────────────────
# PRD-02 §2.2: 分析维度定义
ANALYSIS_DIMENSIONS: dict[str, dict] = {
    "change": {
        "name": "变更分析",
        "description": "检查最近变更（部署、配置、扩缩容）是否与告警时间吻合",
        "tools": ["query_recent_changes"],
        "priority": 1,
        "time_window": "24h",
    },
    "upstream": {
        "name": "上游流量分析",
        "description": "分析上游调用方 QPS、流量分布是否异常",
        "tools": ["query_metrics", "query_topology"],
        "priority": 2,
        "time_window": "1h",
    },
    "downstream": {
        "name": "下游依赖分析",
        "description": "分析下游依赖服务（DB/Redis/Mafka/RPC）健康状态",
        "tools": ["query_metrics", "query_trace", "query_topology"],
        "priority": 3,
        "time_window": "1h",
    },
    "cluster": {
        "name": "集群状态分析",
        "description": "检查集群资源（CPU/Memory/Network/Pod）是否异常",
        "tools": ["query_metrics", "query_topology"],
        "priority": 4,
        "time_window": "1h",
    },
    "errorlog": {
        "name": "错误日志分析",
        "description": "扫描 ErrorLog，提取异常堆栈和错误模式",
        "tools": ["query_error_logs"],
        "priority": 5,
        "time_window": "30m",
    },
    "problem": {
        "name": "已知问题匹配",
        "description": "匹配已知问题库（历史故障、FAQ、SOP）",
        "tools": ["query_related_alerts"],
        "priority": 6,
        "time_window": "7d",
    },
}

# PRD-02 §2.2: 告警类型 → 维度映射表
_ALERT_TYPE_DIMENSION_MAP: dict[str, list[str]] = {
    "timeout": ["change", "downstream", "cluster", "errorlog", "upstream", "problem"],
    "error_rate": ["change", "downstream", "errorlog", "cluster", "upstream", "problem"],
    "resource": ["cluster", "change", "errorlog", "problem"],
    "custom": list(ANALYSIS_DIMENSIONS.keys()),
}


def planner_node(state: DeepRCAState) -> dict:
    """任务拆解，根据告警类型生成对应维度的分析计划。

    输入: state["alert"] = ParsedAlert dict
    输出: {"task_plan": list[TaskPlan]}
    """
    settings = get_settings()
    alert = state.get("alert", {})
    service_name = alert.get("service_name", "unknown")
    window_start = alert.get("time_window_start", "")
    window_end = alert.get("time_window_end", "")
    alert_type = alert.get("alert_type", "custom")

    # 按告警类型选择分析维度
    dimensions = _ALERT_TYPE_DIMENSION_MAP.get(alert_type, _ALERT_TYPE_DIMENSION_MAP["custom"])

    task_plan: list[dict] = []

    for dim in dimensions:
        config = ANALYSIS_DIMENSIONS[dim]
        task_plan.append({
            "task_id": f"task-{alert.get('alert_id', 'unknown')}-{dim}",
            "dimension": dim,
            "name": config["name"],
            "description": config["description"],
            "tools": config["tools"],
            "time_window": config["time_window"],
            "priority": config["priority"],
            "status": "pending",
            "params": {
                "service_name": service_name,
                "start_time": window_start,
                "end_time": window_end,
                "alert": alert,
            },
            "timeout": settings.tool_call_timeout,
        })

    # 按优先级排序
    task_plan.sort(key=lambda t: t["priority"])

    return {
        "task_plan": task_plan,
        "messages": [f"[planner] 生成 {len(task_plan)} 个分析任务 (alert_type={alert_type})"],
    }


# ─────────────────────────────────────────────
# B03: dispatcher — 并发调度
# ─────────────────────────────────────────────
async def dispatcher_node(state: DeepRCAState) -> dict:
    """并发派发 L1 维度分析任务 + L2 领域专家子图。

    使用 asyncio.gather 并发执行 L1 六维度分析器。
    L1 完成后，根据 downstream 维度发现的异常线索触发 L2 领域专家子图。
    单任务超时降级为 error result，不阻塞其他任务。
    """
    task_plan = state.get("task_plan", [])
    if not task_plan:
        return {"sub_agent_results": [], "messages": ["[dispatcher] 无任务可执行"]}

    settings = get_settings()

    # L1 维度分析函数映射（PRD-03 已实现）
    from deeprca.agents.dimensions import (
        analyze_change,
        analyze_cluster,
        analyze_downstream,
        analyze_errorlog,
        analyze_problem,
        analyze_upstream,
    )

    _func_map = {
        "change": analyze_change,
        "upstream": analyze_upstream,
        "downstream": analyze_downstream,
        "cluster": analyze_cluster,
        "errorlog": analyze_errorlog,
        "problem": analyze_problem,
    }

    async def _run_one(task: dict) -> dict:
        """执行单个 L1 维度分析，带超时和错误降级。"""
        dimension = task["dimension"]
        params = task["params"]
        timeout = task.get("timeout", 10)

        func = _func_map.get(dimension)
        if func is None:
            return SubAgentResult(
                agent_name=f"{dimension}_analyzer",
                dimension=dimension,
                confidence=0.0,
                error=f"未知的分析维度: {dimension}",
                timestamp=_now_iso(),
            ).model_dump()

        try:
            result = await asyncio.wait_for(
                func(params.get("alert", {})),
                timeout=timeout,
            )
            return result.model_dump() if hasattr(result, "model_dump") else result
        except asyncio.TimeoutError:
            return SubAgentResult(
                agent_name=f"{dimension}_analyzer",
                dimension=dimension,
                confidence=0.0,
                error=f"分析超时 ({timeout}s)",
                timestamp=_now_iso(),
            ).model_dump()
        except Exception as e:
            return SubAgentResult(
                agent_name=f"{dimension}_analyzer",
                dimension=dimension,
                confidence=0.0,
                error=f"分析异常: {e!s}",
                timestamp=_now_iso(),
            ).model_dump()

    # 并发执行所有 L1 维度分析
    l1_results = await asyncio.gather(*[_run_one(t) for t in task_plan])
    all_results = list(l1_results)

    # L2 领域专家调度：根据 L1 发现的异常线索触发
    from deeprca.graph.subgraphs import dispatch_to_experts

    alert = state.get("alert", {})
    l1_findings = [
        f for r in l1_results
        if r.get("findings") and not r.get("error")
        for f in r["findings"]
    ]
    # 将 L1 发现作为上下文传递给 L2 专家
    l2_context = {"l1_findings": l1_findings, "task_plan": task_plan}

    l2_results = await dispatch_to_experts(task_plan, alert, l2_context, timeout=settings.tool_call_timeout)
    all_results.extend(r.model_dump() for r in l2_results)

    # PRD-02 §2.3: 全部维度失败时触发降级模式
    all_failed = all(r.get("error") or r.get("confidence", 0) == 0 for r in all_results)
    degraded = all_failed and len(all_results) > 0

    messages = [
        f"[dispatcher] L1 并发完成 {len(l1_results)} 个维度分析",
    ]
    if l2_results:
        messages.append(f"[dispatcher] L2 触发 {len(l2_results)} 个领域专家")
    if degraded:
        messages.append("[dispatcher] 全部分析失败，触发降级模式")

    return {
        "sub_agent_results": all_results,
        "degraded_mode": degraded,
        "messages": messages,
    }


# ─────────────────────────────────────────────
# B04: collector — 证据池聚合
# ─────────────────────────────────────────────
def collector_node(state: DeepRCAState) -> dict:
    """汇聚所有维度分析结果，构建证据池摘要。

    输入: state["sub_agent_results"] = list[SubAgentResult dict]
    输出: {"collected_evidence": EvidencePool summary dict}
    """
    sub_results = state.get("sub_agent_results", [])
    pool = EvidencePool()

    for result_dict in sub_results:
        agent_name = result_dict.get("agent_name", "unknown")
        dimension = result_dict.get("dimension", "unknown")
        confidence = result_dict.get("confidence", 0.0)
        findings = result_dict.get("findings", [])
        error = result_dict.get("error")

        # 错误结果记录为低等级证据
        if error:
            pool.add(Evidence(
                source=agent_name,
                dimension=dimension,
                finding=f"分析失败: {error}",
                level=EvidenceLevel.LOW,
                confidence=0.0,
                timestamp=result_dict.get("timestamp", _now_iso()),
            ))
            continue

        # 将每个 finding 转为 Evidence
        for finding in findings:
            finding_str = str(finding.get("desc", finding.get("description", finding)))
            level = EvidenceLevel.HIGH if confidence >= 0.7 else (
                EvidenceLevel.MEDIUM if confidence >= 0.4 else EvidenceLevel.LOW
            )
            pool.add(Evidence(
                source=agent_name,
                dimension=dimension,
                finding=finding_str,
                level=level,
                confidence=confidence,
                data=finding,
                timestamp=result_dict.get("timestamp", _now_iso()),
            ))

    summary = pool.to_summary()

    return {
        "collected_evidence": summary,
        "messages": [f"[collector] 汇聚 {len(pool.evidences)} 条证据"],
    }


# ─────────────────────────────────────────────
# B05+root_cause: 根因定位节点（调用 L3 Agent）
# ─────────────────────────────────────────────
async def root_cause_node(state: DeepRCAState) -> dict:
    """根因定位节点。

    调用 L3 RootCauseAgent 执行 6 步根因定位。
    如果 L3 Agent 不可用，降级为基于证据池的简单排序。
    超时降级路径下（collector 被跳过），从 sub_agent_results 构建简易证据池。
    """
    alert = state.get("alert", {})
    evidence_summary = state.get("collected_evidence") or {}
    sub_agent_results = state.get("sub_agent_results", [])
    trace_id = state.get("trace_id", "")

    # 超时降级路径：collector 被跳过，collected_evidence 为空或 None
    # 从 sub_agent_results 构建简易证据池，避免 L3 Agent 在空证据下分析
    if not evidence_summary and sub_agent_results:
        evidence_summary = _build_emergency_evidence(sub_agent_results)

    try:
        # 延迟导入 RootCauseAgent（Worker-3 实现）
        from deeprca.agents.root_cause import RootCauseAgent

        agent = RootCauseAgent()
        result = await agent.analyze(alert, evidence_summary, sub_agent_results)

        # 确保 trace_id 和 timestamp 存在
        if isinstance(result, dict):
            result.setdefault("trace_id", trace_id)
            result.setdefault("timestamp", _now_iso())

        return {
            "root_cause": result,
            "messages": [f"[root_cause] 根因定位完成"],
        }
    except ImportError:
        # L3 Agent 尚未实现，降级为简单排序
        return _fallback_root_cause(state)
    except Exception as e:
        return {
            "root_cause": {
                "candidates": [],
                "best_candidate": None,
                "anomalies_detected": [],
                "rule_matched": False,
                "llm_used": False,
                "trace_id": trace_id,
                "timestamp": _now_iso(),
                "error": str(e),
            },
            "messages": [f"[root_cause] 异常: {e}"],
        }


def _build_emergency_evidence(sub_results: list[dict]) -> dict:
    """超时降级路径下从 sub_agent_results 构建简易证据池摘要。

    当 check_timeout 返回 "timeout" 时，collector 节点被跳过，
    collected_evidence 为空。此函数从已有的 L1/L2 分析结果中提取
    关键信息，构建一个简易证据池供 root_cause_node 使用。
    """
    top_evidences = []
    for result in sub_results:
        if result.get("error"):
            continue
        confidence = result.get("confidence", 0.0)
        findings = result.get("findings", [])
        for finding in findings[:2]:
            finding_str = str(finding.get("desc", finding.get("description", finding)))
            top_evidences.append({
                "source": result.get("agent_name", "unknown"),
                "dimension": result.get("dimension", "unknown"),
                "finding": finding_str,
                "confidence": confidence,
                "level": "high" if confidence >= 0.7 else ("medium" if confidence >= 0.4 else "low"),
            })

    return {
        "top_evidences": top_evidences[:10],
        "total_count": len(top_evidences),
        "emergency_mode": True,
    }


def _fallback_root_cause(state: DeepRCAState) -> dict:
    """降级根因定位：基于证据池置信度排序。"""
    sub_results = state.get("sub_agent_results", [])
    trace_id = state.get("trace_id", "")

    # 按置信度排序
    sorted_results = sorted(
        [r for r in sub_results if r.get("confidence", 0) > 0 and not r.get("error")],
        key=lambda r: r.get("confidence", 0),
        reverse=True,
    )

    candidates: list[dict] = []
    for i, result in enumerate(sorted_results[:3]):
        findings = result.get("findings", [])
        root_cause_desc = "; ".join(
            str(f.get("desc", f.get("description", f))) for f in findings[:3]
        ) or f"{result.get('dimension', 'unknown')} 维度发现异常"
        candidates.append({
            "rank": i + 1,
            "root_cause": root_cause_desc,
            "confidence": result.get("confidence", 0.0),
            "evidence_chain": result.get("evidence", []),
            "matched_rule": None,
            "source": "fallback",
        })

    best = candidates[0] if candidates else None

    return {
        "root_cause": {
            "candidates": candidates,
            "best_candidate": best,
            "anomalies_detected": [],
            "rule_matched": False,
            "llm_used": False,
            "trace_id": trace_id,
            "timestamp": _now_iso(),
        },
        "messages": ["[root_cause] 降级模式：基于置信度排序"],
    }


# ─────────────────────────────────────────────
# B06: reporter — 报告生成
# ─────────────────────────────────────────────
def reporter_node(state: DeepRCAState) -> dict:
    """生成分析报告。

    汇总根因结果、证据链、分析维度，输出 AnalysisReport。
    包含建议措施和满意度反馈 URL。
    """
    alert = state.get("alert", {})
    root_cause = state.get("root_cause", {})
    evidence_summary = state.get("collected_evidence") or {}
    sub_results = state.get("sub_agent_results", [])
    trace_id = state.get("trace_id", "")
    start_time = state.get("start_time", "")
    degraded_mode = state.get("degraded_mode", False)

    # 计算分析耗时
    analysis_duration = None
    if start_time:
        try:
            start_dt = _parse_iso(start_time)
            analysis_duration = (datetime.now(timezone.utc) - start_dt).total_seconds()
        except (ValueError, TypeError):
            pass

    # 提取根因信息
    best_candidate = root_cause.get("best_candidate") or {}
    candidates = root_cause.get("candidates", [])

    # 提取关键证据
    top_evidences = evidence_summary.get("top_evidences", [])
    key_evidence = [e.get("finding", "") for e in top_evidences[:5]]

    # PRD-04 §8: 优先使用 L3 RootCauseAgent 生成的建议措施
    # L3 已实现 8 种精细建议模板（change/db_slave_delay/db_lock/redis_memory/kafka_lag/rpc_circuit_breaker/resource_saturation/oom）
    # 仅当 L3 未生成建议时，降级使用 PRD-02 的粗粒度建议
    suggestions = root_cause.get("suggestions", [])
    if not suggestions:
        suggestions = _generate_suggestions(best_candidate, sub_results, degraded_mode)

    # PRD-04 §7: 使用 L3 生成的证据链补充 key_evidence
    l3_evidence_chain = root_cause.get("evidence_chain", [])
    if l3_evidence_chain:
        for ev in l3_evidence_chain[:3]:
            if isinstance(ev, dict):
                ev_text = ev.get("evidence", "")
            else:
                ev_text = str(ev)
            if ev_text and ev_text not in key_evidence:
                key_evidence.insert(0, ev_text)

    # 已分析维度
    dimensions_analyzed = list({r.get("dimension", "") for r in sub_results if r.get("dimension")})

    # 调用的子 Agent
    sub_agents_invoked = [r.get("agent_name", "") for r in sub_results]

    # PRD-02 §2.6: 构建满意度反馈 URL
    feedback_token = str(uuid.uuid4())[:8]
    satisfaction_url = _build_feedback_url(trace_id, feedback_token)

    report = AnalysisReport(
        trace_id=trace_id,
        alert_id=alert.get("alert_id", ""),
        service_name=alert.get("service_name", "unknown"),
        severity=alert.get("severity", "P2"),
        status="completed",
        root_cause=best_candidate.get("root_cause"),
        confidence=best_candidate.get("confidence", 0.0),
        top_candidates=candidates,
        key_evidence=key_evidence,
        analysis_duration=analysis_duration,
        dimensions_analyzed=dimensions_analyzed,
        sub_agents_invoked=sub_agents_invoked,
        suggestions=suggestions,
        satisfaction_url=satisfaction_url,
        timestamp=_now_iso(),
        feedback_token=feedback_token,
    )

    # PRD-02 §2.6: 推送通知（IM/邮件/Webhook）
    _push_notification(report.model_dump(), alert, degraded_mode)

    return {
        "report": report.model_dump_json(),
        "status": "completed",
        "messages": [f"[reporter] 报告生成完成, trace_id={trace_id}"],
    }


def _generate_suggestions(best_candidate: dict, sub_results: list[dict], degraded: bool) -> list[str]:
    """根据根因结果生成建议措施。

    PRD-02 §2.6: 报告需包含可执行的建议措施。
    """
    if degraded:
        return ["建议人工介入分析，所有自动分析维度均未得到有效结果"]

    suggestions: list[str] = []
    root_cause = best_candidate.get("root_cause", "")
    dimension = best_candidate.get("source", "")

    # 基于根因维度生成建议
    if "change" in root_cause.lower() or dimension == "change":
        suggestions.append("检查最近变更记录，考虑回滚高风险变更")
    if "db" in root_cause.lower() or "database" in root_cause.lower():
        suggestions.append("检查数据库主从同步状态和慢查询日志")
    if "timeout" in root_cause.lower():
        suggestions.append("检查服务超时配置和下游依赖响应时间")
    if "resource" in root_cause.lower() or "cpu" in root_cause.lower() or "memory" in root_cause.lower():
        suggestions.append("检查集群资源使用情况，考虑扩容")

    # 通用建议
    if not suggestions:
        suggestions.append("根据根因分析结果，检查相关服务和依赖状态")

    suggestions.append("持续监控告警服务指标，确认问题是否恢复")
    return suggestions


def _build_feedback_url(trace_id: str, feedback_token: str) -> str:
    """构建满意度反馈 URL。"""
    settings = get_settings()
    # 使用 app_external_host 而非 app_host（0.0.0.0 外部不可访问）
    return f"http://{settings.app_external_host}:{settings.app_port}/api/v1/feedback?trace_id={trace_id}&token={feedback_token}"


def _push_notification(report: dict, alert: dict, degraded: bool) -> None:
    """推送通知（IM/邮件/Webhook）。

    PRD-02 §2.6: 报告生成后推送通知。
    当前为日志输出，生产环境可对接 IM/邮件系统。
    """
    severity = alert.get("severity", "P2")
    service = alert.get("service_name", "unknown")
    root_cause = report.get("root_cause", "未定位")
    confidence = report.get("confidence", 0.0)
    degraded_tag = " [降级模式]" if degraded else ""

    # 当前仅输出日志，生产环境对接通知系统
    logger.info(
        "分析报告通知%s: service=%s severity=%s root_cause=%s confidence=%.2f trace_id=%s",
        degraded_tag, service, severity, root_cause, confidence, report.get("trace_id", ""),
    )


# ─────────────────────────────────────────────
# 超时检查条件边
# ─────────────────────────────────────────────
def check_timeout(state: DeepRCAState) -> str:
    """条件边：检查分析是否超时。

    Returns:
        "normal" → 进入 collector 节点
        "timeout" → 跳过 collector，直接进入 root_cause
    """
    settings = get_settings()
    start_time = state.get("start_time", "")

    if not start_time:
        return "normal"

    try:
        start_dt = _parse_iso(start_time)
        elapsed = (datetime.now(timezone.utc) - start_dt).total_seconds()
        if elapsed > settings.analysis_timeout:
            return "timeout"
    except (ValueError, TypeError):
        pass

    return "normal"


# ─────────────────────────────────────────────
# 运行时类型导入（必须在模块级别，不能使用 TYPE_CHECKING guard）
# 原因 1: LangGraph 的 add_conditional_edges 通过 get_type_hints() 在运行时
#         解析 check_timeout 的参数类型 DeepRCAState，TYPE_CHECKING 会导致 NameError。
# 原因 2: 放在文件底部避免循环导入（deeprca.graph → coordinator → deeprca.graph.state）。
# ─────────────────────────────────────────────
from deeprca.graph.state import DeepRCAState  # noqa: E402
