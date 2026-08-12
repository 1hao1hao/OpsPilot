"""根因结果数据模型。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：RootCauseResult, RootCauseCandidate</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.4</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RootCauseCandidate(BaseModel):
    """根因候选项。"""

    rank: int = Field(..., description="排名（1 为 Top-1）")
    root_cause: str = Field(..., description="根因描述")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="置信度")
    evidence_chain: list[str] = Field(default_factory=list, description="证据链")
    matched_rule: Optional[str] = Field(default=None, description="匹配的专家规则 ID（如 R001）")
    source: str = Field(default="llm", description="来源: rule（规则命中）或 llm（LLM 推理）")


class RootCauseResult(BaseModel):
    """根因定位结果。"""

    candidates: list[RootCauseCandidate] = Field(default_factory=list, description="Top-3 候选")
    best_candidate: Optional[RootCauseCandidate] = Field(default=None, description="Top-1 最佳候选")
    anomalies_detected: list[dict] = Field(default_factory=list, description="检测到的异常列表")
    rule_matched: bool = Field(default=False, description="是否命中专家规则")
    llm_used: bool = Field(default=False, description="是否使用了 LLM 推理")
    trace_id: str = Field(default="", description="追踪 ID")
    timestamp: str = Field(default="", description="结果生成时间戳")
    suggestions: list[str] = Field(default_factory=list, description="建议措施列表（PRD-04 §8 模板）")
    evidence_chain: list[dict] = Field(default_factory=list, description="证据链（按置信度排序，最多 5 条）")
    matched_rules: list[dict] = Field(default_factory=list, description="命中的规则列表 [{rule_id, name}]")
