# 学习笔记 04:演示稳定性(依赖本地化 + 署名显示)

## 1. 改了什么
- three.js / GLTFLoader / three-vrm 从 CDN 改为放在 `app/static/vendor/`(固定版本,MIT,见 vendor/README.md)
  → 展会会场断网、CDN 被墙或变慢时,3D 虚拟主播照样能显示
- 新增 `GET /api/meta`:返回音声署名(例 `VOICEVOX:ずんだもん`)和回答生成方式,页脚自动显示
  → 换声线时只改 `.env` 的 `TTS_CREDIT`;TTS 关闭时显示「なし(文字のみ)」
- 测试:检查 importmap 里不再有 CDN 地址、引用的本地文件都存在

## 2. 对应的知识
- importmap / ES Module:Node-02 的 ESM(`import ... from`)在浏览器里的用法;JS-2-15 的 Three.js 就是这样加载的
- 静态文件:Node-06 `express.static` ↔ FastAPI `app.mount("/static", StaticFiles(...))`

## 3. 取舍(面试会问)
- **CDN vs 本地**:CDN 有缓存、减轻服务器负担;本地不依赖外部、版本可控。演示场景「能不能显示」比「快一点」重要 → 本地。
- **为什么不用 npm + 打包工具(Vite 等)?** 项目刻意保持「零构建」(直接写 HTML/JS),部署简单。规模变大后再考虑。

## 4. 还需要你确认的
- ~~`app/static/models/hana.vrm` 的使用条件~~ → 已确认没有问题(2026-11-13)。
  面试被问到时的要点:VRoid Hub 等发布的模型各自有利用条件(是否允许商用・改变・二次配布),使用前要确认 —— 这和使用 OSS 时确认 MIT 等许可证是同一件事。
- VOICEVOX 各角色的利用规约(署名格式等)请以官网为准。

## 5. 自测问题
1. importmap 是做什么的?
2. 为什么 BufferGeometryUtils.js 也要下载?(提示:GLTFLoader 内部 import 了它)
3. 署名文字为什么由服务器返回,而不是写死在 HTML 里?
