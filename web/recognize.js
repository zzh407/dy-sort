// ---------- 图像识别页 ----------
let _bloggers = [], _styleLabels = [], _bodyLabels = [];
let _styleSummary = [], _bodySummary = [];
let _chip = '';
let _pollTimer = null, _wasRunning = false;

const TAG_SRC = { vision: '大模型识别', 'local-model': '本地模型', local: '关键词规则', library: 'Emby 库名', name: '博主名', manual: '人工标注' };

/** 风格标签（彩色胶囊，跟在博主名后） */
function tagStyle(x, small) {
  const cls = `style-tag${small ? ' sm' : ''}`;
  const cnt = x.count > 1 ? `<em>×${x.count}</em>` : '';
  return `<button class="${cls}" data-style="${esc(x.style)}"
    title="${esc(x.style)}：命中 ${x.count} 张（${Math.round((x.ratio || 0) * 100)}%）">${esc(x.style)}${cnt}</button>`;
}

/** 身材标签（暖色方角，跟在风格标签后） */
function tagBody(x, small) {
  const cls = `body-tag${small ? ' sm' : ''}`;
  const cnt = x.count > 1 ? `<em>×${x.count}</em>` : '';
  return `<button class="${cls}" data-body="${esc(x.style)}"
    title="身材：${esc(x.style)}，命中 ${x.count} 张（${Math.round((x.ratio || 0) * 100)}%）">${esc(x.style)}${cnt}</button>`;
}

async function loadRecognize() {
  try {
    const d = await fetch(`${API}/bloggers?page_size=1000`).then(x => x.json());
    if (d.error) { toast(d.error, 'err'); return; }
    _bloggers = d.items || [];
    _styleLabels = d.labels || [];
    _bodyLabels = d.body_labels || [];
    _styleSummary = d.summary || [];
    _bodySummary = d.body_summary || [];
    renderStats();
    renderChips();
    renderBloggers();
  } catch (e) { toast('读取博主列表失败: ' + e.message, 'err'); }
}

function renderStats() {
  const total = _bloggers.length;
  const styled = _bloggers.filter(b => b.style).length;
  const bodied = _bloggers.filter(b => b.body).length;
  const box = document.getElementById('rec-stats');
  const item = (num, label, cls) => `<div class="rec-stat ${cls || ''}">
      <span class="rec-stat-num">${num}</span><span class="rec-stat-label">${label}</span></div>`;
  box.innerHTML = item(`${styled}<span class="rec-stat-of">/${total}</span>`, '已标注风格')
    + item(bodied, '已标注身材')
    + item(_styleSummary.length, '风格标签种类')
    + item(_bodySummary.length, '身材标签种类');
}

function renderChips() {
  const box = document.getElementById('rec-chips');
  const chips = [
    { style: '', count: _bloggers.length, label: '全部' },
    ..._styleSummary.slice(0, 8).map(s => ({ style: s.style, count: s.count, label: s.style, kind: 'style' })),
    ..._bodySummary.slice(0, 5).map(s => ({ style: s.style, count: s.count, label: s.style, kind: 'body' })),
  ];
  box.innerHTML = chips.map(c => `<button class="rec-chip${_chip === c.style ? ' cur' : ''}
      ${c.kind === 'body' ? 'body' : ''}" data-chip="${esc(c.style)}">${esc(c.label)}
      <span>${c.count}</span></button>`).join('');
  box.querySelectorAll('.rec-chip').forEach(b => {
    b.onclick = () => { _chip = b.dataset.chip || ''; renderChips(); renderBloggers(); };
  });
}

