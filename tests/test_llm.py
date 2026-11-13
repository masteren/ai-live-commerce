"""LLM Provider(Anthropic / OpenAI)と、返答の解釈・縮退。通信は httpx.MockTransport で置き換える"""
import json

import httpx
import pytest

from app import llm as llm_mod
from app.ai_host import host, parse_llm_reply
from app.llm import AnthropicLLM, OpenAILLM, build_llm

GOOD = json.dumps({"reply": "マグは1,880円です。", "product_id": 2, "emotion": "happy"}, ensure_ascii=False)


def anthropic_transport(text, calls, status=200):
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"content": [{"type": "text", "text": text}]})
    return httpx.MockTransport(handler)


def openai_transport(text, calls, status=200):
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"choices": [{"message": {"role": "assistant", "content": text}}]})
    return httpx.MockTransport(handler)


@pytest.fixture
def use_llm():
    """テスト中だけホストの LLM を差し替える"""
    original = host.llm
    host.history = []
    yield lambda provider: setattr(host, "llm", provider)
    host.llm, host.history = original, []


# ---------- 2社のリクエスト形式の違い ----------
async def test_anthropic_request_shape():
    calls = []
    out = await AnthropicLLM("key-a", "claude-x", anthropic_transport(GOOD, calls)).complete(
        "SYSTEM", [{"role": "user", "content": "taro: いくら?"}])
    body = json.loads(calls[0].content)
    assert out == GOOD
    assert str(calls[0].url) == "https://api.anthropic.com/v1/messages"
    assert calls[0].headers["x-api-key"] == "key-a"
    assert body["system"] == "SYSTEM" and body["messages"][0]["role"] == "user"      # system は別フィールド


async def test_openai_request_shape():
    calls = []
    out = await OpenAILLM("key-o", "gpt-x", "https://api.openai.com/v1/", openai_transport(GOOD, calls)).complete(
        "SYSTEM", [{"role": "user", "content": "taro: いくら?"}])
    body = json.loads(calls[0].content)
    assert out == GOOD
    assert str(calls[0].url) == "https://api.openai.com/v1/chat/completions"
    assert calls[0].headers["authorization"] == "Bearer key-o"
    assert body["messages"][0] == {"role": "system", "content": "SYSTEM"}              # system は messages の先頭
    assert body["response_format"] == {"type": "json_object"}


# ---------- どの Provider を使うか ----------
@pytest.mark.parametrize("provider,a_key,o_key,expected", [
    ("auto", "", "", type(None)),
    ("auto", "", "ok", OpenAILLM),
    ("auto", "ak", "ok", AnthropicLLM),        # 両方あれば Anthropic
    ("openai", "ak", "ok", OpenAILLM),         # 明示すればそちら
    ("none", "ak", "ok", type(None)),
    ("openai", "ak", "", type(None)),
])
def test_build_llm_selection(monkeypatch, provider, a_key, o_key, expected):
    monkeypatch.setattr(llm_mod.settings, "llm_provider", provider)
    monkeypatch.setattr(llm_mod.settings, "anthropic_api_key", a_key)
    monkeypatch.setattr(llm_mod.settings, "openai_api_key", o_key)
    assert isinstance(build_llm(), expected)


# ---------- 返答の解釈(LLM の出力を信用しない) ----------
def test_parse_accepts_valid_json_and_code_fence():
    assert parse_llm_reply(GOOD, {1, 2, 3}) == ("マグは1,880円です。", 2, "happy")
    assert parse_llm_reply(f"```json\n{GOOD}\n```", {1, 2, 3}) == ("マグは1,880円です。", 2, "happy")


@pytest.mark.parametrize("raw", ["すみません、わかりません", "[1, 2]", '{"reply": ""}', '{"reply": 123}', '{"text": "x"}', ""])
def test_parse_rejects_wrong_format(raw):
    assert parse_llm_reply(raw, {1, 2, 3}) is None


def test_parse_ignores_unknown_product_and_emotion():
    raw = json.dumps({"reply": "はい", "product_id": 999, "emotion": "angry"})
    assert parse_llm_reply(raw, {1, 2, 3}) == ("はい", None, "normal")


def test_parse_truncates_overlong_reply():
    reply, _, _ = parse_llm_reply(json.dumps({"reply": "あ" * 1000}), set())
    assert len(reply) <= 181 and reply.endswith("…")       # reply_max_chars(90) の 2 倍 + 省略記号


# ---------- ホスト全体:LLM の返答を使う/使えないときは縮退 ----------
@pytest.mark.parametrize("make", [
    lambda t: AnthropicLLM("k", "m", anthropic_transport(GOOD, t)),
    lambda t: OpenAILLM("k", "m", "https://api.openai.com/v1", openai_transport(GOOD, t)),
])
async def test_host_uses_llm_reply_and_keeps_history(use_llm, make):
    calls = []
    use_llm(make(calls))
    assert await host._generate("taro", "マグはいくら?") == ("マグは1,880円です。", 2, "happy")
    await host._generate("taro", "在庫は?")
    second = json.loads(calls[1].content)
    texts = json.dumps(second, ensure_ascii=False)
    assert "taro: マグはいくら?" in texts and "taro: 在庫は?" in texts        # 2回目は前の会話を含む
    assert "1,880" in texts and "在庫60点" in texts                          # プロンプトに DB の実データ


