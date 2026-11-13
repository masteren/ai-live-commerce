"""AI 虚拟主播「ハナ」核心逻辑

■ 人设(与前端立绘、VOICEVOX 声线联动)
  花奈(はな)— 公式オンラインストア所属のAI販売アシスタント。
  丁寧で落ち着いた接客(です・ます調)。商品知識が正確で、押し売りをしない。
  視聴者の質問に的確に答え、必要な情報だけを簡潔に案内するプロフェッショナル。

■ 情绪系统
  LLM 每次回答同时输出 emotion ∈ {normal, happy, excited, troubled, shy}
  → 前端切换立绘表情;后端映射 VOICEVOX 声线风格与语速(EMOTION_VOICE)

■ 应答策略(混合模式)
  规则判定"像问题"的弹幕必答;纯闲聊按 chat_reply_prob 概率随机接一句
  单一 worker 顺序消费队列 + 最小回答间隔限流;积压只保留最新几条

■ 降级链
  无 API key / LLM 失败 → 规则回复;TTS 失败 → 纯文字。任何一环挂掉直播不中断
  (情绪 → 声音参数的对应关系由各 TTS Provider 自己决定,见 tts.py)
"""
import asyncio
import json
import logging
import random
import re
import time
from typing import Optional

import httpx
from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import Comment, Product
from .tts import EMOTIONS, tts
from .ws import manager

logger = logging.getLogger(__name__)

MAX_BACKLOG = 3       # 弹幕队列积压上限(旧问题直接放弃)
HISTORY_TURNS = 6     # 携带的历史问答轮数

# 「像问题」的规则特征:疑问符 / 疑问词 / 购买意图词
_QUESTION_RE = re.compile(
    r"[??]|どう|どんな|いくら|何|なに|できる|いつ|どこ|だれ|誰"
    r"|なぜ|どれ|どっち|教えて|ください|おすすめ|オススメ|比べ|違い|サイズ|色|カラー"
    r"|在庫|送料|配送|届く|支払|返品|保証|セール|割引|クーポン"
)

FALLBACK_REPLIES = [
    ("コメントありがとうございます。気になる商品がございましたら、商品名でお尋ねください。", "normal"),
    ("ご質問ありがとうございます。画面下の商品カードから詳細もご確認いただけます。", "normal"),
    ("ありがとうございます。全国送料無料でお届けしております。", "happy"),
]

THANKS_TEMPLATES = [
    "{name}様、「{product}」をご購入いただきありがとうございます。心を込めてお届けいたします。",
    "{name}様、「{product}」のご注文を承りました。ありがとうございます!",
    "{name}様、お買い上げありがとうございます。「{product}」、ぜひお楽しみください。",
]

IDLE_LINES = [
    ("ご視聴ありがとうございます。AIアシスタントの{host}です。ご質問はコメントでお気軽にどうぞ。", "normal"),
    ("本日ご紹介の商品は、期間限定の特別価格でご案内しております。", "happy"),
    ("配送は全国送料無料、ご注文から3〜5日でお届けいたします。", "normal"),
    ("未開封の商品は到着後7日以内の返品を承っております。安心してお買い求めください。", "normal"),
    ("在庫に限りのある商品もございます。気になる方はお早めにご検討ください。", "normal"),
]


def looks_like_question(text: str) -> bool:
    return bool(_QUESTION_RE.search(text))


