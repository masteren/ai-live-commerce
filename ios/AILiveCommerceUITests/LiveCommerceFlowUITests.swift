import XCTest

/// iOS アプリの主要フローを実際にタップ・入力して確認する UI テスト
/// 前提:Mac 上でサーバ(http://127.0.0.1:8000)が起動していること
///   未ログインでコメント → ログインシート → 新規登録 → コメント送信・AI 回答
///   → カート追加 → 注文 → 購入告知 → 注文履歴 → ログアウト
final class LiveCommerceFlowUITests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["--reset-session"]   // 前回のテストのログイン状態(Keychain)を消して開始
        app.launch()
    }

    /// スクリーンショットを結果に添付(環境変数 SHOTS_DIR があれば PNG も保存)
    private func snap(_ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let attachment = XCTAttachment(screenshot: shot)
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
        if let dir = ProcessInfo.processInfo.environment["SHOTS_DIR"] {
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent("\(name).png"))
        }
    }

    private func text(containing s: String) -> XCUIElement {
        app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", s)).firstMatch
    }

    /// タブを切り替える(バッジが付くとラベルが「カート, 1件」のように変わることがあるので前方一致で探す)
    private func tab(_ name: String) {
        let pred = NSPredicate(format: "label == %@ OR label BEGINSWITH %@", name, name + ",")
        let inTabBar = app.tabBars.buttons.matching(pred).firstMatch
        if inTabBar.waitForExistence(timeout: 3) { inTabBar.tap(); return }
        app.buttons.matching(pred).firstMatch.tap()
    }

    /// 失敗調査用:画面の要素ツリーを SHOTS_DIR に保存
    private func dumpTree(_ name: String) {
        if let dir = ProcessInfo.processInfo.environment["SHOTS_DIR"] {
            try? app.debugDescription.write(toFile: dir + "/\(name).txt", atomically: true, encoding: .utf8)
        }
    }

    func testRegisterCommentAndPurchaseFlow() throws {
        let user = "ios_\(Int(Date().timeIntervalSince1970) % 1_000_000)"
        let question = "送料はいくらですか?"

        // 1. ライブ画面:WebSocket で視聴者数が届く
        let viewers = app.staticTexts["viewers"]
        XCTAssertTrue(viewers.waitForExistence(timeout: 15))
        XCTAssertTrue(app.wait(for: viewers, labelContains: "1人", timeout: 10), "視聴者数: \(viewers.label)")
        snap("01_live")

        // 2. 未ログインでコメント → ログインシートが開く
        let field = app.textFields["commentField"]
        field.tap()
        field.typeText(question)
        app.buttons["sendButton"].tap()
        let username = app.textFields["usernameField"]
        XCTAssertTrue(username.waitForExistence(timeout: 5), "ログインシートが開かない")

        // 3. 新規登録
        app.buttons["新規登録"].firstMatch.tap()
        username.tap()
        username.typeText(user)
        let password = app.secureTextFields["passwordField"]
        password.tap()
        password.typeText("pass1234")
        snap("02_register")
        app.buttons["authSubmit"].tap()
        XCTAssertTrue(app.textFields["usernameField"].waitForNonExistence(timeout: 10), "登録に失敗(シートが閉じない)")

        // 4. ログイン後、保留していたコメントが送信され、AI ホストが回答する
        if !text(containing: question).waitForExistence(timeout: 10) { snap("zz_comment_debug"); dumpTree("zz_comment_tree") }
        XCTAssertTrue(text(containing: question).exists, "自分のコメントが表示されない")
        XCTAssertTrue(app.descendants(matching: .any)["hostComment"].firstMatch.waitForExistence(timeout: 20), "ホストの回答がない")
        snap("03_comment_reply")

        // 5. 紹介中の商品をカートへ
        app.buttons["featuredAddToCart"].tap()
        XCTAssertTrue(text(containing: "カートに追加しました").waitForExistence(timeout: 5))

        // 6. カート → 注文確定(キーボードがタブバーを隠しているので、ホスト部分をタップして閉じる)
        if app.keyboards.count > 0 {
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.2)).tap()
            XCTAssertTrue(app.keyboards.firstMatch.waitForNonExistence(timeout: 5), "キーボードが閉じない")
        }
        tab("カート")
        let checkout = app.buttons["checkoutButton"]
        if !checkout.waitForExistence(timeout: 8) { snap("zz_cart_debug"); dumpTree("zz_cart_tree") }
        XCTAssertTrue(checkout.exists, "注文ボタンが見つからない")
        snap("04_cart")
        checkout.tap()
        let alert = app.alerts.firstMatch
        XCTAssertTrue(alert.waitForExistence(timeout: 10))
        XCTAssertTrue(alert.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", "ご注文ありがとうございます")).firstMatch.exists,
                      "注文が成功しない")
        snap("05_ordered")
        alert.buttons["OK"].tap()

        // 7. ライブ画面に購入告知
        tab("ライブ")
        XCTAssertTrue(app.descendants(matching: .any)["systemComment"].firstMatch.waitForExistence(timeout: 10), "購入告知がない")
        snap("06_purchase_notice")

        // 8. アカウント:注文履歴 → ログアウト
        tab("アカウント")
        XCTAssertTrue(app.staticTexts["#1"].waitForExistence(timeout: 10) || text(containing: "#").exists, "注文履歴がない")
        snap("07_account")
        app.buttons["logoutButton"].tap()
        XCTAssertTrue(app.buttons["openLogin"].waitForExistence(timeout: 10), "ログアウトできない")
    }
}

private extension XCUIApplication {
    /// 要素のラベルが指定の文字列を含むまで待つ
    func wait(for element: XCUIElement, labelContains s: String, timeout: TimeInterval) -> Bool {
        let exp = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label CONTAINS %@", s), object: element)
        return XCTWaiter().wait(for: [exp], timeout: timeout) == .completed
    }
}
