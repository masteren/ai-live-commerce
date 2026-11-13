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

from sqlalchemy import select

from . import host_rules
from .config import settings
from .database import SessionLocal
from .llm import LLMProvider, build_llm
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

# 購入のお礼(LLM を使わない定型。毎回同じにならないよう数パターン)
THANKS_TEMPLATES = [
    "{name}さん、「{product}」のご購入ありがとうございます。お届けまで、もう少しお待ちくださいね。",
    "{name}さん、「{product}」をお選びいただきありがとうございます。大切にお届けします。",
    "{name}さん、ありがとうございます。「{product}」のご注文、たしかに承りました。",
]

# 無人時の自動トーク(店舗情報)。商品紹介(host_rules.spotlight)と交互に話す
IDLE_LINES = [
    ("{host}です。商品のことは、コメントでいつでも聞いてくださいね。", "normal"),
    (host_rules.SHIPPING + "。", "normal"),
    (host_rules.RETURNS + "。", "normal"),
    ("お支払いは" + host_rules.PAYMENT + "。", "normal"),
]


def looks_like_question(text: str) -> bool:
    return bool(_QUESTION_RE.search(text))


def parse_llm_reply(raw: str, product_ids: "set[int]") -> "Optional[tuple[str, Optional[int], str]]":
    """LLM の返答(JSON 文字列)を (返答, 商品ID, 感情) に変換。形式が違えば None。

    LLM の出力は信用しない:
      - 商品ID は実在するものだけ採用(存在しない ID を返されても商品カードを切り替えない)
      - 感情は決められた5種類だけ採用
      - 文字数の上限はプロンプトで指示しているが、守られない場合に備えてコード側でも切り詰める
    """
    text = raw.replace("```json", "").replace("```", "").strip()
    try:
        j = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(j, dict) or not isinstance(j.get("reply"), str) or not j["reply"].strip():
        return None
    reply = j["reply"].strip()
    hard_limit = settings.reply_max_chars * 2
    if len(reply) > hard_limit:
        reply = reply[:hard_limit].rstrip() + "…"
    pid = j.get("product_id")
    product_id = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid in product_ids else None
    emotion = j.get("emotion") if j.get("emotion") in EMOTIONS else "normal"
    return reply, product_id, emotion


class AIHost:
    def __init__(self) -> None:
        self.llm: Optional[LLMProvider] = build_llm()
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.history: list[dict] = []
        self.last_reply_at: float = 0.0
        self.last_activity_at: float = time.time()
        self._products: list = []          # 販売中の商品(略称での言及判定に使う)

    # ---------- 对外入口 ----------
    async def on_comment(self, nickname: str, text: str) -> None:
        """混合应答策略:问题/提到商品(简称也算,如「マグ」)必答;闲聊按概率接话"""
        self.last_activity_at = time.time()
        must = looks_like_question(text) or host_rules.mentions_product(text, self._products)
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
                    text, product_id, emotion = await self._idle_line(i)
                    i += 1
                    await self._speak(text, product_id, emotion, save=False)
                    self.last_activity_at = time.time()
            except Exception:
                logger.exception("自動トークに失敗しました(次の周期で再試行)")
                self.last_activity_at = time.time()

    async def _idle_line(self, i: int) -> "tuple[str, Optional[int], str]":
        """無人時のトーク:店舗情報と商品紹介を交互に(商品紹介のときは商品カードも切り替わる)"""
        products = await self._load_products()
        if i % 2 == 1 and products:
            p = products[(i // 2) % len(products)]
            return host_rules.spotlight(p), p.id, "happy"
        text, emotion = IDLE_LINES[(i // 2) % len(IDLE_LINES)]
        return text.format(host=settings.host_name), None, emotion

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
        if self.llm is None:
            # LLM なし:コメントの意図と商品を判定し、DB の実データで答える(host_rules)
            return host_rules.rule_reply(nickname, question, products)

        system = self._build_system_prompt(products)
        self.history = self.history[-HISTORY_TURNS * 2:]
        messages = self.history + [{"role": "user", "content": f"{nickname}: {question}"}]

        try:
            raw = await self.llm.complete(system, messages)
        except Exception as e:
            # 縮退:タイムアウト・レート制限・通信障害 → ルール応答。配信は止めない
            logger.warning("LLM 调用失败,降级为规则回复: %s", e)
            return host_rules.rule_reply(nickname, question, products)

        parsed = parse_llm_reply(raw, {p.id for p in products})
        if parsed is None:
            # 指示した JSON 形式で返ってこなかった → そのまま視聴者に見せず、ルール応答に切り替える
            logger.warning("LLM の返答を解釈できません,降级为规则回复: %.80r", raw)
            return host_rules.rule_reply(nickname, question, products)

        self.history = messages + [{"role": "assistant", "content": raw}]
        return parsed

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
- 視聴者は「◯◯さん」とお呼びする
- 温かみはあるが、過度にくだけた表現・若者言葉・顔文字は使わない

# 販売中の商品
{lines}

# 店舗情報
- 配送: 全国送料無料、3〜5日でお届け
- 返品: 未開封なら到着後7日以内可
- 支払い: クレジットカード / コンビニ払い / PayPay

# 話し方
- ライブ配信で実際に話しているような、自然な話し言葉(です・ます)。1〜2文で短く
- 毎回「ありがとうございます」「ご質問ありがとうございます」から始めない。いきなり答えから話してよい
- 視聴者の名前を呼ぶのはときどきでよい(毎回呼ぶと機械的になる)
- 商品の話をするときは、価格・割引率・在庫・仕様のうち具体的な事実を1つ入れる
- 決まり文句の繰り返しや大げさな表現は避ける

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
        self._products = products
        return products

    async def refresh_product_names(self) -> None:
        await self._load_products()


host = AIHost()
