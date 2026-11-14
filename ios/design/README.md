# アプリアイコン

- `appicon.html`:アイコンの元データ(1024×1024)。ホスト「花奈」の立ち絵(`../AILiveCommerce/Resources/hana_normal.png`、自作の VRM モデルから書き出した画像)に、明るい背景と下部の「LIVE」の帯を重ねている。
- `render_icon.mjs`:上の HTML を PNG に書き出すスクリプト(playwright-core と Chrome が必要)。

```bash
node render_icon.mjs appicon.html ../AILiveCommerce/Assets.xcassets/AppIcon.appiconset/AppIcon.png ../AILiveCommerce/Resources/hana_shy.png 1380 -140
# 最後の2つは、立ち絵の幅(px)と上端の位置(px)。顔の大きさと位置を変えたいときに調整する
```
スクリプトは `playwright-core` が入っているフォルダで実行する(このフォルダには入れていない)。

表情は立ち絵のファイルで選ぶ(`hana_normal` / `hana_happy` / `hana_excited` / `hana_shy`)。現在のアイコンは `hana_shy`(目を開けたほほえみ)。
見比べ用の画像は `variants/compare.png`。

iOS が角を丸く切り抜くので、画像は四角のまま・透過なしで作る。各サイズは Xcode がビルド時に生成する。
