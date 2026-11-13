# 学习笔记 02:TTS 的 Provider 化、logging、后台任务的异常保护

## 1. 改了什么
- `app/tts.py`:把「调用 VOICEVOX」改成 **TTSProvider 接口 + 多个实现**
  - `VoicevoxTTS`(VOICEVOX)、`NullTTS`(不发声),用 `.env` 的 `TTS_PROVIDER` 切换
  - 「情绪 → 声音参数」的对应表从 `ai_host.py` 移到了 `VoicevoxTTS` 里(这是 VOICEVOX 专用的知识)
  - `ai_host` 只写 `await tts.synthesize(text, emotion)`,不再知道背后是哪个引擎
- 全部 `print` 换成 `logging`(带时间、级别、模块名;`.env` 的 `LOG_LEVEL` 控制输出量)
- `_idle_worker` 加了 try/except:以前这里出异常,自动播报的 task 会**静默死掉**,之后再也不播报
- 顺手修了一个 bug:以前情绪的语速会**覆盖** `.env` 的 `VOICEVOX_SPEED`,配置项实际上没生效;现在是「基础语速 × 情绪系数」

## 2. 和学过的内容的对应
| 概念 | 对应 |
|---|---|
| 接口(Protocol)和多个实现 | 课上没正式学;思路接近 Node-04 把加密处理做成 `utils/cryptoUtil.js` 模块再导入 |
| 用 `.env` 切换行为 | Node-04 dotenv、Node-05 `process.env` |
| 日志的级别 | Node 课里的 `console.log` / `console.error` 的正式版 |

## 3. 为什么这样设计(面试会问)
- **依赖倒置**:业务逻辑(ai_host)依赖「抽象」而不是「具体的 VOICEVOX」。换引擎(比如 OmniVoice)只需新增一个类,ai_host 不用改。
- **方便测试**:测试时换成 `NullTTS` 或假的实现(mock),不需要真的启动 VOICEVOX。
- **logging 比 print 好在哪**:有时间戳和级别,可以按级别过滤;部署后能统一收集(`docker logs` 等);`logger.exception` 会自动带上异常的堆栈。
- **后台 task 为什么要包 try**:`asyncio.create_task` 启动的任务如果抛异常,只会在任务结束时留下一条警告,主程序不会察觉 → 功能悄悄停掉。常驻循环必须在循环内部兜底。

## 4. 自测问题
1. 想加一个新的 TTS 引擎,需要改哪几个地方?
2. `NullTTS` 有什么用?
3. `logger.warning` 和 `logger.exception` 的区别?
4. 如果 `_idle_worker` 里没有 try/except,`_speak` 抛异常后会发生什么?
