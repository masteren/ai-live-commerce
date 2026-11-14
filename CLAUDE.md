# AI Live Commerce — Claude Code 用プロジェクトメモ

別の PC(Windows など)で開いたときも、ここを読めば経緯と決まりごとが分かるようにしている。

## プロジェクトについて
- 学校の「就職作品プレゼンテーション」(2027年2月18日 or 19日)に出す就職作品。作者の志望はフルスタック/バックエンド。
- 作者は「先に動くものを作り、あとから理解する」方針。変更のたびに `docs/learning/NN-*.md`(中国語の学習ノート:何を変えたか・なぜか・自己チェック問題)を書く。
- **機能追加は一区切り。** これからの優先は ①中核機能の実動確認(Claude API の実キーでの応答、VOICEVOX の音声)②作者自身の理解(学習ノート 01〜18)。新機能を足す前に、本当に必要か確認すること。

## 会話と文書の言語
- 作者への返答・学習ノート:中国語(簡体字)
- README・設計資料(`docs/design/`)・コミットメッセージ・コード内コメント(新規分):日本語

## 開発の決まりごと
- Python 3.12 以上。`venv/` を使う(`uv venv venv --python 3.12 --seed` → `pip install -r requirements-dev.txt`)。Windows では `venv\Scripts\activate`。
- `.env` は Git に入れない(`.env.example` をコピーして作る)。キーやパスワードの値を出力・コミットしない。
- 開発 DB は MySQL(`docker compose up -d db`)。単体テストは一時 SQLite。**テストを開発 DB に対して実行しない**(テーブルを作り直すため)。MySQL でテストするときは別のデータベース(例 `livecommerce_test`)を `TEST_DATABASE_URL` で指定。
- モデル(`app/models.py`)を変えたら Alembic のマイグレーションを作る。既存データがある前提で書く(NOT NULL 列には server_default、制約追加の前にデータを整える)。
- サーバは単一プロセスで動かす(WebSocket 接続・AI キュー・レート制限がメモリ上にある)。
- 変更したら `pytest` → 必要に応じて E2E(`test_e2e.py`、空の DB で起動したサーバに対して)→ コミット。push は作者の了承を得てから(GitHub は private)。
- 同じ処理を2か所に書かない。販売中の商品の取得は `app/queries.py`、クライアント IP は `ratelimit.client_ip`、画面の割引率は `index.html` の `offRate()` を使う。
- パスワードのハッシュ計算は重い(60万回)ので `asyncio.to_thread` で呼ぶ。テストでは `PASSWORD_ITERATIONS=1000`(`tests/conftest.py`)。
- 「読んでから書く」処理を見つけたら同時実行を疑う(これまでに売り越し・二重注文・カート重複の3件を修正)。再現テストを書いてから直す。

## 確認コマンド
```bash
pytest                                   # 単体テスト(約2秒)
# E2E:別ポート・空の SQLite でサーバを起動してから
DATABASE_URL=sqlite+aiosqlite:///./test.db ADMIN_TOKEN=test-token TTS_PROVIDER=none LLM_PROVIDER=none uvicorn app.main:app --port 8001
E2E_BASE=http://127.0.0.1:8001 python test_e2e.py
# iOS(Mac のみ)。テスト用サーバを別ポート(例 8011、空の SQLite・LLM_PROVIDER=none)で起動してから。
# 開発サーバ(8000)に向けると、開いたままのブラウザが視聴者数に数えられて失敗し、LLM も呼ばれる
TEST_RUNNER_API_BASE=http://127.0.0.1:8011 xcodebuild test -project ios/AILiveCommerce.xcodeproj -scheme AILiveCommerce -destination 'platform=iOS Simulator,name=iPhone 18 Pro'
```

## 未着手・保留
- (済)iOS 実機(iPhone 15)で確認:主要フロー・バックグラウンド復帰・音声(消音スイッチがオンでも再生)。実機での UI テストは `-destination 'id=<端末ID>'`、サーバは `--host 0.0.0.0`、接続先は Mac の LAN の IP。無料アカウントの署名は 7 日で切れる(Xcode から再インストール)
- (済)OpenAI の実キー(gpt-4o-mini)でホストの応答を確認。作り話・数字の誤り・感情の偏りを、プロンプト修正とコード側の検証(`facts_are_consistent` など)で対処。Anthropic 側は実キー未確認
- LLM を実際に呼ぶ確認は作者の了承を得てから(費用が発生する)
- VOICEVOX を起動しての音声・口パクの確認
- サーバへのデプロイ(Docker + Nginx + HTTPS)は保留中
- (済)プロンプトインジェクション対策(コメントを引用として渡す)、管理 API・新規登録の試行回数制限、PBKDF2 60万回。実 LLM(gpt-4o-mini)で確認済み(2026-11-14:指示の上書き5種類をすべて拒否、通常の質問5件も正常)
- Nginx の後ろに置くときは uvicorn に `--proxy-headers`(付けないとレート制限が全員共通の IP になる)
