// 視聴者ページ(index.html)のスクリプト。状態・認証・配信の受信・商品・カート・決済
// ================= 状態 =================
const $ = (id) => document.getElementById(id);
// 認証は HttpOnly Cookie(JS からは読めない)。JS が持つのは表示用のユーザー名と CSRF トークンだけ
let username = '';
let csrf = '';
let products = [], featured = null, cart = [];
let soundOn = false, ws = null;
const audioQueue = []; let playing = false;
let authMode = 'login';
let pendingAfterAuth = null;

const yen = (n) => '¥' + Number(n).toLocaleString('ja-JP');
const esc = (s) => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(msg){
  document.querySelectorAll('.toast').forEach(t=>t.remove());
  const t = document.createElement('div'); t.className='toast'; t.textContent = msg;
  document.body.appendChild(t); setTimeout(()=>t.remove(), 2600);
}
// Cookie は同一オリジンの fetch で自動送信される。更新系リクエストには CSRF トークンを付ける
function authHeaders(){ return { 'Content-Type':'application/json', 'X-CSRF-Token': csrf }; }
function setSession(user, csrfToken){ username = user || ''; csrf = csrfToken || ''; renderWho(); }

// ================= 認証 =================
function openAuth(after){ $('input').blur(); pendingAfterAuth = after || null; $('authErr').textContent=''; $('authMask').classList.add('open'); }
function setAuthMode(m){
  authMode = m;
  $('authTitle').textContent = m==='login' ? 'ログイン' : '新規登録';
  $('authSubmit').textContent = m==='login' ? 'ログイン' : '登録して利用を開始';
  $('authToggle').textContent = m==='login' ? '新規登録' : 'ログイン';
}
$('authToggle').onclick = () => setAuthMode(authMode==='login' ? 'register' : 'login');
$('authClose').onclick = () => $('authMask').classList.remove('open');
$('authSubmit').onclick = async () => {
  const u = $('authUser').value.trim(), p = $('authPass').value;
  if (!u || !p){ $('authErr').textContent = 'ユーザー名とパスワードを入力してください'; return; }
  const res = await fetch(`/api/auth/${authMode}`, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({username:u, password:p}) });
  const data = await res.json().catch(()=>({}));
  if (!res.ok){ $('authErr').textContent = data.detail?.[0]?.msg || data.detail || 'エラーが発生しました'; return; }
  setSession(data.username, data.csrf_token);
  $('authMask').classList.remove('open');
  reconnect();   // WebSocket は接続時に Cookie で認証するため、ログイン後に張り直す
  refreshCart();
  toast(`ようこそ、${username}さん`);
  if (pendingAfterAuth){ const f = pendingAfterAuth; pendingAfterAuth = null; f(); }
};
$('userBtn').onclick = async () => {
  if (!username) return openAuth();
  if (confirm(`${username} さん、ログアウトしますか?`)){
    await fetch('/api/auth/logout', { method:'POST', headers: authHeaders() }).catch(()=>{});
    setSession('', '');
    cart=[]; renderCartUI();
    reconnect();
  }
};
function renderWho(){
  $('userBtn').textContent = username ? `${username} さん` : 'ログイン';
  $('footLogin').textContent = username ? 'ログアウト' : 'ログイン / 新規登録';
}
// ページ読み込み時:Cookie が有効ならログイン状態と CSRF トークンを復元
async function checkSession(){
  const res = await fetch('/api/auth/me').catch(()=>null);
  if (res && res.ok){ const d = await res.json(); setSession(d.username, d.csrf_token); }
  else setSession('', '');
}

