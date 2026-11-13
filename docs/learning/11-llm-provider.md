# 学习笔记 11:LLM 做成可切换的(Anthropic / OpenAI)

## 1. 改了什么
- 新增 `app/llm.py`:`LLMProvider` 接口 + `AnthropicLLM` / `OpenAILLM` 两个实现。
- `.env` 的 `LLM_PROVIDER`:`auto`(哪个 key 有值就用哪个,都有则用 Anthropic)/ `anthropic` / `openai` / `none`。
- `ai_host` 只写 `raw = await self.llm.complete(system, messages)`,不知道背后是哪一家。
- 新增 `parse_llm_reply()`:解析并**校验** LLM 的返回。
- OpenAI 的 key 同时给 bot 的弹幕池用(本来就是 OpenAI 兼容接口)。

## 2. 两家 API 的差别(只有这些,所以抽象很薄)
| | Anthropic | OpenAI |
|---|---|---|
| 地址 | `/v1/messages` | `/chat/completions` |
| 认证头 | `x-api-key` | `Authorization: Bearer` |
| system 提示词 | 单独的 `system` 字段 | 放在 `messages` 的第一条(`role: system`) |
| 取回答 | `content[].text` | `choices[0].message.content` |
提示词、历史、JSON 解析、失败降级都是共用的。

## 3. 「不信任 LLM 的输出」(面试重点)
`parse_llm_reply` 做的检查:
- 不是 JSON、不是对象、没有 `reply` 字符串 → 返回 None → **不把原文给观众看**,改用规则回答
- `product_id` 只接受真实存在的 id(防止 LLM 编造)
- `emotion` 只接受 5 种之一
- 字数上限:提示词里要求 90 字,但 LLM 不一定遵守 → 代码里再截断
以前的写法是「解析失败就把原文当回答」,格式不对时观众会直接看到一段 JSON 或乱七八糟的文字。

## 4. 测试(`tests/test_llm.py`)
用 `httpx.MockTransport` 模拟两家的服务器:请求格式是否正确、API 返回 500 时降级、返回的不是 JSON 时降级、第二次请求是否带上了前一轮对话、提示词里是否有 DB 的真实价格和库存。
**注意:模拟测试只能证明「我们发出的请求和对返回的处理」符合预期,不能证明真实的 API 接受这个请求** —— 所以还需要用真 key 跑一次。

## 5. 和 TTS 的做法是同一个模式
接口(Protocol)+ 多个实现 + 用配置选择 + 构造函数里可以注入假的 transport 方便测试。两处都这样做,所以可以总结成一句话:「外部服务一律通过接口访问,不绑定具体厂商,失败时有降级」。

## 6. 自测问题
1. 两家 API 在 system 提示词的传法上有什么不同?
2. `LLM_PROVIDER=auto` 时,两个 key 都填了会用哪个?
3. LLM 返回了一个不存在的 product_id,会发生什么?
4. 模拟测试全部通过,为什么还要用真 key 再跑一次?
