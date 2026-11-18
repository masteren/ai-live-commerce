"""ルールベースの応答(LLM を使わない/使えない時の「話し方」)

以前は 3 つの定型文からランダムに選ぶだけで、何を聞いても同じような返事になっていた。
ここではコメントの「意図」(送料・在庫・価格・特徴…)と「どの商品の話か」を判定し、
DB の実データ(価格・割引率・在庫・説明)を使って答える。純粋関数なのでテストしやすい。

LLM(Claude)がある場合も、API 障害時の縮退先としてこの応答を使う。
"""
import random
import re
from typing import Iterable, List, Optional, Sequence, Tuple

# ---- 店舗情報(プロンプトとルール応答で共通) ----
SHIPPING = "全国送料無料で、ご注文から3〜5日ほどでお届けしています"
RETURNS = "未開封であれば、到着から7日以内は返品を承っています"
PAYMENT = "クレジットカード、コンビニ払い、PayPayからお選びいただけます"

LOW_STOCK = 15        # 在庫がこれ以下なら「残りわずか」として扱う
BIG_DISCOUNT = 30     # 割引率(%)がこれ以上なら「大きな割引」として扱う

# 意図の判定(上から順に評価)
INTENTS: List[Tuple[str, "re.Pattern[str]"]] = [
    ("haggle", re.compile(r"安くして|まけて|値引き(して|できない|できます|できる)|おまけ")),
    ("shipping", re.compile(r"送料|配送|発送|届く|届け|何日|いつ届")),
    ("returns", re.compile(r"返品|返金|交換|キャンセル")),
    ("payment", re.compile(r"支払|払い|カード|PayPay|ペイペイ|コンビニ|分割")),
    ("stock", re.compile(r"在庫|残り|売り切れ|品切れ|まだあり")),
    ("price", re.compile(r"いくら|値段|価格|割引|セール|安い|お得")),
    ("variant", re.compile(r"色|カラー|サイズ|容量違い|種類")),
    ("recommend", re.compile(r"おすすめ|オススメ|どれがいい|どれが|迷って|迷う")),
    ("greeting", re.compile(r"こんにちは|こんばんは|おはよう|はじめまして|初めて|初見")),
    ("feature", re.compile(r"どう|どんな|使い|特徴|音質|保温|保冷|敏感肌|肌|性能|教えて|って何|について")),
]

_GENERIC_TOKENS = {"pro", "plus", "max", "mini"}

# ---- 言語の判定(コメントと同じ言語で返答するために使う) ----
_KANA_RE = re.compile(r"[ぁ-ゖァ-ヺ]")
# 中国語にだけ出てくる字。「在庫」「送料」のような漢字だけの日本語コメントを中国語と誤判定しないよう、日本語で使う字は入れない
_CHINESE_RE = re.compile(r"[吗嗎呢么麼这這们們您你没沒钱錢价價库货发哪谁为请还]")

# 日本語以外のコメントに、LLM なしで答えるときの案内(ルール応答は日本語しか作れないため)
FOREIGN_FALLBACK = {
    "zh": "不好意思,现在暂时无法详细回答您的问题。价格和库存请查看画面上的商品卡片。",
    "other": ("Sorry, I can't answer that in detail right now. "
              "Please check the product cards on screen for prices and stock."),
}


def _is_han(c: str) -> bool:
    return "一" <= c <= "鿿"


def comment_lang(text: str) -> str:
    """コメントの言語を "ja" / "zh" / "other" に分ける。
    「OK」「www」や数字・絵文字だけのような、言語を決められない短いコメントは日本語として扱う"""
    if _KANA_RE.search(text):
        return "ja"
    if _CHINESE_RE.search(text):
        return "zh"
    letters = [c for c in text if c.isalpha()]
    if not letters or any(_is_han(c) for c in letters):
        return "ja"
    if all(c.isascii() for c in letters) and len(letters) < 5:
        return "ja"
    return "other"


def is_japanese(text: str) -> bool:
    """返答が日本語か。外国語の文に日本語の名前(「みほさん」)が混ざることがあるので、かながあるだけでは日本語としない"""
    letters = [c for c in text if c.isalpha()]
    if not letters or _CHINESE_RE.search(text):
        return False
    kana = len(_KANA_RE.findall(text))
    other_script = sum(1 for c in letters if not _KANA_RE.match(c) and not _is_han(c))
    return kana / len(letters) >= 0.2 and kana * 2 > other_script


def yen(n: int) -> str:
    return f"{n:,}円"


def off_rate(price: int, original: int) -> int:
    return round((1 - price / original) * 100) if original > price > 0 else 0


def spoken(name: str) -> str:
    """話すときの商品名(末尾の容量・個数は読まない):「ステンレス保温マグ 480ml」→「ステンレス保温マグ」"""
    return re.sub(r"\s*\d+\s*(ml|mL|g|個|枚|本)$", "", name).strip()


def product_keywords(name: str) -> List[str]:
    """商品名から、略称でも当たるキーワードを作る(長い順)
    例:「ワイヤレスイヤホン Pro」→ ワイヤレスイヤホン / イヤホン …、「ステンレス保温マグ 480ml」→ 保温マグ / マグ …"""
    kws = {name}
    base = spoken(name)
    kws.add(base)
    for tok in re.findall(r"[ァ-ヶー]{2,}|[一-龠々]{2,}|[A-Za-z]{3,}", base):
        if tok.lower() in _GENERIC_TOKENS:
            continue
        kws.add(tok)
        if re.fullmatch(r"[ァ-ヶー]+", tok):        # カタカナ語は後ろの部分でも当てる(…イヤホン)
            kws.update(tok[i:] for i in range(1, len(tok) - 3))   # 4文字以上の後半部分
        else:                                       # 漢字語は2文字以上の部分で当てる(美容液)
            kws.update(tok[i:j] for i in range(len(tok)) for j in range(i + 2, len(tok) + 1))
    # 「保温マグ」のようにカタカナと漢字がつながった語も当てる
    kws.update(m for m in re.findall(r"[一-龠々]+[ァ-ヶー]+", base))
    return sorted((k for k in kws if len(k) >= 2), key=len, reverse=True)


