# AI Live Commerce 🌸

AI 販売アシスタント「花奈(ハナ)」がライブ配信でコメントに答え、商品を案内する **日本向けライブコマース** の Web アプリです。
視聴者のコメントに AI がリアルタイムで回答し(音声・表情つき)、そのまま購入まで完結します。

> 就職作品(ポートフォリオ)です。決済はモックで、実際の課金は行いません。
> 中文版说明:[docs/README.zh-CN.md](docs/README.zh-CN.md)

<p>
  <img src="docs/images/web_live.png" alt="Web版ライブ画面(3Dアバター・コメント・商品カード)" width="62%">
  <img src="docs/images/ios_live.png" alt="iOS版ライブ画面(購入告知とホストのお礼)" width="18%">
  <img src="docs/images/ios_account.png" alt="iOS版アカウント画面(注文履歴)" width="18%">
</p>

## 主な機能
| 分類 | 内容 |
|---|---|
| AI ホスト | コメントを分類(質問は必ず回答・雑談は確率で返答)し、LLM(Claude / OpenAI を設定で切替)で回答。API なし・障害時も、質問の意図と商品を判定して DB の価格・在庫で答える。商品データを毎回プロンプトに注入し価格・在庫を誤答しない。感情(5種)も同時に出力 |
| 音声・アバター | VOICEVOX で感情に合わせた音声合成(キャッシュあり)。three-vrm の 3D アバターが表情・口パク・まばたき |
| ライブ配信 | WebSocket で多人数のコメントを即時配信、視聴者数表示、自動再接続、無人時の自動トーク、盛り上げ用コメントBOT |
| EC | 商品 → カート → 決済(クレジット/コンビニ/PayPay のモック)→ 注文。購入をライブで告知し、ホストがお礼を言う |
| 管理画面 | 商品の登録・価格/在庫変更・販売停止(論理削除)、売上統計、BOT の ON/OFF |
| フォールバック | API キーなし → 定型文、VOICEVOX 停止 → 文字のみ。どこが落ちても配信は止まらない |

## 技術スタック
| 層 | 技術 |
|---|---|
| バックエンド | Python 3.12+ / FastAPI(非同期)/ SQLAlchemy 2.0(async)/ Alembic / Pydantic |
| DB | SQLite(開発)・MySQL 8.4(docker-compose)、`DATABASE_URL` で切り替え |
| リアルタイム | WebSocket(FastAPI)+ asyncio のキューとバックグラウンドタスク |
| AI・音声 | LLM は Provider 構造で切替(Anthropic Claude API / OpenAI 互換 API)、VOICEVOX ENGINE |
| フロントエンド | HTML / CSS / JavaScript(ビルドなし)、three.js + three-vrm |
| iOS | SwiftUI / URLSession(REST・WebSocket)/ Keychain(同じ API を Bearer 認証で利用、[ios/README.md](ios/README.md)) |
| テスト・CI | pytest(115件)、E2E(41件)、iOS UI テスト(XCUITest:登録〜注文〜ログアウト)、GitHub Actions(SQLite と MySQL の両方で実行) |

## アーキテクチャ
```mermaid
flowchart LR
  V["視聴者ブラウザ<br/>index.html + three-vrm"] -- "WebSocket(コメント・配信)" --> API
  V -- "REST + Cookie" --> API
  I["iOS アプリ(SwiftUI)"] -- "REST / WebSocket + Bearer" --> API
  A["管理画面 admin.html"] -- "REST + X-Admin-Token" --> API
  subgraph API["FastAPI(単一プロセス)"]
    R["REST ルーター<br/>auth / products / cart / orders / admin"]
    W["WebSocket /ws/live"]
    Q[("AI 回答キュー")]
    H["AI ホスト worker"]
    B["コメント BOT"]
    W --> Q --> H
    B --> Q
  end
  H -- "回答生成" --> C["LLM(Claude / OpenAI)"]
  H -- "音声合成" --> T["VOICEVOX"]
  API -- "SQLAlchemy" --> D[("SQLite / MySQL")]
```
設計資料:[要件定義](docs/design/requirements.md) / [ER 図](docs/design/er.md) / [シーケンス図](docs/design/sequence.md) / [画面遷移図](docs/design/screens.md)

## 工夫した点
1. **同時実行でも壊れない注文処理(売り越し・二重注文・カートの重複の防止)**
   在庫の減算を「`UPDATE ... SET stock = stock - :q WHERE stock >= :q`」の条件付き更新にし、判定と減算を DB 内で一度に行う。
   当初は `SELECT ... FOR UPDATE` だけで防いでいたが、自作の並行テスト(在庫1点に5人が同時注文)で **SQLite では行ロックが無視されて5件とも成功する** ことを発見し、方式を見直した。現在は SQLite / MySQL の両方で「成功1件・在庫0」をテストで保証している。
   その後のコードレビューで、同じ「読んでから書く」問題が他にも残っていることを再現テストで確認し、修正した:
   同じユーザーの注文が同時に届くと二重に注文される → カートの行を「id+数量を条件に削除できたか」で確認、
   「カートへ」の同時押しで重複行ができる → 一意制約 + 「INSERT、制約に当たれば加算 UPDATE」。
   後者は MySQL でだけデッドロックする書き方(先に UPDATE)を CI 前のローカル MySQL テストで見つけて直した。
