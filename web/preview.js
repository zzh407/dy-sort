// ---------- 视频预览页 ----------
const params = new URLSearchParams(location.search);
let vid = parseInt(params.get('id') || '0', 10);
let current = null;      // 当前视频详情
let siblings = [];       // 同博主视频（上一个/下一个用）

async function loadVideo(id) {
  vid = id;
  try {
    const v = await fetch(`${API}/video/${id}`).then(x => x.json());
    if (v.error || !v.id) { toast('视频不存在或已删除', 'err'); return; }
    current = v;
    render();
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
  try {
    const d = await fetch(
      `${API}/videos?blogger=${encodeURIComponent(current.blogger)}&page=1&page_size=200`
    ).then(x => x.json());
    siblings = d.items || [];
    renderNav();
  } catch (e) { /* 忽略 */ }
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
    toast(d.added.length
      ? `已把该视频所属目录「${current.blogger}」加入「${library}」`
      : '该博主目录已在库中', 'ok');
    current.in_emby = true;
    render();
  } catch (e) { toast('加入 Emby 失败: ' + e, 'err'); }
}

window.onpopstate = e => {
  const id = e.state && e.state.id;
  if (id) loadVideo(id);
};

loadVideo(vid);
