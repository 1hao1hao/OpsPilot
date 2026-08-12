"""告警事件数据模型。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：AlertEvent, ParsedAlert 数据模型</td><td>REQ: 20260713-总体架构, TECH: 04b §3.3.1</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class AlertType(str, Enum):
    """告警类型枚举。"""

    TIMEOUT = "timeout"
    ERROR_RATE = "error_rate"
    RESOURCE = "resource"
    CUSTOM = "custom"


class AlertSeverity(str, Enum):
    """告警严重度枚举。"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class AlertEvent(BaseModel):
    """原始告警事件。

    从告警平台或 API 接收的原始告警数据。
    """

    alert_id: str = Field(..., description="告警唯一标识")
    service_name: str = Field(..., description="告警服务名称")
    alert_type: AlertType = Field(..., description="告警类型")
    severity: AlertSeverity = Field(..., description="告警严重度")
    timestamp: str = Field(..., description="告警时间戳 ISO 8601")
    description: str = Field(default="", description="告警描述")
    labels: dict[str, str] = Field(default_factory=dict, description="标签: cluster, env, app 等")


class ParsedAlert(BaseModel):
    """解析后的告警信息。

    intake 节点的输出，从 AlertEvent 提取关键字段并标准化。
    """

    alert_id: str = Field(..., description="告警唯一标识")
    service_name: str = Field(..., description="服务名称")
    alert_type: AlertType = Field(..., description="告警类型")
    severity: AlertSeverity = Field(..., description="严重度")
    timestamp: str = Field(..., description="告警时间戳")
    description: str = Field(default="", description="告警描述")
    labels: dict[str, str] = Field(default_factory=dict, description="标签")
    time_window_start: Optional[str] = Field(default=None, description="分析时间窗口起始")
    time_window_end: Optional[str] = Field(default=None, description="分析时间窗口结束")
    cluster: Optional[str] = Field(default=None, description="集群名称（从 labels 提取）")
    env: Optional[str] = Field(default=None, description="环境（从 labels 提取）")
    app: Optional[str] = Field(default=None, description="应用名（从 labels 提取）")
