import Foundation

/// 接続先サーバの設定(アカウント画面から変更可能、UserDefaults に保存)
/// - 既定: 公開サーバ(インストールしてすぐ、どのネットワークからでも使える)
/// - シミュレータで開発サーバを使う: http://127.0.0.1:8000
/// - 実機で開発サーバを使う: Mac の LAN IP(例 http://192.168.0.10:8000)を指定し、サーバを --host 0.0.0.0 で起動する
enum AppConfig {
    static let baseURLKey = "apiBaseURL"
    static let defaultBaseURL = "https://live.masteren.cloud"

    /// UI テストから起動されたか。テスト中は iOS のパスワード自動入力(候補の表示・「保存しますか?」の確認)を止める。
    /// これらは OS が出す画面で、テストの操作を横取りして結果が不安定になるため
    static let isUITesting = ProcessInfo.processInfo.arguments.contains("--ui-testing")

    static var baseURL: URL {
        let s = UserDefaults.standard.string(forKey: baseURLKey) ?? defaultBaseURL
        return URL(string: s) ?? URL(string: defaultBaseURL)!
    }

    /// REST の URL から WebSocket の URL を作る(http→ws, https→wss)
    static var webSocketURL: URL {
        var c = URLComponents(url: baseURL, resolvingAgainstBaseURL: false)!
        c.scheme = (c.scheme == "https") ? "wss" : "ws"
        c.path = "/ws/live"
        return c.url!
    }

    /// サーバが返す相対パス(/static/audio/xxx.wav など)を絶対 URL に
    static func absolute(_ path: String) -> URL? {
        URL(string: path, relativeTo: baseURL)?.absoluteURL
    }
}
