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
