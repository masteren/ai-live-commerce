"""用户认证:注册 / 登录 / 当前用户

- 密码:标准库 pbkdf2_hmac(sha256, 12万次迭代) + 随机盐,无额外依赖
- 令牌:登录/注册签发 64hex 随机 token,存 users.token,重新登录即失效旧令牌
- 客户端通过 Header `X-Auth-Token`(REST)或消息体 token 字段(WebSocket)携带
"""
import hashlib
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import SessionLocal, get_db
from ..models import User

router = APIRouter(prefix="/api/auth", tags=["auth"])

_ITER = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITER)
    return f"{salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, dk_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), _ITER)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except ValueError:
        return False


class AuthIn(BaseModel):
    username: str = Field(min_length=2, max_length=20, pattern=r"^[\w\-ぁ-んァ-ヶ一-龠ー]+$")
    password: str = Field(min_length=6, max_length=64)


class AuthOut(BaseModel):
    username: str
    token: str


async def get_current_user(
    x_auth_token: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> User:
    user = await resolve_token(x_auth_token, db)
    if not user:
        raise HTTPException(401, "ログインが必要です")
    return user


async def resolve_token(token: str, db: Optional[AsyncSession] = None) -> Optional[User]:
    """token → User;WebSocket 等无依赖注入场景也可直接调用"""
    if not token or len(token) < 32:
        return None
    if db is not None:
        row = await db.execute(select(User).where(User.token == token))
        return row.scalar_one_or_none()
    async with SessionLocal() as session:
        row = await session.execute(select(User).where(User.token == token))
        return row.scalar_one_or_none()


@router.post("/register", response_model=AuthOut)
async def register(body: AuthIn, db: AsyncSession = Depends(get_db)):
    exists = await db.execute(select(User).where(User.username == body.username))
    if exists.scalar_one_or_none():
        raise HTTPException(409, "このユーザー名は既に使われています")
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        token=secrets.token_hex(32),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # 并发注册同名用户:查重通过但 commit 撞唯一约束 → 统一返回 409
        await db.rollback()
        raise HTTPException(409, "このユーザー名は既に使われています")
    return AuthOut(username=user.username, token=user.token)


@router.post("/login", response_model=AuthOut)
async def login(body: AuthIn, db: AsyncSession = Depends(get_db)):
    row = await db.execute(select(User).where(User.username == body.username))
    user = row.scalar_one_or_none()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, "ユーザー名またはパスワードが正しくありません")
    user.token = secrets.token_hex(32)   # 重登刷新令牌
    await db.commit()
    return AuthOut(username=user.username, token=user.token)


@router.get("/me")
async def me(user: User = Depends(get_current_user)):
    return {"username": user.username}