function renderBloggers() {
  const q = (val('rec-q') || '').trim().toLowerCase();
  const rows = _bloggers.filter(b => {
    if (_chip) {
      const hit = (b.labels || []).some(x => x.style === _chip)
        || (b.body_labels || []).some(x => x.style === _chip);
      if (!hit) return false;
    }
    if (!q) return true;
    if (b.blogger.toLowerCase().includes(q)) return true;
    return (b.labels || []).some(x => x.style.toLowerCase().includes(q))
      || (b.body_labels || []).some(x => x.style.toLowerCase().includes(q));
  });
  const grid = document.getElementById('rec-grid');
  document.getElementById('rec-empty').style.display = rows.length ? 'none' : 'block';
  grid.innerHTML = rows.map(b => {
    const tags = [
      ...(b.labels || []).map(x => tagStyle(x, true)),
      ...(b.body_labels || []).map(x => tagBody(x, true)),
    ].join('');
    return `<div class="rec-card" data-blogger="${esc(b.blogger)}">
      <div class="rec-card-poster">${b.poster
        ? `<img src="${esc(b.poster)}" loading="lazy" onerror="this.remove()">` : '<span>无海报</span>'}</div>
      <div class="rec-card-main">
        <div class="rec-card-title">
          <span class="rec-name" title="${esc(b.blogger)}">${esc(b.blogger)}</span>
          ${tags || '<span class="rec-no-tag">未标注</span>'}
        </div>
        <div class="rec-card-sub">${b.videos} 个视频${b.source ? ' · ' + esc(TAG_SRC[b.source] || b.source) : ''}</div>
      </div>
    </div>`;
  }).join('');
  grid.querySelectorAll('.rec-card').forEach(card => {
    card.onclick = () => openDetail(card.dataset.blogger);
  });
}

// ---------- 博主详情窗 ----------
function _detailMask() {
  let mask = document.getElementById('rec-mask');
  if (mask) return mask;
  mask = document.createElement('div');
  mask.id = 'rec-mask';
  mask.className = 'modal-mask';
  mask.style.display = 'none';
  mask.innerHTML = `
    <div class="modal rec-detail-modal">
      <h3 id="rec-detail-title"></h3>
      <div class="rec-detail" id="rec-detail-body"></div>
      <div class="modal-foot rec-detail-foot">
        <button class="btn primary" id="rec-detail-emby">加入 Emby</button>
        <button class="btn" id="rec-detail-videos">预览视频</button>
        <button class="btn" id="rec-detail-quick">快速识别该博主</button>
        <button class="btn" id="rec-detail-full">全量识别该博主</button>
        <button class="btn" id="rec-detail-close">关闭</button>
      </div>
    </div>`;
  mask.addEventListener('click', e => { if (e.target === mask) mask.style.display = 'none'; });
  document.body.appendChild(mask);   // 必须先入文档，getElementById 才能找到里面的按钮
  document.getElementById('rec-detail-close').onclick = () => { mask.style.display = 'none'; };
  return mask;
}

function _labelRows(list, kind) {
  if (!list || !list.length) return '<div class="rec-label-empty">暂无</div>';
  return list.map(x => {
    const pct = Math.round((x.ratio || 0) * 100);
    const tag = kind === 'body' ? tagBody(x) : tagStyle(x);
    return `<div class="rec-label-row">
        ${tag}
        <span class="rec-label-bar"><i style="width:${pct}%"></i></span>
        <span class="rec-label-num">${x.count} 张 · ${pct}%</span>
      </div>`;
  }).join('');
}

function openDetail(name) {
  const b = _bloggers.find(x => x.blogger === name);
  if (!b) return;
  const mask = _detailMask();
  document.getElementById('rec-detail-title').innerHTML =
    `${esc(b.blogger)}<span class="rec-detail-sub">${b.videos} 个视频
     · 来源：${esc(TAG_SRC[b.source] || '未标注')}</span>`;
  document.getElementById('rec-detail-body').innerHTML = `
    <div class="rec-detail-poster">${b.poster
      ? `<img src="${esc(b.poster)}" onerror="this.remove()">` : '<span>无海报</span>'}</div>
    <div class="rec-detail-labels">
      <div class="rec-group-title">穿衣风格（全部）</div>
      ${_labelRows(b.labels, 'style')}
      <div class="rec-group-title">身材（全部）</div>
      ${_labelRows(b.body_labels, 'body')}
      ${b.note ? `<div class="rec-note">备注：${esc(b.note)}</div>` : ''}
    </div>`;
  document.getElementById('rec-detail-quick').onclick = () => {
    mask.style.display = 'none';
    startRun('quick', [name]);
  };
  document.getElementById('rec-detail-full').onclick = () => {
    mask.style.display = 'none';
    startRun('full', [name]);
  };
  const embyBtn = document.getElementById('rec-detail-emby');
  embyBtn.textContent = b.in_emby ? '已在 Emby' : '加入 Emby';
  embyBtn.disabled = !!b.in_emby;
  embyBtn.title = b.in_emby ? '该博主目录已在 Emby' : '把该博主目录整体加入所选 Emby 媒体库';
  embyBtn.onclick = () => addToEmby(name);
  document.getElementById('rec-detail-videos').onclick = () => openRecVideos(name);
  mask.style.display = 'flex';
}

