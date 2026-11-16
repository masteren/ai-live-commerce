# vendor/(フロントエンド依存ライブラリのローカル配置)

会場のネットワークや CDN が使えなくても 3D アバターが表示されるよう、CDN から固定バージョンを取得して同梱しています。

| ライブラリ | バージョン | 取得元 | ライセンス |
|---|---|---|---|
| three.js(build/three.module.min.js、examples/jsm/loaders/GLTFLoader.js、examples/jsm/utils/BufferGeometryUtils.js) | 0.160.0 | cdn.jsdelivr.net/npm/three@0.160.0 | MIT(three/LICENSE) |
| @pixiv/three-vrm(lib/three-vrm.module.min.js) | 2.1.1 | cdn.jsdelivr.net/npm/@pixiv/three-vrm@2.1.1 | MIT(three-vrm/LICENSE) |

圧縮版(.min.js)を使う(非圧縮版の合計 3.4MB → 0.8MB。読み込みが速い)。
バージョンを上げるときは index.html の importmap と、このファイルを一緒に更新してください。
