# 学习笔记 08:iOS 精简版(SwiftUI)

## 1. 做了什么
`ios/` 下的 SwiftUI 应用,**用和 Web 版完全相同的 API**,服务器一行没改。
| 文件 | 作用 | 对应 Web 版 / 后端 |
|---|---|---|
| `APIClient.swift` | REST 调用(async/await)、错误信息解析 | `fetch(...)` |
| `LiveSocket.swift` | WebSocket 收发、断线 2 秒后重连 | `connect()` / `ws.onclose` |
| `Keychain.swift` | 保存登录令牌 | Web 用 HttpOnly Cookie |
| `AppState.swift` | 整个 App 的状态(登录、弹幕、商品、购物车) | index.html 顶部的全局变量 |
| `LiveView.swift` 等 | 画面 | index.html 的 HTML 部分 |
| `Models.swift` | JSON ↔ Swift 结构体 | `schemas.py`(Pydantic)的镜像 |

## 2. 和你学过的内容的对应
- `async/await`、`fetch` → Swift 的 `async/await`、`URLSession.data(for:)`(思路完全一样)
- JS 的对象 ↔ Swift 的 `struct` + `Codable`(JSON 自动转换;`convertFromSnakeCase` 把 `original_price` 变成 `originalPrice`)
- JS-2-16/17 的 LocalStorage、Cookie ↔ iOS 的 UserDefaults(不加密)和 Keychain(加密)

## 3. 设计上的要点(面试会问)
- **为什么 iOS 用 Bearer、不用 Cookie?** 原生 App 没有「别的网站借用 Cookie」的问题(CSRF),在请求头里带令牌更简单直接。服务器两种都支持,共用同一张会话表 → 这就是认证改造时设计成两种方式的原因。
- **为什么令牌放 Keychain?** UserDefaults 是普通的 plist 文件,不加密;Keychain 由系统加密保存。和 Web 版不放 localStorage 是同一个思想。
- **一套 API,多种客户端**:REST + WebSocket 的接口设计没有依赖浏览器,所以 iOS 能直接复用 → 后端设计的好坏在这里体现。
- **@MainActor**:画面的状态只能在主线程修改;WebSocket 的回调在别的线程,所以用 `Task { @MainActor in ... }` 切回主线程。

## 4. 验证情况
- `swiftc` 对 iOS 模拟器 SDK 的类型检查、编译、链接:通过(无警告)
- **还没在模拟器里实际运行**:需要先执行 `sudo xcodebuild -runFirstLaunch`,并下载 iOS 模拟器运行环境

## 5. 自测问题
1. iOS 版登录后,为什么要重新连接 WebSocket?
2. `Codable` 和 `convertFromSnakeCase` 分别做什么?
3. Web 版和 iOS 版的令牌各存在哪里?为什么不同?
4. 服务器怎么区分请求来自浏览器(Cookie)还是 App(Bearer)?
