import SwiftUI
import UIKit

/// Web 版(app/static/index.html の :root)と同じ色
enum Theme {
    static let ink = Color(red: 0.114, green: 0.114, blue: 0.122)      // #1D1D1F 文字
    static let blue = Color(red: 0.0, green: 0.443, blue: 0.890)       // #0071E3 ボタン
    static let brand = Color(red: 0.518, green: 0.075, blue: 0.247)    // #84133F 見出し
    static let accent = Color(red: 0.749, green: 0.282, blue: 0.0)     // #BF4800 タグ・割引
    static let live = Color(red: 0.890, green: 0.0, blue: 0.0)         // #E30000 LIVE の点
    static let stage = Color(red: 0.961, green: 0.961, blue: 0.969)    // #F5F5F7 背景
    static let line = Color(red: 0.910, green: 0.910, blue: 0.929)     // #E8E8ED 罫線
    static let thumb = Color(red: 0.961, green: 0.961, blue: 0.969)
    /// 画面に出すホストの名前(Web 版と同じ表記)
    static let hostDisplayName = "花奈"
}

/// 商品画像(サーバの image_url を読み込む。無い・失敗した場合は絵文字で代替)
struct ProductThumb: View {
    let product: Product
    let size: CGFloat

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: size * 0.2).fill(Theme.thumb)
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
        .clipShape(RoundedRectangle(cornerRadius: size * 0.2))
        .accessibilityHidden(true)
    }
}

/// ホストの立ち絵(感情ごとの画像を切り替える)
struct HostPortrait: View {
    let emotion: String
    /// true のとき(キーボード表示中):全身を縮めるのではなく、顔のあたりだけを大きめに切り出して見せる
    var compact = false

    var body: some View {
        let image = UIImage(named: "hana_\(emotion)") ?? UIImage(named: "hana_normal")
        Group {
            if let image, compact {
                Color.clear.overlay(alignment: .top) {
                    Image(uiImage: image).resizable().scaledToFit().frame(height: 200)
                }
            } else if let image {
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