async def test_host_falls_back_when_api_fails(use_llm):
    use_llm(OpenAILLM("k", "m", "https://api.openai.com/v1", openai_transport("", [], status=500)))
    reply, pid, _ = await host._generate("taro", "マグはいくら?")
    assert pid == 2 and "1,880円" in reply and host.history == []            # ルール応答に切り替え


async def test_host_falls_back_when_llm_returns_non_json(use_llm):
    use_llm(AnthropicLLM("k", "m", anthropic_transport("JSONではない返答です", [])))
    reply, pid, _ = await host._generate("taro", "マグはいくら?")
    assert "JSON" not in reply and pid == 2 and "1,880円" in reply            # 生の出力を視聴者に見せない


def test_bot_never_calls_api_when_llm_provider_is_none(monkeypatch):
    """LLM_PROVIDER=none なら、シェルの環境変数に OPENAI_API_KEY があってもBOTは外部 API を呼ばない
    (以前はテスト実行のたびに実際の API を呼び、利用料が発生していた)"""
    from app import bot as bot_mod
    monkeypatch.setattr(bot_mod.settings, "openai_api_key", "sk-real-key-from-shell-env")
    monkeypatch.setattr(bot_mod.settings, "llm_provider", "none")
    b = bot_mod.DanmakuBot()
    b.set_enabled(True)                 # ここで補充(API 呼び出し)が予約されないこと
    assert b._refilling is False


# ---------- 数値で決まる感情はコードで補正 ----------
def test_adjust_emotion_uses_real_stock_and_discount():
    from types import SimpleNamespace as P
    from app.ai_host import adjust_emotion
    low = P(stock=15, price=2680, original_price=3980)        # 在庫少・33%オフ
    plenty = P(stock=60, price=1880, original_price=2480)     # 在庫十分・24%オフ
    assert adjust_emotion("美容液は在庫が15点ございます。", low, "normal") == "troubled"
    assert adjust_emotion("¥2,680で販売中です。通常は¥3,980です。", low, "normal") == "excited"
    assert adjust_emotion("マグは在庫が60点ございます。", plenty, "normal") == "normal"
    assert adjust_emotion("1,880円です。", plenty, "normal") == "normal"          # 割引が小さい
    assert adjust_emotion("在庫は15点です。", low, "happy") == "happy"            # LLM が選んだ感情は尊重
    assert adjust_emotion("送料は無料です。", None, "normal") == "normal"


async def test_host_overrides_normal_emotion_for_low_stock(use_llm):
    raw = json.dumps({"reply": "美容液は在庫が15点ございます。", "product_id": 3, "emotion": "normal"}, ensure_ascii=False)
    use_llm(OpenAILLM("k", "m", "https://api.openai.com/v1", openai_transport(raw, [])))
    assert (await host._generate("taro", "美容液の在庫は?"))[2] == "troubled"


# ---------- 事実の検証:数字は DB と照合、存在しない案内先は削る ----------
def _products():
    from types import SimpleNamespace as P
    return [P(id=1, price=4980, original_price=7980, stock=30), P(id=2, price=1880, original_price=2480, stock=60),
            P(id=3, price=2680, original_price=3980, stock=15)]


@pytest.mark.parametrize("reply,ok", [
    ("現在¥4,980で、通常価格から38%の割引です。", True),
    ("現在¥4,980で、通常価格から36%の割引です。", False),      # 実際に gpt-4o-mini が返した誤り
    ("イヤホンは4,980円です。", True),
    ("イヤホンは3,980円です。", True),                         # 別商品の通常価格と同じ数字は見分けられない(限界)
    ("イヤホンは4,500円です。", False),
    ("在庫が15点ございます。", True),
    ("在庫は残り3点です。", False),
    ("最大32時間再生、12時間保温です。", True),                # 仕様の数字は対象外
    ("送料は全国無料です。", True),
])
def test_facts_are_consistent(reply, ok):
    from app.ai_host import facts_are_consistent
    assert facts_are_consistent(reply, _products()) is ok


def test_strip_fake_referrals_removes_only_that_sentence():
    from app.ai_host import strip_fake_referrals
    assert strip_fake_referrals("色の情報は手元にありません。詳しくは公式サイトをご覧ください。") == "色の情報は手元にありません。"
    assert strip_fake_referrals("送料は無料です。") == "送料は無料です。"
    assert strip_fake_referrals("公式サイトをご覧ください。") == "公式サイトをご覧ください。"     # 全部消えるなら元のまま


async def test_host_rejects_reply_with_wrong_number(use_llm):
    raw = json.dumps({"reply": "イヤホンは¥4,980で、36%の割引です。", "product_id": 1, "emotion": "excited"}, ensure_ascii=False)
    use_llm(OpenAILLM("k", "m", "https://api.openai.com/v1", openai_transport(raw, [])))
    reply, pid, _ = await host._generate("taro", "イヤホンはいくら?")
    assert "36%" not in reply and "38%オフ" in reply and host.history == []      # ルール応答に切替・履歴に残さない
