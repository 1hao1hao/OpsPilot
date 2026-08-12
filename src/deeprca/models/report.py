"""分析报告数据模型。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：AnalysisReport</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.5</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AnalysisReport(BaseModel):
    """故障分析报告。

    reporter 节点的输出，包含完整的分析结论和证据链。
    """

    trace_id: str = Field(..., description="追踪 ID")
    alert_id: str = Field(..., description="告警 ID")
    service_name: str = Field(..., description="服务名称")
    severity: str = Field(..., description="告警严重度")
    status: str = Field(default="completed", description="分析状态: completed/failed/timeout")
    root_cause: Optional[str] = Field(default=None, description="根因结论（Top-1）")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="根因置信度")
    top_candidates: list[dict] = Field(default_factory=list, description="Top-3 候选根因")
    key_evidence: list[str] = Field(default_factory=list, description="关键证据链")
    analysis_duration: Optional[float] = Field(default=None, description="分析耗时（秒）")
    dimensions_analyzed: list[str] = Field(default_factory=list, description="已分析维度列表")
    sub_agents_invoked: list[str] = Field(default_factory=list, description="调用的子 Agent 列表")
    suggestions: list[str] = Field(default_factory=list, description="建议措施列表")
    satisfaction_url: Optional[str] = Field(default=None, description="满意度反馈 URL")
    timestamp: str = Field(default="", description="报告生成时间戳")
    feedback_token: Optional[str] = Field(default=None, description="满意度反馈 token")
