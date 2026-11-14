"""外部サービス(LLM・VOICEVOX)への HTTP 接続を使い回す

httpx.AsyncClient を `async with` で毎回作ると、リクエストのたびに TCP 接続(HTTPS なら TLS の握手も)をやり直す。
1つのサービスにつき1つのクライアントを持ち続ければ、接続プールが前回の接続を再利用するので、その時間がなくなる。

- クライアントは最初に使うときに作る(import 時に作らない:イベントループがまだ無いため)
- 閉じられていたら作り直す
- サーバ終了時に aclose() で閉じる(app/main.py の lifespan)
"""
from typing import Optional

import httpx


class SharedClient:
    def __init__(self, timeout: float, base_url: str = "",
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.timeout, self.base_url, self.transport = timeout, base_url, transport
        self._client: Optional[httpx.AsyncClient] = None

    def get(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, base_url=self.base_url, transport=self.transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
