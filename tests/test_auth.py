"""認証:パスワードハッシュ / Cookie+CSRF / Bearer / 有効期限 / ログアウト / 総当たり対策"""
from datetime import timedelta

from sqlalchemy import update

from app.database import SessionLocal
from app.models import AuthSession, now_utc
from app.routers.auth import (COOKIE_NAME, _hash_token, extract_token, hash_password,
                              verify_password)
from conftest import PW, register


# ---------- 純粋関数 ----------
def test_password_hash_is_salted_and_verifiable():
    h1, h2 = hash_password("secret123"), hash_password("secret123")
    assert h1 != h2                                   # ソルトが毎回違う
    assert verify_password("secret123", h1)
    assert not verify_password("wrong", h1)
    assert not verify_password("secret123", "broken-format")


def test_extract_token_prefers_bearer_over_cookie():
    assert extract_token({"authorization": "Bearer abc"}, {COOKIE_NAME: "zzz"}) == ("abc", "bearer")
    assert extract_token({}, {COOKIE_NAME: "zzz"}) == ("zzz", "cookie")
    assert extract_token({}, {}) == ("", "cookie")


def test_token_hash_is_deterministic_sha256():
    assert _hash_token("a") == _hash_token("a") and len(_hash_token("a")) == 64


# ---------- Web(Cookie + CSRF) ----------
async def test_register_sets_httponly_cookie_without_token_in_body(client):
    r = await client.post("/api/auth/register", json={"username": "taro", "password": PW})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.cookies[COOKIE_NAME] not in r.text


async def test_state_changing_request_requires_csrf(client):
    csrf = await register(client)
    body = {"product_id": 1, "quantity": 1}
    assert (await client.post("/api/cart", json=body)).status_code == 403
    assert (await client.post("/api/cart", json=body, headers={"X-CSRF-Token": "x" * 32})).status_code == 403
    assert (await client.post("/api/cart", json=body, headers=csrf)).status_code == 200
    assert (await client.get("/api/cart")).status_code == 200      # GET は CSRF 不要


async def test_duplicate_username_conflict(client, make_client):
    await register(client)
    other = await make_client()
    r = await other.post("/api/auth/register", json={"username": "taro", "password": PW})
    assert r.status_code == 409


# ---------- Bearer(iOS 等) ----------
async def test_bearer_token_flow_needs_no_csrf(client, make_client):
    await register(client)
    app_client = await make_client()
    r = await app_client.post("/api/auth/token", json={"username": "taro", "password": PW})
    assert r.status_code == 200 and COOKIE_NAME not in app_client.cookies
    bearer = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await app_client.post("/api/cart", headers=bearer, json={"product_id": 1, "quantity": 1})
    assert r.status_code == 200


async def test_expired_session_is_rejected(client):
    await register(client)
    async with SessionLocal() as db:
        await db.execute(update(AuthSession).values(expires_at=now_utc() - timedelta(seconds=1)))
        await db.commit()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_logout_only_affects_current_device(client, make_client):
    csrf = await register(client)
    phone = await make_client()
    tok = (await phone.post("/api/auth/token", json={"username": "taro", "password": PW})).json()["access_token"]
    assert (await client.post("/api/auth/logout", headers=csrf)).status_code == 200
    assert (await client.get("/api/auth/me")).status_code == 401
    assert (await phone.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})).status_code == 200


async def test_db_stores_only_token_hash(client):
    await register(client)
    raw = client.cookies[COOKIE_NAME]
    async with SessionLocal() as db:
        rows = (await db.execute(AuthSession.__table__.select())).all()
    assert all(raw not in str(tuple(r)) for r in rows)
    assert any(r.token_hash == _hash_token(raw) for r in rows)


async def test_login_brute_force_lockout(client, make_client):
    await register(client)
    attacker = await make_client()
    for i in range(5):
        r = await attacker.post("/api/auth/login", json={"username": "taro", "password": f"bad{i}xx"})
        assert r.status_code == 401
    r = await attacker.post("/api/auth/login", json={"username": "taro", "password": PW})
    assert r.status_code == 429


# ---------- パスワードハッシュの強度と移行 ----------
def test_hash_format_records_iterations():
    from app.config import settings
    h = hash_password("secret123")
    assert h.startswith(f"pbkdf2_sha256${settings.password_iterations}$") and len(h.split("$")) == 4


def test_default_iterations_follow_owasp_recommendation():
    from app.config import Settings
    assert Settings.model_fields["password_iterations"].default >= 600_000


async def test_legacy_hash_still_verifies_and_is_upgraded_on_login(client):
    """旧形式(salt$hash・12万回)のユーザーもログインでき、成功時に現在の形式へ自動で更新される"""
    import hashlib
    import secrets as pysecrets
    from sqlalchemy import select
    from app.models import User
    from app.routers.auth import LEGACY_ITERATIONS
    salt = pysecrets.token_bytes(16)
    legacy = f"{salt.hex()}${hashlib.pbkdf2_hmac('sha256', PW.encode(), salt, LEGACY_ITERATIONS).hex()}"
    async with SessionLocal() as db:
        db.add(User(username="olduser", password_hash=legacy))
        await db.commit()
    assert verify_password(PW, legacy) and not verify_password("wrong", legacy)
    assert (await client.post("/api/auth/login", json={"username": "olduser", "password": PW})).status_code == 200
    async with SessionLocal() as db:
        stored = (await db.execute(select(User.password_hash).where(User.username == "olduser"))).scalar_one()
    assert stored.startswith("pbkdf2_sha256$") and stored != legacy
    assert verify_password(PW, stored)


