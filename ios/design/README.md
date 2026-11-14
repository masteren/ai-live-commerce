# アプリアイコン

ホスト「花奈」(自作の VRM モデル `app/static/models/hana.vrm`)が顔の横でピースサインをしている絵に、明るい背景と下部の「LIVE」の帯を重ねたもの。

| ファイル | 内容 |
|---|---|
| `pose.mjs` | VRM モデルにポーズ(骨の回転)と表情を付けて、透過 PNG に書き出す |
| `pose_peace.json` | ピースサインのポーズ(骨ごとの回転・表情・カメラ位置) |
| `hana_peace.png` | 上の2つで書き出した絵 |
| `appicon.html` | アイコンの構図(背景・絵の位置・LIVE の帯) |
| `render_icon.mjs` | `appicon.html` を 1024×1024 の PNG に書き出す |
| `variants/` | 表情違い・旧案の見比べ用 |

## 作り直すとき
`playwright-core` が入っているフォルダで実行する(このフォルダには入れていない)。サーバを 8000 番で起動しておく(モデルと three.js をそこから読む)。

```bash
# 1. ポーズを付けた絵を書き出す(ポーズを変えるときは pose_peace.json を編集)
node pose.mjs pose_peace.json hana_peace.png
# 2. アイコンにする。最後の2つは絵の幅(px)と上端の位置(px)
node render_icon.mjs appicon.html ../AILiveCommerce/Assets.xcassets/AppIcon.appiconset/AppIcon.png hana_peace.png 1300 -20
```

## ポーズの付け方のメモ
- 骨の回転は「T ポーズ(両腕を水平に広げた姿勢)」からの角度(ラジアン)。左腕は +X 方向に伸びている。
- 腕を下ろす:`leftUpperArm` の z を負に。ひじを曲げて手を上げる:`leftLowerArm` の z を正に。
- 手のひらをカメラに向ける:`leftHand` の x(腕の軸まわりのひねり)。
- 指を曲げる:各指の z を負に。人差し指と中指を開く:それぞれの y を逆向きに。
- こぶしをしっかり握る:薬指・小指の3つの関節をどれも深く曲げる(-1.5 前後)。親指は3軸とも少しずつ回して、握った指の上にかぶせる。
- 手だけを確認したいときは、`camera` の `dist` を 0.5、`x`/`tx` を 0.21 くらいにして手に寄せる。
- 髪より手前に手を出すには、ひじを外側に(`leftUpperArm` の z を 0 に近づける)。

iOS が角を丸く切り抜くので、画像は四角のまま・透過なしで作る。各サイズは Xcode がビルド時に生成する。
