// ---------- 浏览页：视频列表 / 筛选 / 扫描 / 加入 Emby ----------
// 注意：items/bloggersList/selected/page/pageSize/totalPages 等全局变量
// 由 app.js 声明，本页直接复用，避免 let 重复声明

// 支持从映射页带入博主筛选：/browse.html?blogger=xxx
const _initBlogger = new URLSearchParams(location.search).get('blogger') || '';
// 支持从映射页带入「已映射目录」筛选：/browse.html?blogger=xxx&mapped=1
const _initMapped = new URLSearchParams(location.search).get('mapped') === '1';

// 默认只看「未映射目录」的视频；
// 从映射页跳来（blogger 或 mapped=1）时改为「已映射目录」
if (_initBlogger || _initMapped) document.getElementById('f-emby').value = '1';

// 首次请求时 f-blogger 下拉框尚未填充，URL 带入的博主筛选先挂起，
// 由 renderFilters 消费（选中对应选项）后清空，保证首次请求就带上该筛选
let _pendingBlogger = _initBlogger;

// 筛选条件变化 -> 回到第一页
function reload() { page = 1; load(); }
const debouncedReload = debounce(reload, 400);

async function load() {
  const p = new URLSearchParams({
    page, page_size: pageSize,
    q: val('f-q'), blogger: _pendingBlogger || val('f-blogger'),
    in_emby: val('f-emby'),
  });
  const r = await fetch(`${API}/videos?${p}`);
  const data = await r.json();
  if (data.error) { toast(data.error, 'err'); return; }
  items = data.items;
  bloggersList = data.bloggers;
  totalPages = Math.max(1, Math.ceil(data.total / pageSize));
  renderFilters(data.bloggers);
  render();
  renderPager();
  pollStates();
}

function renderFilters(bloggers) {
  const fb = document.getElementById('f-blogger');
  const bv = _pendingBlogger || fb.value;
  fb.innerHTML = '<option value="">全部博主</option>' +
    bloggers.map(b => `<option value="${esc(b.dir_name)}" ${b.dir_name===bv?'selected':''}>${esc(b.dir_name)} (${b.video_count})${b.in_emby?' ✓':''}</option>`).join('');
  // 初始筛选已同步到下拉框，消费掉；找不到对应选项时也清空，避免影响后续请求
  _pendingBlogger = '';
}

function render() {
  const grid = document.getElementById('grid');
  document.getElementById('empty').style.display = items.length ? 'none' : 'block';
  grid.innerHTML = items.map(i => `
    <div class="card ${selected.has(i.id) ? 'selected' : ''}" onclick="location.href='/preview.html?id=${i.id}'">
      <div class="check" onclick="event.stopPropagation();toggleSel(${i.id})"></div>
      <div class="thumb">
        ${i.poster ? `<img src="${i.poster}" loading="lazy" onerror="this.remove()">` : '无海报'}
        ${i.in_emby ? '<span class="badge-emby">已在 Emby</span>' : ''}
      </div>
      <div class="card-body">
        <div class="name" title="${esc(i.name)}">${esc(i.name)}</div>
        <div class="meta"><span title="${esc(i.blogger)}">${esc(i.blogger)}</span><span>${fmtSize(i.size)}</span></div>
      </div>
    </div>`).join('');
  updateStats();
}

