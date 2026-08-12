"""API WebSocket 实时推送模块。

@changelog
<table>
<tr><th>版本</th><th>变更说明</th><th>关联</th></tr>
<tr><td>0.1.0</td><td>初始创建：WebSocket 连接管理器</td><td>REQ: 20260713-总体架构</td></tr>
</table>
@author DeepRCA Team
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import WebSocket

__all__ = ["ConnectionManager"]


class ConnectionManager:
    """WebSocket 连接管理器，按 trace_id 管理连接。"""

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, trace_id: str, ws: WebSocket) -> None:
        """接受连接并注册。"""
        await ws.accept()
        if trace_id not in self._connections:
            self._connections[trace_id] = []
        self._connections[trace_id].append(ws)

    def disconnect(self, trace_id: str, ws: WebSocket) -> None:
        """移除连接。"""
        if trace_id in self._connections:
            self._connections[trace_id] = [
                w for w in self._connections[trace_id] if w is not ws
            ]
            if not self._connections[trace_id]:
                del self._connections[trace_id]

    async def broadcast(self, trace_id: str, data: dict[str, Any]) -> None:
        """向指定 trace_id 的所有连接推送消息。"""
        if trace_id not in self._connections:
            return
        message = json.dumps(data, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in self._connections[trace_id]:
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(trace_id, ws)
