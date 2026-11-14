import SwiftUI

/// ライブ画面:ホスト(表情+字幕)・紹介中の商品・コメント欄・入力欄
struct LiveView: View {
    @EnvironmentObject var state: AppState
    @State private var input = ""
    @FocusState private var inputFocused: Bool   // キーボードの表示状態(閉じる操作に使う)

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                hostArea
                // 入力中は商品カードを隠す。小さい画面+背の高いキーボードだと、コメントが1行しか見えなくなるため(実機で確認)
                if let p = state.featured, !inputFocused { featuredCard(p) }
                commentList
                inputBar
            }
            .navigationTitle("HANA")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    // ツールバーの中では幅が詰められるため fixedSize で潰れないようにする
                    HStack(spacing: 6) {
                        Circle().fill(state.connected ? Theme.live : Color.gray).frame(width: 7, height: 7)
                        Text("LIVE").font(.caption.weight(.semibold)).kerning(1.5)
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
                    .accessibilityValue("再生済み \(state.audioPlayedCount)")
                    .accessibilityIdentifier("soundButton")
                }
            }
        }
    }

    private var hostArea: some View {
        VStack(spacing: 0) {
            // Web 版の 3D アバターを表情ごとに書き出した立ち絵(Resources/hana_*.png)
            // キーボード表示中は立ち絵を小さくして、コメント欄の高さを確保する
            HostPortrait(emotion: state.hostEmotion, compact: inputFocused)
                .frame(height: inputFocused ? 104 : 190)
                .clipped()
                .animation(.easeInOut(duration: 0.2), value: inputFocused)
            HStack(alignment: .top, spacing: 0) {
                VStack(alignment: .leading, spacing: 0) {
                    Text(Theme.hostDisplayName).font(.subheadline.weight(.semibold))
                    Text("AIアシスタント").font(.system(size: 9)).foregroundStyle(.secondary)
                }
                .padding(.trailing, 12)
                Divider()
                Text(state.hostText).font(.subheadline).padding(.leading, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(12)
            .background(Color(.systemBackground), in: RoundedRectangle(cornerRadius: 14))
            .shadow(color: .black.opacity(0.06), radius: 10, y: 3)
            .padding(.horizontal).padding(.bottom, 12)
        }
        .background(Theme.stage)
        .contentShape(Rectangle())
        .onTapGesture { inputFocused = false }       // ホストの部分をタップするとキーボードを閉じる
    }

    private func featuredCard(_ p: Product) -> some View {
        HStack {
            ProductThumb(product: p, size: 56)
            VStack(alignment: .leading, spacing: 2) {
                Text(p.name).font(.subheadline.weight(.semibold)).lineLimit(1)
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(yen(p.price)).font(.headline)
                    if let off = offText(p) { Text(off).font(.caption.weight(.semibold)).foregroundStyle(Theme.accent) }
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
            .overlay {
                if state.comments.isEmpty {
                    Text("コメントはまだありません。\n気になることを花奈に質問してみましょう。")
                        .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        .accessibilityIdentifier("emptyComments")
                }
            }
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
            (Text(line.nickname + " ").fontWeight(.semibold).foregroundColor(Theme.brand) + Text(line.text))
                .font(.subheadline)
                .padding(.leading, 10)
                .overlay(alignment: .leading) { Capsule().fill(Theme.brand).frame(width: 2) }
                .accessibilityIdentifier("hostComment")
        case .system:
            Text(line.text).font(.caption).foregroundStyle(.secondary)
                .accessibilityIdentifier("systemComment")
        case .mine, .user:
            (Text(line.nickname + " ").bold().foregroundColor(line.kind == .mine ? Theme.blue : .secondary) + Text(line.text))
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
        Task {
            // 送れなかったとき(切断中)は入力内容を戻す。その間に別の文字を打っていたら上書きしない
            if await !state.sendComment(text), input.isEmpty { input = text }
        }
    }
}
