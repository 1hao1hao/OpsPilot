"""DB 领域专家子 Agent。

针对数据库维度（慢查询、连接池、锁等待、主从延迟）进行指标采集与阈值分析，
作为 LangGraph 子图嵌入 L2 领域专家层。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.3</td></tr>
</table>
@author xianhuimeng
"""

from __future__ import annotations

import time
from typing import Optional, TypedDict

import httpx
from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from deeprca.config import get_settings
from deeprca.graph.subgraphs.base_expert import BaseExpertAgent
from deeprca.models import SubAgentResult

__all__ = ["DBExpertAgent"]


class _DBSubGraphState(TypedDict):
    """DB 专家子图局部状态。"""

    alert: dict
    context: dict
    metrics: dict
    findings: list[dict]
    evidence: list[str]
    confidence: float
    error: Optional[str]
    result: Optional[SubAgentResult]


class DBExpertAgent(BaseExpertAgent):
    """数据库领域专家子 Agent。

    通过 collect → analyze → conclude 三节点子图，
    采集 DB 指标并进行确定性阈值判断，定位数据库维度的异常根因。
    """

    @property
    def agent_name(self) -> str:
        """子 Agent 名称。"""
        return "db_expert"

    @property
    def trigger_keywords(self) -> list[str]:
        """触发关键词列表。"""
        return [
            "mysql", "database", "db", "慢查询", "slow query",
            "连接池", "connection pool", "锁等待", "lock wait",
            "主从延迟", "replication lag",
        ]

    def build_subgraph(self) -> CompiledStateGraph:
        """构建 collect → analyze → conclude 三节点子图。"""
        graph = StateGraph(_DBSubGraphState)
        graph.add_node("collect", self._collect)
        graph.add_node("analyze", self._analyze_metrics)
        graph.add_node("conclude", self._conclude)
        graph.set_entry_point("collect")
        graph.add_edge("collect", "analyze")
        graph.add_edge("analyze", "conclude")
        graph.add_edge("conclude", END)
        return graph.compile()

    async def _collect(self, state: _DBSubGraphState) -> dict:
        """采集 DB 指标：慢查询数、连接数、锁等待数、主从延迟。"""
        try:
            settings = get_settings()
            service = state.get("alert", {}).get("service_name", "")

            if settings.mock_env_enabled:
                # 通过 Mock API 获取场景感知数据（嵌套格式），转换为扁平格式
                async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                    resp = await client.get(
                        f"{settings.mock_monitor_api}/api/v1/mock/db/mysql-prod-01/metrics",
                    )
                    resp.raise_for_status()
                    raw = resp.json()
                # 嵌套格式 → 扁平格式转换
                active_conn = raw.get("active_connections", {})
                metrics = {
                    "active_connections": active_conn.get("current", 0),
                    "max_connections": active_conn.get("max", 1),
                    "slow_query_count": raw.get("slow_query_count", {}).get("current", 0),
                    "lock_wait_count": raw.get("innodb_lock_waits", {}).get("current", 0),
                    "replication_lag_seconds": raw.get("slave_delay_seconds", {}).get("current", 0.0),
                }
                return {"metrics": metrics, "error": None}

            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/metrics/db",
                    params={"service": service},
                )
                resp.raise_for_status()
                metrics = resp.json()
            return {"metrics": metrics, "error": None}
        except Exception as e:
            return {"metrics": {}, "error": str(e)}

    async def _analyze_metrics(self, state: _DBSubGraphState) -> dict:
        """阈值判断：慢查询数、连接使用率、锁等待、主从延迟。"""
        if state.get("error"):
            return {"findings": [], "evidence": [], "confidence": 0.0}

        metrics = state.get("metrics", {})
        findings: list[dict] = []
        evidence: list[str] = []

        # 利用 L1 维度分析的发现作为辅助信号
        context = state.get("context", {})
        l1_findings = context.get("l1_findings", [])
        l1_db_hints: list[str] = []
        for l1_group in l1_findings:
            if isinstance(l1_group, list):
                for f in l1_group:
                    finding_str = str(f.get("desc", f.get("description", f))).lower()
                    if any(kw in finding_str for kw in self.trigger_keywords):
                        l1_db_hints.append(str(f.get("desc", f.get("description", f))))

        slow_query_count = metrics.get("slow_query_count", 0)
        if slow_query_count > 50:
            findings.append({
                "type": "slow_query",
                "severity": "critical",
                "message": f"慢查询数严重超标: {slow_query_count} (>50)",
                "value": slow_query_count,
                "threshold": 50,
            })
            evidence.append(f"slow_query_count={slow_query_count}, 超过临界阈值50")
        elif slow_query_count > 10:
            findings.append({
                "type": "slow_query",
                "severity": "warning",
                "message": f"慢查询数偏多: {slow_query_count} (>10)",
                "value": slow_query_count,
                "threshold": 10,
            })
            evidence.append(f"slow_query_count={slow_query_count}, 超过告警阈值10")

        active_conn = metrics.get("active_connections", 0)
        max_conn = metrics.get("max_connections", 1)
        conn_ratio = active_conn / max_conn if max_conn > 0 else 0.0
        if conn_ratio > 0.8:
            findings.append({
                "type": "connection_pool",
                "severity": "warning",
                "message": f"连接池使用率高: {active_conn}/{max_conn} ({conn_ratio:.1%})",
                "value": conn_ratio,
                "threshold": 0.8,
            })
            evidence.append(f"connection_usage={conn_ratio:.1%}, 超过阈值80%")

        lock_wait_count = metrics.get("lock_wait_count", 0)
        if lock_wait_count > 5:
            findings.append({
                "type": "lock_wait",
                "severity": "critical",
                "message": f"锁等待数过多: {lock_wait_count} (>5)",
                "value": lock_wait_count,
                "threshold": 5,
            })
            evidence.append(f"lock_wait_count={lock_wait_count}, 超过临界阈值5")
        elif lock_wait_count > 0:
            findings.append({
                "type": "lock_wait",
                "severity": "warning",
                "message": f"存在锁等待: {lock_wait_count}",
                "value": lock_wait_count,
                "threshold": 0,
            })
            evidence.append(f"lock_wait_count={lock_wait_count}, 存在锁等待")

        repl_lag = metrics.get("replication_lag_seconds", 0.0)
        if repl_lag > 30:
            findings.append({
                "type": "replication_lag",
                "severity": "warning",
                "message": f"主从延迟较高: {repl_lag}s (>30s)",
                "value": repl_lag,
                "threshold": 30,
            })
            evidence.append(f"replication_lag={repl_lag}s, 超过阈值30s")

        if len(findings) == 0:
            confidence = 0.3
        elif len(findings) == 1:
            confidence = 0.6
        elif len(findings) == 2:
            confidence = 0.8
        else:
            confidence = 0.9

        # L1 发现了 DB 相关异常时提升置信度
        if l1_db_hints and findings:
            confidence = min(confidence + 0.05, 1.0)
            evidence.append(f"L1 维度分析也发现了 DB 相关异常: {', '.join(l1_db_hints[:2])}")
        elif l1_db_hints and not findings:
            # L1 发现了 DB 异常线索但 DB 指标未超阈值，提升基础置信度
            confidence = max(confidence, 0.5)
            evidence.append(f"L1 维度分析提示 DB 相关异常: {', '.join(l1_db_hints[:2])}")

        return {"findings": findings, "evidence": evidence, "confidence": confidence}

    async def _conclude(self, state: _DBSubGraphState) -> dict:
        """组装 SubAgentResult。"""
        result = SubAgentResult(
            agent_name=self.agent_name,
            dimension="database",
            findings=state.get("findings", []),
            confidence=state.get("confidence", 0.0),
            evidence=state.get("evidence", []),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            error=state.get("error"),
        )
        return {"result": result}

    async def analyze(self, alert: dict, context: dict) -> SubAgentResult:
        """执行 DB 领域分析，返回 SubAgentResult。"""
        subgraph = self.build_subgraph()
        initial_state: _DBSubGraphState = {
            "alert": alert,
            "context": context,
            "metrics": {},
            "findings": [],
            "evidence": [],
            "confidence": 0.0,
            "error": None,
            "result": None,
        }
        final_state = await subgraph.ainvoke(initial_state)
        result = final_state.get("result")
        if result is None:
            result = SubAgentResult(
                agent_name=self.agent_name,
                dimension="database",
                confidence=0.0,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                error="子图执行未返回结果",
            )
        return result