// ================= アシスタント:感情 + 発話 =================
let emoTimer=null, talkTimer=null, lastReplyId=null;
function setEmotion(emo){
  const box = $('avatarBox');
  box.classList.remove('happy','excited','troubled','shy');
  if (emo && emo!=='normal') box.classList.add(emo);
  window.HANA && window.HANA.setEmotion(emo || 'normal');
  setStill(emo);
  clearTimeout(emoTimer);
  emoTimer = setTimeout(()=>{ box.classList.remove('happy','excited','troubled','shy'); window.HANA && window.HANA.setEmotion('normal'); setStill('normal'); }, 8000);
}
// 3D がまだ・使えないときは、立ち絵の表情を切り替える(3D 表示中は画像を読み込まない)
function setStill(emo){
  if (window.HANA && window.HANA.ready) return;
  $('hostStill').src = `/static/host/hana_${['happy','excited','troubled','shy'].includes(emo) ? emo : 'normal'}.png`;
}
// 文字は host_reply で先に届き、音声は合成が終わってから host_audio で届く
function hostSpeak(text, emotion, audioPending){
  $('bubbleText').textContent = text;
  setEmotion(emotion);
  $('avatarBox').classList.add('talking');
  clearTimeout(talkTimer);
  if (soundOn && audioPending){
    // 音声がまもなく届く:口パクは音声に合わせるので、ここでは始めない。届かなかったとき(合成の失敗)に備えて時間で解除
    talkTimer = setTimeout(()=>$('avatarBox').classList.remove('talking'), 8000);
  } else {
    const dur = Math.min(text.length*150, 10000);
    window.HANA && window.HANA.talkFor(dur);   // 音声がないとき:文章の長さに合わせて口を動かす
    talkTimer = setTimeout(()=>$('avatarBox').classList.remove('talking'), dur);
  }
}
function hostAudio(audioUrl){
  if (!soundOn) return;
  clearTimeout(talkTimer);
  $('avatarBox').classList.add('talking');
  audioQueue.push(audioUrl); playNext();
}
function playNext(){
  if (playing || !audioQueue.length) return;
  playing = true;
  const a = new Audio(audioQueue.shift());
  window.HANA && window.HANA.attachAudio(a);   // 実際の音声に合わせて口を動かす
  a.onended = a.onerror = () => { playing=false; $('avatarBox').classList.remove('talking'); playNext(); };
  a.play().catch(()=>{ playing=false; $('avatarBox').classList.remove('talking'); });
}
$('soundBtn').onclick = () => {
  soundOn = !soundOn;
  window.HANA && window.HANA.resumeAudio();   // 利用者の操作の中で AudioContext を有効にする(口の動きの解析に使う)
  $('soundBtn').textContent = soundOn ? '音声 オン' : '音声 オフ';
};

// ================= コメント =================
function addComment(nick, text, cls=''){
  const box = $('comments');
  const div = document.createElement('div');
  div.className = 'cm ' + cls;
  div.innerHTML = `<b>${esc(nick)}</b>${esc(text)}`;
  box.appendChild(div);
  while (box.children.length > 80) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}

// ================= 商品 =================
async function loadProducts(){
  products = await (await fetch('/api/products')).json();
  if (!featured || !products.find(p=>p.id===featured.id)) featured = products[0] || null;
  else featured = products.find(p=>p.id===featured.id);
  renderFeatured(); renderStrip(); renderShelf(); renderChrome();
}
// 商品画像(image_url があれば画像、なければ絵文字)
function thumbHtml(p){
  return p.image_url ? `<img src="${esc(p.image_url)}" alt="${esc(p.name)}" loading="lazy">` : esc(p.emoji);
}
// 割引率(%)。通常価格より安いときだけ。計算はここ1か所(サーバ側の host_rules.off_rate と同じ式)
function offRate(p){
  return p.original_price > p.price && p.price > 0 ? Math.round((1 - p.price / p.original_price) * 100) : 0;
}
function offText(p){ const r = offRate(p); return r ? `${r}% OFF` : ''; }
function renderFeatured(){
  if (!featured){ $('productCard').style.display='none'; return; }
  $('productCard').style.display='flex';
  $('pImg').innerHTML = thumbHtml(featured);
  $('pTag').textContent = featured.tag || 'ご紹介中';
  $('pName').textContent = featured.name;
  $('pDesc').textContent = featured.description;
  $('pPrice').textContent = yen(featured.price);
  $('pOrig').textContent = offRate(featured) ? yen(featured.original_price) : '';
  $('pOff').textContent = offText(featured);
  const st = $('pStock');
  st.textContent = featured.stock > 0 ? `残り${featured.stock}点` : '在庫切れ';
  st.className = 'pstock' + (featured.stock <= 15 ? ' low' : '');
  $('buyBtn').disabled = featured.stock <= 0;
}
// ページ上部の商品の横並び(押すと「紹介中の商品」が切り替わる)
function renderStrip(){
  $('productStrip').innerHTML = products.map(p=>`
    <button data-pick="${p.id}" class="${featured && featured.id===p.id ? 'on' : ''}">
      <div class="simg">${thumbHtml(p)}</div>
      <div class="sname">${esc(p.name)}</div>
      <div class="sprice">${yen(p.price)}</div>
    </button>`).join('');
  $('productStrip').querySelectorAll('[data-pick]').forEach(b=>{ b.onclick = ()=>setFeaturedById(+b.dataset.pick); });
}
function setFeaturedById(id){ const p = products.find(x=>x.id===id); if (p){ featured=p; renderFeatured(); renderStrip(); } }

