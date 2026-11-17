# 学习笔记 23:手机上打字时的画面——键盘会「吃掉」半个屏幕

## 做了什么
- **手机宽度(≤600px)下,点评论输入框会进入「聊天模式」**(`body.chatting`):键盘上方只显示「花奈的画面(上 40%)+ 评论(下 60%)」,输入框贴在键盘上沿。
  - 改之前:键盘弹出后页面被顶上去,花奈完全看不到,评论区上半是空白,下面还露出商品卡片。
- 点「送信」**键盘不收起**,可以连续提问(`mousedown` 时 `preventDefault()`,焦点不离开输入框)。
- 评论区右上角加「完了」按钮,点了收起键盘、回到原来的页面位置。
- **修了一个 bug**:用日语/中文输入法时,按 Enter 确定候选词也会触发发送,结果把没打完的字发出去了。现在判断 `e.isComposing`(旧 Safari 用 `keyCode === 229`)。
- 输入框加了 `enterkeyhint="send"`(键盘右下角显示「发送」)和 `autocomplete="off"`。

改动的文件:`app/static/js/live.js`(「スマホ:入力中の表示」一节)、`app/static/css/live.css`(`body.chatting`)、`index.html`(「完了」按钮)。

## 关键概念

### visualViewport(ビジュアルビューポート / visual viewport)
iOS Safari 弹出键盘时,**页面的布局尺寸(layout viewport)不变**,只是「看得见的部分」变小了。
`window.visualViewport` 告诉你看得见的部分:
- `height`:键盘以上还剩多高
- `offsetTop`:看得见的部分距离布局视口顶端多远(iOS 会自己滚动页面)

所以聊天模式把这两个值写进 CSS 变量 `--vv-h` / `--vv-top`,固定定位的元素按它们摆放;键盘高度变化(`resize`)、iOS 自己滚动(`scroll`)时再更新一次。

> 面试用:「スマホでキーボードが表示されると、visualViewport の高さが変わるので、それに合わせて入力欄の位置を調整しました。」

### IME 変換中の Enter(アイエムイー / IME composition)
日语输入时,Enter 先用来「确定变换」,这时 `keydown` 的 `isComposing` 是 `true`。只看 `e.key === 'Enter'` 就会误发送。日本的面试官对这个很敏感——日本用户每天都会遇到这种 bug。

> 面試用:「日本語入力の変換確定の Enter で送信されないよう、isComposing を見ています。」

### 为什么「盖一层白布」不行,改成 visibility:hidden
一开始用 `position:fixed` 的白色遮罩盖住其他内容,结果键盘上方的工具条(^ v ✓)后面还是透出商品卡片。原因:iOS 上 fixed 元素只画到「看得见的部分」为止,再往下 Safari 直接显示页面本身。
所以改成**让其他内容本身不可见**(`visibility:hidden`,子元素可以用 `visibility:visible` 单独显示出来——这点 `display:none` 和 `opacity` 做不到)。

## 怎么确认的
用 iOS 模拟器的 Safari,写了一个临时的 XCUITest 去点输入框、打字、发送、截图(确认后删掉了)。桌面浏览器的开发者工具模拟不出 iOS 键盘的行为。

## 自我检查
1. 键盘弹出时,`window.innerHeight` 和 `visualViewport.height` 哪个会变?
2. 点送信按钮时,为什么要在 `mousedown` 而不是 `click` 里 `preventDefault()`?
3. 进入聊天模式时,为什么要给 `#live` 设 `minHeight`?不设会怎样?
4. `visibility:hidden` 和 `display:none` 的区别是什么?为什么这里必须用前者?
5. 只判断 `e.key === 'Enter'` 时,用日语输入「ぼうすい」→ 变换成「防水」→ 按 Enter,会发生什么?
