// ---------- 视频预览页 ----------
const params = new URLSearchParams(location.search);
let vid = parseInt(params.get('id') || '0', 10);
let current = null;      // 当前视频详情
let siblings = [];       // 同博主视频（上一个/下一个 / 左侧网格用）
let siblingsLoaded = false;
let _leftMode = 'videos'; // 左侧栏：videos = 当前博主其他视频；bloggers = 博主列表

async function loadVideo(id) {
  vid = id;
  try {
    const v = await fetch(`${API}/video/${id}`).then(x => x.json());
    if (v.error || !v.id) { toast('视频不存在或已删除', 'err'); return; }
    current = v;
    siblings = [];
    siblingsLoaded = false;
    render();
    renderLeft();
    loadSiblings();
  } catch (e) {
    toast('加载失败: ' + e, 'err');
  }
}

function render() {
  const v = current;
  document.title = `${v.name} - DY Sort`;
  const player = document.getElementById('pv-video');
  player.src = v.stream;
  player.play().catch(() => {});
  document.getElementById('pv-name').textContent = v.name;

  const metaStr = v.metadata && Object.keys(v.metadata).length
    ? JSON.stringify(v.metadata).slice(0, 300) : '';
  document.getElementById('pv-meta').innerHTML =
    `博主: <b class="pv-blogger" title="${esc(v.blogger)}">${esc(v.blogger)}</b>`
    + styleTagsHtml(v.blogger_style_labels || (v.blogger_style ? [{ style: v.blogger_style, count: 1 }] : []), v.blogger, 4)
    + bodyTagsHtml(v.blogger_body_labels || (v.blogger_body ? [{ style: v.blogger_body, count: 1 }] : []), v.blogger, 3)
    + `<button class="btn btn-copy" onclick="copyBlogger()">复制</button>`
    + ` ｜ ${fmtSize(v.size)}${metaStr ? ' ｜ ' + esc(metaStr) : ''}`;
  document.getElementById('pv-path').textContent = `路径: ${v.rel}`;
  document.getElementById('pv-badges').innerHTML = v.in_emby
    ? '<span class="badge-emby">已在 Emby</span>' : '';
  const btn = document.getElementById('pv-add');
  btn.disabled = v.in_emby;
  btn.title = '把该视频所属的博主目录整体加入所选 Emby 媒体库';
  btn.textContent = v.in_emby ? '该博主目录已在 Emby' : '加入 Emby';
}

// ---------- 复制博主 ID ----------
async function copyBlogger() {
  if (!current) return;
  const text = current.blogger;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      // http 访问（非安全上下文）下 clipboard API 不可用，退回 execCommand
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    toast(`已复制博主 ID: ${text}`, 'ok');
  } catch (e) {
    toast('复制失败，请手动复制: ' + text, 'err');
  }
}

async function loadSiblings() {
  const blogger = current ? current.blogger : '';
  try {
    const d = await fetch(
      `${API}/videos?blogger=${encodeURIComponent(blogger)}&page=1&page_size=200`
    ).then(x => x.json());
    if (!current || current.blogger !== blogger) return;   // 已切到别的博主，丢弃旧响应
    siblings = d.items || [];
  } catch (e) { /* 忽略 */ }
  siblingsLoaded = true;
  renderNav();
  renderLeft();
}

function renderNav() {
  const idx = siblings.findIndex(x => x.id === vid);
  const pos = document.getElementById('pv-pos');
  document.getElementById('pv-prev').disabled = idx <= 0;
  document.getElementById('pv-next').disabled = idx < 0 || idx >= siblings.length - 1;
  pos.textContent = idx >= 0 ? `${idx + 1} / ${siblings.length}` : '';
}

function step(delta) {
  const idx = siblings.findIndex(x => x.id === vid);
  const next = siblings[idx + delta];
  if (!next) return;
  history.pushState({ id: next.id }, '', `/preview.html?id=${next.id}`);
  loadVideo(next.id);
}

async function addBlogger() {
  if (!current) return;
  if (current.in_emby) { toast('该博主目录已在 Emby'); return; }
  const library = await chooseLibrary();
  if (!library) return;
  try {
    const r = await fetch(`${API}/emby/add`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bloggers: [current.blogger], library_name: library }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    const a = (d.assignments || [])[0];
    const where = a ? a.library : library;
    toast(d.added.length
      ? `已把该视频所属目录「${current.blogger}」加入「${where}」${a && a.created ? '（新建库）' : ''}`
      : '该博主目录已在库中', 'ok');
    current.in_emby = true;
    render();
  } catch (e) { toast('加入 Emby 失败: ' + e, 'err'); }
}