// 「本日の商品」の棚:大きな色つきカードを横に並べる
const TINTS = ['#F8D2DF', '#C7DDF1', '#DAD1F1', '#FBE1CB', '#CFEADB', '#F3E9C6'];
function askAbout(p){
  setFeaturedById(p.id);
  $('live').scrollIntoView();
  trySend(`${p.name}について教えてください`);
}
function renderShelf(){
  $('shelf').innerHTML = products.map((p,i)=>`
    <article class="tile" style="background:${TINTS[i % TINTS.length]}">
      <div class="tile-eyebrow">${esc(p.tag || '本日の商品')}</div>
      <h3 class="tile-name">${esc(p.name)}</h3>
      <div class="tile-price">${yen(p.price)}${offRate(p) ? `<s>${yen(p.original_price)}</s><em>${offText(p)}</em>` : ''}</div>
      <div class="tile-actions">
        <button class="pill" data-cart="${p.id}" ${p.stock<=0 ? 'disabled' : ''}>${p.stock<=0 ? '在庫切れ' : 'カートに入れる'}</button>
        <a data-ask="${p.id}">花奈に質問する</a>
      </div>
      <div class="tile-img">${thumbHtml(p)}</div>
    </article>`).join('');
  $('shelf').querySelectorAll('[data-cart]').forEach(b=>{ b.onclick = ()=>addToCart(+b.dataset.cart); });
  $('shelf').querySelectorAll('[data-ask]').forEach(a=>{ a.onclick = ()=>askAbout(products.find(x=>x.id===+a.dataset.ask)); });
  updatePaddles();
}
function updatePaddles(){
  const el = $('shelf');
  $('shelfPrev').disabled = el.scrollLeft <= 4;
  $('shelfNext').disabled = el.scrollLeft + el.clientWidth >= el.scrollWidth - 4;
  $('shelfPrev').parentElement.classList.toggle('hidden', el.scrollWidth <= el.clientWidth + 4);
}
$('shelf').addEventListener('scroll', updatePaddles, { passive:true });
window.addEventListener('resize', updatePaddles);
$('shelfPrev').onclick = ()=>$('shelf').scrollBy({ left:-420, behavior:'smooth' });
$('shelfNext').onclick = ()=>$('shelf').scrollBy({ left: 420, behavior:'smooth' });

// お知らせの帯・フッターの商品リンク(数字は商品データから作る)
function renderChrome(){
  const maxOff = Math.max(0, ...products.map(offRate));
  $('ribbonText').textContent = (maxOff ? `ライブ限定価格で、最大${maxOff}%オフ。` : '') + '全国どこでも送料無料でお届けします。';
  $('footProducts').innerHTML = products.map(p=>`<a data-foot="${p.id}">${esc(p.name)}</a>`).join('');
  $('footProducts').querySelectorAll('[data-foot]').forEach(a=>{ a.onclick = ()=>{ setFeaturedById(+a.dataset.foot); $('live').scrollIntoView(); }; });
}
function focusAsk(){ $('live').scrollIntoView(); setTimeout(()=>$('input').focus({ preventScroll:true }), 350); }

