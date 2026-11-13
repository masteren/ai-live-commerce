# 学习笔记 01:认证改造(HttpOnly Cookie + CSRF + Bearer)

> 先做后懂:这份笔记对应 commit「認証改修」。复习时对照代码读,最后用「自测问题」检查自己能不能讲出来。

## 1. 改了什么(一句话版)
- **以前**:登录后服务器返回 token,前端存进 `localStorage`,每次请求放进 `X-Auth-Token` 头。
- **现在**:
  - **Web**:token 放进 **HttpOnly Cookie**,JS 读不到;修改数据的请求额外带 **CSRF 令牌**。
  - **iOS 等原生 App**:用 `POST /api/auth/token` 拿 **Bearer 令牌**,放进 `Authorization` 头。
  - 新增 `auth_sessions` 表:**一个用户可多端同时登录**,DB 只存令牌的 **SHA-256 哈希**,有**有效期**,**登出即删除**。
  - 登录失败限流(防暴力破解)、弹幕限流(防刷屏)、WebSocket 的 **Origin 检查**(防跨站劫持)、emoji 字段转义。

## 2. 和你学过的内容的对应
| 这次的改动 | 你在哪里学过 |
|---|---|
| HttpOnly / SameSite / Secure 属性、CSRF 令牌 | JS-2-18「Cookie と セキュリティ」(当时后端是 PHP) |
| XSS 能偷 localStorage | JS-2-18、SL-04 Webサイト利用者への攻撃 |
| 会话劫持 | SL-04 セッションハイジャック |
| 暴力破解、字典攻击 → 失败限流 | SL-03 パスワードクラック |
| 令牌只存哈希、加盐 | SL-12 ソルト/ペッパー、Node-04 bcrypt |
| `secrets.compare_digest`(防计时攻击) | 原来的代码就在用,概念上和 bcrypt.compare 相同 |

## 3. 三条主要流程
**Web 登录**
```
浏览器 POST /api/auth/login {username, password}
  → 服务器校验密码 → 新建 auth_sessions 行(存 token 的哈希 + csrf_token)
  → 响应头 Set-Cookie: lc_session=<token>; HttpOnly; SameSite=Lax
  → 响应体只返回 {username, csrf_token}(token 本身不出现在 JS 能读到的地方)
```
**Web 修改数据(加购物车、下单等)**
```
fetch POST /api/cart  (浏览器自动带 Cookie)  + 头 X-CSRF-Token: <csrf_token>
  → get_current_session(): Cookie → 查会话 → 比较 CSRF 头和会话里的 csrf_token
  → 不一致 → 403
```
**iOS(Bearer)**
```
POST /api/auth/token → {access_token, expires_at}
之后所有请求: Authorization: Bearer <access_token>   (浏览器不会自动带这个头 → 不需要 CSRF)
WebSocket 握手时也放这个头
```

## 4. 关键代码位置
- 令牌的提取顺序(Bearer 优先,其次 Cookie):`app/routers/auth.py` 的 `extract_token`
- 会话查询 + 过期判断(在 SQL 里做):`resolve_session`
- CSRF 检查:`get_current_session`
- Cookie 属性:`_set_session_cookie`
- 登录失败限流:`_authenticate` + `app/ratelimit.py`
- WebSocket 的 Origin 检查与弹幕限流:`app/main.py` 的 `_origin_allowed` 与 `live_ws`
- 前端:`app/static/index.html` 的 `setSession` / `checkSession` / `reconnect`

## 5. 设计上的取舍(面试会问)
- **为什么 Web 和 iOS 用两种方式?** Cookie 是浏览器的机制,能做到 JS 读不到;原生 App 没有 XSS 的问题,放请求头更自然,也不需要 CSRF 对策。两种方式共用同一张会话表,所以服务器逻辑只有一份。
- **为什么 DB 只存哈希?** 万一 DB 泄露,攻击者拿到的是哈希,不能直接拿来登录。令牌是 32 字节随机值,不像密码那样会被字典猜中,所以用一次 SHA-256 就够了,不需要 pbkdf2 那样的慢哈希。
- **SameSite=Lax 已经防 CSRF 了,为什么还要 CSRF 令牌?** 纵深防御:老浏览器、同站的子域名等情况下 SameSite 不一定可靠。这也是你在 JS-2-18 学的做法。
- **为什么 WebSocket 要查 Origin?** WebSocket 不受 CORS 保护,浏览器连接时又会自动附带 Cookie,所以恶意网站可以替用户连上来发言(CSWSH)。没有 Origin 头的连接(App、测试脚本)用 Bearer 认证,直接放行。
- **限流为什么放在内存里?** 简单,而且本应用本来就必须单进程运行(WebSocket 连接表、AI 队列都在内存)。多台服务器时要换成 Redis。

## 6. 已知限制 / 以后可以改进
- 旧的 `live.db` 的 users 表残留 `token` 列(NOT NULL),新代码注册会失败。引入 Alembic 后用迁移解决。
- 注册时把所有 `IntegrityError` 都当作「用户名重复」→ 遇到别的约束错误也会报 409(这次排查旧 DB 时就踩到了)。
- 部署到 Nginx 后方时,要给 uvicorn 加 `--proxy-headers`,否则登录限流拿到的都是 Nginx 的 IP。
- HTTPS 环境要把 `COOKIE_SECURE=true`。

## 7. 自测问题(能不看代码讲出来,就算掌握)
1. HttpOnly 防的是什么攻击?SameSite=Lax 防的是什么攻击?
2. 为什么用 Bearer 令牌的请求不需要 CSRF 令牌?
3. 用户在 iPhone 上登出,浏览器里还登录着吗?为什么?
4. 登录后为什么要重新连接 WebSocket?
5. 连续输错 5 次密码后,输入正确密码会怎样?过多久恢复?
6. DB 里的 token_hash 被偷了,能拿来登录吗?
