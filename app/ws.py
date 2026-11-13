"""WebSocket 连接管理:观众连接、弹幕广播、在线人数统计"""
import asyncio
import json
from typing import Any

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self.peak_viewers: int = 0
        self._lock = asyncio.Lock()

    @property
    def viewer_count(self) -> int:
        return len(self.connections)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self.connections.add(ws)
            self.peak_viewers = max(self.peak_viewers, len(self.connections))
        await self.broadcast({"type": "viewers", "count": self.viewer_count})

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self.connections.discard(ws)
        await self.broadcast({"type": "viewers", "count": self.viewer_count})

    async def broadcast(self, message: dict[str, Any]) -> None:
        """向所有在线观众并发广播;发送失败/超时(卡死)的连接剔除,不拖累全房间"""
        data = json.dumps(message, ensure_ascii=False)
        conns = list(self.connections)
        if not conns:
            return
        results = await asyncio.gather(*(self._safe_send(ws, data) for ws in conns))
        dead = [ws for ws, ok in zip(conns, results) if not ok]
        if dead:
            async with self._lock:
                for ws in dead:
                    self.connections.discard(ws)

    @staticmethod
    async def _safe_send(ws: WebSocket, data: str) -> bool:
        try:
            await asyncio.wait_for(ws.send_text(data), timeout=5)
            return True
        except Exception:
            return False


manager = ConnectionManager()
