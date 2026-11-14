"""pytest 共通設定

- アプリを import する「前」に環境変数を設定する(settings は import 時に読まれるため)
  → テスト専用の一時 SQLite、VOICEVOX/LLM は使わない
- 各テストの前に DB を作り直し、レート制限の状態もリセット → テスト同士が影響しない
- HTTP はサーバを起動せず、httpx.ASGITransport でアプリを直接呼ぶ(高速)
"""
import os
import tempfile

_tmpdir = tempfile.mkdtemp(prefix="alc-test-")
os.environ.update(
    # 既定は一時 SQLite。TEST_DATABASE_URL を指定すると MySQL 等で同じテストを実行できる(CI で使用)
    DATABASE_URL=os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmpdir}/test.db"),
    ADMIN_TOKEN="test-token",
    TTS_PROVIDER="none",
    LLM_PROVIDER="none",
    PASSWORD_ITERATIONS="1000",       # テストを速くする(本番の既定は 60 万回)
    ANTHROPIC_API_KEY="",
    OPENAI_API_KEY="",
    AUDIO_DIR=f"{_tmpdir}/audio",
    LOG_LEVEL="WARNING",
)

import httpx  # noqa: E402
import pytest  # noqa: E402

from app import main  # noqa: E402
from app.ai_host import host  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.models import Product  # noqa: E402
from app.routers import admin as admin_router  # noqa: E402
from app.routers import auth  # noqa: E402

ADMIN = {"X-Admin-Token": "test-token"}
PW = "pass1234"


@pytest.fixture(autouse=True)
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as db:
        for p in main.SEED_PRODUCTS:
            db.add(Product(**p))
        await db.commit()
    auth.login_limiter._hits.clear()
    auth.register_limiter._hits.clear()
    admin_router.admin_limiter._hits.clear()
    main.comment_limiter._hits.clear()
    while not host.queue.empty():     # 道謝キューの残りを捨てる(worker は起動していない)
        host.queue.get_nowait()
    yield


@pytest.fixture
async def client():
    """Cookie を保持するクライアント(ブラウザ相当)"""
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as c:
        yield c


@pytest.fixture
async def make_client():
    """追加のクライアントを作る(複数ユーザー・同時実行のテスト用)"""
    clients = []

    async def _make():
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://127.0.0.1")
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


async def register(c: httpx.AsyncClient, username: str = "taro") -> dict:
    """Web 方式で登録し、CSRF ヘッダを返す"""
    r = await c.post("/api/auth/register", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return {"X-CSRF-Token": r.json()["csrf_token"]}