2. **認証:Web と iOS で方式を使い分け**
   - Web:HttpOnly Cookie(JS からトークンを読めない → XSS 対策)+ SameSite=Lax + CSRF トークン(二重送信)
   - iOS:`Authorization: Bearer`(ブラウザが自動送信しないので CSRF 対策は不要)
   - 共通:セッション表は 1 ユーザー複数端末に対応、DB にはトークンの SHA-256 ハッシュのみ保存、有効期限あり、ログイン失敗・新規登録・管理トークンのレート制限(総当たり対策)、WebSocket 接続時の Origin 検証(CSWSH 対策)
   - パスワードは PBKDF2-SHA256(60万回、OWASP の推奨値)。ハッシュに回数を記録してあり、古い回数のユーザーはログイン成功時に自動で現在の強度に更新される。計算は別スレッドで行い、その間も配信(WebSocket)が止まらないようにしている
3. **AI 応答の制御(コストと体験)**
   単一 worker のキューで回答を順番に処理し、最小間隔でレート制限。積み残しは最新 3 件だけ残して古い質問は捨てる。雑談は確率で返答し、購入のお礼は定型文(LLM を使わない)にして API 費用を抑えた。
4. **外部サービスが落ちても止まらない設計**
   LLM(Claude / OpenAI)と TTS(VOICEVOX / なし)は Provider インターフェースで差し替え可能。LLM の出力は信用せず、JSON 形式・商品ID・文字数に加えて、返答中の金額・割引率・在庫数を DB と照合する(実際に LLM が割引率を誤って答えたため)。合わなければ視聴者に見せずルール応答に切り替える。LLM・TTS・BOT 用 API はすべて失敗時に縮退し、テストでは `httpx.MockTransport` でエンジン停止を再現して確認している。

## 起動方法
```bash
# Python 3.12 以上が必要(例:uv python install 3.12 && uv venv venv --python 3.12 --seed)
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # 必要に応じて ANTHROPIC_API_KEY・ADMIN_TOKEN などを設定
uvicorn app.main:app --reload   # 起動時に DB マイグレーション(Alembic)を自動実行
```
- 視聴者ページ:http://127.0.0.1:8000/(ブラウザを 2 つ開くと複数人のコメントを体験できます)
- 管理画面:http://127.0.0.1:8000/admin(`.env` の ADMIN_TOKEN でログイン)
- API ドキュメント:http://127.0.0.1:8000/docs(オフライン版は `openapi.json`)

LLM のキー(`OPENAI_API_KEY` または `ANTHROPIC_API_KEY`)がなくても全機能が動きます(AI ホストはルール応答で回答)。
音声を出すには [VOICEVOX](https://voicevox.hiroshiba.jp/) を起動し、画面右上の 🔊 を押してください。

### MySQL を使う場合(Docker が必要)
```bash
docker compose up -d db
# .env: DATABASE_URL=mysql+aiomysql://liveuser:livepass@127.0.0.1:3306/livecommerce?charset=utf8mb4
```

## テスト
```bash
pip install -r requirements-dev.txt
pytest                       # 単体テスト(サーバ起動不要・一時 DB を使用)

# E2E(サーバを空の DB で起動してから実行。live.db には絶対に実行しない)
DATABASE_URL=sqlite+aiosqlite:///./test.db ADMIN_TOKEN=test-token TTS_PROVIDER=none LLM_PROVIDER=none \
  uvicorn app.main:app --port 8001 &
E2E_BASE=http://127.0.0.1:8001 python test_e2e.py
```

## ディレクトリ構成
```
app/
├── main.py         # エントリポイント:ルーター登録・WebSocket・起動処理(マイグレーション・初期データ)
├── config.py       # 設定(.env / 環境変数で上書き)
├── database.py     # 非同期エンジン(SQLite / MySQL / PostgreSQL)
├── migrate.py      # 起動時の Alembic マイグレーション
├── models.py       # User / AuthSession / Product / CartItem / Order / OrderItem / Comment
├── queries.py      # 複数箇所で使う問い合わせ(販売中の商品一覧)
├── schemas.py      # Pydantic の入出力モデル
├── ws.py           # WebSocket 接続管理(一斉配信・視聴者数)
├── ai_host.py      # AI ホスト:プロンプト・感情・応答戦略・キュー
├── llm.py          # LLM Provider(Anthropic / OpenAI 互換)
├── host_rules.py   # ルール応答(LLM なし・障害時)
├── tts.py          # 音声合成 Provider(VOICEVOX / なし)
├── bot.py          # 盛り上げ用コメント BOT
├── ratelimit.py    # スライディングウィンドウ方式のレート制限
├── routers/        # auth / products / cart / orders / admin
└── static/         # 視聴者ページ・管理画面・VRM モデル・vendor(three.js 等)
migrations/         # Alembic マイグレーション
tests/              # pytest
docs/design/        # 要件定義・ER 図・シーケンス図・画面遷移図
ios/                # iOS アプリ(SwiftUI)
test_e2e.py         # E2E テスト
```

## 今後の予定
- iOS アプリの 3D アバター表示(現在は絵文字の表情で代替)
- サーバへのデプロイ(Docker + Nginx + HTTPS)

## クレジット
- 音声:VOICEVOX(キャラクターの利用規約に従いクレジットを画面に表示)
- 3D:three.js、@pixiv/three-vrm(MIT)
