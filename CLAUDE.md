# AI Live Commerce — Claude Code 用プロジェクトメモ

別の PC(Windows など)で開いたときも、ここを読めば経緯と決まりごとが分かるようにしている。

## プロジェクトについて
- 学校の「就職作品プレゼンテーション」(2027年2月18日 or 19日)に出す就職作品。作者の志望はフルスタック/バックエンド。
- 作者は「先に動くものを作り、あとから理解する」方針。変更のたびに `docs/learning/NN-*.md`(中国語の学習ノート:何を変えたか・なぜか・自己チェック問題)を書く。
- **機能追加は一区切り。** これからの優先は ①中核機能の実動確認(Claude API の実キーでの応答、VOICEVOX の音声)②作者自身の理解(学習ノート 01〜10)。新機能を足す前に、本当に必要か確認すること。

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
- 「読んでから書く」処理を見つけたら同時実行を疑う(これまでに売り越し・二重注文・カート重複の3件を修正)。再現テストを書いてから直す。

## 確認コマンド
```bash
pytest                                   # 単体テスト(約2秒)
# E2E:別ポート・空の SQLite でサーバを起動してから
DATABASE_URL=sqlite+aiosqlite:///./test.db ADMIN_TOKEN=test-token VOICEVOX_ENABLED=false LLM_PROVIDER=none uvicorn app.main:app --port 8001
E2E_BASE=http://127.0.0.1:8001 python test_e2e.py
# iOS(Mac のみ、サーバを 8000 で起動してから)
xcodebuild test -project ios/AILiveCommerce.xcodeproj -scheme AILiveCommerce -destination 'platform=iOS Simulator,name=iPhone 18 Pro'
```

## 未着手・保留
- (済)OpenAI の実キーでホストの応答を確認(2026-11-13、gpt-4o-mini)。Anthropic 側は実キー未確認。感情が normal に偏る点は要調整
- VOICEVOX を起動しての音声・口パクの確認
- サーバへのデプロイ(Docker + Nginx + HTTPS)は保留中
- プロンプトインジェクション対策、管理 API の試行回数制限
