"""气氛组机器人:自动发日语弹幕维持直播间热度(就活演示防冷场)

- 管理后台 API 开关(默认关);状态存内存,重启后回到默认
- 随机间隔 4〜9 秒发一条;弹幕落库(is_bot=True)+ 全员广播
- 少量弹幕(BOT_ASK_PROB)带疑问词,会自然触发 AI 主播回答,演示时更生动;
  其余闲聊弹幕不进 LLM 队列,不额外消耗 API

■ 弹幕池(OpenAI 兼容 API,可选)
  配置 openai_api_key 后,按当前在售商品批量生成一池弹幕(一次调用生成几十条),
  机器人从池里抽取,抽过即删避免重复;池子低于水位线时后台自动补一批。
  未配置 key / 生成失败 → 退回内置固定列表,机器人不中断
"""
import asyncio
import json
import logging
import random
import time
from typing import Optional

import httpx
from sqlalchemy import select

from .ai_host import host, looks_like_question
from .config import settings
from .database import SessionLocal
from .models import Comment, Product
from .ws import manager

logger = logging.getLogger(__name__)

BOT_ASK_PROB = 0.15   # 提问型弹幕占比(会触发主播回答)
POOL_LOW_WATER = 10   # 池内剩余低于此数时触发后台补货

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


async def _generate_pool() -> "tuple[list[str], list[str]]":
    """按当前在售商品批量生成弹幕(OpenAI 兼容 /chat/completions)"""
    async with SessionLocal() as db:
        rows = await db.execute(select(Product).where(Product.is_active == True))  # noqa: E712
        products = list(rows.scalars())
    lines = "\n".join(f"- {p.name} ¥{p.price:,} - {p.description}" for p in products) or "- (商品情報なし)"

    n_ask = max(1, int(settings.bot_pool_batch * BOT_ASK_PROB * 2))  # 提问消耗更慢,少生成些
    n_chat = settings.bot_pool_batch - n_ask
    prompt = _POOL_PROMPT.format(products=lines, n_chat=n_chat, n_ask=n_ask)

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
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
            self._schedule_refill()  # 开启时预热弹幕池

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._loop())
        return self._task

    # ---------- 弹幕池 ----------
    def _pick(self, ask: bool) -> str:
        """优先从 LLM 弹幕池抽取(抽过即删);池空退回内置列表"""
        pool = self._pool_ask if ask else self._pool_chat
        if pool:
            text = pool.pop(random.randrange(len(pool)))
        else:
            text = random.choice(ASK_LINES if ask else CHAT_LINES)
        if len(self._pool_chat) + len(self._pool_ask) < POOL_LOW_WATER:
            self._schedule_refill()
        return text

    def _schedule_refill(self) -> None:
        if self._refilling or not settings.openai_api_key:
            return
        self._refilling = True
        asyncio.create_task(self._refill())

    async def _refill(self) -> None:
        try:
            chat, ask = await _generate_pool()
            self._pool_chat.extend(chat)
            self._pool_ask.extend(ask)
            logger.info("弹幕池补充完成: 闲聊+%d 提问+%d", len(chat), len(ask))
        except Exception as e:
            # 降级链:生成失败 → 内置列表兜底,机器人照常发
            logger.warning("弹幕池生成失败,使用内置列表: %s", e)
        finally:
            self._refilling = False

    # ---------- 发送循环 ----------
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
                # 提问型弹幕交给主播;闲聊不进队列(不耗 LLM)
                if ask or looks_like_question(text):
                    await host.on_comment(nickname, text)
                else:
                    host.last_activity_at = time.time()
            except Exception as e:
                logger.warning("bot 弹幕发送失败: %s", e)


bot = DanmakuBot()
