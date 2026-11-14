# AI Live Commerce(中文简介)

> 正式的说明是根目录的 [README.md](../README.md)(日文)。这里只放中文的简要说明,细节以日文版和代码为准。

日语电商直播的全栈作品:AI 主播「花奈」实时回答弹幕、介绍商品,观众可以直接下单。

## 组成
| 部分 | 技术 |
|---|---|
| 后端 | Python 3.12+ / FastAPI(async)/ SQLAlchemy 2 / Alembic |
| 数据库 | MySQL 8.4(开发,Docker)/ SQLite(单元测试) |
| 实时 | WebSocket + asyncio 队列 |
| AI・语音 | LLM(OpenAI 或 Anthropic,可切换;没有 key 时用规则回答)、VOICEVOX |
| 前端 | 原生 HTML/CSS/JS + three-vrm |
| iOS | SwiftUI(同一套 API,Bearer 认证) |
| 测试 | pytest 125 项、E2E 41 项、iOS UI 测试、GitHub Actions |

## 启动
```bash
source venv/bin/activate          # Windows: venv\Scripts\activate
docker compose up -d db           # 开发用 MySQL
uvicorn app.main:app              # 启动时自动执行数据库迁移
```
- 观众页面:http://127.0.0.1:8000/
- 管理页面:http://127.0.0.1:8000/admin(用 `.env` 里的 ADMIN_TOKEN 登录)

## 测试
```bash
pytest                            # 单元测试,用临时 SQLite,不碰开发数据库
```
E2E 的跑法见日文 README 的「テスト」。**不要对开发数据库跑测试。**

## 想理解代码时
按顺序读 [docs/learning/](learning/) 的学习笔记(01〜20),每篇末尾有自测问题。
设计资料在 [docs/design/](design/)(需求、ER 图、时序图、画面迁移图)。
