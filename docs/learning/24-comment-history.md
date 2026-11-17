# 学习笔记 24:进直播间先看到历史弹幕

## 做了什么
- 以前:评论虽然存进了数据库(`comments` 表),但打开页面时评论区是空的,只能看到「之后」的弹幕。
- 现在:WebSocket 连接建立后,服务器**第一条**发 `{"type":"history","items":[...]}`(最近 30 条,旧的在前,`kind` 是 user / host / bot)。网页和 iOS App 收到后重建评论列表。
- 改动:`app/queries.py` 的 `recent_comments()`、`app/ws.py` 的 `connect(ws, first=...)`、`app/main.py`、`live.js`、iOS 的 `Models.swift` / `AppState.swift`。
- 测试:`tests/test_history.py`(条数、顺序、kind),E2E「新连接先收到历史弹幕」。

## 设计上的几个点

### 1. 为什么先发历史、再加入广播名单
`connect()` 里的顺序是:`accept()` → **只给这个连接发历史** → 加入 `connections`(之后才收得到广播)。
如果反过来(先加入名单再发历史),在这之间有人发言,就可能出现:
- 新弹幕比历史先到(顺序乱)
- 同一条既在历史里、又被广播一次(重复)

这就是 CLAUDE.md 里说的「读了再写的处理要怀疑并发」的同类问题。

### 2. 客户端「重建」而不是「追加」
网络断了会自动重连,每次重连都会再收到一次历史。如果是追加,就会出现两份。所以收到 `history` 时先清空(`replaceChildren()` / `comments = ...`)。

### 3. 网页:先确认登录状态,再连接
历史里自己的评论要显示成蓝色气泡(`nickname === username`)。原来 `checkSession()` 和 `connect()` 同时开始,历史可能先到 → 那时还不知道自己是谁。改成 `checkSession()` 结束后再 `connect()`。

### 4. 旧版 App 不受影响
iOS 的 `LiveEvent.decode` 遇到不认识的 `type` 返回 `nil`(忽略)。所以服务器先上线,旧版 App 也不会出错——这叫**後方互換性**(こうほうごかんせい / backward compatibility)。

> 面接用:「新しいメッセージ種別を追加しても、古いクライアントは知らない種別を無視する設計なので、後方互換性を保てます。」

## 自我检查
1. 为什么 `history` 用 `ws.send_text` 只发给一个人,而不是 `manager.broadcast`?
2. 如果先 `connections.add(ws)` 再发历史,举一个会出现重复弹幕的具体时序。
3. iOS 里 `items` 这个键在 `purchase` 和 `history` 里形状不同,`decode` 是怎么分开处理的?
4. 网页刷新后,自己以前发的评论为什么能显示成蓝色?登录之前看会怎样?
