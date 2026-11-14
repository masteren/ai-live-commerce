"""スライディングウィンドウ方式のレート制限(プロセスのメモリ上で管理)

用途:
- コメントの連投防止:1人あたり N 秒に M 件まで
- ログインの総当たり対策:同じ IP+ユーザー名 の失敗が上限を超えたら、しばらく拒否する

注意:状態はプロセスのメモリにあるので、「1プロセスで動かす」ときだけ正確。
このアプリは WebSocket の接続一覧や AI のキューもメモリ上にあり、もともと1プロセスで動かす前提なので問題ない。
複数プロセス・複数サーバに広げるときは、Redis などの共有ストレージに移す必要がある。
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
        """ウィンドウより古い記録を消す。空になったキーは辞書から外す(メモリが増え続けないように)"""
        q = self._hits.get(key)
        if q is None:
            return deque()
        while q and now - q[0] >= self.window:
            q.popleft()
        if not q:
            del self._hits[key]
        return q

    def is_limited(self, key: str) -> bool:
        """記録はせず確認だけ:ウィンドウ内の回数が上限に達しているか"""
        return len(self._prune(key, time.monotonic())) >= self.limit

    def hit(self, key: str) -> bool:
        """1回記録する。上限内なら True、上限を超えていれば False(超えた分は記録しない)"""
        now = time.monotonic()
        q = self._prune(key, now)
        if len(q) >= self.limit:
            return False
        q.append(now)
        self._hits[key] = q
        return True

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
