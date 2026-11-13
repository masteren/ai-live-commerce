# AI Live Commerce 🌸

日本のライブコマース(直播带货)全栈系统:AI 販売アシスタント「花奈」实时回答弹幕、按情绪切换表情与语音语调、专业带货。面向就职活动作品集的商用级演示。

```
浏览器(观众) ──WebSocket──▶ FastAPI ──▶ AI主播worker ──▶ Anthropic API (LLM回答+情绪)
     ▲                        │  │              │
     │◀── 弹幕/回答/购买广播 ──┘  │              └──▶ VOICEVOX (情绪声线TTS → wav缓存)
     │                           └──▶ SQLite (用户/商品/购物车/订单/弹幕)
浏览器(管理后台 /admin) ──REST──▶ 商品CRUD · 统计 · 气氛组BOT开关
```

## 功能一览

| 模块 | 说明 |
|---|---|
| AI 主播「花奈」 | 专业販売アシスタント人设(丁寧語、不夸大、拒绝议价),问题弹幕必答、闲聊 30% 概率接话,带 6 轮上下文 |
| 情绪系统 | LLM 每次输出 emotion(normal/happy/excited/troubled/shy)→ 前端立绘表情微变化,后端对 VOICEVOX 做克制的语速/音高微调 |
| VOICEVOX TTS | ずんだもん声线,按「文本+声线参数」md5 缓存 wav;引擎不在线自动降级纯文字 |
| 多用户弹幕 | WebSocket 广播,在线人数实时统计,断线自动重连;按用户限流防刷屏;Origin 检查防跨站劫持 |
| 用户系统 | 注册/登录(pbkdf2 哈希);Web 用 HttpOnly Cookie + CSRF 令牌,iOS 等原生 App 用 Bearer 令牌;会话有有效期、DB 只存哈希、支持多端登录;登录失败限流防暴力破解 |
| 电商闭环 | 商品 → 购物车 → 模拟支付页(信用卡/コンビニ/PayPay)→ 条件 UPDATE 原子扣库存防超卖(并发测试验证)→ 订单 |
| 直播联动 | 购买事件全房间播报 + 主播语音道谢;主播提到的商品自动切换商品卡 |
| 管理后台 | 商品上架/改价/改库存/下架(软删除),销售统计,气氛组 BOT 开关 |
| 气氛组 BOT | 自动发日语弹幕防冷场(演示用),15% 为提问型会触发主播回答 |
| 降级链 | 无 API key → 定型文回复;VOICEVOX 挂 → 纯文字。任何一环失败直播不中断 |

## 快速开始(本机)

```bash
cd ai-live-commerce
python3 -m venv venv && source venv/bin/activate    # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env                                 # 按需填 ANTHROPIC_API_KEY 等
uvicorn app.main:app --reload
```

- 观众端: http://127.0.0.1:8000/ (开两个浏览器窗口即可体验多用户弹幕)
- 管理后台: http://127.0.0.1:8000/admin (令牌 = .env 的 ADMIN_TOKEN)
- API 文档: http://127.0.0.1:8000/docs (交互式;离线版见 openapi.json)

首次启动自动执行数据库迁移(Alembic)并播种 3 件示例商品。不填 `ANTHROPIC_API_KEY` 也能跑通全部流程(主播用定型文回复)。

## VOICEVOX 语音(用 Windows 台式机跑)

