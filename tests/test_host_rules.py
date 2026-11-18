"""ルールベース応答(LLM なしの時の話し方):意図と商品を判定し、実データで答える"""
import random
from types import SimpleNamespace as P

import pytest

from app.host_rules import (
    FOREIGN_FALLBACK, comment_lang, detect_intent, find_product, is_japanese, off_rate, product_keywords,
    rule_reply, spotlight,
)

PRODUCTS = [
    P(id=1, name="ワイヤレスイヤホン Pro", price=4980, original_price=7980, stock=30,
      description="ノイズキャンセリング搭載、最大32時間再生、IPX5防水"),
    P(id=2, name="ステンレス保温マグ 480ml", price=1880, original_price=2480, stock=60,
      description="12時間保温・保冷、ワンタッチ開閉、食洗機OK"),
    P(id=3, name="ヒアルロン酸美容液 30ml", price=2680, original_price=3980, stock=15,
      description="高濃度ヒアルロン酸配合、無香料・無着色、敏感肌OK"),
]
RNG = lambda: random.Random(1)  # noqa: E731


@pytest.mark.parametrize("text,expected_id", [
    ("イヤホンの音質どう?", 1),            # 略称
    ("マグ気になる", 2),
    ("保温マグは食洗機いける?", 2),
    ("美容液について教えて", 3),
    ("敏感肌でも使えますか", 3),            # 商品名はないが説明文の語と一致
    ("こんにちは", None),
])
def test_find_product_by_short_name_or_description(text, expected_id):
    p = find_product(text, PRODUCTS)
    assert (p.id if p else None) == expected_id


def test_keywords_do_not_include_generic_words():
    assert "Pro" not in product_keywords("ワイヤレスイヤホン Pro")
    assert "イヤホン" in product_keywords("ワイヤレスイヤホン Pro")


@pytest.mark.parametrize("text,intent", [
    ("送料はいくらですか?", "shipping"),       # 「いくら」より送料を優先
    ("返品できますか", "returns"),
    ("在庫ありますか", "stock"),
    ("もう少し安くしてほしい", "haggle"),
    ("おすすめはどれ?", "recommend"),
    ("わーい", None),
])
def test_detect_intent(text, intent):
    assert detect_intent(text) == intent


def test_price_reply_uses_real_numbers():
    reply, pid, emotion = rule_reply("taro", "イヤホンはいくら?", PRODUCTS, RNG())
    assert pid == 1 and "4,980円" in reply and f"{off_rate(4980, 7980)}%オフ" in reply
    assert emotion == "excited"                       # 30%以上の割引


def test_stock_reply_warns_when_low():
    reply, pid, emotion = rule_reply("taro", "美容液の在庫ありますか", PRODUCTS, RNG())
    assert pid == 3 and "残り15点" in reply and emotion == "troubled"


def test_feature_reply_quotes_description_and_switches_product_card():
    reply, pid, _ = rule_reply("taro", "敏感肌でも使えますか", PRODUCTS, RNG())
    assert pid == 3 and "敏感肌OK" in reply


def test_haggling_is_declined_politely():
    reply, _, emotion = rule_reply("taro", "安くしてください", PRODUCTS, RNG())
    assert "価格はご案内しているとおり" in reply and emotion == "troubled"


def test_replies_do_not_all_start_with_thanks():
    """以前は何を聞いても「ありがとうございます」で始まる定型文だった"""
    questions = ["送料は?", "イヤホンいくら?", "在庫ある?", "おすすめは?", "マグについて教えて", "返品できる?"]
    replies = [rule_reply("taro", q, PRODUCTS, RNG())[0] for q in questions]
    assert len(set(replies)) == len(questions)
    assert not any(r.startswith(("ありがとうございます", "ご質問ありがとうございます", "コメントありがとう")) for r in replies)


def test_spotlight_mentions_discount():
    assert "38%オフ" in spotlight(PRODUCTS[0]) and "4,980円" in spotlight(PRODUCTS[0])


# ---------- 多言語:コメントの言語の判定と、LLM なしのときの案内 ----------
@pytest.mark.parametrize("text,expected", [
    ("在庫ありますか", "ja"),
    ("在庫", "ja"),                        # 漢字だけの日本語
    ("送料無料", "ja"),
    ("OK", "ja"),                          # 短い英字は言語を決めない
    ("www", "ja"),
    ("👍", "ja"),
    ("这个多少钱", "zh"),
    ("這個多少錢嗎", "zh"),
    ("How much is this?", "other"),
    ("耳机", "zh"),
    ("얼마예요", "ko"),
    ("¿Cuánto cuesta la taza?", "other"),
])
def test_comment_lang(text, expected):
    assert comment_lang(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("マグは1,880円です。", True),
    ("PayPayが使えます。", True),
    ("合成に失敗", True),
    ("这款耳机现在是¥4,980。", False),
    ("みほさん,这款还有货。", False),      # 中国語の文に日本語の名前
    ("Hi みほさん, the price is ¥4,980.", False),   # 英語の文に日本語の名前
    ("가격은 ¥4,980입니다.", False),
    ("4,980", False),
])
def test_is_japanese(text, expected):
    assert is_japanese(text) is expected


def test_rule_reply_does_not_answer_foreign_comment_in_japanese():
    assert rule_reply("a", "这个耳机多少钱?", PRODUCTS, RNG()) == (FOREIGN_FALLBACK["zh"], None, "troubled")
    assert rule_reply("a", "How much is this?", PRODUCTS, RNG()) == (FOREIGN_FALLBACK["other"], None, "troubled")
