import AVFoundation
import Foundation

/// アプリ全体の状態(画面はこれを監視して自動で再描画される)
@MainActor
final class AppState: ObservableObject {
    enum Tab: Hashable { case live, products, cart, account }

    // 認証
    @Published var username: String?
    @Published var showLogin = false
    // ライブ
    @Published var connected = false
    @Published var viewers = 0
    @Published var comments: [ChatLine] = []
    @Published var hostText = "ご視聴ありがとうございます。ご質問はコメントでお気軽にどうぞ。"
    @Published var hostEmotion = "normal"
    @Published var featuredId: Int?
    @Published var soundOn = false
    // EC
    @Published var products: [Product] = []
    @Published var cart: [CartItem] = []
    @Published var orders: [Order] = []
    // UI
    @Published var tab: Tab = .live
    @Published var toast: String?

    private let api = APIClient.shared
    private let socket = LiveSocket()
    private var player: AVPlayer?
    private var pendingComment: String?

    var isLoggedIn: Bool { username != nil }
    var cartCount: Int { cart.reduce(0) { $0 + $1.quantity } }
    var cartTotal: Int { cart.reduce(0) { $0 + $1.quantity * $1.product.price } }
    var featured: Product? { products.first { $0.id == featuredId } ?? products.first }

    init() {
        socket.onEvent = { [weak self] in self?.handle($0) }
        socket.onStatus = { [weak self] in self?.connected = $0 }
    }

    // MARK: 起動
    func bootstrap() async {
        // UI テストは毎回「未ログイン」から始めるため、起動引数でトークンを消せるようにする
        if ProcessInfo.processInfo.arguments.contains("--reset-session") { Keychain.delete() }
        if Keychain.load() != nil {
            // 保存済みトークンが有効か確認(期限切れなら破棄)
            if let me = try? await api.me() { username = me.username } else { Keychain.delete() }
        }
        await loadProducts()
        await refreshCart()
        socket.connect()
    }

    func reconnect() {
        socket.connect()
    }

    // MARK: 認証
    func login(username: String, password: String, register: Bool) async -> String? {
        do {
            let res = register ? try await api.register(username: username, password: password)
                               : try await api.login(username: username, password: password)
            self.username = res.username
            showLogin = false
            showToast("ようこそ、\(res.username)さん")
            socket.connect()                      // 新しいトークンで張り直す
            await refreshCart()
            if let c = pendingComment {
                pendingComment = nil
                await waitUntilConnected()        // 張り直した接続が確立する前に送ると、コメントが失われることがある
                await sendComment(c)
            }
            return nil
        } catch {
            return error.localizedDescription
        }
    }

    /// WebSocket の再接続が完了する(サーバから最初のメッセージが届く)まで最大 3 秒待つ
    private func waitUntilConnected() async {
        for _ in 0..<30 where !connected {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    func logout() async {
        await api.logout()
        username = nil
        cart = []
        orders = []
        socket.connect()
    }

    // MARK: ライブ
    func sendComment(_ raw: String) async {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        guard isLoggedIn else { pendingComment = text; showLogin = true; return }
        do { try await socket.sendComment(text) } catch { showToast("送信できませんでした") }
    }

    func ask(about product: Product) async {
        featuredId = product.id
        tab = .live
        await sendComment("\(product.name)について教えてください")
    }

    private func handle(_ ev: LiveEvent) {
        switch ev {
        case .viewers(let n):
            viewers = n
        case .comment(let nick, let text):
            append(ChatLine(nickname: nick, text: text, kind: nick == username ? .mine : .user))
        case .hostReply(let r):
            append(ChatLine(nickname: r.nickname, text: r.text, kind: .host))
            hostText = r.text
            hostEmotion = r.emotion
            if let pid = r.productId { featuredId = pid }
            if soundOn, let path = r.audioUrl, let url = AppConfig.absolute(path) {
                player = AVPlayer(url: url)
                player?.play()
            }
        case .purchase(let nick, let items, let stocks):
            let desc = items.map { "\($0.name)×\($0.quantity)" }.joined(separator: "、")
            append(ChatLine(nickname: "お知らせ", text: "\(nick)さんが \(desc) を購入しました", kind: .system))
            for i in products.indices {
                if let s = stocks[String(products[i].id)] { products[i].stock = s }
            }
        case .productsChanged:
            Task { await loadProducts() }
        case .error(let msg):
            showToast(msg)
            if !isLoggedIn { showLogin = true }
        }
    }

    private func append(_ line: ChatLine) {
        comments.append(line)
        if comments.count > 100 { comments.removeFirst(comments.count - 100) }
    }

    // MARK: EC
    func loadProducts() async {
        if let p = try? await api.products() { products = p }
    }

    func refreshCart() async {
        guard isLoggedIn else { cart = []; return }
        cart = (try? await api.cart()) ?? []
    }

    func addToCart(_ product: Product) async {
        guard isLoggedIn else { showLogin = true; return }
        do {
            cart = try await api.addToCart(productId: product.id)
            showToast("カートに追加しました")
        } catch {
            showToast(error.localizedDescription)
        }
    }

    func removeFromCart(_ item: CartItem) async {
        try? await api.removeCartItem(id: item.id)
        await refreshCart()
    }

    /// 注文を確定。成功なら注文、失敗ならエラーメッセージを返す
    func checkout(method: PaymentMethod) async -> Result<Order, APIError> {
        do {
            let order = try await api.checkout(method: method)
            await refreshCart()
            return .success(order)
        } catch let e as APIError {
            await refreshCart()
            return .failure(e)
        } catch {
            return .failure(APIError(status: 0, message: error.localizedDescription))
        }
    }

    func loadOrders() async {
        guard isLoggedIn else { return }
        orders = (try? await api.orders()) ?? []
    }

    // MARK: UI
    func showToast(_ msg: String) {
        toast = msg
        Task {
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            if toast == msg { toast = nil }
        }
    }
}

/// 割引率の表示("38% OFF")。通常価格より安いときだけ
func offText(_ p: Product) -> String? {
    guard p.originalPrice > p.price, p.originalPrice > 0 else { return nil }
    return "\(Int((1 - Double(p.price) / Double(p.originalPrice)) * 100 + 0.5))% OFF"
}

/// 金額表示 ¥4,980
func yen(_ n: Int) -> String {
    let f = NumberFormatter()
    f.numberStyle = .decimal
    return "¥" + (f.string(from: NSNumber(value: n)) ?? "\(n)")
}