def find_product(text: str, products: Sequence) -> Optional[object]:
    """コメント中で言及されている商品。
    ① 商品名(略称を含む)で最も長く一致したもの
    ② なければ、説明文と共通する語(例:「敏感肌」「食洗機」)がある商品"""
    best, best_len = None, 0
    for p in products:
        for kw in product_keywords(p.name):
            if kw in text and len(kw) > best_len:
                best, best_len = p, len(kw)
                break
    if best:
        return best
    words = [w for w in re.findall(r"[ァ-ヶー]{3,}|[一-龠々]{2,}", text)]
    for p in products:
        if any(w in p.description for w in words):
            return p
    return None


def detect_intent(text: str) -> Optional[str]:
    for name, pat in INTENTS:
        if pat.search(text):
            return name
    return None


def _maybe_call(nickname: str, rng: random.Random) -> str:
    """毎回名前を呼ぶと機械的なので、ときどきだけ呼ぶ"""
    return f"{nickname}さん、" if nickname and rng.random() < 0.4 else ""


def rule_reply(nickname: str, text: str, products: Sequence,
               rng: Optional[random.Random] = None) -> Tuple[str, Optional[int], str]:
    """(返答, 話題にした商品ID, 感情) を返す"""
    rng = rng or random.Random()
    p = find_product(text, products)
    intent = detect_intent(text)
    lang = comment_lang(text)
    if lang == "zh" or (lang == "other" and not p and not intent):
        return FOREIGN_FALLBACK[lang], None, "troubled"
    call = _maybe_call(nickname, rng)
    pid = p.id if p else None

    if intent == "haggle":
        return (f"{call}ごめんなさい、価格はご案内しているとおりなんです。"
                "そのぶん本日はセール価格でご用意していますので、ぜひご検討ください。", pid, "troubled")
    if intent == "shipping":
        return f"{call}{SHIPPING}。", pid, "normal"
    if intent == "returns":
        return f"{call}{RETURNS}。安心してお選びくださいね。", pid, "normal"
    if intent == "payment":
        return f"お支払いは{PAYMENT}。", pid, "normal"

    if intent == "stock":
        if p:
            if p.stock <= 0:
                return f"{call}{spoken(p.name)}は、申し訳ありません、ただいま在庫切れです。", pid, "troubled"
            if p.stock <= LOW_STOCK:
                return f"{call}{spoken(p.name)}は残り{p.stock}点です。少なくなってきていますので、お早めにどうぞ。", pid, "troubled"
            return f"{call}{spoken(p.name)}は、まだ在庫があります。残り{p.stock}点です。", pid, "normal"
        low = [x for x in products if 0 < x.stock <= LOW_STOCK]
        if low:
            items = "、".join(f"{spoken(x.name)}(残り{x.stock}点)" for x in low)
            return f"在庫が少なくなっているのは、{items}です。ほかはまだ余裕があります。", low[0].id, "troubled"
        return "どの商品も、まだ在庫に余裕があります。", None, "normal"

    if intent == "price":
        target = p or (max(products, key=lambda x: off_rate(x.price, x.original_price)) if products else None)
        if target:
            off = off_rate(target.price, target.original_price)
            tail = f"通常{yen(target.original_price)}のところ、{off}%オフです。" if off else ""
            return (f"{call}{spoken(target.name)}は{yen(target.price)}です。{tail}", target.id,
                    "excited" if off >= BIG_DISCOUNT else "happy")

    if intent == "variant":
        name = spoken(p.name) if p else "商品"
        return f"ごめんなさい、{name}のカラーやサイズについては、いまお伝えできる情報がないんです。", pid, "troubled"

    if intent == "recommend" and products:
        pick = p or max(products, key=lambda x: off_rate(x.price, x.original_price))
        off = off_rate(pick.price, pick.original_price)
        why = f"本日いちばん割引が大きく、{off}%オフになっています" if off else "人気の商品です"
        return f"{call}迷ったら{spoken(pick.name)}がおすすめです。{why}。", pick.id, "happy"

    if intent == "greeting":
        return f"{nickname}さん、こんにちは。ゆっくりご覧になってくださいね。", None, "happy"

    if p:
        # 商品について(特徴の質問、または商品名だけのコメント)
        return f"{spoken(p.name)}は、{p.description}です。本日は{yen(p.price)}でご用意しています。", pid, "happy"

    return rng.choice([
        ("ありがとうございます。気になる商品があれば、商品名で聞いてみてくださいね。", None, "normal"),
        ("コメントうれしいです。商品のことなら何でもお答えしますね。", None, "happy"),
        ("ゆっくりご覧ください。価格や在庫も、その場でお答えできます。", None, "normal"),
    ])


def spotlight(p) -> str:
    """無人時の自動トーク:商品の紹介"""
    off = off_rate(p.price, p.original_price)
    deal = f"本日は{off}%オフの{yen(p.price)}です" if off else f"価格は{yen(p.price)}です"
    return f"ただいまご紹介しているのは{spoken(p.name)}。{p.description}です。{deal}。"


def mentions_product(text: str, products: Iterable) -> bool:
    return find_product(text, list(products)) is not None
