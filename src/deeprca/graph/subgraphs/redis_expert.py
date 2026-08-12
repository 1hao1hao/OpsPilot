"""Redis 领域专家子 Agent。

针对 Redis 维度（内存使用率、命中率、连接数、大key）进行指标采集与阈值分析，
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

__all__ = ["RedisExpertAgent"]


class _RedisSubGraphState(TypedDict):
    """Redis 专家子图局部状态。"""

    alert: dict
    context: dict
    metrics: dict
    findings: list[dict]
    evidence: list[str]
    confidence: float
    error: Optional[str]
    result: Optional[SubAgentResult]


class RedisExpertAgent(BaseExpertAgent):
    """Redis 领域专家子 Agent。

    通过 collect → analyze → conclude 三节点子图，
    采集 Redis 指标并进行确定性阈值判断，定位缓存维度的异常根因。
    """

    @property
    def agent_name(self) -> str:
        """子 Agent 名称。"""
        return "redis_expert"

    @property
    def trigger_keywords(self) -> list[str]:
        """触发关键词列表。"""
        return [
            "redis", "缓存", "cache", "热点key", "hot key",
            "大key", "big key", "命中率", "hit rate", "内存", "memory",
        ]

    def build_subgraph(self) -> CompiledStateGraph:
        """构建 collect → analyze → conclude 三节点子图。"""
        graph = StateGraph(_RedisSubGraphState)
        graph.add_node("collect", self._collect)
        graph.add_node("analyze", self._analyze_metrics)
        graph.add_node("conclude", self._conclude)
        graph.set_entry_point("collect")
        graph.add_edge("collect", "analyze")
        graph.add_edge("analyze", "conclude")
        graph.add_edge("conclude", END)
        return graph.compile()

    async def _collect(self, state: _RedisSubGraphState) -> dict:
        """采集 Redis 指标：内存使用率、命中率、连接数。"""
        try:
            settings = get_settings()
            service = state.get("alert", {}).get("service_name", "")

            if settings.mock_env_enabled:
                # 通过 Mock API 获取场景感知数据（嵌套格式），转换为扁平格式
                async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                    resp = await client.get(
                        f"{settings.mock_monitor_api}/api/v1/mock/redis/redis-cluster-01/metrics",
                    )
                    resp.raise_for_status()
                    raw = resp.json()
                used_memory = raw.get("used_memory", {})
                usage_ratio = used_memory.get("usage_ratio", 0.0)
                hit_rate = raw.get("hit_rate", {})
                clients = raw.get("connected_clients", {})
                metrics = {
                    "memory_usage_percent": round(usage_ratio * 100, 1) if usage_ratio <= 1 else usage_ratio,
                    "hit_rate_percent": round(hit_rate.get("current", 1.0) * 100, 1),
                    "connected_clients": clients.get("current", 0),
                    "max_clients": clients.get("max", 1),
                    "big_keys_count": 0,  # get_metrics 不返回 bigkeys，默认 0
                }
                return {"metrics": metrics, "error": None}

            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/metrics/redis",
                    params={"service": service},
                )
                resp.raise_for_status()
                metrics = resp.json()
            return {"metrics": metrics, "error": None}
        except Exception as e:
            return {"metrics": {}, "error": str(e)}

    async def _analyze_metrics(self, state: _RedisSubGraphState) -> dict:
        """阈值判断：内存>80%告警，命中率<90%告警。"""
        if state.get("error"):
            return {"findings": [], "evidence": [], "confidence": 0.0}

        metrics = state.get("metrics", {})
        findings: list[dict] = []
        evidence: list[str] = []

        # 利用 L1 维度分析的发现作为辅助信号
        context = state.get("context", {})
        l1_findings = context.get("l1_findings", [])
        l1_redis_hints: list[str] = []
        for l1_group in l1_findings:
            if isinstance(l1_group, list):
                for f in l1_group:
                    finding_str = str(f.get("desc", f.get("description", f))).lower()
                    if any(kw in finding_str for kw in self.trigger_keywords):
                        l1_redis_hints.append(str(f.get("desc", f.get("description", f))))

        memory_usage = metrics.get("memory_usage_percent", 0.0)
        if memory_usage > 95:
            findings.append({
                "type": "memory",
                "severity": "critical",
                "message": f"Redis 内存使用率严重超标: {memory_usage:.1f}% (>95%)",
                "value": memory_usage,
                "threshold": 95,
            })
            evidence.append(f"memory_usage={memory_usage:.1f}%, 超过临界阈值95%")
        elif memory_usage > 80:
            findings.append({
                "type": "memory",
                "severity": "warning",
                "message": f"Redis 内存使用率偏高: {memory_usage:.1f}% (>80%)",
                "value": memory_usage,
                "threshold": 80,
            })
            evidence.append(f"memory_usage={memory_usage:.1f}%, 超过告警阈值80%")

        hit_rate = metrics.get("hit_rate_percent", 100.0)
        if hit_rate < 70:
            findings.append({
                "type": "hit_rate",
                "severity": "critical",
                "message": f"Redis 命中率严重偏低: {hit_rate:.1f}% (<70%)",
                "value": hit_rate,
                "threshold": 70,
            })
            evidence.append(f"hit_rate={hit_rate:.1f}%, 低于临界阈值70%")
        elif hit_rate < 90:
            findings.append({
                "type": "hit_rate",
                "severity": "warning",
                "message": f"Redis 命中率偏低: {hit_rate:.1f}% (<90%)",
                "value": hit_rate,
                "threshold": 90,
            })
            evidence.append(f"hit_rate={hit_rate:.1f}%, 低于告警阈值90%")

        connected_clients = metrics.get("connected_clients", 0)
        max_clients = metrics.get("max_clients", 1)
        client_ratio = connected_clients / max_clients if max_clients > 0 else 0.0
        if client_ratio > 0.8:
            findings.append({
                "type": "connection",
                "severity": "warning",
                "message": f"Redis 连接数偏高: {connected_clients}/{max_clients} ({client_ratio:.1%})",
                "value": client_ratio,
                "threshold": 0.8,
            })
            evidence.append(f"client_connection_ratio={client_ratio:.1%}, 超过阈值80%")

        big_keys_count = metrics.get("big_keys_count", 0)
        if big_keys_count > 0:
            findings.append({
                "type": "big_key",
                "severity": "warning",
                "message": f"检测到大key: {big_keys_count} 个",
                "value": big_keys_count,
                "threshold": 0,
            })
            evidence.append(f"big_keys_count={big_keys_count}, 存在大key")

        if len(findings) == 0:
            confidence = 0.3
        elif len(findings) == 1:
            confidence = 0.6
        elif len(findings) == 2:
            confidence = 0.8
        else:
            confidence = 0.9

        # L1 发现了 Redis 相关异常时提升置信度
        if l1_redis_hints and findings:
            confidence = min(confidence + 0.05, 1.0)
            evidence.append(f"L1 维度分析也发现了 Redis 相关异常: {', '.join(l1_redis_hints[:2])}")
        elif l1_redis_hints and not findings:
            confidence = max(confidence, 0.5)
            evidence.append(f"L1 维度分析提示 Redis 相关异常: {', '.join(l1_redis_hints[:2])}")

        return {"findings": findings, "evidence": evidence, "confidence": confidence}

    async def _conclude(self, state: _RedisSubGraphState) -> dict:
        """组装 SubAgentResult。"""
        result = SubAgentResult(
            agent_name=self.agent_name,
            dimension="redis",
            findings=state.get("findings", []),
            confidence=state.get("confidence", 0.0),
            evidence=state.get("evidence", []),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            error=state.get("error"),
        )
        return {"result": result}

    async def analyze(self, alert: dict, context: dict) -> SubAgentResult:
        """执行 Redis 领域分析，返回 SubAgentResult。"""
        subgraph = self.build_subgraph()
        initial_state: _RedisSubGraphState = {
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
                dimension="redis",
                confidence=0.0,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                error="子图执行未返回结果",
            )
        return result
