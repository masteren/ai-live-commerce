import SwiftUI
import UIKit

/// Web 版と揃えた配色(ほぼ無彩色 + 朱のアクセントは LIVE と割引だけ)
enum Theme {
    static let ink = Color(red: 0.086, green: 0.086, blue: 0.086)
    static let accent = Color(red: 0.659, green: 0.216, blue: 0.169)
    static let stage = Color(red: 0.929, green: 0.922, blue: 0.902)
    static let line = Color(red: 0.902, green: 0.890, blue: 0.867)
    static let thumb = Color(red: 0.953, green: 0.945, blue: 0.929)
}

/// 商品画像(サーバの image_url を読み込む。無い・失敗した場合は絵文字で代替)
struct ProductThumb: View {
    let product: Product
    let size: CGFloat

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 4).fill(Theme.thumb)
            if let path = product.imageUrl, !path.isEmpty, let url = AppConfig.absolute(path) {
                AsyncImage(url: url) { phase in
                    if let image = phase.image {
                        image.resizable().scaledToFit()
                    } else if phase.error != nil {
                        Text(product.emoji).font(.system(size: size * 0.45))
                    } else {
                        ProgressView().controlSize(.small)
                    }
                }
            } else {
                Text(product.emoji).font(.system(size: size * 0.45))
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .accessibilityHidden(true)
    }
}

/// ホストの立ち絵(感情ごとの画像を切り替える)
struct HostPortrait: View {
    let emotion: String

    var body: some View {
        let image = UIImage(named: "hana_\(emotion)") ?? UIImage(named: "hana_normal")
        Group {
            if let image {
                Image(uiImage: image).resizable().scaledToFit()   // 枠の高さに収める(はみ出さない)
            } else {
                Image(systemName: "person.fill").resizable().scaledToFit().foregroundStyle(.secondary).padding(40)
            }
        }
        .frame(maxWidth: .infinity)
        .clipped()
        .animation(.easeInOut(duration: 0.2), value: emotion)
        .accessibilityLabel("AIアシスタント 花奈")
    }
}
