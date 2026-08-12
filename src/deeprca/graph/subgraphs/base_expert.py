"""L2 领域专家子 Agent 基类。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：BaseExpertAgent 抽象基类</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.3</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from langgraph.graph.state import CompiledStateGraph

from deeprca.models import SubAgentResult

__all__ = ["BaseExpertAgent"]


class BaseExpertAgent(ABC):
    """领域专家子 Agent 基类。

    所有 L2 领域专家（DB/Redis/Mafka/RPC）继承此基类，
    通过 LangGraph 子图实现 collect → analyze → conclude 三节点流程。
    """

    @property
    @abstractmethod
    def agent_name(self) -> str:
        """子 Agent 名称。"""
        ...

    @property
    @abstractmethod
    def trigger_keywords(self) -> list[str]:
        """触发关键词列表。

        L1 维度分析发现这些关键词相关的异常时，按需派生调用此子 Agent。
        """
        ...

    @abstractmethod
    def build_subgraph(self) -> CompiledStateGraph:
        """构建领域专家子图。

        子图节点流程: collect → analyze → conclude

        Returns:
            编译后的 LangGraph 子图
        """
        ...

    @abstractmethod
    async def analyze(self, alert: dict, context: dict) -> SubAgentResult:
        """执行领域分析。

        Args:
            alert: 告警信息（ParsedAlert 序列化）
            context: L1 分析上下文（含已发现的异常线索）

        Returns:
            子 Agent 分析结果
        """
        ...

    def should_trigger(self, evidence_findings: list[dict]) -> bool:
        """判断是否应该触发此子 Agent。

        检查 L1 维度分析发现的异常线索是否包含触发关键词。

        Args:
            evidence_findings: L1 已发现的异常线索列表

        Returns:
            True 表示应该触发
        """
        for finding in evidence_findings:
            finding_str = str(finding).lower()
            for keyword in self.trigger_keywords:
                if keyword.lower() in finding_str:
                    return True
        return False
