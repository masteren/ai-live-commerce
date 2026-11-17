import SwiftUI
import WebKit

/// Web 版と同じ 3D アバター(サーバの /stage)をアプリの中に表示する
/// - ページは WebSocket に接続しない(視聴者数に数えない)。表情と口の動きは、アプリが回答を受け取ったときに
///   JavaScript の window.HANA を呼んで伝える
/// - 3D の準備ができるまで(約 14MB のモデルの読み込み)と、読み込めなかったときは、これまでどおり立ち絵を見せる
@MainActor
final class HostStageController: NSObject, ObservableObject {
    @Published private(set) var ready = false
    let webView: WKWebView
    private var loadedURL: URL?

    /// サーバから届く感情のうち、3D に渡してよいもの(それ以外は normal。JavaScript に文字列をそのまま埋め込まないため)
    private static let emotions: Set<String> = ["normal", "happy", "excited", "troubled", "shy"]

    override init() {
        let config = WKWebViewConfiguration()
        config.userContentController = WKUserContentController()
        webView = WKWebView(frame: .zero, configuration: config)
        super.init()
        // WKUserContentController はハンドラを強く持つので、間に弱参照の箱をはさむ(循環参照で解放されなくなるのを防ぐ)
        config.userContentController.add(WeakMessageHandler(self), name: "hana")
        webView.isOpaque = false                      // 背景を透明にして、アプリのグラデーションを見せる
        webView.backgroundColor = .clear
        webView.scrollView.backgroundColor = .clear
        webView.scrollView.isScrollEnabled = false
    }

    /// 接続先のサーバの /stage を読み込む(接続先が変わったときだけ読み込み直す)
    func loadIfNeeded() {
        let url = AppConfig.baseURL.appendingPathComponent("stage")
        guard url != loadedURL else { return }
        loadedURL = url
        ready = false
        webView.load(URLRequest(url: url))
    }

    /// 花奈の回答に合わせて、表情を変え、文章の長さだけ口を動かす(Web 版で音声がないときと同じ計算)
    func speak(emotion: String, text: String) {
        guard ready else { return }
        let emo = Self.emotions.contains(emotion) ? emotion : "normal"
        let ms = min(text.count * 150, 10_000)
        webView.evaluateJavaScript("HANA.setEmotion('\(emo)'); HANA.talkFor(\(ms));")
    }

    fileprivate func received(_ body: Any) {
        if body as? String == "ready" { ready = true }
    }
}

/// stage.js からの知らせ(3D の準備ができた)を受け取る
private final class WeakMessageHandler: NSObject, WKScriptMessageHandler {
    weak var target: HostStageController?
    init(_ target: HostStageController) { self.target = target }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        let body = message.body
        Task { @MainActor in self.target?.received(body) }
    }
}

/// HostStageController の WKWebView を SwiftUI に置く
struct HostStageView: UIViewRepresentable {
    let controller: HostStageController
    func makeUIView(context: Context) -> WKWebView { controller.webView }
    func updateUIView(_ view: WKWebView, context: Context) {}
}
