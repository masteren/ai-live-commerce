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