/** 识别页直接加入 Emby：把该博主目录整体入库 */
async function addToEmby(name) {
  const b = _bloggers.find(x => x.blogger === name);
  if (b && b.in_emby) { toast('该博主目录已在 Emby'); return; }
  const library = await chooseLibrary();
  if (!library) return;
  try {
    const r = await fetch(`${API}/emby/add`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ bloggers: [name], library_name: library }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    const a = (d.assignments || [])[0];
    const where = a ? a.library : library;
    if (d.added && d.added.length) {
      toast(`已把「${name}」加入 Emby「${where}」${a && a.created ? '（新建库）' : ''}`, 'ok');
    } else {
      toast('该博主目录已在库中', 'ok');
    }
    if (b) b.in_emby = true;
    const btn = document.getElementById('rec-detail-emby');
    if (btn) { btn.textContent = '已在 Emby'; btn.disabled = true; }
  } catch (e) { toast('加入 Emby 失败: ' + e.message, 'err'); }
}

// ---------- 博主视频预览弹窗（内嵌播放器，不跳转；关闭后回到该博主详情） ----------
let _rvState = { blogger: '', page: 1, pageSize: 24, total: 0 };

function _rvMask() {
  let mask = document.getElementById('rv-mask');
  if (mask) return mask;
  mask = document.createElement('div');
  mask.id = 'rv-mask';
  mask.className = 'modal-mask';
  mask.style.display = 'none';
  mask.innerHTML = `
    <div class="modal rec-video-modal">
      <h3 id="rv-title"></h3>
      <div class="rv-player"><video id="rv-video" controls playsinline></video></div>
      <div class="rv-hint">点下方任意视频即可在上方播放；关闭后回到该博主详情，不会离开当前页。</div>
      <div class="rv-grid" id="rv-grid"></div>
      <div class="rv-foot">
        <button class="btn" id="rv-prev">‹ 上一页</button>
        <span class="rv-pos" id="rv-pos"></span>
        <button class="btn" id="rv-next">下一页 ›</button>
        <button class="btn" id="rv-close">关闭</button>
      </div>
    </div>`;
  mask.addEventListener('click', e => { if (e.target === mask) closeRecVideos(); });
  document.body.appendChild(mask);   // 先入文档，getElementById 才能取到里面按钮
  document.getElementById('rv-close').onclick = closeRecVideos;
  document.getElementById('rv-prev').onclick = () =>
    loadRecVideos(_rvState.blogger, _rvState.page - 1);
  document.getElementById('rv-next').onclick = () =>
    loadRecVideos(_rvState.blogger, _rvState.page + 1);
  return mask;
}

function closeRecVideos() {
  const m = document.getElementById('rv-mask');
  if (m) m.style.display = 'none';
  const v = document.getElementById('rv-video');
  if (v) { v.pause(); v.removeAttribute('src'); v.load(); }
  const detail = document.getElementById('rec-mask');
  if (detail) detail.style.display = 'flex';   // 回到原博主详情，不跳页
}

async function openRecVideos(blogger) {
  if (!blogger) return;
  const detail = document.getElementById('rec-mask');
  if (detail) detail.style.display = 'none';
  _rvMask().style.display = 'flex';
  await loadRecVideos(blogger, 1);
}