// ================= カート =================
async function refreshCart(){
  if (!username){ cart=[]; renderCartUI(); return; }
  const res = await fetch('/api/cart', { headers: authHeaders() });
  cart = res.ok ? await res.json() : [];
  renderCartUI();
}
function renderCartUI(){
  const count = cart.reduce((s,c)=>s+c.quantity,0);
  $('cartInfo').textContent = count ? `カートに${count}点` : '';
  $('bagCount').textContent = count || '';
  $('cartCount').textContent = count ? `(${count}点)` : '';
  const total = cart.reduce((s,c)=>s+c.quantity*c.product.price,0);
  $('cartTotal').textContent = yen(total);
  $('toPayBtn').disabled = !count;
  $('cartList').innerHTML = cart.length
    ? cart.map(c=>`<div class="cartrow">
        <div class="thumb sm">${thumbHtml(c.product)}</div>
        <span class="nm">${esc(c.product.name)} × ${c.quantity}</span>
        <span>${yen(c.product.price*c.quantity)}</span><a href="#" data-rm="${c.id}">削除</a></div>`).join('')
    : `<div style="color:var(--sub);padding:10px 0">${username?'カートは空です':'ログインするとカートをご利用いただけます'}</div>`;
  $('cartList').querySelectorAll('[data-rm]').forEach(a=>{
    a.onclick = async (e)=>{ e.preventDefault(); await fetch(`/api/cart/${a.dataset.rm}`,{method:'DELETE',headers:authHeaders()}); refreshCart(); };
  });
}
async function addToCart(productId){
  if (!username) return openAuth(()=>addToCart(productId));
  const res = await fetch('/api/cart', { method:'POST', headers: authHeaders(), body: JSON.stringify({product_id:productId, quantity:1}) });
  if (!res.ok){ const e = await res.json().catch(()=>({})); toast(e.detail||'エラーが発生しました'); return; }
  toast('カートに追加しました');
  refreshCart();
}
$('buyBtn').onclick = () => featured && addToCart(featured.id);

// ================= 決済(モック) =================
const PAY_METHODS = [
  { id:'credit_card', label:'クレジットカード' },
  { id:'convenience_store', label:'コンビニ払い' },
  { id:'paypay', label:'PayPay' },
];
let payMethod = 'credit_card';
$('toPayBtn').onclick = () => {
  if (!username) return openAuth();
  if (!cart.length) return;
  $('drawerMask').classList.remove('open');
  renderPayStep1();
  $('payMask').classList.add('open');
};
function bindClosePay(){
  $('payBody').querySelector('[data-close-pay]').onclick = ()=>$('payMask').classList.remove('open');
}
function renderPayStep1(){
  const total = cart.reduce((s,c)=>s+c.quantity*c.product.price,0);
  $('payBody').innerHTML = `
    <h3 style="text-align:center">お支払い方法</h3>
    <div class="note" style="text-align:center">合計 <b style="font-size:17px;color:var(--ink)">${yen(total)}</b>(送料無料)</div>
    ${PAY_METHODS.map(m=>`<div class="payopt ${m.id===payMethod?'sel':''}" data-pay="${m.id}"><span class="radio"></span>${m.label}</div>`).join('')}
    <div class="note" style="text-align:center;margin-top:10px">※デモのため実際の決済は行われません</div>
    <button class="primarybtn" id="payGo">注文を確定する</button>
    <div class="switchline"><a id="payCancel">キャンセル</a></div>`;
  $('payBody').querySelectorAll('[data-pay]').forEach(d=>{ d.onclick = ()=>{ payMethod=d.dataset.pay; renderPayStep1(); }; });
  $('payCancel').onclick = ()=>$('payMask').classList.remove('open');
  $('payGo').onclick = doPay;
}
async function doPay(){
  $('payBody').innerHTML = `<h3 style="text-align:center">決済処理中</h3><div class="spinner"></div>
    <div class="note" style="text-align:center">${PAY_METHODS.find(m=>m.id===payMethod).label}で処理しています…</div>`;
  await new Promise(r=>setTimeout(r,1200));
  const res = await fetch('/api/orders/checkout', { method:'POST', headers: authHeaders(), body: JSON.stringify({payment_method: payMethod}) });
  const data = await res.json().catch(()=>({}));
  if (!res.ok){
    $('payBody').innerHTML = `<h3 style="text-align:center">決済に失敗しました</h3>
      <div class="note" style="text-align:center">${esc(data.detail||'エラーが発生しました')}</div>
      <button class="primarybtn" data-close-pay>閉じる</button>`;
    bindClosePay(); refreshCart(); loadProducts(); return;
  }
  $('payBody').innerHTML = `<div class="successcheck">✓</div>
    <h3 style="text-align:center">ご注文ありがとうございます</h3>
    <div class="note" style="text-align:center">注文番号 #${data.id} / ${yen(data.total)}<br>3〜5日でお届けいたします</div>
    <button class="primarybtn" data-close-pay>配信にもどる</button>`;
  bindClosePay(); refreshCart(); loadProducts();
}

