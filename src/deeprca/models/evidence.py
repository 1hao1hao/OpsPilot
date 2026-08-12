"""证据与子 Agent 结果数据模型。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：Evidence, EvidencePool, SubAgentResult</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.1</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EvidenceLevel(str, Enum):
    """证据等级。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Evidence(BaseModel):
    """单条证据。"""

    source: str = Field(..., description="证据来源（工具名或 Agent 名）")
    dimension: str = Field(..., description="分析维度: change/upstream/downstream/cluster/errorlog/problem")
    finding: str = Field(..., description="发现的异常线索描述")
    level: EvidenceLevel = Field(default=EvidenceLevel.MEDIUM, description="证据等级")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度 0.0~1.0")
    data: Optional[dict] = Field(default=None, description="原始数据")
    timestamp: str = Field(default="", description="证据生成时间戳")


class EvidencePool(BaseModel):
    """证据池，汇聚所有子 Agent 和工具调用的证据。"""

    evidences: list[Evidence] = Field(default_factory=list, description="所有证据列表")

    def add(self, evidence: Evidence) -> None:
        """添加证据到池中。"""
        self.evidences.append(evidence)

    def sorted_by_confidence(self) -> list[Evidence]:
        """按证据等级 + 置信度降序排序。

        PRD-02 §2.4: 先按 EvidenceLevel 排序（HIGH > MEDIUM > LOW），
        同等级内按置信度降序。
        """
        level_order = {EvidenceLevel.HIGH: 3, EvidenceLevel.MEDIUM: 2, EvidenceLevel.LOW: 1}
        return sorted(
            self.evidences,
            key=lambda e: (level_order.get(e.level, 0), e.confidence),
            reverse=True,
        )

    def filter_by_dimension(self, dimension: str) -> list[Evidence]:
        """按维度过滤。"""
        return [e for e in self.evidences if e.dimension == dimension]

    def to_summary(self) -> dict:
        """生成证据池摘要。"""
        return {
            "total": len(self.evidences),
            "by_level": {
                level.value: sum(1 for e in self.evidences if e.level == level)
                for level in EvidenceLevel
            },
            "by_dimension": {
                dim: sum(1 for e in self.evidences if e.dimension == dim)
                for dim in {"change", "upstream", "downstream", "cluster", "errorlog", "problem"}
            },
            "top_evidences": [e.model_dump() for e in self.sorted_by_confidence()[:5]],
        }


class SubAgentResult(BaseModel):
    """子 Agent / 维度分析结果。"""

    agent_name: str = Field(..., description="Agent 或工具名称")
    dimension: str = Field(..., description="分析维度: change/upstream/downstream/cluster/errorlog/problem")
    findings: list[dict] = Field(default_factory=list, description="发现的异常线索列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    evidence: list[str] = Field(default_factory=list, description="证据链描述列表")
    timestamp: str = Field(default="", description="结果生成时间戳")
    error: Optional[str] = Field(default=None, description="错误信息（如果执行失败）")
