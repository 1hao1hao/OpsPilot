"""Mafka（消息队列）领域专家子 Agent。

针对 Kafka/Mafka 维度（消费延迟、生产/消费速率、积压、rebalance）进行指标采集与阈值分析，
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

__all__ = ["MafkaExpertAgent"]


class _MafkaSubGraphState(TypedDict):
    """Mafka 专家子图局部状态。"""

    alert: dict
    context: dict
    metrics: dict
    findings: list[dict]
    evidence: list[str]
    confidence: float
    error: Optional[str]
    result: Optional[SubAgentResult]


class MafkaExpertAgent(BaseExpertAgent):
    """消息队列领域专家子 Agent。

    通过 collect → analyze → conclude 三节点子图，
    采集 Kafka 消费者指标并进行确定性阈值判断，定位消息队列维度的异常根因。
    """

    @property
    def agent_name(self) -> str:
        """子 Agent 名称。"""
        return "mafka_expert"

    @property
    def trigger_keywords(self) -> list[str]:
        """触发关键词列表。"""
        return [
            "kafka", "mafka", "消息队列", "message queue",
            "消费延迟", "consumer lag", "积压", "backlog", "rebalance",
        ]

    def build_subgraph(self) -> CompiledStateGraph:
        """构建 collect → analyze → conclude 三节点子图。"""
        graph = StateGraph(_MafkaSubGraphState)
        graph.add_node("collect", self._collect)
        graph.add_node("analyze", self._analyze_metrics)
        graph.add_node("conclude", self._conclude)
        graph.set_entry_point("collect")
        graph.add_edge("collect", "analyze")
        graph.add_edge("analyze", "conclude")
        graph.add_edge("conclude", END)
        return graph.compile()

    async def _collect(self, state: _MafkaSubGraphState) -> dict:
        """采集 Kafka 指标：消费者 lag、生产/消费速率。"""
        try:
            settings = get_settings()
            service = state.get("alert", {}).get("service_name", "")

            if settings.mock_env_enabled:
                # 通过 Mock API 获取场景感知数据
                async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                    resp = await client.get(
                        f"{settings.mock_monitor_api}/api/v1/mock/kafka/kafka-prod-01/metrics",
                    )
                    resp.raise_for_status()
                    raw = resp.json()
                    metrics = {
                        "consumer_lag": raw.get("total_lag", 0),
                        "produce_rate": raw.get("produce_rate", 0.0),
                        "consume_rate": raw.get("consume_rate", 0.0),
                        "rebalance_count": 0,  # get_metrics 不返回 rebalance
                    }
                    # 尝试获取 rebalance 信息
                    try:
                        lag_resp = await client.get(
                            f"{settings.mock_monitor_api}/api/v1/mock/kafka/kafka-prod-01/topics/order-events/lag",
                        )
                        if lag_resp.status_code == 200:
                            lag_data = lag_resp.json()
                            metrics["rebalance_count"] = lag_data.get("rebalance_events", 0)
                    except Exception:
                        pass
                return {"metrics": metrics, "error": None}

            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/metrics/kafka",
                    params={"service": service},
                )
                resp.raise_for_status()
                metrics = resp.json()
            return {"metrics": metrics, "error": None}
        except Exception as e:
            return {"metrics": {}, "error": str(e)}

    async def _analyze_metrics(self, state: _MafkaSubGraphState) -> dict:
        """阈值判断：lag>10000 告警，速率突变检测。"""
        if state.get("error"):
            return {"findings": [], "evidence": [], "confidence": 0.0}

        metrics = state.get("metrics", {})
        findings: list[dict] = []
        evidence: list[str] = []

        # 利用 L1 维度分析的发现作为辅助信号
        context = state.get("context", {})
        l1_findings = context.get("l1_findings", [])
        l1_mafka_hints: list[str] = []
        for l1_group in l1_findings:
            if isinstance(l1_group, list):
                for f in l1_group:
                    finding_str = str(f.get("desc", f.get("description", f))).lower()
                    if any(kw in finding_str for kw in self.trigger_keywords):
                        l1_mafka_hints.append(str(f.get("desc", f.get("description", f))))

        consumer_lag = metrics.get("consumer_lag", 0)
        if consumer_lag > 100000:
            findings.append({
                "type": "consumer_lag",
                "severity": "critical",
                "message": f"消费积压严重: lag={consumer_lag} (>100000)",
                "value": consumer_lag,
                "threshold": 100000,
            })
            evidence.append(f"consumer_lag={consumer_lag}, 超过临界阈值100000")
        elif consumer_lag > 10000:
            findings.append({
                "type": "consumer_lag",
                "severity": "warning",
                "message": f"消费延迟告警: lag={consumer_lag} (>10000)",
                "value": consumer_lag,
                "threshold": 10000,
            })
            evidence.append(f"consumer_lag={consumer_lag}, 超过告警阈值10000")

        produce_rate = metrics.get("produce_rate", 0.0)
        consume_rate = metrics.get("consume_rate", 0.0)
        if produce_rate > 0 and consume_rate < produce_rate * 0.5:
            findings.append({
                "type": "rate_mismatch",
                "severity": "warning",
                "message": (
                    f"消费速率远低于生产速率: produce={produce_rate:.1f}/s, "
                    f"consume={consume_rate:.1f}/s (比值<0.5)"
                ),
                "value": consume_rate / produce_rate if produce_rate > 0 else 0.0,
                "threshold": 0.5,
            })
            evidence.append(
                f"produce_rate={produce_rate:.1f}, consume_rate={consume_rate:.1f}, "
                f"消费速率不足生产速率的50%"
            )

        rebalance_count = metrics.get("rebalance_count", 0)
        if rebalance_count > 0:
            findings.append({
                "type": "rebalance",
                "severity": "warning",
                "message": f"检测到 consumer rebalance: {rebalance_count} 次",
                "value": rebalance_count,
                "threshold": 0,
            })
            evidence.append(f"rebalance_count={rebalance_count}, 发生过rebalance")

        if len(findings) == 0:
            confidence = 0.3
        elif len(findings) == 1:
            confidence = 0.6
        elif len(findings) == 2:
            confidence = 0.8
        else:
            confidence = 0.9

        # L1 发现了 Kafka/Mafka 相关异常时提升置信度
        if l1_mafka_hints and findings:
            confidence = min(confidence + 0.05, 1.0)
            evidence.append(f"L1 维度分析也发现了消息队列相关异常: {', '.join(l1_mafka_hints[:2])}")
        elif l1_mafka_hints and not findings:
            confidence = max(confidence, 0.5)
            evidence.append(f"L1 维度分析提示消息队列相关异常: {', '.join(l1_mafka_hints[:2])}")

        return {"findings": findings, "evidence": evidence, "confidence": confidence}

    async def _conclude(self, state: _MafkaSubGraphState) -> dict:
        """组装 SubAgentResult。"""
        result = SubAgentResult(
            agent_name=self.agent_name,
            dimension="message_queue",
            findings=state.get("findings", []),
            confidence=state.get("confidence", 0.0),
            evidence=state.get("evidence", []),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            error=state.get("error"),
        )
        return {"result": result}

    async def analyze(self, alert: dict, context: dict) -> SubAgentResult:
        """执行消息队列领域分析，返回 SubAgentResult。"""
        subgraph = self.build_subgraph()
        initial_state: _MafkaSubGraphState = {
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
                dimension="message_queue",
                confidence=0.0,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                error="子图执行未返回结果",
            )
        return result

