"""反馈请求数据模型。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：FeedbackRequest Pydantic 模型</td><td>reviewer-fix-15</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    """用户满意度反馈请求。

    用于 POST /feedback 端点的结构化校验。
    trace_id 可通过请求体或 URL query string 传入。
    """

    trace_id: str = Field(
        default="",
        description="分析追踪 ID（可通过请求体或 URL query string 传入）",
    )
    satisfaction: int = Field(
        ...,
        ge=1,
        le=5,
        description="满意度评分 1-5",
    )
    root_cause_correct: Optional[bool] = Field(
        default=None,
        description="根因是否正确",
    )
    comment: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="用户评论（最大 1000 字符）",
    )
    feedback_token: Optional[str] = Field(
        default=None,
        description="反馈 token（也可通过 URL query string 传入）",
    )