// ================= カートのモーダル =================
const openDrawer = ()=>{ $('drawerMask').classList.add('open'); refreshCart(); };
$('drawerBtn').onclick = openDrawer;
$('bagBtn').onclick = openDrawer;
$('footCart').onclick = openDrawer;
$('footLogin').onclick = ()=>$('userBtn').click();
$('heroAsk').onclick = focusAsk;
$('askBubble').onclick = focusAsk;
// 「花奈に質問する」の丸いボタンは、ライブ欄(入力欄)が画面に見えている間は隠す(送信ボタンに重なるため。見えているなら不要)
if ('IntersectionObserver' in window){
  new IntersectionObserver(([e])=>{ $('askBubble').classList.toggle('away', e.isIntersecting); }, { threshold: 0.25 })
    .observe(document.querySelector('.livegrid'));
}
$('drawerClose').onclick = ()=>$('drawerMask').classList.remove('open');
[['drawerMask'],['payMask'],['authMask']].forEach(([id])=>{
  $(id).addEventListener('click', (e)=>{ if (e.target===$(id)) $(id).classList.remove('open'); });
});

// ================= WebSocket =================
let pendingSend = null;   // 接続確立前に送ろうとしたコメント(ログイン直後など)
function connect(){
  const proto = location.protocol==='https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/live`);
  ws.onopen = ()=>{
    $('connState').textContent = 'ライブ接続中';
    if (pendingSend){ const t = pendingSend; pendingSend = null; trySend(t); }
  };
  ws.onclose = ()=>{ $('connState').textContent = '再接続中…'; setTimeout(connect, 2000); };
  ws.onmessage = (ev)=>{
    const m = JSON.parse(ev.data);
    switch (m.type){
      case 'viewers': $('viewers').textContent = m.count; break;
      case 'comment': addComment(m.nickname, m.text, m.nickname===username?'me':''); break;
      case 'host_reply':
        addComment('花奈', m.text, 'host');   // 表示名は字幕と揃える(m.nickname は読み上げ用のカタカナ表記)
        if (m.product_id) setFeaturedById(m.product_id);
        lastReplyId = m.id;
        hostSpeak(m.text, m.emotion, m.audio_pending);
        break;
      case 'host_audio':
        if (m.id === lastReplyId) hostAudio(m.audio_url);   // すでに次の発言に移っていたら、古い音声は流さない
        break;
      case 'purchase': {
        addComment('お知らせ', `${m.nickname}さんが ${m.items.map(i=>`${i.name}×${i.quantity}`).join('、')} を購入しました`, 'sys');
        if (m.stocks){ products.forEach(p=>{ if (m.stocks[p.id]!==undefined) p.stock=m.stocks[p.id]; }); renderFeatured(); renderShelf(); }
        break;
      }
      case 'products_changed': loadProducts(); break;
      case 'error': toast(m.message); if (!username) openAuth(); break;
    }
  };
}
// ログイン/ログアウト後に張り直す(サーバは接続時の Cookie でユーザーを判定するため)
function reconnect(){
  if (ws){ ws.onclose = null; ws.close(); }
  connect();
}
function trySend(text){
  text = (text||'').trim();
  if (!text) return;
  if (!username) return openAuth(()=>trySend(text));
  if (!ws || ws.readyState!==1){ pendingSend = text; return; }
  ws.send(JSON.stringify({ type:'comment', text }));   // トークンは送らない(Cookie で認証済み)
}
$('sendBtn').onclick = ()=>{ trySend($('input').value); $('input').value=''; };
$('input').addEventListener('keydown', (e)=>{
  // 日本語入力で変換を確定する Enter は送信にしない(isComposing。古い Safari は keyCode 229 で判定)
  if (e.key==='Enter' && !e.isComposing && e.keyCode!==229){ e.preventDefault(); trySend($('input').value); $('input').value=''; }
});