# ---------- レート制限・ヘッダ ----------
async def test_registration_is_rate_limited_per_ip(client, monkeypatch):
    from app.routers import auth as auth_mod
    monkeypatch.setattr(auth_mod.register_limiter, "limit", 2)
    codes = [(await client.post("/api/auth/register", json={"username": f"user_{i}", "password": PW})).status_code
             for i in range(3)]
    assert codes == [200, 200, 429]


async def test_admin_token_guessing_is_rate_limited(client, monkeypatch):
    from app.routers import admin as admin_mod
    monkeypatch.setattr(admin_mod.admin_limiter, "limit", 3)
    codes = [(await client.get("/api/admin/stats", headers={"X-Admin-Token": f"guess{i}"})).status_code for i in range(3)]
    assert codes == [401, 401, 401]
    # 上限に達した後は、正しいトークンでも一定時間は拒否(総当たりを続けても意味がない)
    assert (await client.get("/api/admin/stats", headers={"X-Admin-Token": "test-token"})).status_code == 429


async def test_security_headers_and_no_cors_by_default(client):
    r = await client.get("/api/products", headers={"Origin": "http://evil.example.com"})
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in r.headers      # 既定では他オリジンに許可を出さない
    assert (await client.get("/")).headers["x-frame-options"] == "DENY"


# ---------- 入力チェックのエラー文 ----------
async def test_validation_errors_are_one_japanese_sentence(client):
    """入力チェックのエラーは、英語の配列ではなく利用者向けの日本語1文で返る(Web・iOS がそのまま表示する)"""
    r = await client.post("/api/auth/register", json={"username": "taro", "password": "123"})
    assert r.status_code == 422 and isinstance(r.json()["detail"], str) and "パスワードは6" in r.json()["detail"]
    r = await client.post("/api/auth/register", json={"username": "a", "password": PW})
    assert r.status_code == 422 and "ユーザー名は2" in r.json()["detail"]
    r = await client.post("/api/auth/register", json={"username": "bad name!", "password": PW})
    assert r.status_code == 422 and "ユーザー名" in r.json()["detail"]
    csrf = await register(client)
    r = await client.post("/api/cart", json={"product_id": 1, "quantity": 0}, headers=csrf)
    assert r.status_code == 422 and "数量は1〜99" in r.json()["detail"]
    r = await client.post("/api/cart", json={"product_id": "x"}, headers=csrf)
    assert r.status_code == 422 and r.json()["detail"] == "入力内容を確認してください"


# ---------- ログイン状態の維持(スライディング方式) ----------
async def _expiry_seconds_from_now() -> float:
    from datetime import timezone
    async with SessionLocal() as db:
        exp = (await db.execute(AuthSession.__table__.select())).first().expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    return (exp - now_utc()).total_seconds()


async def test_me_extends_session_when_less_than_half_remains(client):
    """使い続けていれば切れない:残りが半分を切ったセッションは /me で延長され、Cookie も出し直される"""
    from app.config import settings
    await register(client)
    ttl = settings.session_ttl_hours * 3600
    async with SessionLocal() as db:
        await db.execute(update(AuthSession).values(expires_at=now_utc() + timedelta(hours=1)))
        await db.commit()
    r = await client.get("/api/auth/me")
    assert r.status_code == 200 and COOKIE_NAME in r.headers.get("set-cookie", "")
    assert await _expiry_seconds_from_now() > ttl - 60


async def test_me_does_not_rewrite_fresh_session(client):
    """延長は残りが半分を切ったときだけ(毎回の書き込みを避ける)"""
    await register(client)
    before = await _expiry_seconds_from_now()
    r = await client.get("/api/auth/me")
    assert r.status_code == 200 and "set-cookie" not in r.headers
    assert abs(await _expiry_seconds_from_now() - before) < 5


async def test_bearer_session_is_extended_too(client, make_client):
    await register(client)
    app_client = await make_client()
    tok = (await app_client.post("/api/auth/token", json={"username": "taro", "password": PW})).json()["access_token"]
    async with SessionLocal() as db:
        await db.execute(update(AuthSession).values(expires_at=now_utc() + timedelta(minutes=5)))
        await db.commit()
    r = await app_client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200 and "set-cookie" not in r.headers       # アプリには Cookie を出さない
    async with SessionLocal() as db:
        rows = (await db.execute(AuthSession.__table__.select())).all()
    from datetime import timezone
    extended = [(r.expires_at.replace(tzinfo=timezone.utc) if r.expires_at.tzinfo is None else r.expires_at) > now_utc() + timedelta(days=1) for r in rows]
    assert sorted(extended) == [False, True]      # 延長されるのは今回使ったセッション(アプリ)だけ。ブラウザ側はそのまま
