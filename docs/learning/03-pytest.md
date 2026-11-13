# 学习笔记 03:pytest 单元测试

## 1. 现在有两种测试
| | `pytest`(tests/) | `test_e2e.py` |
|---|---|---|
| 怎么跑 | `pytest`,不需要启动服务器 | 先启动服务器,再 `python test_e2e.py` |
| 速度 | 35 项约 1.5 秒 | 41 项约 15 秒 |
| 测什么 | 单个函数、单个 API 的行为,包括很难手动制造的情况(会话过期、VOICEVOX 挂掉) | 真实的服务器 + WebSocket + 后台任务,整条流程 |
| 外部服务 | 用假的代替(MockTransport、NullTTS) | 关掉(VOICEVOX_ENABLED=false) |

## 2. 关键技巧
- **`conftest.py`**:pytest 自动加载的共通设定。这里在 **import app 之前** 设置环境变量(settings 在 import 时就读取了),让测试用临时 DB。
- **fixture**:测试的「准备工作」。`fresh_db` 加了 `autouse=True`,每个测试前都会重建 DB → 测试之间互不影响。`client` fixture 给测试一个 HTTP 客户端。
- **`httpx.ASGITransport`**:不启动服务器,直接在进程内调用 FastAPI 应用 → 快。
- **`httpx.MockTransport`**:把「发往 VOICEVOX 的 HTTP 请求」换成自己写的函数,可以返回假数据,或者故意抛出连接错误来测试降级。
- **`monkeypatch`**:测试期间临时替换函数,比如把 `time.monotonic` 换成可控的时间,不用真的等 10 秒;把 `random.random` 固定,测试概率分支。
- **`@pytest.mark.parametrize`**:同一个测试用多组输入跑。

## 3. 为了可测试而做的设计
`VoicevoxTTS` 的构造函数接收 `transport` 参数(平时是 None = 真实网络,测试时传 MockTransport)。这叫 **依赖注入(DI)**:把依赖从外部传进来,而不是在内部写死。

## 4. 测试覆盖的重点(面试可以举例)
- 并发下单不超卖(5 人抢 1 件 → 1 个成功,库存 0)
- 库存不足时整单回滚(另一件商品的库存也不变、购物车保留)
- 会话过期 → 401;登出只影响当前设备;DB 里查不到原始令牌
- VOICEVOX 挂掉 → 返回 None 不抛异常;同一句话第二次走缓存

## 5. 自测问题
1. 为什么环境变量要在 import app 之前设置?
2. 不用 MockTransport 的话,怎么测试「VOICEVOX 挂了」的情况?麻烦在哪?
3. `fresh_db` 如果不是 autouse,会出现什么问题?
4. 单元测试全部通过,为什么还需要 E2E 测试?
