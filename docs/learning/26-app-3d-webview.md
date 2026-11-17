# 学习笔记 26:App 里也显示会动的 3D 花奈(WKWebView 嵌入)

## 做了什么
- 以前 App 的花奈是**静态立绘**(按表情换图),网页版是 3D,会跳舞、挥手。
- 现在 App 的画面区域用 **WKWebView** 打开服务器的 `/stage`(只有 3D 画面的页面),**直接复用网页版的 `vrm.js`**。3D 准备好之前、或者加载失败时,还是显示立绘。
- App 收到花奈的回复 → 调 JS `HANA.setEmotion('happy'); HANA.talkFor(毫秒)` → 3D 换表情、动嘴。

改动:`app/static/stage.html` / `stage.css` / `js/stage.js`(新)、`vrm.js`(准备好时发 `hana-ready`)、`security.py`(给 /stage 加 CSP)、iOS `HostStage.swift`(新)・`LiveView.swift`・`AppState.swift`。

## 为什么选「嵌入网页」而不是在 Swift 里重写 3D
| | WKWebView 嵌入 | Swift 原生(SceneKit/RealityKit) |
|---|---|---|
| VRM 支持 | 现成(three-vrm) | 要自己写 VRM 读取、表情、骨骼 |
| 动作 | 网页改了 App 自动跟着变 | 两边各写一份 |
| 缺点 | 首次下载约 14MB、WebGL 比原生重 | 工作量大 |
→ 「同じ処理を2か所に書かない」。

> 面接用:「3D の描画は Web 版のコードを WKWebView で再利用し、アプリからは JavaScript を呼んで表情と口の動きを指示しています。同じ処理を二重に実装しないためです。」

## 几个细节
1. **这个页面不连 WebSocket**:如果 3D 页面自己也连直播,一个人会被算成 2 个观众。所以由 App 把事件「转告」给页面。
2. **不把服务器的字符串直接拼进 JS**:表情名只接受 5 种已知值,其余当 normal。拼接未检查的字符串 = 注入风险(和 SQL 注入同理)。
3. **何时切换成 3D**:一开始是「模型下载完」就切,结果模拟器上空白了 15 秒——第一次渲染要编译着色器(シェーダー)。改成「实际画出 3 帧后」才通知 App。
4. **隐藏时 opacity 用 0.01 不用 0**:完全透明时 WebView 可能停止渲染,一切换就要重新开始。
5. **WKUserContentController 会强引用 handler** → 中间夹一个弱引用的小类,防止循环引用导致内存不释放(循環参照 / retain cycle)。
6. **UI 测试时不用 3D**:模型下载和 WebGL 会让测试变慢、不稳定。

## 自我检查
1. 为什么 /stage 页面不自己连接 WebSocket?
2. `HANA.setEmotion('\(emotion)')` 如果 emotion 直接用服务器传来的值,可能出什么问题?
3. 「模型下载完就切换」为什么会出现空白画面?
4. `WeakMessageHandler` 去掉、直接 `add(self, ...)` 会怎样?
