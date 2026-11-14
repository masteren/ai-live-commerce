"""用户认证:注册 / 登录 / 登出 / 当前用户

■ 两种客户端,两种携带方式(共用同一张 auth_sessions 表)
  - Web 浏览器: HttpOnly Cookie(lc_session)
      JS 读不到 Cookie → 即使发生 XSS,令牌也偷不走(对比:放 localStorage 会被 XSS 读取)
      SameSite=Lax + CSRF 令牌双重提交 → 防止其他网站借用 Cookie 发起请求(CSRF)
  - iOS 等原生 App: Authorization: Bearer <token>(POST /api/auth/token 获取)
      浏览器不会自动附带这个头,所以不存在 CSRF 问题,无需 CSRF 令牌

■ 安全要点
  - 密码:标准库 pbkdf2_hmac(sha256, 60万次迭代·次数随哈希一起保存) + 随机盐;登录成功时自动把旧哈希升级
  - 令牌:secrets 生成的随机值;DB 只存 SHA-256 哈希;有有效期;登出即删除会话
  - 登录失败限流:同一 IP+用户名 在窗口期内失败过多 → 429(防暴力破解)
"""
import asyncio
import hashlib
import secrets
from datetime import timedelta
from typing import Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import SessionLocal, get_db
from ..models import AuthSession, User, now_utc
from ..ratelimit import SlidingWindowLimiter, client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE_NAME = "lc_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
LEGACY_ITERATIONS = 120_000      # 旧形式(salt$hash)のハッシュに使っていた回数

login_limiter = SlidingWindowLimiter(settings.login_fail_limit, settings.login_fail_window)
register_limiter = SlidingWindowLimiter(settings.register_limit, settings.register_window)


# ---------- 密码 ----------
def hash_password(password: str, iterations: Optional[int] = None) -> str:
    """形式: pbkdf2_sha256$反復回数$ソルト(hex)$ハッシュ(hex)
    反復回数をハッシュと一緒に保存する → 将来回数を増やしても、古いハッシュを検証できる"""
    n = iterations or settings.password_iterations
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
    return f"pbkdf2_sha256${n}${salt.hex()}${dk.hex()}"


def _parse_hash(stored: str) -> Tuple[int, bytes, str]:
    parts = stored.split("$")
    if len(parts) == 4 and parts[0] == "pbkdf2_sha256":
        return int(parts[1]), bytes.fromhex(parts[2]), parts[3]
    if len(parts) == 2:                       # 旧形式(salt$hash、12万回固定)
        return LEGACY_ITERATIONS, bytes.fromhex(parts[0]), parts[1]
    raise ValueError("unknown password hash format")


def verify_password(password: str, stored: str) -> bool:
    try:
        n, salt, dk_hex = _parse_hash(stored)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
        return secrets.compare_digest(dk.hex(), dk_hex)
    except ValueError:
        return False


def needs_rehash(stored: str) -> bool:
    """保存されているハッシュが、現在の設定より弱い(旧形式・反復回数が少ない)か"""
    if not stored.startswith("pbkdf2_sha256$"):
        return True                       # 旧形式は、回数に関係なく新形式へ移行する
    try:
        return _parse_hash(stored)[0] < settings.password_iterations
    except ValueError:
        return True


# ---------- 会话 ----------
def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _create_session(db: AsyncSession, user: User) -> Tuple[str, AuthSession]:
    """新建会话,返回 (原始令牌, 会话行)。原始令牌只在这里出现一次,DB 里只有哈希"""
    now = now_utc()
    # 顺便清理已过期的会话,避免表无限增长
    await db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    raw = secrets.token_urlsafe(32)
    sess = AuthSession(
        user_id=user.id,
        token_hash=_hash_token(raw),
        csrf_token=secrets.token_urlsafe(24),
        expires_at=now + timedelta(hours=settings.session_ttl_hours),
    )
    db.add(sess)
    await db.commit()
    return raw, sess


async def resolve_session(raw: str, db: Optional[AsyncSession] = None) -> Optional[Tuple[User, AuthSession]]:
    """原始令牌 → (User, AuthSession);无效/过期返回 None。WebSocket 等无依赖注入场景也可直接调用"""
    if not raw or len(raw) < 32:
        return None
    stmt = (
        select(User, AuthSession)
        .join(AuthSession, AuthSession.user_id == User.id)
        # 过期判断放在 SQL 里做(SQLite 取回的时间不带时区,在 Python 里比较容易出错)
        .where(AuthSession.token_hash == _hash_token(raw), AuthSession.expires_at > now_utc())
    )
    if db is not None:
        row = (await db.execute(stmt)).first()
    else:
        async with SessionLocal() as session:
            row = (await session.execute(stmt)).first()
    return (row[0], row[1]) if row else None


def extract_token(headers, cookies) -> Tuple[str, str]:
    """从请求中取出令牌:优先 Authorization: Bearer(原生 App),其次 Cookie(浏览器)。返回 (令牌, 方式)"""
    authz = headers.get("authorization", "")
    if authz[:7].lower() == "bearer ":
        return authz[7:].strip(), "bearer"
    return cookies.get(COOKIE_NAME, ""), "cookie"


