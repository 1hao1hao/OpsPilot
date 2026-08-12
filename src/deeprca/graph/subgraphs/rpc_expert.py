"""RPC 领域专家子 Agent。

针对 RPC 调用维度（失败率、RT突变、调用量、超时）进行指标采集与阈值分析，
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

__all__ = ["RPCExpertAgent"]


class _RPCSubGraphState(TypedDict):
    """RPC 专家子图局部状态。"""

    alert: dict
    context: dict
    metrics: dict
    findings: list[dict]
    evidence: list[str]
    confidence: float
    error: Optional[str]
    result: Optional[SubAgentResult]


class RPCExpertAgent(BaseExpertAgent):
    """RPC 调用领域专家子 Agent。

    通过 collect → analyze → conclude 三节点子图，
    采集 RPC 调用指标并进行确定性阈值判断，定位调用链维度的异常根因。
    """

    @property
    def agent_name(self) -> str:
        """子 Agent 名称。"""
        return "rpc_expert"

    @property
    def trigger_keywords(self) -> list[str]:
        """触发关键词列表。"""
        return [
            "rpc", "调用链", "trace", "失败率", "failure rate",
            "rt突变", "latency spike", "依赖", "dependency",
            "超时", "timeout",
        ]

    def build_subgraph(self) -> CompiledStateGraph:
        """构建 collect → analyze → conclude 三节点子图。"""
        graph = StateGraph(_RPCSubGraphState)
        graph.add_node("collect", self._collect)
        graph.add_node("analyze", self._analyze_metrics)
        graph.add_node("conclude", self._conclude)
        graph.set_entry_point("collect")
        graph.add_edge("collect", "analyze")
        graph.add_edge("analyze", "conclude")
        graph.add_edge("conclude", END)
        return graph.compile()

    async def _collect(self, state: _RPCSubGraphState) -> dict:
        """采集 RPC 调用指标：失败率、RT、调用量。"""
        try:
            settings = get_settings()
            service = state.get("alert", {}).get("service_name", "")

            if settings.mock_env_enabled:
                # 无独立 RPC 模拟器，从 service_simulator 指标推导
                async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                    err_resp = await client.get(
                        f"{settings.mock_monitor_api}/api/v1/mock/service/{service}/metrics/error_rate",
                    )
                    err_resp.raise_for_status()
                    err_data = err_resp.json()
                    rt_resp = await client.get(
                        f"{settings.mock_monitor_api}/api/v1/mock/service/{service}/metrics/tp99",
                    )
                    rt_resp.raise_for_status()
                    rt_data = rt_resp.json()
                    qps_resp = await client.get(
                        f"{settings.mock_monitor_api}/api/v1/mock/service/{service}/metrics/qps",
                    )
                    qps_resp.raise_for_status()
                    qps_data = qps_resp.json()

                def _latest(data_points):
                    if not data_points:
                        return 0.0
                    return data_points[-1].get("value", 0.0)

                def _baseline(data_points):
                    if not data_points:
                        return 0.0
                    values = [dp.get("value", 0.0) for dp in data_points]
                    return sum(values) / len(values) if values else 0.0

                failure_rate = _latest(err_data.get("data_points", []))
                if failure_rate <= 1.0:
                    failure_rate *= 100
                avg_rt = _latest(rt_data.get("data_points", []))
                baseline_rt = _baseline(rt_data.get("data_points", []))
                call_volume = int(_latest(qps_data.get("data_points", [])))
                baseline_volume = int(_baseline(qps_data.get("data_points", [])))
                metrics = {
                    "failure_rate_percent": failure_rate,
                    "avg_rt_ms": avg_rt,
                    "baseline_rt_ms": baseline_rt,
                    "timeout_count": 0,
                    "call_volume": call_volume,
                    "baseline_volume": baseline_volume,
                }
                return {"metrics": metrics, "error": None}

            async with httpx.AsyncClient(timeout=settings.tool_call_timeout) as client:
                resp = await client.get(
                    f"{settings.mock_monitor_api}/api/metrics/rpc",
                    params={"service": service},
                )
                resp.raise_for_status()
                metrics = resp.json()
            return {"metrics": metrics, "error": None}
        except Exception as e:
            return {"metrics": {}, "error": str(e)}

    async def _analyze_metrics(self, state: _RPCSubGraphState) -> dict:
        """阈值判断：失败率>5%告警，RT>基线3倍告警。"""
        if state.get("error"):
            return {"findings": [], "evidence": [], "confidence": 0.0}

        metrics = state.get("metrics", {})
        findings: list[dict] = []
        evidence: list[str] = []

        # 利用 L1 维度分析的发现作为辅助信号
        context = state.get("context", {})
        l1_findings = context.get("l1_findings", [])
        l1_rpc_hints: list[str] = []
        for l1_group in l1_findings:
            if isinstance(l1_group, list):
                for f in l1_group:
                    finding_str = str(f.get("desc", f.get("description", f))).lower()
                    if any(kw in finding_str for kw in self.trigger_keywords):
                        l1_rpc_hints.append(str(f.get("desc", f.get("description", f))))

        failure_rate = metrics.get("failure_rate_percent", 0.0)
        if failure_rate > 10:
            findings.append({
                "type": "failure_rate",
                "severity": "critical",
                "message": f"RPC 失败率严重超标: {failure_rate:.1f}% (>10%)",
                "value": failure_rate,
                "threshold": 10,
            })
            evidence.append(f"failure_rate={failure_rate:.1f}%, 超过临界阈值10%")
        elif failure_rate > 5:
            findings.append({
                "type": "failure_rate",
                "severity": "warning",
                "message": f"RPC 失败率偏高: {failure_rate:.1f}% (>5%)",
                "value": failure_rate,
                "threshold": 5,
            })
            evidence.append(f"failure_rate={failure_rate:.1f}%, 超过告警阈值5%")

        avg_rt = metrics.get("avg_rt_ms", 0.0)
        baseline_rt = metrics.get("baseline_rt_ms", 0.0)
        if baseline_rt > 0 and avg_rt > baseline_rt * 3:
            rt_ratio = avg_rt / baseline_rt
            findings.append({
                "type": "rt_spike",
                "severity": "critical",
                "message": (
                    f"RT 突变: avg={avg_rt:.1f}ms, baseline={baseline_rt:.1f}ms "
                    f"(比值={rt_ratio:.1f}x >3x)"
                ),
                "value": rt_ratio,
                "threshold": 3.0,
            })
            evidence.append(
                f"avg_rt={avg_rt:.1f}ms, baseline_rt={baseline_rt:.1f}ms, "
                f"RT为基线的{rt_ratio:.1f}倍"
            )

        timeout_count = metrics.get("timeout_count", 0)
        if timeout_count > 10:
            findings.append({
                "type": "timeout",
                "severity": "critical",
                "message": f"RPC 超时次数过多: {timeout_count} (>10)",
                "value": timeout_count,
                "threshold": 10,
            })
            evidence.append(f"timeout_count={timeout_count}, 超过临界阈值10")
        elif timeout_count > 0:
            findings.append({
                "type": "timeout",
                "severity": "warning",
                "message": f"存在 RPC 超时: {timeout_count} 次",
                "value": timeout_count,
                "threshold": 0,
            })
            evidence.append(f"timeout_count={timeout_count}, 存在超时")

        call_volume = metrics.get("call_volume", 0)
        baseline_volume = metrics.get("baseline_volume", 0)
        if baseline_volume > 0:
            volume_ratio = call_volume / baseline_volume
            if volume_ratio > 3.0:
                findings.append({
                    "type": "call_volume_spike",
                    "severity": "warning",
                    "message": (
                        f"调用量激增: current={call_volume}, baseline={baseline_volume} "
                        f"(比值={volume_ratio:.1f}x >3x)"
                    ),
                    "value": volume_ratio,
                    "threshold": 3.0,
                })
                evidence.append(
                    f"call_volume={call_volume}, baseline={baseline_volume}, "
                    f"调用量为基线的{volume_ratio:.1f}倍"
                )

        if len(findings) == 0:
            confidence = 0.3
        elif len(findings) == 1:
            confidence = 0.6
        elif len(findings) == 2:
            confidence = 0.8
        else:
            confidence = 0.9

        # L1 发现了 RPC 相关异常时提升置信度
        if l1_rpc_hints and findings:
            confidence = min(confidence + 0.05, 1.0)
            evidence.append(f"L1 维度分析也发现了 RPC 调用相关异常: {', '.join(l1_rpc_hints[:2])}")
        elif l1_rpc_hints and not findings:
            confidence = max(confidence, 0.5)
            evidence.append(f"L1 维度分析提示 RPC 调用相关异常: {', '.join(l1_rpc_hints[:2])}")

        return {"findings": findings, "evidence": evidence, "confidence": confidence}

    async def _conclude(self, state: _RPCSubGraphState) -> dict:
        """组装 SubAgentResult。"""
        result = SubAgentResult(
            agent_name=self.agent_name,
            dimension="rpc",
            findings=state.get("findings", []),
            confidence=state.get("confidence", 0.0),
            evidence=state.get("evidence", []),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            error=state.get("error"),
        )
        return {"result": result}

    async def analyze(self, alert: dict, context: dict) -> SubAgentResult:
        """执行 RPC 领域分析，返回 SubAgentResult。"""
        subgraph = self.build_subgraph()
        initial_state: _RPCSubGraphState = {
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
                dimension="rpc",
                confidence=0.0,
                timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
                error="子图执行未返回结果",
            )
        return result
