import SwiftUI

/// ログイン / 新規登録(シートで表示)
struct LoginView: View {
    @EnvironmentObject var state: AppState
    @Environment(\.dismiss) private var dismiss
    @State private var isRegister = false
    @State private var username = ""
    @State private var password = ""
    @State private var error: String?
    @State private var busy = false

    var body: some View {
        NavigationStack {
            Form {
                Picker("", selection: $isRegister) {
                    Text("ログイン").tag(false)
                    Text("新規登録").tag(true)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("authMode")
                Section {
                    TextField("ユーザー名(2〜20文字)", text: $username)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .textContentType(AppConfig.isUITesting ? nil : .username)
                        .accessibilityIdentifier("usernameField")
                    // .newPassword だと「強力なパスワード」の自動入力が割り込み、入力内容が変わることがあるため .password に統一
                    SecureField("パスワード(6文字以上)", text: $password)
                        .textContentType(AppConfig.isUITesting ? nil : .password)
                        .accessibilityIdentifier("passwordField")
                }
                if let error {
                    Text(error).foregroundStyle(.red).font(.footnote)
                        .accessibilityIdentifier("authError")
                }
                Section {
                Button {
                    Task {
                        busy = true
                        error = await state.login(username: username, password: password, register: isRegister)
                        busy = false
                    }
                } label: {
                    if busy { ProgressView().frame(maxWidth: .infinity) }
                    else { Text(isRegister ? "登録して利用を開始" : "ログイン").bold().frame(maxWidth: .infinity) }
                }
                .buttonStyle(.borderedProminent)
                .buttonBorderShape(.capsule)
                .controlSize(.large)
                .disabled(busy || username.isEmpty || password.isEmpty)
                .accessibilityIdentifier("authSubmit")
                } footer: {
                    Text("コメントの投稿とお買い物にはログインが必要です。")
                }
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
            }
            .tint(Theme.blue)
            .navigationTitle(isRegister ? "新規登録" : "ログイン")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("閉じる") { dismiss() } }
            }
        }
    }
}

/// アカウント:注文履歴・ログアウト・接続先サーバの設定
struct AccountView: View {
    @EnvironmentObject var state: AppState
    @AppStorage(AppConfig.baseURLKey) private var baseURL = AppConfig.defaultBaseURL

    var body: some View {
        NavigationStack {
            List {
                Section("アカウント") {
                    if let name = state.username {
                        LabeledContent("ユーザー名", value: name)
                            .accessibilityIdentifier("accountUsername")
                        Button("ログアウト", role: .destructive) { Task { await state.logout() } }
                            .accessibilityIdentifier("logoutButton")
                    } else {
                        Button("ログイン / 新規登録") { state.showLogin = true }
                            .accessibilityIdentifier("openLogin")
                    }
                }
                if state.isLoggedIn {
                    Section("注文履歴") {
                        if state.orders.isEmpty {
                            Text("まだ注文はありません").foregroundStyle(.secondary)
                        }
                        ForEach(state.orders) { o in
                            VStack(alignment: .leading, spacing: 4) {
                                HStack {
                                    Text("#\(o.id)").bold()
                                    Spacer()
                                    Text(yen(o.total))
                                }
                                Text(o.items.map { "\($0.productName)×\($0.quantity)" }.joined(separator: "、"))
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
                Section {
                    TextField("http://127.0.0.1:8000", text: $baseURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    Button("再接続") {
                        Task { await state.switchServer() }
                    }
                } header: {
                    Text("接続先サーバ")
                } footer: {
                    Text("シミュレータは 127.0.0.1、実機は Mac の LAN の IP アドレスを指定します。ログインは接続先ごとに保存されます。")
                }
            }
            .scrollContentBackground(.hidden)
            .background(Theme.stage)
            .navigationTitle("アカウント")
            .navigationBarTitleDisplayMode(.inline)
            .task(id: state.username) { await state.loadOrders() }
            .refreshable { await state.loadOrders() }
        }
    }
}