1. 台式机安装并启动 [VOICEVOX](https://voicevox.hiroshiba.jp/)(桌面版自带引擎,监听 `127.0.0.1:50021`)。
2. **FastAPI 与 VOICEVOX 同一台机器** → `.env` 保持默认即可。
3. **FastAPI 在 Mac、VOICEVOX 在台式机** → 引擎需允许局域网访问:
   - 找到 VOICEVOX 安装目录下的 `vv-engine/run.exe`,命令行启动:
     `run.exe --host 0.0.0.0`(Windows 防火墙放行 50021 端口)
   - Mac 侧 `.env` 改 `VOICEVOX_URL=http://<台式机局域网IP>:50021`
4. 验证:浏览器访问 `http://<IP>:50021/speakers` 能看到声线列表即通。
5. 换声线:改 `VOICEVOX_SPEAKER`(3=ずんだもん、8=春日部つむぎ、2=四国めたん…全列表见 /speakers)。
   情绪→声线风格映射在 `app/ai_host.py` 的 `EMOTION_VOICE`,可自行微调。

浏览器音频自动播放有限制:进直播间后点右上角 🔊 开启声音。

## AI 主播接入 Claude API

`.env` 填入 `ANTHROPIC_API_KEY=sk-ant-...` 后重启即可。相关成本/体验旋钮:

- `REPLY_MIN_INTERVAL` 两次回答最小间隔(限流控费)
- `CHAT_REPLY_PROB` 闲聊接话概率(问题类弹幕始终必答)
- 人设与硬规则在 `app/ai_host.py` 的 `_build_system_prompt()`,商品数据每次实时注入,不会答错价格库存

## 数据库:SQLite(默认)/ MySQL

表结构由 **Alembic** 管理(`migrations/`),应用启动时自动执行 `alembic upgrade head`(`AUTO_MIGRATE=false` 可关闭)。

```bash
# MySQL(需要 Docker)
docker compose up -d db
# .env: DATABASE_URL=mysql+aiomysql://liveuser:livepass@127.0.0.1:3306/livecommerce?charset=utf8mb4
uvicorn app.main:app --reload

# 修改了 models.py 之后
alembic revision --autogenerate -m "变更内容"
alembic upgrade head
```

- MySQL 使用 `utf8mb4` + 照合顺序 `utf8mb4_bin`(保证日文/emoji 可存、用户名按完全一致判定重复)。
- 下单用 `UPDATE ... SET stock = stock - q WHERE stock >= q` 条件更新原子扣库存,SQLite / MySQL / PostgreSQL 都防并发超卖(SQLite 会忽略 `FOR UPDATE`,所以不能只靠行锁);MySQL/PG 下另有 `SELECT ... FOR UPDATE` 行锁。
- PostgreSQL 也可用(`pip install asyncpg`,`DATABASE_URL=postgresql+asyncpg://...`)。

## 项目结构

```
app/
├── main.py            # 入口:路由挂载、WebSocket 端点、启动任务、种子数据
├── config.py          # 全部配置(.env 覆盖)
├── database.py        # 异步 SQLAlchemy 引擎(SQLite/MySQL/PG 切换)
├── migrate.py         # 启动时执行 Alembic 迁移
├── models.py          # User / Product / CartItem / Order / OrderItem / Comment
├── schemas.py         # Pydantic 请求/响应模型
├── ws.py              # WebSocket 连接管理器(广播/人数/峰值)
├── ai_host.py         # ★ AI主播:人设prompt、情绪系统、应答策略、限流队列、道谢
├── tts.py             # VOICEVOX 客户端(情绪声线参数、wav缓存、降级)
├── bot.py             # 气氛组机器人
├── routers/
│   ├── auth.py        # 注册/登录/登出/me/token(pbkdf2 + Cookie 会话 + Bearer)
│   ├── products.py    # 公开商品接口
│   ├── cart.py        # 购物车(需登录)
│   ├── orders.py      # 模拟支付下单(事务扣库存)+ 我的订单
│   └── admin.py       # 商品CRUD/统计/BOT开关(X-Admin-Token)
└── static/
    ├── index.html     # 观众端直播间(原生JS,零构建)
    └── admin.html     # 管理后台
test_e2e.py            # 端到端测试(41项:认证/CSRF/Bearer/下单/并发超卖/限流/WS/BOT,全部通过)
openapi.json           # 导出的 API 规格(iOS 端对接用)
```

## FAQ

- **弹幕发不出去?** 需要先注册/登录(右上角 👤)。
- **主播不说话?** 检查 VOICEVOX 引擎是否启动、`VOICEVOX_URL` 是否可达、是否点了 🔊。后端日志会打印 `[tts] 降级纯文字` 的原因。
- **主播不回答闲聊?** 这是混合应答策略的预期行为;把 `CHAT_REPLY_PROB` 调到 1.0 就变成每条必答。
- **重置数据?** 停服后删除 `live.db` 再启动即可(会重新播种示例商品)。
- **升级后注册失败(提示用户名已存在)?** 引入 Alembic 之前生成的旧 `live.db`(users 表还有已废弃的 token 列)与当前结构不兼容,请备份后删除 `live.db` 重新生成。之后的结构变更都通过迁移完成。
- **跑单元测试?** `pip install -r requirements-dev.txt` 后执行 `pytest`(不需要启动服务,自动使用临时数据库,约 2 秒)。
- **跑端到端测试?** 测试会注册用户、下单,且假设数据库为空 —— **不要对 live.db 跑**。用独立 DB 另开端口启动服务,再指定地址运行:
  ```bash
  DATABASE_URL=sqlite+aiosqlite:///./test.db ADMIN_TOKEN=test-token VOICEVOX_ENABLED=false LLM_PROVIDER=none \
    uvicorn app.main:app --port 8001
  E2E_BASE=http://127.0.0.1:8001 python3 test_e2e.py     # 结束后停服并 rm test.db
  ```
