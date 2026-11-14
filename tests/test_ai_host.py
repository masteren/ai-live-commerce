"""AI ホスト:質問判定 / 応答戦略 / キューの積み残し制御 / LLM なしのフォールバック"""
import pytest

from app import ai_host
from app.ai_host import MAX_BACKLOG, host, looks_like_question


@pytest.mark.parametrize("text,expected", [
    ("イヤホンの音質はどうですか?", True),
    ("在庫ありますか", True),
    ("おすすめ教えて", True),
    ("こんにちは", False),
    ("わーい", False),
])
def test_looks_like_question(text, expected):
    assert looks_like_question(text) is expected


async def test_question_is_always_enqueued(monkeypatch):
    monkeypatch.setattr(ai_host.random, "random", lambda: 0.99)    # 雑談なら答えない値
    await host.on_comment("taro", "送料はいくらですか?")
    assert host.queue.qsize() == 1


async def test_chat_reply_depends_on_probability(monkeypatch):
    monkeypatch.setattr(ai_host.random, "random", lambda: 0.99)
    await host.on_comment("taro", "わーい")
    assert host.queue.qsize() == 0
    monkeypatch.setattr(ai_host.random, "random", lambda: 0.0)
    await host.on_comment("taro", "わーい")
    assert host.queue.qsize() == 1


async def test_backlog_keeps_only_latest():
    for i in range(MAX_BACKLOG + 3):
        await host._enqueue({"kind": "reply", "nickname": "u", "text": f"q{i}"})
    assert host.queue.qsize() == MAX_BACKLOG
    texts = [host.queue.get_nowait()["text"] for _ in range(MAX_BACKLOG)]
    assert texts == [f"q{i}" for i in range(3, MAX_BACKLOG + 3)]     # 古いものから捨てる


async def test_generate_without_api_key_answers_from_db():
    """LLM なしでも、DB の商品データを使って質問に答える(定型文の使い回しではない)"""
    reply, product_id, emotion = await host._generate("taro", "マグはいくらですか?")
    assert product_id == 2 and "1,880円" in reply


async def test_product_short_name_always_gets_reply(monkeypatch):
    monkeypatch.setattr(ai_host.random, "random", lambda: 0.99)    # 雑談なら答えない値
    await host.refresh_products()
    await host.on_comment("taro", "マグ気になる")                   # 疑問形ではないが商品の略称を含む
    assert host.queue.qsize() == 1


async def test_idle_talk_alternates_shop_info_and_product_spotlight():
    text0, pid0, _ = await host._idle_line(0)
    text1, pid1, _ = await host._idle_line(1)
    assert pid0 is None and pid1 == 1 and "ワイヤレスイヤホン Pro" in text1 and "38%オフ" in text1


async def test_meta_endpoint_shows_credit_and_mode(client):
    m = (await client.get("/api/meta")).json()
    assert m["tts_credit"] == ""                      # TTS_PROVIDER=none ならクレジット不要
    assert m["llm"] == "ルール応答(デモモード)"


def test_idle_talk_only_when_someone_is_watching(monkeypatch):
    """視聴者が0人のときは自動トークをしない(誰もいないのに音声合成を続けない)"""
    import time
    from app.ws import manager
    host.last_activity_at = time.time() - 9999
    monkeypatch.setattr(manager, "connections", set())
    assert host._should_idle_talk() is False
    monkeypatch.setattr(manager, "connections", {object()})
    assert host._should_idle_talk() is True
    host.last_activity_at = time.time()
    assert host._should_idle_talk() is False


async def test_idle_talk_skips_sold_out_products():
    """在庫切れの商品は自動トークで紹介しない。全部が在庫切れなら店舗情報だけを話す"""
    from sqlalchemy import update
    from app.database import SessionLocal
    from app.models import Product
    async with SessionLocal() as db:
        await db.execute(update(Product).where(Product.id == 1).values(stock=0))
        await db.commit()
    picked = {(await host._idle_line(i))[1] for i in range(1, 12, 2)}
    assert 1 not in picked and picked - {None}
    async with SessionLocal() as db:
        await db.execute(update(Product).values(stock=0))
        await db.commit()
    assert {(await host._idle_line(i))[1] for i in range(1, 12, 2)} == {None}


# ---------- 発言の配信:文字を先に、音声はあとから ----------
async def test_speak_sends_text_before_audio_is_ready(monkeypatch):
    """文字(host_reply)は音声の合成を待たずに配信され、音声(host_audio)は同じ id であとから届く"""
    import asyncio
    sent, synth_started, release = [], asyncio.Event(), asyncio.Event()

    class SlowTTS:
        name = "voicevox"
        credit = ""

        async def synthesize(self, text, emotion="normal"):
            synth_started.set()
            await release.wait()                 # 合成に時間がかかっている状態
            return "/static/audio/x.wav"

    async def capture(message):
        sent.append(message)

    monkeypatch.setattr(ai_host, "tts", SlowTTS())
    monkeypatch.setattr(ai_host.manager, "broadcast", capture)
    task = asyncio.create_task(host._speak("こんにちは", 1, "happy", save=False))
    await asyncio.wait_for(synth_started.wait(), 2)
    assert [m["type"] for m in sent] == ["host_reply"]          # 合成中でも文字はもう届いている
    assert sent[0]["text"] == "こんにちは" and sent[0]["audio_pending"] is True and sent[0]["audio_url"] is None
    release.set()
    await asyncio.wait_for(task, 2)
    assert [m["type"] for m in sent] == ["host_reply", "host_audio"]
    assert sent[1] == {"type": "host_audio", "id": sent[0]["id"], "audio_url": "/static/audio/x.wav"}


async def test_speak_without_voice_sends_text_only(monkeypatch):
    """音声なしの設定、または合成に失敗したときは、文字だけを配信する"""
    sent = []

    async def capture(message):
        sent.append(message)

    monkeypatch.setattr(ai_host.manager, "broadcast", capture)
    await host._speak("文字だけ", None, "normal", save=False)      # テストの既定は TTS_PROVIDER=none
    assert [m["type"] for m in sent] == ["host_reply"] and sent[0]["audio_pending"] is False

    class FailingTTS:
        name = "voicevox"
        credit = ""

        async def synthesize(self, text, emotion="normal"):
            return None                          # VOICEVOX が落ちているとき

    sent.clear()
    monkeypatch.setattr(ai_host, "tts", FailingTTS())
    await host._speak("合成に失敗", None, "normal", save=False)
    assert [m["type"] for m in sent] == ["host_reply"] and sent[0]["audio_pending"] is True
