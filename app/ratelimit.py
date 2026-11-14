"""滑动窗口限流器(进程内存版)

用途:
- 弹幕防刷屏:每个用户 N 秒内最多发 M 条
- 登录防暴力破解:同一 IP+用户名 的失败次数超过上限后暂时拒绝

注意:状态保存在进程内存里,只在「单进程运行」时准确。
本应用本来就因 WebSocket 连接表 / AI 队列在内存中而必须单进程运行,所以一致;
将来要多进程/多台服务器扩展时,需改用 Redis 等共享存储。
"""
import time
from collections import deque


def client_ip(request) -> str:
    """レート制限のキーに使うクライアントの IP。
    Nginx などの後ろに置く場合は uvicorn を --proxy-headers 付きで起動しないと、全員がプロキシの IP になる"""
    return request.client.host if request.client else "unknown"


class SlidingWindowLimiter:
    def __init__(self, limit: int, window: float) -> None:
        self.limit = limit
        self.window = window
        self._hits: "dict[str, deque[float]]" = {}

    def _prune(self, key: str, now: float) -> "deque[float]":
        """删除窗口外的旧记录;队列变空时把 key 从字典移除,避免内存无限增长"""
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            del self._hits[key]
        return q

    def is_limited(self, key: str) -> bool:
        """只检查不记录:窗口内次数是否已达上限"""
        return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> bool:
        """记录一次;未超限返回 True,已超限返回 False(超限的这次不计入)"""
        now = time.monotonic()
        q = self._prune(key, now)
        if len(q) >= self.limit:
            return False
        q.append(now)
        self._hits[key] = q
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
