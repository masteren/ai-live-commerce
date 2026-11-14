"""LLM クライアント:差し替え可能な Provider 構造(TTS と同じ考え方)

呼び出し側(ai_host)は「system プロンプトと会話履歴を渡すと、返答の文字列が返る」ことだけに依存する:
    raw = await llm.complete(system, messages)

実装:
  - AnthropicLLM : Anthropic Messages API(Claude)
  - OpenAILLM    : OpenAI 互換の Chat Completions API(OpenAI のほか、互換 API なら base_url を変えるだけ)
.env の LLM_PROVIDER で選ぶ(auto = キーが設定されている方。両方あれば Anthropic)。
どちらのキーも無ければ None(ai_host がルール応答に切り替える)。

2社の違いは「リクエストの形」と「返答の取り出し方」だけなので、ここに閉じ込める。
プロンプト・JSON の解釈・失敗時の縮退は ai_host 側で共通。
"""
import logging
from typing import List, Optional, Protocol

import httpx

from .config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 15      # 秒。回答は単一キューで順番に処理するため、LLM が固まると後ろが全部待つ → 短めにして縮退させる


class LLMProvider(Protocol):
    name: str        # 表示用(フッター・ログ)

    async def complete(self, system: str, messages: List[dict]) -> str:
        """返答のテキストを返す。通信エラーや HTTP エラーは例外として投げる(呼び出し側で縮退する)"""
        ...


class AnthropicLLM:
    def __init__(self, api_key: str, model: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.api_key, self.model, self.transport = api_key, model, transport
        self.name = f"Anthropic Claude API({model})"

    async def complete(self, system: str, messages: List[dict]) -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=self.transport) as client:
            res = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 300,
                    # ライブの即答が目的:短い返答を速く返すため思考はオフ
                    "thinking": {"type": "disabled"},
                    "system": system,                 # Anthropic は system を別フィールドで渡す
                    "messages": messages,
                },
            )
            res.raise_for_status()
            data = res.json()
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")


class OpenAILLM:
    def __init__(self, api_key: str, model: str, base_url: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self.api_key, self.model, self.transport = api_key, model, transport
        self.base_url = base_url.rstrip("/")
        self.name = f"OpenAI API({model})"

    async def complete(self, system: str, messages: List[dict]) -> str:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=self.transport) as client:
            res = await client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    # OpenAI 形式は system も messages の先頭に入れる
                    "messages": [{"role": "system", "content": system}] + messages,
                    # 返答を必ず JSON にする(プロンプトでも JSON を指示している)
                    "response_format": {"type": "json_object"},
                },
            )
            res.raise_for_status()
            data = res.json()
        return data["choices"][0]["message"]["content"] or ""


def build_llm() -> Optional[LLMProvider]:
    provider = settings.llm_provider.lower()
    if provider == "none":
        return None
    if provider in ("auto", "anthropic") and settings.anthropic_api_key:
        return AnthropicLLM(settings.anthropic_api_key, settings.anthropic_model)
    if provider in ("auto", "openai") and settings.openai_api_key:
        return OpenAILLM(settings.openai_api_key, settings.openai_model, settings.openai_base_url)
    if provider not in ("auto", "anthropic", "openai"):
        logger.warning("LLM_PROVIDER=%s は未対応です(auto / anthropic / openai / none)", provider)
    return None
