import SwiftUI

/// 商品一覧:質問する(ライブにコメント)/ カートへ
struct ProductsView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        NavigationStack {
            List(state.products) { p in
                HStack(alignment: .top, spacing: 12) {
                    Text(p.emoji).font(.largeTitle)
                    VStack(alignment: .leading, spacing: 4) {
                        if !p.tag.isEmpty {
                            Text(p.tag).font(.caption2.bold()).foregroundStyle(.red)
                        }
                        Text(p.name).font(.headline)
                        Text(p.description).font(.caption).foregroundStyle(.secondary)
                        HStack {
                            Text(yen(p.price)).font(.headline).foregroundStyle(.red)
                            if p.originalPrice > p.price {
                                Text(yen(p.originalPrice)).font(.caption).strikethrough().foregroundStyle(.secondary)
                            }
                            Text("残り\(p.stock)点").font(.caption).foregroundStyle(.secondary)
                        }
                        HStack {
                            Button("質問する") { Task { await state.ask(about: p) } }
                                .buttonStyle(.bordered)
                            Button("カートへ") { Task { await state.addToCart(p) } }
                                .buttonStyle(.borderedProminent)
                                .disabled(p.stock <= 0)
                        }
                        .font(.caption)
                    }
                }
                .padding(.vertical, 4)
            }
            .navigationTitle("本日の商品")
            .refreshable { await state.loadProducts() }
        }
    }
}

/// カート → 支払方法の選択 → 注文確定(決済はモック)
struct CartView: View {
    @EnvironmentObject var state: AppState
    @State private var method: PaymentMethod = .creditCard
    @State private var processing = false
    @State private var resultMessage: String?

    var body: some View {
        NavigationStack {
            Group {
                if !state.isLoggedIn {
                    ContentUnavailableView {
                        Label("ログインが必要です", systemImage: "person.crop.circle.badge.exclamationmark")
                    } actions: {
                        Button("ログイン") { state.showLogin = true }.buttonStyle(.borderedProminent)
                    }
                } else if state.cart.isEmpty {
                    ContentUnavailableView("カートは空です", systemImage: "cart")
                } else {
                    cartList
                }
            }
            .navigationTitle("カート")
            .task { await state.refreshCart() }
            .alert("お知らせ", isPresented: Binding(get: { resultMessage != nil }, set: { if !$0 { resultMessage = nil } })) {
                Button("OK") {}
            } message: {
                Text(resultMessage ?? "")
            }
        }
    }

    private var cartList: some View {
        List {
            Section {
                ForEach(state.cart) { item in
                    HStack {
                        Text(item.product.emoji)
                        Text(item.product.name).lineLimit(1)
                        Spacer()
                        Text("×\(item.quantity)").foregroundStyle(.secondary)
                        Text(yen(item.product.price * item.quantity))
                    }
                }
                .onDelete { idx in
                    let items = idx.map { state.cart[$0] }
                    Task { for i in items { await state.removeFromCart(i) } }
                }
            } footer: {
                Text("左にスワイプで削除")
            }
            Section("お支払い方法") {
                Picker("お支払い方法", selection: $method) {
                    ForEach(PaymentMethod.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                Text("※デモのため実際の決済は行われません").font(.caption).foregroundStyle(.secondary)
            }
            Section {
                HStack {
                    Text("合計(送料無料)")
                    Spacer()
                    Text(yen(state.cartTotal)).font(.title3.bold()).foregroundStyle(.red)
                }
                Button {
                    Task { await placeOrder() }
                } label: {
                    if processing { ProgressView().frame(maxWidth: .infinity) }
                    else { Text("注文を確定する").bold().frame(maxWidth: .infinity) }
                }
                .buttonStyle(.borderedProminent)
                .disabled(processing)
                .accessibilityIdentifier("checkoutButton")
            }
        }
    }

    private func placeOrder() async {
        processing = true
        defer { processing = false }
        switch await state.checkout(method: method) {
        case .success(let order):
            resultMessage = "ご注文ありがとうございます\n注文番号 #\(order.id) / \(yen(order.total))"
        case .failure(let e):
            resultMessage = "決済に失敗しました\n\(e.message)"
        }
    }
}