// ---------- 删除（单视频 / 整个博主） ----------
async function delThisVideo() {
  if (!current) return;
  if (!await ensureDeleteEnabled()) return;   // 未开启时弹提示框
  let pre, r;
  try {
    r = await fetch(`${API}/delete/video/${vid}/preview`);
    pre = await r.json();
  } catch (e) { toast('预览失败: ' + e, 'err'); return; }
  if (pre.error) {
    r.status === 403 ? alertDeleteDisabled() : toast(pre.error, 'err');
    return;
  }
  const go = await confirmDelete('video', pre);
  if (!go) return;
  try {
    const r = await fetch(`${API}/video/${vid}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    toast(`已删除「${d.name}」`, 'ok');
    // 回退到浏览页（当前视频已不存在）
    location.href = '/browse.html';
  } catch (e) { toast('删除失败: ' + e, 'err'); }
}

async function delThisBlogger() {
  if (!current) return;
  if (!await ensureDeleteEnabled()) return;   // 未开启时弹提示框
  let pre, r;
  try {
    r = await fetch(`${API}/delete/blogger/${encodeURIComponent(current.blogger)}/preview`);
    pre = await r.json();
  } catch (e) { toast('预览失败: ' + e, 'err'); return; }
  if (pre.error) {
    r.status === 403 ? alertDeleteDisabled() : toast(pre.error, 'err');
    return;
  }
  const go = await confirmDelete('blogger', pre);
  if (!go) return;
  try {
    const r = await fetch(`${API}/blogger/${encodeURIComponent(current.blogger)}`, {
      method: 'DELETE', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true, remove_from_emby: true }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    toast(`已删除博主「${d.blogger}」（${d.videos} 个视频）`, 'ok');
    location.href = '/browse.html';
  } catch (e) { toast('删除失败: ' + e, 'err'); }
}

window.onpopstate = e => {
  const id = e.state && e.state.id;
  if (id) loadVideo(id);
};

// ---------- 左侧博主列表 + 穿衣风格标签 ----------
let _bloggers = [], _labels = [], _bodyLabelList = [], _summary = [];

const STYLE_SOURCE = { vision: '图像识别', 'local-model': '本地模型', local: '关键词规则', library: '来自媒体库名', name: '来自博主名', manual: '人工标注' };

/** 身材标签（第二个维度，样式与风格区分） */
function bodyTagsHtml(labels, blogger, max) {
  const list = (labels || []).filter(x => x && x.style);
  if (!list.length) return '';
  const top = list.slice(0, max || 2);
  const rest = list.length - top.length;
  const html = top.map(x => `<button class="body-tag" data-body="${esc(x.style)}"
    data-blogger="${esc(blogger)}"
    title="身材：${esc(x.style)}，命中 ${x.count} 张（共 ${Math.round((x.ratio || 0) * 100)}%）">${esc(x.style)}${x.count > 1 ? `<em>×${x.count}</em>` : ''}</button>`).join('');
  return html + (rest > 0
    ? `<span class="body-tag more" title="${esc(list.slice(3).map(x => `${x.style}×${x.count}`).join('、'))}">+${rest}</span>`
    : '');
}

/** 多标签：按数量降序，默认最多显示 3 个，多的收成 +N */
function styleTagsHtml(labels, blogger, max) {
  const list = (labels || []).filter(x => x && x.style);
  if (!list.length) {
    return `<button class="style-tag add" data-blogger="${esc(blogger)}"
      title="点击标注穿衣风格">+ 风格</button>`;
  }
  const top = list.slice(0, max || 3);
  const rest = list.length - top.length;
  const html = top.map(x => `<button class="style-tag" data-style="${esc(x.style)}"
    data-blogger="${esc(blogger)}"
    title="${esc(x.style)}：命中 ${x.count} 次（共 ${Math.round((x.ratio || 0) * 100)}%）">${esc(x.style)}${x.count > 1 ? `<em>×${x.count}</em>` : ''}</button>`).join('');
  return html + (rest > 0
    ? `<span class="style-tag more" title="${esc(list.slice(3).map(x => `${x.style}×${x.count}`).join('、'))}">+${rest}</span>`
    : '');
}

async function loadBloggers() {
  try {
    const d = await fetch(`${API}/bloggers?page_size=1000`).then(x => x.json());
    if (d.error) { toast(d.error, 'err'); return; }
    _bloggers = d.items || [];
    _labels = d.labels || [];
    _bodyLabelList = d.body_labels || [];
    _summary = d.summary || [];
    renderLeft();
  } catch (e) { toast('读取博主列表失败: ' + e.message, 'err'); }
}

function renderStyleHint(summary) {
  const labeled = _bloggers.filter(b => b.style).length;
  const parts = (summary || []).slice(0, 6).map(s => `${s.style} ${s.count}`).join(' · ');
  document.getElementById('pvb-hint').textContent =
    `已标注 ${labeled}/${_bloggers.length}${parts ? ' ｜ ' + parts : ''}`;
}

function renderBloggerList() {
  const q = (val('pvb-q') || '').trim().toLowerCase();
  const cur = current ? current.blogger : '';
  const rows = _bloggers.filter(b => !q
    || b.blogger.toLowerCase().includes(q)
    || (b.style || '').toLowerCase().includes(q));
  const box = document.getElementById('pvb-list');
  if (!rows.length) { box.innerHTML = '<div class="pvb-empty">没有匹配的博主</div>'; return; }
  box.innerHTML = rows.map(b => `
    <div class="pvb-item${b.blogger === cur ? ' active' : ''}" data-blogger="${esc(b.blogger)}"
         onclick="openBloggerVideos(this.dataset.blogger)"
         title="点击查看「${esc(b.blogger)}」的全部 ${b.videos} 个视频">
      ${b.poster ? `<img class="pvb-poster" src="${esc(b.poster)}" loading="lazy" onerror="this.remove()">`
                 : '<span class="pvb-poster pvb-ph"></span>'}
      <span class="pvb-name">${esc(b.blogger)}</span>
      <span class="pvb-tags">${(b.labels && b.labels.length) ? styleTagsHtml(b.labels, b.blogger, 2) : ''}${
        (b.body_labels && b.body_labels.length) ? bodyTagsHtml(b.body_labels, b.blogger, 1) : ''}</span>
      <span class="pvb-videos">${b.videos}</span>
    </div>`).join('');
}

// ---------- 左侧栏：当前博主的其他视频（默认）/ 博主列表（可切换） ----------
function renderLeft() {
  const box = document.getElementById('pvb-list');
  if (!box) return;
  const title = document.getElementById('pvb-title');
  const count = document.getElementById('pvb-count');
  const search = document.getElementById('pvb-q');
  const hint = document.getElementById('pvb-hint');
  const toggle = document.getElementById('pvb-toggle');
  if (toggle) toggle.textContent = _leftMode === 'videos' ? '博主列表' : '视频列表';

  if (_leftMode === 'videos') {
    const name = current ? current.blogger : '';
    const others = siblings.filter(x => !current || x.id !== current.id);
    title.textContent = name ? `「${name}」的视频` : '视频';
    count.textContent = others.length ? `${others.length} 个` : '';
    search.style.display = 'none';
    hint.style.display = 'none';
    box.className = 'pvb-list pvb-grid';
    if (!siblingsLoaded) {
      box.innerHTML = '<div class="pvb-empty">加载中…</div>';
    } else if (!others.length) {
      box.innerHTML = '<div class="pvb-empty">该博主没有其他视频</div>';
    } else {
      box.innerHTML = others.map(it => `
        <div class="pvb-video" data-id="${it.id}" title="${esc(it.name)}">
          <div class="pvb-vthumb">
            ${it.poster ? `<img src="${esc(it.poster)}" loading="lazy" onerror="this.remove()">` : ''}
            <span class="pvb-vsize">${fmtSize(it.size)}</span>
          </div>
          <div class="pvb-vname">${esc(it.name)}</div>
        </div>`).join('');
      box.querySelectorAll('.pvb-video').forEach(el => {
        el.onclick = () => playLeftVideo(Number(el.dataset.id));
      });
    }
    return;
  }

  title.textContent = '博主';
  count.textContent = String(_bloggers.length);
  search.style.display = '';
  hint.style.display = '';
  box.className = 'pvb-list';
  renderStyleHint(_summary);
  renderBloggerList();
}

function toggleLeftMode() {
  _leftMode = _leftMode === 'videos' ? 'bloggers' : 'videos';
  renderLeft();
}

/** 左侧视频网格里点一个视频：切过去播，仍在预览页 */
function playLeftVideo(id) {
  if (!id || (current && current.id === id)) return;
  history.pushState({ id }, '', `/preview.html?id=${id}`);
  loadVideo(id);
}

// ---------- 博主视频网格（点博主 → 看他所有视频，点任意一个直接播） ----------
let _bvState = { blogger: '', page: 1, pageSize: 48, total: 0 };

function _bvMask() {
  let mask = document.getElementById('bv-mask');
  if (mask) return mask;
  mask = document.createElement('div');
  mask.id = 'bv-mask';
  mask.className = 'modal-mask';
  mask.style.display = 'none';
  mask.innerHTML = `
    <div class="modal bv-modal">
      <h3 id="bv-title"></h3>
      <div class="bv-grid" id="bv-grid"></div>
      <div class="bv-foot">
        <button class="btn" id="bv-prev">‹ 上一页</button>
        <span class="bv-pos" id="bv-pos"></span>
        <button class="btn" id="bv-next">下一页 ›</button>
        <button class="btn" id="bv-close">关闭</button>
      </div>
    </div>`;
  mask.addEventListener('click', e => { if (e.target === mask) hideBloggerVideos(); });
  document.body.appendChild(mask);   // 先入文档，getElementById 才能取到里面的按钮
  document.getElementById('bv-close').onclick = hideBloggerVideos;
  document.getElementById('bv-prev').onclick = () =>
    loadBloggerVideos(_bvState.blogger, _bvState.page - 1);
  document.getElementById('bv-next').onclick = () =>
    loadBloggerVideos(_bvState.blogger, _bvState.page + 1);
  return mask;
}

function hideBloggerVideos() {
  const m = document.getElementById('bv-mask');
  if (m) m.style.display = 'none';
}

async function openBloggerVideos(blogger) {
  if (!blogger) return;
  _bvMask().style.display = 'flex';
  await loadBloggerVideos(blogger, 1);
}

async function loadBloggerVideos(blogger, page) {
  const size = _bvState.pageSize || 48;
  page = Math.max(1, page || 1);
  const grid = document.getElementById('bv-grid');
  grid.innerHTML = '<div class="bv-empty">加载中…</div>';
  try {
    const d = await fetch(`${API}/videos?blogger=${encodeURIComponent(blogger)}`
      + `&page=${page}&page_size=${size}`).then(x => x.json());
    const items = d.items || [];
    const total = d.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / size));
    _bvState = { blogger, page, pageSize: size, total };
    document.getElementById('bv-title').textContent =
      `「${blogger}」的视频（共 ${total} 个）`;
    grid.innerHTML = items.length ? items.map(it => `
      <div class="bv-item${current && current.id === it.id ? ' active' : ''}"
           data-id="${it.id}" title="${esc(it.name)}">
        <div class="bv-thumb">
          ${it.poster ? `<img src="${esc(it.poster)}" loading="lazy" onerror="this.remove()">` : '无海报'}
          <span class="bv-size">${fmtSize(it.size)}</span>
          ${current && current.id === it.id ? '<span class="bv-playing">播放中</span>' : ''}
        </div>
        <div class="bv-name">${esc(it.name)}</div>
      </div>`).join('') : '<div class="bv-empty">该博主没有视频</div>';
    grid.querySelectorAll('.bv-item').forEach(el => {
      el.onclick = () => playVideo(Number(el.dataset.id));
    });
    document.getElementById('bv-pos').textContent = `${page} / ${totalPages}`;
    document.getElementById('bv-prev').disabled = page <= 1;
    document.getElementById('bv-next').disabled = page >= totalPages;
  } catch (e) {
    grid.innerHTML = `<div class="bv-empty">加载失败：${esc(e.message)}</div>`;
  }
}

/** 在网格里点某个视频：关掉网格、切到该视频（上一个/下一个仍在该博主内） */
function playVideo(id) {
  hideBloggerVideos();
  history.pushState({ id }, '', `/preview.html?id=${id}`);
  loadVideo(id);
}

/** 标签弹窗：风格 + 身材两个维度，都可多选 */
async function editStyle(blogger) {
  if (!blogger) return;
  if (!_labels.length) await loadBloggers();
  const rec = _bloggers.find(b => b.blogger === blogger) || {};
  const chosenStyle = new Set((rec.labels || []).map(x => x.style));
  const chosenBody = new Set((rec.body_labels || []).map(x => x.style));
  const picked = await new Promise(resolve => {
    const mask = _modal();
    document.getElementById('modal-title').textContent = `「${blogger}」的标签（可多选）`;
    const chips = (list, set, kind, cls) => list.map(l => `
      <button type="button" class="${cls}${set.has(l) ? ' cur' : ''}"
              data-kind="${kind}" data-style="${esc(l)}">${esc(l)}</button>`).join('');
    document.getElementById('modal-body').innerHTML =
      `<div class="picker-label">穿衣风格</div>
       <div class="style-picker" data-kind="style">${chips(_labels, chosenStyle, 'style', 'style-pick')}</div>
       <div class="picker-label">身材</div>
       <div class="style-picker" data-kind="body">${chips(_bodyLabelList, chosenBody, 'body', 'style-pick body')}</div>
       <div class="hint" style="margin-top:10px">
         点击切换选中；保存后为人工标注（优先级最高，不会被大模型覆盖）。
         当前来源：${esc(STYLE_SOURCE[rec.source] || '未标注')}${rec.note ? ' · ' + esc(rec.note) : ''}
       </div>
       <div style="margin-top:10px"><button type="button" class="btn" id="style-clear">清除全部标签</button></div>`;
    mask.querySelectorAll('.style-pick').forEach(b => {
      b.onclick = () => {
        const set = b.dataset.kind === 'body' ? chosenBody : chosenStyle;
        const s = b.dataset.style;
        if (set.has(s)) { set.delete(s); b.classList.remove('cur'); }
        else { set.add(s); b.classList.add('cur'); }
      };
    });
    document.getElementById('style-clear').onclick = () => closeModal({ clear: true });
    const ok = document.getElementById('modal-ok');
    ok.style.display = '';
    ok.textContent = '保存';
    ok.onclick = () => closeModal({
      styles: [...chosenStyle], bodies: [...chosenBody] });
    const cancel = document.getElementById('modal-cancel');
    cancel.textContent = '取消';
    cancel.onclick = () => closeModal(null);
    mask.style.display = 'flex';
    _modalResolve = resolve;
  });
  if (!picked) return;
  try {
    const body = { blogger, styles: picked.clear ? [] : picked.styles,
                   bodies: picked.clear ? [] : picked.bodies };
    const r = await fetch(`${API}/styles/set`, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    const row = _bloggers.find(b => b.blogger === blogger);
    if (row) {
      row.labels = d.labels || [];
      row.body_labels = d.body_labels || [];
      row.style = d.style || '';
      row.body = d.body || '';
      row.source = d.cleared ? '' : 'manual';
    }
    renderBloggerList();
    if (current && current.blogger === blogger) {
      current.blogger_style = d.style || '';
      current.blogger_style_labels = d.labels || [];
      current.blogger_body = d.body || '';
      current.blogger_body_labels = d.body_labels || [];
      render();
    }
    if (d.cleared) {
      toast('已清除全部标签', 'ok');
    } else {
      const st = (d.labels || []).map(x => x.style).join('、');
      const bd = (d.body_labels || []).map(x => x.style).join('、');
      toast(`已标注：${st || '（无风格）'}${bd ? ' ｜ ' + bd : ''}`, 'ok');
    }
  } catch (e) { toast('保存失败: ' + e.message, 'err'); }
}

/** 刷新当前视频详情（标签改动后） */
async function refreshCurrent() {
  if (!current) return;
  try {
    const v = await fetch(`${API}/video/${current.id}`).then(x => x.json());
    if (v && v.id) { current = v; render(); }
  } catch { /* 忽略 */ }
}

// 点标签（标题旁 / 列表里）→ 打开标签弹窗（风格与身材都走这里）
document.addEventListener('click', e => {
  const tag = e.target.closest('.style-tag[data-blogger], .body-tag[data-blogger]');
  if (!tag) return;
  e.stopPropagation();
  editStyle(tag.dataset.blogger);
}, true);

// ---------- init ----------
loadVideo(vid);
loadBloggers();
