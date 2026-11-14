// 管理画面(admin.html)のスクリプト
const $ = (id) => document.getElementById(id);
let TOKEN = sessionStorage.getItem('admin_token') || '';
const yen = (n) => '¥' + Number(n).toLocaleString('ja-JP');
const esc = (s) => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function msg(t){ const m=$('msg'); m.textContent=t; m.style.display='block'; clearTimeout(m._h); m._h=setTimeout(()=>m.style.display='none',2600); }

async function api(path, opts={}){
  const res = await fetch(path, { ...opts, headers:{ 'Content-Type':'application/json', 'X-Admin-Token': TOKEN, ...(opts.headers||{}) } });
  if (res.status === 401){ msg('トークンが正しくありません'); throw new Error('401'); }
  if (res.status === 429){ msg('試行回数が多すぎます。しばらく待ってからお試しください'); throw new Error('429'); }
  if (!res.ok){ const e = await res.json().catch(()=>({})); msg(e.detail||('エラー: '+res.status)); throw new Error(res.status); }
  return res.json();
}

async function login(){
  TOKEN = $('token').value.trim();
  try{
    await api('/api/admin/stats');
    sessionStorage.setItem('admin_token', TOKEN);
    $('panel').style.display = 'block';
    loadAll();
  }catch(e){}
}

let botEnabled = false;
function renderBot(){
  $('botBtn').textContent = botEnabled ? '賑やかしBOT:動作中' : '賑やかしBOT:停止中';
  $('botBtn').classList.toggle('on', botEnabled); $('botBtn').classList.toggle('ghost', !botEnabled);
}
async function toggleBot(){
  const r = await api('/api/admin/bot', { method:'POST', body: JSON.stringify({ enabled: !botEnabled }) });
  botEnabled = r.enabled; renderBot();
  msg(botEnabled ? '賑やかしBOTを開始しました(配信画面に自動コメントが流れます)' : '賑やかしBOTを停止しました');
}

async function loadAll(){
  const [stats, prods, orders] = await Promise.all([
    api('/api/admin/stats'), api('/api/admin/products'), api('/api/admin/orders'),
  ]);
  $('stats').innerHTML = [
    ['売上合計', yen(stats.total_revenue)], ['注文数', stats.total_orders],
    ['コメント数', stats.total_comments], ['登録ユーザー', stats.total_users],
    ['現在の視聴者', stats.current_viewers], ['最大同時視聴', stats.peak_viewers],
    ['販売中商品', stats.active_products],
  ].map(([l,v])=>`<div class="card"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
  botEnabled = stats.bot_enabled; renderBot();
  $('stats').innerHTML = $('stats').innerHTML
  + (stats.top_products.length
      ? `<div class="card" style="grid-column:1/-1"><div class="l" style="margin-bottom:6px">よく売れている商品</div>${
          stats.top_products.map(t=>`<div class="toprow"><span>${esc(t.name)}</span><span>${t.sold}点 / ${yen(t.revenue)}</span></div>`).join('')}</div>`
      : '');

  $('pbody').innerHTML = prods.map(p=>rowHtml(p)).join('') + rowHtml(null);
  bindRows();

  $('obody').innerHTML = orders.map(o=>`
    <tr><td>#${o.id}</td><td>${esc(o.nickname)}</td>
    <td>${o.items.map(i=>`${esc(i.product_name)}×${i.quantity}`).join('、')}</td>
    <td>${yen(o.total)}</td><td>${new Date(o.created_at).toLocaleString('ja-JP')}</td></tr>`).join('')
    || '<tr><td colspan="5" class="empty">注文はまだありません</td></tr>';
}

function rowHtml(p){
  const n = p === null;
  const v = (k,d='') => n ? d : esc(p[k]);
  return `<tr class="${n?'row-new':(p.is_active?'':'inactive')}" data-id="${n?'':p.id}">
    <td>${n?'新規':p.id}</td>
    <td><input class="f-emoji" value="${v('emoji','🛍️')}" style="width:52px"></td>
    <td><input class="f-name" value="${v('name')}" placeholder="商品名"></td>
    <td><input class="f-description" value="${v('description')}"></td>
    <td><input class="f-price num" type="number" value="${n?0:p.price}"></td>
    <td><input class="f-original_price num" type="number" value="${n?0:p.original_price}"></td>
    <td><input class="f-stock num" type="number" value="${n?0:p.stock}"></td>
    <td><input class="f-tag" value="${v('tag')}" style="width:100px"></td>
    <td>${n?'—':(p.is_active?'販売中':'停止')}</td>
    <td style="white-space:nowrap">
      ${n ? '<button class="small" data-act="create">登録</button>'
          : `<button class="small" data-act="save">保存</button>
             <button class="small ghost" data-act="toggle">${p.is_active?'停止':'再開'}</button>`}
    </td></tr>`;
}

function collect(tr){
  const g = (c) => tr.querySelector('.f-'+c).value;
  return { emoji:g('emoji'), name:g('name'), description:g('description'),
    price:+g('price')||0, original_price:+g('original_price')||0, stock:+g('stock')||0, tag:g('tag') };
}

function bindRows(){
  document.querySelectorAll('#pbody [data-act]').forEach(btn=>{
    btn.onclick = async ()=>{
      const tr = btn.closest('tr'), id = tr.dataset.id, act = btn.dataset.act;
      if (act === 'create'){
        const body = collect(tr);
        if (!body.name){ msg('商品名を入力してください'); return; }
        await api('/api/admin/products', { method:'POST', body: JSON.stringify(body) });
        msg('商品を登録しました');
      } else if (act === 'save'){
        await api(`/api/admin/products/${id}`, { method:'PATCH', body: JSON.stringify(collect(tr)) });
        msg('保存しました');
      } else if (act === 'toggle'){
        const active = !tr.classList.contains('inactive');
        await api(`/api/admin/products/${id}`, { method:'PATCH', body: JSON.stringify({ is_active: !active }) });
        msg(active ? '販売を停止しました' : '販売を再開しました');
      }
      loadAll();
    };
  });
}

$('loginBtn').onclick = login;
$('token').addEventListener('keydown', e => { if (e.key === 'Enter') login(); });
$('reloadBtn').onclick = loadAll;
$('botBtn').onclick = toggleBot;

if (TOKEN){ $('token').value = TOKEN; login(); }