async function loadRecVideos(blogger, page) {
  const size = _rvState.pageSize || 24;
  page = Math.max(1, page || 1);
  const grid = document.getElementById('rv-grid');
  grid.innerHTML = '<div class="rv-empty">加载中…</div>';
  try {
    const d = await fetch(`${API}/videos?blogger=${encodeURIComponent(blogger)}`
      + `&page=${page}&page_size=${size}`).then(x => x.json());
    const items = d.items || [];
    const total = d.total || 0;
    const totalPages = Math.max(1, Math.ceil(total / size));
    _rvState = { blogger, page, pageSize: size, total };
    document.getElementById('rv-title').textContent =
      `「${blogger}」的视频（共 ${total} 个）`;
    grid.innerHTML = items.length ? items.map(it => `
      <div class="rv-item" data-id="${it.id}" title="${esc(it.name)}">
        <div class="rv-thumb">
          ${it.poster ? `<img src="${esc(it.poster)}" loading="lazy" onerror="this.remove()">` : '无海报'}
          <span class="rv-size">${fmtSize(it.size)}</span>
        </div>
        <div class="rv-name">${esc(it.name)}</div>
      </div>`).join('') : '<div class="rv-empty">该博主没有视频</div>';
    grid.querySelectorAll('.rv-item').forEach(el => {
      el.onclick = () => playRecVideo(el);
    });
    document.getElementById('rv-pos').textContent = `${page} / ${totalPages}`;
    document.getElementById('rv-prev').disabled = page <= 1;
    document.getElementById('rv-next').disabled = page >= totalPages;
    grid.scrollTop = 0;
  } catch (e) {
    grid.innerHTML = `<div class="rv-empty">加载失败：${esc(e.message)}</div>`;
  }
}

function playRecVideo(el) {
  const id = Number(el.dataset.id);
  if (!id) return;
  const player = document.getElementById('rv-video');
  player.src = `${API}/video/${id}/stream`;
  player.play().catch(() => {});
  document.querySelectorAll('#rv-grid .rv-item').forEach(x =>
    x.classList.toggle('cur', x === el));
}

// ---------- 识别任务 ----------
async function startRun(mode, bloggers) {
  if (mode === 'full') {
    const scope = bloggers && bloggers.length
      ? `「${bloggers[0]}」的全部视频` : '所有博主的所有视频';
    const ok = await confirmAction('全量识别确认',
      `将对${scope}<b>逐张</b>识别（每个视频一次请求），耗时和费用都可能很大，`
      + '过程中可随时点「停止」。确定开始？', '开始全量识别');
    if (!ok) return;
  }
  const body = { mode, force: mode === 'full' };
  if (bloggers && bloggers.length) body.bloggers = bloggers;
  try {
    const r = await fetch(`${API}/styles/run`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); return; }
    toast(`已开始${d.mode === 'full' ? '全量' : '快速'}识别：${d.total} 个博主`
      + (d.mode === 'full' ? '（全部视频）' : `（每人 ${d.sample} 个）`), 'ok');
    pollState();
  } catch (e) { toast('启动失败: ' + e.message, 'err'); }
}

async function stopRun() {
  try {
    const r = await fetch(`${API}/styles/stop`, { method: 'POST' });
    const d = await r.json();
    toast(d.stopped ? '已请求停止，当前批次跑完即结束' : (d.error || '没有任务'),
      d.stopped ? 'ok' : 'err');
  } catch (e) { toast('停止失败: ' + e.message, 'err'); }
}

function _setRunning(ui) {
  document.getElementById('rec-quick').disabled = ui;
  document.getElementById('rec-full').disabled = ui;
  document.getElementById('rec-stop').disabled = !ui;
}

