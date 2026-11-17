"""盛り上げ用 BOT:日本語のコメントを自動で流し、配信が寂しくならないようにする(デモ用)

- 管理画面の API でオン・オフ(既定はオフ)。状態はメモリ上にあり、再起動すると既定に戻る
- 4〜9 秒の間隔で1件ずつ送る。コメントは保存し(is_bot=True)、全員に配信する
- 一部(BOT_ASK_PROB)は質問の形にして、AI ホストが自然に答えるようにする(デモが生き生きする)。
  残りの雑談は LLM のキューに入れないので、API の費用は増えない

■ コメントの在庫(OpenAI 互換 API、任意)
  openai_api_key が設定されていれば、販売中の商品をもとにコメントをまとめて作っておく(1回の呼び出しで数十件)。
  BOT はそこから取り出し、使ったものは消して重複を避ける。残りが少なくなったら裏で補充する。
  キーがない・生成に失敗した → 組み込みの定型コメントに切り替え、BOT は止めない
"""
import asyncio
import json
import logging
import random
import time
from typing import Optional

from .ai_host import host, looks_like_question
from .config import settings
from .http_pool import SharedClient
from .database import SessionLocal
from .models import Comment
from .queries import list_active_products
from .ws import manager

logger = logging.getLogger(__name__)

BOT_ASK_PROB = 0.15   # 質問の形のコメントの割合(AI ホストが答える)
POOL_LOW_WATER = 10   # 在庫がこの数を下回ったら裏で補充する

BOT_NAMES = [
    "yuki_t", "田中M", "sato_1988", "みほ", "k_watanabe", "shopper_jp",
    "hiro55", "aoi.m", "tk_office", "nana_k", "matsu", "R.Suzuki",
]

CHAT_LINES = [
    "初めて見ました", "マグ愛用してます", "音質よさそうですね", "迷いますね…",
    "前回買った美容液よかったです", "仕事の休憩中に見てます", "参考になります",
    "友人にすすめられて来ました", "検討します", "説明わかりやすいです",
    "保温マグ気になってました", "セール価格ありがたい",
]

ASK_LINES = [
    "イヤホンの音質はどうですか?", "在庫はまだありますか?", "配送はどのくらいかかりますか?",
    "カラーは選べますか?", "セールはいつまでですか?", "美容液は敏感肌でも使えますか?",
    "マグは食洗機に対応していますか?", "返品はできますか?", "おすすめはどれですか?",
]

_POOL_PROMPT = """あなたは日本のライブコマース配信の視聴者コメント(弾幕)を生成するツールです。

# 販売中の商品
{products}

# タスク
上記の商品を見ている一般視聴者になりきって、自然な日本語コメントを生成してください。
- chat: 雑談・感想コメントを{n_chat}件(例:「音質よさそう」「迷いますね…」のような短い独り言)
- ask: 商品への質問コメントを{n_ask}件(在庫・色・配送・使い方など、配信者が答えられる具体的な質問)

# ルール
- 1件は5〜25文字程度、話し言葉、絵文字なし
- 商品名や特徴に具体的に触れるコメントを半分程度混ぜる
- 同じ言い回しを繰り返さない
- 次のJSONのみで返答(前置き・コードブロック禁止):
  {{"chat": ["...", "..."], "ask": ["...", "..."]}}"""


bot_http = SharedClient(60)      # コメント生成用の接続(使い回す)


async def _generate_pool() -> "tuple[list[str], list[str]]":
    """販売中の商品をもとに、コメントをまとめて作る(OpenAI 互換の /chat/completions)"""
    async with SessionLocal() as db:
        products = await list_active_products(db)
    lines = "\n".join(f"- {p.name} ¥{p.price:,} - {p.description}" for p in products) or "- (商品情報なし)"

    n_ask = max(1, int(settings.bot_pool_batch * BOT_ASK_PROB * 2))  # 質問は使うペースが遅いので、少なめに作る
    n_chat = settings.bot_pool_batch - n_ask
    prompt = _POOL_PROMPT.format(products=lines, n_chat=n_chat, n_ask=n_ask)

    res = await bot_http.get().post(
        f"{settings.openai_base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.openai_model,
            "messages": [{"role": "user", "content": prompt}],
        },
    )
    res.raise_for_status()
    data = res.json()

    raw = data["choices"][0]["message"]["content"]
    raw = raw.replace("```json", "").replace("```", "").strip()
    j = json.loads(raw)
    chat = [s.strip() for s in j.get("chat", []) if isinstance(s, str) and s.strip()]
    ask = [s.strip() for s in j.get("ask", []) if isinstance(s, str) and s.strip()]
    return chat, ask


class DanmakuBot:
    def __init__(self) -> None:
        self.enabled: bool = False
        self._task: Optional[asyncio.Task] = None
        self._pool_chat: list[str] = []
        self._pool_ask: list[str] = []
        self._refilling: bool = False

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self._schedule_refill()  # オンにしたときに、コメントの在庫を作り始める

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._loop())
        return self._task

    # ---------- コメントの在庫 ----------
    def _pick(self, ask: bool) -> str:
        """LLM で作った在庫から優先して取り出す(使ったら消す)。空なら組み込みの定型コメント"""
        pool = self._pool_ask if ask else self._pool_chat
        if pool:
            text = pool.pop(random.randrange(len(pool)))
        else:
            text = random.choice(ASK_LINES if ask else CHAT_LINES)
        if len(self._pool_chat) + len(self._pool_ask) < POOL_LOW_WATER:
            self._schedule_refill()
        return text

    def _schedule_refill(self) -> None:
        # LLM_PROVIDER=none(テスト・デモ用)のときは、キーが環境変数にあっても外部 API を呼ばない
        if self._refilling or not settings.openai_api_key or settings.llm_provider.lower() == "none":
            return
        self._refilling = True
        asyncio.create_task(self._refill())

    async def _refill(self) -> None:
        try:
            chat, ask = await _generate_pool()
            self._pool_chat.extend(chat)
            self._pool_ask.extend(ask)
            logger.info("BOT のコメントを補充しました: 雑談+%d 質問+%d", len(chat), len(ask))
        except Exception as e:
            # 縮退:生成に失敗 → 組み込みの定型コメントで続ける
            logger.warning("BOT のコメント生成に失敗したため、定型コメントを使います: %s", e)
        finally:
            self._refilling = False

    # ---------- 送信のループ ----------
    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(random.uniform(4, 9))
            if not self.enabled or manager.viewer_count == 0:
                continue
            ask = random.random() < BOT_ASK_PROB
            nickname = random.choice(BOT_NAMES)
            text = self._pick(ask)
            try:
                async with SessionLocal() as db:
                    db.add(Comment(nickname=nickname, text=text, is_bot=True))
                    await db.commit()
                await manager.broadcast({"type": "comment", "nickname": nickname, "text": text})
                # 質問の形なら AI ホストに渡す。雑談はキューに入れない(LLM を使わない)
                if ask or looks_like_question(text):
                    await host.on_comment(nickname, text, is_bot=True)
                else:
                    host.last_activity_at = time.time()
            except Exception as e:
                logger.warning("BOT のコメント送信に失敗しました: %s", e)


bot = DanmakuBot()
