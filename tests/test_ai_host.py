"""AI ホスト:質問判定 / 応答戦略 / キューの積み残し制御 / LLM なしのフォールバック"""
import pytest

from app import ai_host
from app.ai_host import FALLBACK_REPLIES, MAX_BACKLOG, host, looks_like_question


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


async def test_generate_without_api_key_uses_fallback():
    reply, product_id, emotion = await host._generate("taro", "おすすめは?")
    assert (reply, emotion) in FALLBACK_REPLIES and product_id is None