async function pollState(initial) {
  clearTimeout(_pollTimer);
  let s;
  try { s = await fetch(`${API}/styles/state`).then(x => x.json()); }
  catch (e) { _pollTimer = setTimeout(() => pollState(initial), 3000); return; }

  const bar = document.getElementById('rec-bar-in');
  const main = document.getElementById('rec-progress-main');
  const sub = document.getElementById('rec-progress-sub');
  const tip = document.getElementById('rec-tip');

  if (s.running) {
    _wasRunning = true;
    _setRunning(true);
    const dTotal = s.download_total || 0;
    if (dTotal && (s.images_done || 0) === 0 && (s.download_done || 0) < dTotal) {
      const p = Math.min(100, (s.download_done / dTotal) * 100);
      bar.style.width = p.toFixed(1) + '%';
      main.textContent = `${s.download_text || '下载本地模型'}（${p.toFixed(0)}%）`;
      sub.textContent = '首次使用本地模型需下载一次，之后离线可用 · 可随时停止';
    } else {
      const pct = s.images_total ? Math.min(100, (s.images_done / s.images_total) * 100) : 0;
      bar.style.width = pct.toFixed(1) + '%';
      main.textContent = `${s.mode === 'full' ? '全量识别' : '快速识别'}进行中`
        + ` · 博主 ${s.done}/${s.total} · 图片 ${s.images_done}/${s.images_total}`
        + `（${pct.toFixed(0)}%）`;
      sub.textContent = `成功 ${s.ok} · 失败 ${s.failed}`
        + (s.skipped ? ` · 未命中关键词 ${s.skipped}` : '')
        + (s.skipped_blank ? ` · 跳过无内容图 ${s.skipped_blank} 张` : '')
        + (s.current ? ` · 当前：${s.current}` : '');
    }
    _pollTimer = setTimeout(() => pollState(initial), 1500);
    return;
  }

  _setRunning(false);
  if (s.done) {
    bar.style.width = '100%';
    main.textContent = `${s.mode === 'full' ? '全量识别' : '快速识别'}已结束`
      + ` · 博主 ${s.done}/${s.total} · 成功 ${s.ok} · 失败 ${s.failed}`
      + (s.error ? `（${s.error}）` : '');
    sub.textContent = `图片 ${s.images_done}/${s.images_total}`
      + (s.skipped ? ` · 未命中关键词 ${s.skipped}` : '')
      + (s.skipped_blank ? ` · 跳过无内容图 ${s.skipped_blank} 张` : '');
  } else {
    bar.style.width = '0%';
    main.textContent = s.error ? `上次任务：${s.error}` : '空闲中';
    sub.textContent = '';
  }
  let tipText;
  if (!s.configured) {
    tipText = s.engine === 'llm'
      ? '大模型未就绪：到「设置 → 图像识别」填智谱 API Key（默认 glm-4v-flash 免费），或切回本地模型'
      : '当前引擎未配置：先到「设置 → 图像识别」检查配置，或切回本地模型';
  } else if (s.engine === 'llm') {
    tipText = `引擎：大模型（${s.model || '未设置'}） · 采样 ${s.min_sample} 张起`;
  } else if (s.engine === 'rules') {
    tipText = '引擎：关键词规则（不读图片、零成本；只按库名/博主名/视频名里的关键词打标）';
  } else {
    tipText = `引擎：本地模型 CLIP（${s.local_model_variant || 'quantized'}） · `
      + (s.local_model_ready ? '已就绪，离线推理' : '首次识别会自动下载约 155MB')
      + ` · 采样 ${s.min_sample} 张起`;
  }
  tip.textContent = tipText;

  // 关键词规则不读视频，全量识别没有意义
  const rulesOnly = s.engine === 'rules';
  const fullBtn = document.getElementById('rec-full');
  fullBtn.disabled = rulesOnly;
  fullBtn.title = rulesOnly ? '关键词规则不读视频；全量识别需要本地模型或大模型' : '';

  if (_wasRunning && !initial) {
    _wasRunning = false;
    toast(`识别结束：成功 ${s.ok}，失败 ${s.failed}`
      + (s.skipped ? `，未命中关键词 ${s.skipped}` : '')
      + (s.skipped_blank ? `，跳过无内容 ${s.skipped_blank} 张` : ''), 'ok');
    await loadRecognize();
  }
}

// ---------- init ----------
loadRecognize().then(() => pollState(true));
