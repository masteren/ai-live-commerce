import SwiftUI

@main
struct AILiveCommerceApp: App {
    @StateObject private var state = AppState()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(state)
        }
    }
}

struct ContentView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        TabView(selection: $state.tab) {
            LiveView()
                .tabItem { Label("ライブ", systemImage: "dot.radiowaves.left.and.right") }
                .tag(AppState.Tab.live)
            ProductsView()
                .tabItem { Label("商品", systemImage: "bag") }
                .tag(AppState.Tab.products)
            CartView()
                .tabItem { Label("カート", systemImage: "cart") }
                .badge(state.cartCount)
                .tag(AppState.Tab.cart)
            AccountView()
                .tabItem { Label("アカウント", systemImage: "person.crop.circle") }
                .tag(AppState.Tab.account)
        }
        .tint(Theme.ink)
        .sheet(isPresented: $state.showLogin) { LoginView() }
        .overlay(alignment: .top) {
            if let t = state.toast {
                Text(t)
                    .font(.subheadline.bold())
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16).padding(.vertical, 10)
                    .background(.black.opacity(0.8), in: Capsule())
                    .padding(.top, 8)
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.easeInOut, value: state.toast)
        .task { await state.bootstrap() }
    }
}