def _set_session_cookie(response: Response, raw: str) -> None:
    response.set_cookie(
        COOKIE_NAME, raw,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,                  # JS 无法读取
        samesite="lax",                 # 跨站的 POST 等请求不附带 Cookie
        secure=settings.cookie_secure,  # HTTPS 环境下只通过加密连接发送
        path="/",
    )


# ---------- 依赖注入 ----------
async def get_current_session(request: Request, db: AsyncSession = Depends(get_db)) -> Tuple[User, AuthSession]:
    raw, via = extract_token(request.headers, request.cookies)
    found = await resolve_session(raw, db)
    if not found:
        raise HTTPException(401, "ログインが必要です")
    user, sess = found
    # Cookie 认证 + 会修改数据的请求 → 必须带正确的 CSRF 令牌(双重提交)
    if via == "cookie" and request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not secrets.compare_digest(sent, sess.csrf_token):
            raise HTTPException(403, "CSRFトークンが正しくありません")
    return user, sess


async def get_current_user(ctx: Tuple[User, AuthSession] = Depends(get_current_session)) -> User:
    return ctx[0]


# ---------- 请求/响应模型 ----------
class AuthIn(BaseModel):
    username: str = Field(min_length=2, max_length=20, pattern=r"^[\w\-ぁ-んァ-ヶ一-龠ー]+$")
    password: str = Field(min_length=6, max_length=64)


class WebAuthOut(BaseModel):
    username: str
    csrf_token: str      # 前端放在内存里,修改类请求时放进 X-CSRF-Token 头


class TokenOut(BaseModel):
    username: str
    access_token: str
    token_type: str = "bearer"
    expires_at: str      # ISO8601(UTC)


async def _authenticate(body: AuthIn, request: Request, db: AsyncSession) -> User:
    key = f"{client_ip(request)}:{body.username}"
    if login_limiter.is_limited(key):
        raise HTTPException(429, "ログイン試行回数が多すぎます。しばらくしてから再度お試しください")
    row = await db.execute(select(User).where(User.username == body.username))
    user = row.scalar_one_or_none()
    # ハッシュ計算は CPU を数十ms 使う。イベントループ上で直接実行すると、その間ほかの全リクエストと
    # WebSocket の配信が止まるので、別スレッドで実行する
    ok = bool(user) and await asyncio.to_thread(verify_password, body.password, user.password_hash)
    if not ok:
        login_limiter.hit(key)
        raise HTTPException(401, "ユーザー名またはパスワードが正しくありません")
    login_limiter.reset(key)
    if needs_rehash(user.password_hash):
        # ログイン成功時(=平文パスワードが手元にある唯一のタイミング)に、現在の強度でハッシュし直す
        user.password_hash = await asyncio.to_thread(hash_password, body.password)
        await db.commit()
    return user


# ---------- 端点 ----------
@router.post("/register", response_model=WebAuthOut)
async def register(body: AuthIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Web 用:注册并直接登录(设置 Cookie)"""
    if not register_limiter.hit(client_ip(request)):
        raise HTTPException(429, "登録の回数が多すぎます。しばらくしてから再度お試しください")
    exists = await db.execute(select(User).where(User.username == body.username))
    if exists.scalar_one_or_none():
        raise HTTPException(409, "このユーザー名は既に使われています")
    user = User(username=body.username, password_hash=await asyncio.to_thread(hash_password, body.password))
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        # 并发注册同名用户:查重通过但 commit 撞唯一约束 → 统一返回 409
        await db.rollback()
        raise HTTPException(409, "このユーザー名は既に使われています")
    raw, sess = await _create_session(db, user)
    _set_session_cookie(response, raw)
    return WebAuthOut(username=user.username, csrf_token=sess.csrf_token)


@router.post("/login", response_model=WebAuthOut)
async def login(body: AuthIn, request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Web 用:登录(设置 HttpOnly Cookie,令牌本身不出现在响应体里)"""
    user = await _authenticate(body, request, db)
    raw, sess = await _create_session(db, user)
    _set_session_cookie(response, raw)
    return WebAuthOut(username=user.username, csrf_token=sess.csrf_token)


@router.post("/token", response_model=TokenOut)
async def issue_token(body: AuthIn, request: Request, db: AsyncSession = Depends(get_db)):
    """原生 App 用:返回 Bearer 令牌(不设置 Cookie)"""
    user = await _authenticate(body, request, db)
    raw, sess = await _create_session(db, user)
    return TokenOut(username=user.username, access_token=raw, expires_at=sess.expires_at.isoformat())


@router.post("/logout")
async def logout(response: Response,
                 ctx: Tuple[User, AuthSession] = Depends(get_current_session),
                 db: AsyncSession = Depends(get_db)):
    """登出:删除当前会话(只影响当前设备,其他设备的登录不受影响)"""
    await db.execute(delete(AuthSession).where(AuthSession.id == ctx[1].id))
    await db.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me", response_model=WebAuthOut)
async def me(ctx: Tuple[User, AuthSession] = Depends(get_current_session)):
    """当前用户。页面刷新后前端用它恢复登录状态和 CSRF 令牌"""
    user, sess = ctx
    return WebAuthOut(username=user.username, csrf_token=sess.csrf_token)