// ================= スマホ:入力中の表示 =================
// キーボードが出ると見える範囲が狭くなり、配信画面が画面外に押し出される。
// 入力中だけ「配信画面(上)+コメント(下)」をキーボードの上に固定して表示する(見た目は live.css の body.chatting)
const chatMQ = matchMedia('(max-width:600px)');
const vv = window.visualViewport;
function fitChat(){
  const s = document.documentElement.style;
  s.setProperty('--vv-top', (vv ? vv.offsetTop : 0) + 'px');
  s.setProperty('--vv-h', (vv ? vv.height : innerHeight) + 'px');
}
let chatScrollY = 0;
function setChatting(on){
  on = on && chatMQ.matches;
  if (on === document.body.classList.contains('chatting')) return;
  // 配信画面とコメント欄が抜けてもページの高さが変わらないようにする
  $('live').style.minHeight = on ? $('live').offsetHeight + 'px' : '';
  document.body.classList.toggle('chatting', on);
  if (on){
    chatScrollY = scrollY;
    fitChat();
    const box = $('comments'); box.scrollTop = box.scrollHeight;
  } else {
    // キーボードを出すときに iOS がページを勝手にスクロールするので、入力前の位置に戻す
    window.scrollTo({ top: chatScrollY, behavior: 'instant' });
  }
}
$('input').addEventListener('focus', ()=>setChatting(true));
$('input').addEventListener('blur', ()=>setChatting(false));
if (vv){ vv.addEventListener('resize', fitChat); vv.addEventListener('scroll', fitChat); }
chatMQ.addEventListener('change', ()=>{ if (!chatMQ.matches) setChatting(false); });
// 送信ボタンを押しても入力欄からフォーカスを外さない(キーボードが閉じず、続けて入力できる)
$('sendBtn').addEventListener('mousedown', (e)=>e.preventDefault());
$('chatClose').onclick = ()=>$('input').blur();

// ================= アクセシビリティ =================
// href のない <a>(クリックで動作するもの)は、そのままだと Tab キーで選べず Enter でも押せない。
// ボタンとして扱えるようにする(あとから描画される要素にも効くよう、イベントは document で受ける)
function markLinkButtons(root){
  root.querySelectorAll('a:not([href]):not([role])').forEach(a=>{ a.setAttribute('role','button'); a.tabIndex = 0; });
}
new MutationObserver(()=>markLinkButtons(document.body)).observe(document.body, { childList:true, subtree:true });
markLinkButtons(document.body);
document.addEventListener('keydown', (e)=>{
  if ((e.key==='Enter' || e.key===' ') && e.target.matches && e.target.matches('a[role="button"]')){ e.preventDefault(); e.target.click(); }
});
document.addEventListener('keydown', (e)=>{     // Esc でモーダルを閉じる
  if (e.key==='Escape') document.querySelectorAll('.mask.open').forEach(m=>m.classList.remove('open'));
});

// ================= 起動 =================
setAuthMode('login');
// 音声クレジット(VOICEVOX の利用規約に従いキャラクター名を表示)・回答生成の表示
fetch('/api/meta').then(r=>r.json()).then(m=>{
  $('ttsCredit').textContent = m.tts_credit || 'なし(文字のみ)';
  $('llmCredit').textContent = m.llm;
}).catch(()=>{});
checkSession().then(refreshCart); loadProducts(); connect();
