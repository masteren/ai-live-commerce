import SwiftUI

/// ライブ画面:ホスト(表情+字幕)・紹介中の商品・コメント欄・入力欄
struct LiveView: View {
    @EnvironmentObject var state: AppState
    @State private var input = ""
    @FocusState private var inputFocused: Bool   // キーボードの表示状態(閉じる操作に使う)

    private let faces = ["normal": "🙂", "happy": "😊", "excited": "🤩", "troubled": "😥", "shy": "😳"]

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                hostArea
                if let p = state.featured { featuredCard(p) }
                commentList
                inputBar
            }
            .navigationTitle("AIライブコマース")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    // ツールバーの中では幅が詰められるため fixedSize で潰れないようにする
                    HStack(spacing: 6) {
                        Text("LIVE").font(.caption.bold()).foregroundStyle(.white)
                            .padding(.horizontal, 6).padding(.vertical, 2)
                            .background(state.connected ? Color.red : Color.gray, in: RoundedRectangle(cornerRadius: 4))
                        Text("視聴者 \(state.viewers)人").font(.caption).monospacedDigit()
                            .accessibilityIdentifier("viewers")
                    }
                    .fixedSize()
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button { state.soundOn.toggle() } label: {
                        Image(systemName: state.soundOn ? "speaker.wave.2.fill" : "speaker.slash")
                    }
                    .accessibilityLabel(state.soundOn ? "音声オン" : "音声オフ")
                }
            }
        }
    }

    private var hostArea: some View {
        VStack(spacing: 8) {
            Text(faces[state.hostEmotion] ?? "🙂")
                .font(.system(size: 72))
                .animation(.spring, value: state.hostEmotion)
            VStack(alignment: .leading, spacing: 2) {
                Text("花奈 AIアシスタント").font(.caption.bold()).foregroundStyle(.secondary)
                Text(state.hostText).font(.subheadline)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(10)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 10))
        }
        .padding()
        .background(LinearGradient(colors: [Color(white: 0.97), Color(red: 0.9, green: 0.93, blue: 0.97)],
                                   startPoint: .top, endPoint: .bottom))
        .contentShape(Rectangle())
        .onTapGesture { inputFocused = false }       // ホストの部分をタップするとキーボードを閉じる
    }

    private func featuredCard(_ p: Product) -> some View {
        HStack {
            Text(p.emoji).font(.largeTitle)
            VStack(alignment: .leading) {
                Text(p.name).font(.subheadline.bold()).lineLimit(1)
                HStack(spacing: 6) {
                    Text(yen(p.price)).font(.headline).foregroundStyle(.red)
                    Text(p.stock > 0 ? "残り\(p.stock)点" : "在庫切れ").font(.caption).foregroundStyle(.secondary)
                }
            }
            Spacer()
            Button("カートへ") { Task { await state.addToCart(p) } }
                .buttonStyle(.borderedProminent)
                .disabled(p.stock <= 0)
                .accessibilityIdentifier("featuredAddToCart")
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(Color(.systemBackground))
    }

    private var commentList: some View {
        ScrollViewReader { proxy in
            List(state.comments) { line in
                commentRow(line).id(line.id).listRowSeparator(.hidden)
            }
            .listStyle(.plain)
            .scrollDismissesKeyboard(.interactively)      // コメント欄をスクロールするとキーボードが閉じる
            .onChange(of: state.comments.count) {
                if let last = state.comments.last { withAnimation { proxy.scrollTo(last.id, anchor: .bottom) } }
            }
        }
    }

    @ViewBuilder
    private func commentRow(_ line: ChatLine) -> some View {
        switch line.kind {
        case .host:
            (Text(line.nickname + " ").bold().foregroundColor(.pink) + Text(line.text))
                .font(.subheadline)
                .padding(8)
                .background(Color.pink.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityIdentifier("hostComment")
        case .system:
            Text(line.text).font(.caption).foregroundStyle(.orange)
                .accessibilityIdentifier("systemComment")
        case .mine, .user:
            (Text(line.nickname + " ").bold().foregroundColor(line.kind == .mine ? .green : .secondary) + Text(line.text))
                .font(.subheadline)
        }
    }

    private var inputBar: some View {
        HStack {
            TextField("質問・コメントを入力", text: $input)
                .textFieldStyle(.roundedBorder)
                .submitLabel(.send)
                .onSubmit(send)
                .focused($inputFocused)
                .accessibilityIdentifier("commentField")
            Button("送信", action: send)
                .buttonStyle(.borderedProminent)
                .disabled(input.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityIdentifier("sendButton")
        }
        .padding()
        .background(.bar)
    }

    private func send() {
        let text = input
        input = ""
        Task { await state.sendComment(text) }
    }
}
