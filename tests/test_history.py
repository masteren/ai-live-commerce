"""ライブに入ったときに見せるコメントの履歴"""
from app.database import SessionLocal
from app.models import Comment
from app.queries import recent_comments


async def test_recent_comments_oldest_first_with_kind_and_limit():
    async with SessionLocal() as db:
        for i in range(35):
            db.add(Comment(nickname=f"u{i}", text=f"c{i}"))
        db.add(Comment(nickname="ハナ", text="こんにちは", is_host=True))
        db.add(Comment(nickname="bot", text="わぁ", is_bot=True))
        await db.commit()
        items = await recent_comments(db, limit=30)
    assert len(items) == 30
    # 新しい 30 件を、古い順に並べる
    assert items[0]["text"] == "c7" and items[-1]["text"] == "わぁ"
    assert [i["kind"] for i in items[-3:]] == ["user", "host", "bot"]


async def test_recent_comments_empty():
    async with SessionLocal() as db:
        assert await recent_comments(db) == []


async def test_history_and_broadcast_carry_utc_timestamp(monkeypatch):
    """コメント欄の行には時刻(ISO 8601、UTC)が付く。SQLite のタイムゾーンなしの時刻も UTC として出す"""
    from datetime import datetime
    from app import ws as ws_mod
    async with SessionLocal() as db:
        db.add(Comment(nickname="u", text="t", created_at=datetime(2026, 11, 17, 2, 31, 1)))
        await db.commit()
        items = await recent_comments(db)
    assert items[0]["ts"] == "2026-11-17T02:31:01+00:00"

    sent = []

    class FakeWS:
        async def send_text(self, data):
            sent.append(data)
    monkeypatch.setattr(ws_mod.manager, "connections", {FakeWS()})
    await ws_mod.manager.broadcast({"type": "comment", "nickname": "u", "text": "x"})
    await ws_mod.manager.broadcast({"type": "viewers", "count": 1})
    assert '"ts": "20' in sent[0] and "+00:00" in sent[0]
    assert "ts" not in sent[1]                      # コメント欄に出ないメッセージには付けない