// ---------- 删除（单视频 / 整个博主） ----------
// 卡片角标删除已移除，单视频删除走批量函数 deleteSelectedVideos（逐个确认）
async function askDeleteVideo(id) {
  const v = items.find(i => i.id === id);
  if (!v) return;
  if (!await ensureDeleteEnabled()) return;   // 未开启时弹提示框
  let pre, r;
  try {
    r = await fetch(`${API}/delete/video/${id}/preview`);
    pre = await r.json();
  } catch (e) { toast('预览失败: ' + e, 'err'); return; }
  if (pre.error) {
    // 403 = 服务端确认未开启（开关可能刚被改回），弹提示框；其余错误走 toast
    r.status === 403 ? alertDeleteDisabled() : toast(pre.error, 'err');
    return;
  }

  const go = await confirmDelete('video', pre);
  if (!go) return;
  try {
    const r = await fetch(`${API}/video/${id}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    toast(`已删除「${d.name}」，释放 ${fmtSize(d.freed_bytes)}`, 'ok');
    selected.delete(id);
    await load();
  } catch (e) { toast('删除失败: ' + e, 'err'); }
}

/** 博主级删除入口：可从筛选栏「删除该博主」或预览页调用 */
async function askDeleteBlogger(blogger) {
  if (!blogger) return;
  if (!await ensureDeleteEnabled()) return;   // 未开启时弹提示框
  let pre, r;
  try {
    r = await fetch(`${API}/delete/blogger/${encodeURIComponent(blogger)}/preview`);
    pre = await r.json();
  } catch (e) { toast('预览失败: ' + e, 'err'); return; }
  if (pre.error) {
    r.status === 403 ? alertDeleteDisabled() : toast(pre.error, 'err');
    return;
  }

  const go = await confirmDelete('blogger', pre);
  if (!go) return;
  try {
    const r = await fetch(`${API}/blogger/${encodeURIComponent(blogger)}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true, remove_from_emby: true }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    toast(`已删除博主「${d.blogger}」（${d.videos} 个视频，释放 ${fmtSize(d.freed_bytes)}）`
        + (d.emby_removed ? '，已从 Emby 移除' : ''), 'ok');
    selected.clear();
    document.getElementById('f-blogger').value = '';
    reload();
  } catch (e) { toast('删除失败: ' + e, 'err'); }
}

// 批量：删除当前勾选的视频（逐个弹确认框，可随时取消）
async function deleteSelectedVideos() {
  try {
    console.log('[DYSort] del-video click, selected =', selected.size);
    if (!selected.size) { toast('请先勾选要删除的视频', 'err'); return; }
    if (!await ensureDeleteEnabled()) return;   // 未开启时弹提示框
    const vids = items.filter(i => selected.has(i.id));
    if (!vids.length) { toast('没有可删除的视频', 'err'); return; }
    for (const v of vids) {
      await askDeleteVideo(v.id);   // 每个视频都弹独立确认框，可随时取消
    }
  } catch (e) { console.error('[DYSort] del-video failed:', e); toast('删除失败: ' + e, 'err'); }
}

// 批量：删除当前勾选视频所属的全部博主（去重后逐个弹窗确认）
async function deleteSelectedBloggers() {
  try {
    console.log('[DYSort] del-blogger click, selected =', selected.size);
    if (!selected.size) { toast('请先勾选视频', 'err'); return; }
    if (!await ensureDeleteEnabled()) return;   // 未开启时弹提示框，先于批量确认
    const bloggers = [...new Set(items.filter(i => selected.has(i.id)).map(i => i.blogger))];
    if (!bloggers.length) { toast('没有可删除的博主目录', 'err'); return; }
    for (const b of bloggers) {
      await askDeleteBlogger(b);   // 每个博主都弹独立确认框，可随时取消
    }
  } catch (e) { console.error('[DYSort] del-blogger failed:', e); toast('删除失败: ' + e, 'err'); }
}

// 统计栏（含定时扫描状态），内容不变时不重绘
let scanState = null, _statCache = '';
function updateStats() {
  const sch = scanState && scanState.scheduler;
  const timer = sch && sch.enabled ? `，定时扫描每 ${sch.interval_min} 分钟` : '';
  const txt = `本页 ${items.length} 个，已映射目录 ${items.filter(i => i.in_emby).length}，已选 ${selected.size}${timer}`;
  if (txt === _statCache) return;
  _statCache = txt;
  document.getElementById('stats').textContent = txt;
  // 未勾选时仅视觉变暗、保持可点：disabled 会静默吞掉点击且无任何反馈，
  // 容易被当成「点了没反应」；具体提示由删除函数的 toast 负责
  const btn = document.getElementById('btn-del-blogger');
  if (btn) { btn.disabled = false; btn.classList.toggle('dim', !selected.size); }
  const btnv = document.getElementById('btn-del-video');
  if (btnv) { btnv.disabled = false; btnv.classList.toggle('dim', !selected.size); }
}

