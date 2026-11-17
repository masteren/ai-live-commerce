"""ゲスト(登録せずに参加)"""
from sqlalchemy import select

from app.database import SessionLocal
from app.models import User
from tests.conftest import PW


async def test_guest_web_can_use_cart_like_a_member(client):
    r = await client.post("/api/auth/guest")
    assert r.status_code == 200
    name, csrf = r.json()["username"], r.json()["csrf_token"]
    assert name.startswith("ゲスト") and name[3:].isdigit()
    assert (await client.get("/api/auth/me")).json()["username"] == name
    r = await client.post("/api/cart", headers={"X-CSRF-Token": csrf}, json={"product_id": 1, "quantity": 1})
    assert r.status_code == 200
    async with SessionLocal() as db:
        u = (await db.execute(select(User).where(User.username == name))).scalar_one()
    assert u.is_guest is True


async def test_guest_token_for_app(client):
    r = await client.post("/api/auth/guest/token")
    assert r.status_code == 200 and "lc_session" not in r.cookies      # アプリ用は Cookie を使わない
    tok = r.json()["access_token"]
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert me.json()["username"] == r.json()["username"]


async def test_each_guest_gets_a_different_name(make_client):
    names = set()
    for _ in range(5):
        c = await make_client()
        names.add((await c.post("/api/auth/guest")).json()["username"])
    assert len(names) == 5


async def test_guest_name_cannot_be_logged_into(client, make_client):
    """ゲストにはパスワードがない:名前が分かっても、どんなパスワードでもログインできない"""
    name = (await client.post("/api/auth/guest")).json()["username"]
    other = await make_client()
    for pw in ("!!!!!!", PW, "password"):
        r = await other.post("/api/auth/login", json={"username": name, "password": pw})
        assert r.status_code == 401


async def test_cannot_register_guest_like_name(client):
    r = await client.post("/api/auth/register", json={"username": "ゲスト1234", "password": PW})
    assert r.status_code == 400
