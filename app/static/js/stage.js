// iOS アプリに埋め込む 3D アバターのページ(stage.html)とアプリの橋渡し。
// 3D の準備ができたら(vrm.js が hana-ready を送る)アプリに知らせる。アプリは立ち絵を隠して、このページを見せる
window.addEventListener('hana-ready', () => {
  const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.hana;
  if (h) h.postMessage('ready');
});