class AIHost:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.history: list[dict] = []
        self.last_reply_at: float = 0.0
        self.last_activity_at: float = time.time()
        self._product_names: list[str] = []

    # ---------- 对外入口 ----------
    async def on_comment(self, nickname: str, text: str) -> None:
        """混合应答策略:问题/含商品名必答;闲聊按概率接话"""
        self.last_activity_at = time.time()
        must = looks_like_question(text) or any(n and n in text for n in self._product_names)
        if not must and random.random() > settings.chat_reply_prob:
            return
        await self._enqueue({"kind": "reply", "nickname": nickname, "text": text})

    async def enqueue_thanks(self, nickname: str, product_name: str) -> None:
        """购买后语音道谢(模板生成,不耗 LLM)"""
        await self._enqueue({"kind": "thanks", "nickname": nickname, "product": product_name})

    def start(self) -> list[asyncio.Task]:
        return [
            asyncio.create_task(self._worker()),
            asyncio.create_task(self._idle_worker()),
        ]

    # ---------- 内部 ----------
    async def _enqueue(self, item: dict) -> None:
        while self.queue.qsize() >= MAX_BACKLOG:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await self.queue.put(item)

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            wait = settings.reply_min_interval - (time.time() - self.last_reply_at)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                if item["kind"] == "thanks":
                    text = random.choice(THANKS_TEMPLATES).format(
                        name=item["nickname"], product=item["product"])
                    await self._speak(text, None, "excited")
                else:
                    reply, product_id, emotion = await self._generate(item["nickname"], item["text"])
                    await self._speak(reply, product_id, emotion)
            except Exception:
                logger.exception("回答の生成・送信に失敗しました")
            self.last_reply_at = time.time()
            self.last_activity_at = time.time()

    async def _idle_worker(self) -> None:
        i = 0
        while True:
            await asyncio.sleep(5)
            # 异常保护:这里抛出异常的话 task 会静默结束,之后再也不会自动播报
            try:
                if time.time() - self.last_activity_at >= settings.idle_interval and self.queue.empty():
                    text, emotion = IDLE_LINES[i % len(IDLE_LINES)]
                    i += 1
                    await self._speak(text.format(host=settings.host_name), None, emotion, save=False)
                    self.last_activity_at = time.time()
            except Exception:
                logger.exception("自動トークに失敗しました(次の周期で再試行)")
                self.last_activity_at = time.time()

    async def _speak(self, text: str, product_id: Optional[int], emotion: str, save: bool = True) -> None:
        """主播发言统一出口:TTS → 落库 → 广播"""
        emotion = emotion if emotion in EMOTIONS else "normal"
        audio_url = await tts.synthesize(text, emotion)
        if save:
            async with SessionLocal() as db:
                db.add(Comment(nickname=settings.host_name, text=text, is_host=True))
                await db.commit()
        await manager.broadcast({
            "type": "host_reply",
            "nickname": settings.host_name,
            "text": text,
            "product_id": product_id,
            "emotion": emotion,
            "audio_url": audio_url,
        })

    async def _generate(self, nickname: str, question: str) -> "tuple[str, Optional[int], str]":
        products = await self._load_products()
        if not settings.anthropic_api_key:
            reply, emotion = random.choice(FALLBACK_REPLIES)
            return reply, None, emotion

        system = self._build_system_prompt(products)
        self.history = self.history[-HISTORY_TURNS * 2:]
        messages = self.history + [{"role": "user", "content": f"{nickname}: {question}"}]

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                res = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.anthropic_api_key,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": settings.anthropic_model,
                        "max_tokens": 300,
                        # 直播即答场景:短回复延迟优先,关闭思考
                        "thinking": {"type": "disabled"},
                        "system": system,
                        "messages": messages,
                    },
                )
                res.raise_for_status()
                data = res.json()
        except Exception as e:
            # 降级链:LLM 超时/限流/网络故障 → 规则回复,直播不冷场
            logger.warning("LLM 调用失败,降级为规则回复: %s", e)
            reply, emotion = random.choice(FALLBACK_REPLIES)
            return reply, None, emotion

        raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        raw = raw.replace("```json", "").replace("```", "").strip()
        reply, product_id, emotion = raw, None, "normal"
        try:
            j = json.loads(raw)
            reply = j.get("reply") or raw
            pid = j.get("product_id")
            if isinstance(pid, int) and any(p.id == pid for p in products):
                product_id = pid
            if j.get("emotion") in EMOTIONS:
                emotion = j["emotion"]
        except json.JSONDecodeError:
            pass

        self.history = messages + [{"role": "assistant", "content": raw}]
        return reply, product_id, emotion

    def _build_system_prompt(self, products: list[Product]) -> str:
        lines = "\n".join(
            f"id={p.id}: {p.name} ¥{p.price:,}(通常¥{p.original_price:,}) 在庫{p.stock}点 - {p.description}"
            for p in products
        )
        return f"""あなたは「{settings.host_name}(花奈)」、日本の公式オンラインストアのライブ配信を担当するAI販売アシスタントです。

# キャラクター設定
- 丁寧で落ち着いた接客のプロフェッショナル(です・ます調)
- 商品知識(価格・在庫・仕様)が正確で、根拠のない説明はしない
- 押し売りはせず、視聴者の質問や状況に合わせて最適な商品を簡潔に提案する
- 視聴者は「◯◯様」または「◯◯さん」とお呼びする
- 温かみはあるが、過度にくだけた表現・若者言葉・顔文字は使わない

# 販売中の商品
{lines}

# 店舗情報
- 配送: 全国送料無料、3〜5日でお届け
- 返品: 未開封なら到着後7日以内可
- 支払い: クレジットカード / コンビニ払い / PayPay

# ルール
- 回答は必ず日本語、{settings.reply_max_chars}文字以内、絵文字は使わない
- 価格・在庫は上記データのみを根拠にする。不明な点は正直に「確認いたします」と答える
- 雑談には礼儀正しく短く応じ、自然に商品のご案内へ戻す
- 医療効果の断定、他社への言及・比較、過度な煽り、値引き交渉への応諾は禁止
- emotionは happy(お礼・好意的な話題)/excited(セール告知・ご購入報告)/troubled(在庫僅少・回答不能)/shy(照れ)/normal から選ぶ
- 必ず次のJSONのみで返答(前置き・コードブロック禁止):
  {{"reply": "回答文", "product_id": 話題にした商品のid(なければnull), "emotion": "normal"}}"""

    async def _load_products(self) -> list[Product]:
        async with SessionLocal() as db:
            rows = await db.execute(select(Product).where(Product.is_active == True))  # noqa: E712
            products = list(rows.scalars())
        self._product_names = [p.name for p in products]
        return products

    async def refresh_product_names(self) -> None:
        await self._load_products()


host = AIHost()