function renderPager() {
  const el = document.getElementById('pager');
  if (totalPages <= 1) { el.innerHTML = ''; return; }
  let html = `<button class="btn" ${page<=1?'disabled':''} onclick="gotoPage(${page-1})">‹</button>`;
  const start = Math.max(1, page - 2), end = Math.min(totalPages, page + 2);
  if (start > 1) html += `<button class="btn" onclick="gotoPage(1)">1</button>`;
  for (let p = start; p <= end; p++) {
    html += `<button class="btn ${p===page?'cur':''}" onclick="gotoPage(${p})">${p}</button>`;
  }
  if (end < totalPages) html += `<button class="btn" onclick="gotoPage(${totalPages})">${totalPages}</button>`;
  html += `<button class="btn" ${page>=totalPages?'disabled':''} onclick="gotoPage(${page+1})">›</button>`;
  el.innerHTML = html;
}
function gotoPage(p) { page = Math.min(Math.max(1, p), totalPages); selected.clear(); load(); }

function toggleSel(id) { selected.has(id) ? selected.delete(id) : selected.add(id); render(); }
function toggleAll(on) { selected = on ? new Set(items.map(i => i.id)) : new Set(); render(); }

// ---------- 后台任务：扫描 ----------
async function startScan() {
  const r = await fetch(`${API}/scan`, { method: 'POST' });
  const d = await r.json();
  d.started === false ? toast(d.error, 'err') : toast('扫描已开始', 'ok');
  pollStates();
}
// 自调度轮询：扫描中 1.5s；空闲且定时扫描开启 30s；空闲且定时关闭则停止
let pollTimer = null, wasRunning = false;
function pollStates() {
  clearTimeout(pollTimer);
  const tick = async () => {
    let s;
    try {
      s = await fetch(`${API}/scan/state`).then(x => x.json());
    } catch (e) {
      pollTimer = setTimeout(tick, 5000);   // 网络异常稍后重试
      return;
    }
    scanState = s;
    updateStats();
    const box = document.getElementById('progress');
    const txt = document.getElementById('progress-text');
    let msg = '';
    if (s.running) msg = `扫描中: ${s.phase}${s.total ? ` (${s.done}/${s.total})` : ''}`;
    if (!s.running && s.error) { toast(`扫描失败: ${s.error}`, 'err'); }
    box.style.display = msg ? 'flex' : 'none';
    if (msg) txt.textContent = msg;
    if (s.running) {
      wasRunning = true;
      pollTimer = setTimeout(tick, 1500);
    } else if (s.scheduler && s.scheduler.enabled) {
      pollTimer = setTimeout(tick, 30000);
    } else {
      pollTimer = null;
      if (wasRunning) { wasRunning = false; load(); }
    }
  };
  pollTimer = setTimeout(tick, 0);
}

// ---------- Emby ----------
// bloggers：博主目录列表；library：手动选择的媒体库名
// vCount/done：本次勾选的视频数、其中已在 Emby 的数量（仅用于提示）
async function addBloggers(bloggers, library, vCount = 0, done = 0) {
  if (!bloggers || !bloggers.length) { toast('没有可加入的博主目录'); return; }
  try {
    const r = await fetch(`${API}/emby/add`, { method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ bloggers, library_name: library }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    toast(`已把 ${d.added.length} 个博主目录（共 ${vCount} 个视频）加入「${library}」`
        + (d.skipped.length ? `，跳过 ${d.skipped.length} 个（已在库中）` : '')
        + (done ? `；勾选中已有 ${done} 个视频在库` : ''), 'ok');
    selected.clear(); await load();
  } catch (e) { toast('加入 Emby 失败: ' + e, 'err'); }
}
async function addSelected() {
  if (!selected.size) { toast('请先勾选要加入 Emby 的视频', 'err'); return; }
  const picked = items.filter(i => selected.has(i.id));
  const bs = [...new Set(picked.map(i => i.blogger))];
  const done = picked.filter(i => i.in_emby).length;
  if (!bs.length) { toast('没有可加入的博主目录'); return; }
  const library = await chooseLibrary();
  if (!library) return;
  addBloggers(bs, library, picked.length, done);
}
async function syncEmby() {
  const r = await fetch(`${API}/emby/sync`, { method: 'POST' });
  const d = await r.json();
  d.error ? toast(d.error, 'err') : toast(`已同步，Emby DY_ 库共 ${d.paths} 个路径`, 'ok');
  await load();
}

load();
