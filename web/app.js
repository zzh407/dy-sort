// ---------- 共享工具（各页面共用） ----------
const API = '/api';
let items = [], bloggersList = [], selected = new Set(),
    page = 1, pageSize = 60, totalPages = 1;

function toast(msg, cls = '') {
  const t = document.getElementById('toast');
  t.textContent = msg; t.className = cls; t.style.display = 'block';
  clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', 4000);
}
function debounce(fn, ms) { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtSize(n) { if (!n) return ''; const u = ['B','KB','MB','GB']; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return n.toFixed(1) + u[i]; }
function val(id) { return document.getElementById(id).value; }

// ---------- 通用弹窗：手动选择 Emby 媒体库 ----------
let _modalResolve = null;

function _modal() {
  let mask = document.getElementById('modal-mask');
  if (mask) return mask;
  mask = document.createElement('div');
  mask.id = 'modal-mask';
  mask.className = 'modal-mask';
  mask.style.display = 'none';
  mask.innerHTML = `
    <div class="modal">
      <h3 id="modal-title"></h3>
      <div id="modal-body"></div>
      <div class="modal-foot">
        <button class="btn" id="modal-cancel">取消</button>
        <button class="btn primary" id="modal-ok">确定</button>
      </div>
    </div>`;
  mask.addEventListener('click', e => { if (e.target === mask) closeModal(null); });
  document.body.appendChild(mask);
  return mask;
}

function closeModal(val) {
  const mask = document.getElementById('modal-mask');
  if (mask) mask.style.display = 'none';
  const r = _modalResolve;
  _modalResolve = null;
  if (r) r(val);
}

/** 弹出媒体库选择框；resolve 选中的库名，取消/出错为 null。 */
function chooseLibrary() {
  return new Promise(async resolve => {
    let libs;
    try {
      const d = await fetch(`${API}/emby/libraries`).then(x => x.json());
      if (d.error) { toast(d.error, 'err'); return resolve(null); }
      libs = d.libraries || [];
    } catch (e) { toast('读取 Emby 媒体库失败: ' + e, 'err'); return resolve(null); }
    if (!libs.length) { toast('未找到可操作的 Emby 媒体库', 'err'); return resolve(null); }

    const mask = _modal();
    document.getElementById('modal-title').textContent = '选择要加入的 Emby 媒体库';
    document.getElementById('modal-body').innerHTML =
      `<div class="modal-list">${libs.map((l, i) => `
        <label class="modal-item">
          <input type="radio" name="mlib" value="${esc(l.name)}"${i ? '' : ' checked'}>
          <span class="modal-name">${esc(l.name)}</span>
          <span class="modal-sub">${(l.paths || []).length} 个目录</span>
        </label>`).join('')}</div>`;
    document.getElementById('modal-ok').onclick = () => {
      const sel = mask.querySelector('input[name=mlib]:checked');
      closeModal(sel ? sel.value : null);
    };
    document.getElementById('modal-cancel').onclick = () => closeModal(null);
    mask.style.display = 'flex';
    _modalResolve = resolve;
  });
}

// ---------- Emby 媒体库概览（首页） ----------
const OV_COLLAPSE = 20;   // 每个库默认展示的目录行数

async function loadOverview() {
  const sec = document.getElementById('emby-overview');
  const body = document.getElementById('ov-body');
  try {
    const d = await fetch(`${API}/emby/overview`).then(x => x.json());
    if (d.error || !d.libraries.length) {
      sec.style.display = 'none';
      document.getElementById('home-empty').style.display = 'block';
      return;
    }
    const totalDirs = d.libraries.reduce((s, l) => s + l.dirs.length, 0);
    const totalIndexed = d.libraries.reduce(
      (s, l) => s + l.dirs.filter(x => x.videos != null).length, 0);
    document.getElementById('ov-root').textContent =
      `本地根 ${d.media_root} ｜ ${d.libraries.length} 个库 / ${totalDirs} 个目录 / ${totalIndexed} 个已索引`;
    body.innerHTML = d.libraries.map(lib => {
      const rows = lib.dirs.map((x, i) => `
        <div class="ov-dir"${i >= OV_COLLAPSE ? ' style="display:none"' : ''}
             onclick="location.href='/browse.html?blogger=${encodeURIComponent(x.blogger)}&mapped=1'"
             title="点击浏览该博主的视频（已映射目录）">
          <span class="ov-blogger" title="${esc(x.emby_path)}">${esc(x.blogger)}</span>
          <span class="ov-arrow">→</span>
          <code class="ov-local" title="${esc(x.local_path)}">${esc(x.local_path)}</code>
          <span class="ov-count ${x.videos == null ? 'warn' : 'ok'}">${x.videos == null ? '未索引' : x.videos + ' 视频'}</span>
          <button class="btn ov-remove" data-remove="${esc(x.blogger)}"
                  title="把该目录从 Emby 媒体库移除（不影响本地文件，之后可重新加入）">移出</button>
        </div>`).join('');
      const hidden = lib.dirs.length - OV_COLLAPSE;
      return `
        <div class="ov-lib">
          <div class="ov-lib-head">
            <span class="ov-lib-name">${esc(lib.name)}</span>
            <span class="ov-lib-count">${lib.dirs.length} 个目录</span>
          </div>
          <div class="ov-dirs">${rows || '<div class="ov-dir ov-empty">（空库）</div>'}</div>
          ${hidden > 0 ? `<button class="btn ov-more" data-open="0" onclick="toggleOvMore(this)">展开其余 ${hidden} 个目录</button>` : ''}
        </div>`;
    }).join('');
    sec.style.display = 'block';
    document.getElementById('home-empty').style.display = 'none';
  } catch (e) {
    console.error(e);
    sec.style.display = 'none';
    const box = document.getElementById('home-empty');
    box.style.display = 'block';
    toast('加载映射信息失败: ' + e.message, 'err');
  }
}

function toggleOvMore(btn) {
  const box = btn.closest('.ov-lib').querySelector('.ov-dirs');
  const opening = btn.dataset.open !== '1';
  const total = box.querySelectorAll('.ov-dir').length;
  btn.dataset.open = opening ? '1' : '';
  box.querySelectorAll('.ov-dir').forEach((el, i) => {
    el.style.display = (opening || i < OV_COLLAPSE) ? '' : 'none';
  });
  btn.textContent = opening ? '收起' : `展开其余 ${total - OV_COLLAPSE} 个目录`;
}

/** 把博主目录从 Emby 媒体库移除（只删库路径条目，本地文件不受影响，可重新加入） */
async function removeFromEmby(btn, blogger) {
  if (!blogger) return;
  if (!window.confirm(`将把目录「${blogger}」从 Emby 媒体库移除。\n`
      + '本地文件不会被删除，之后仍可重新加入。确定继续？')) return;
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/emby/remove`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ blogger, confirm: true }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); btn.disabled = false; return; }
    toast(`已把「${d.removed}」移出 Emby 媒体库`, 'ok');
    await loadOverview();   // 刷新列表（该行已不在库中，按钮随之消失）
  } catch (e) { toast('移出失败: ' + e, 'err'); btn.disabled = false; }
}

// 捕获阶段委托：绑定映射行内「移出」按钮。
// 必须用 capture（先于行的 onclick 跳转触发）并阻断冒泡，否则点击会先跳转浏览页。
document.addEventListener('click', e => {
  const btn = e.target.closest('.ov-remove');
  if (btn) {
    e.stopPropagation();
    removeFromEmby(btn, btn.dataset.remove);
  }
}, true);

// ---------- 通用弹窗：删除确认（严格区分粒度与边界说明） ----------
let _delResolve = null;

function _delModal() {
  let mask = document.getElementById('del-mask');
  if (mask) return mask;
  mask = document.createElement('div');
  mask.id = 'del-mask';
  mask.className = 'modal-mask';
  mask.style.display = 'none';
  document.body.appendChild(mask);
  return mask;
}

/**
 * 删除确认弹窗。resolve 执行结果对象或 null（取消）。
 * mode: 'video' 单视频（需勾选确认）| 'blogger' 整个博主（需输入博主名确认）
 */
function confirmDelete(mode, info) {
  return new Promise(resolve => {
    const mask = _delModal();
    const isBlogger = mode === 'blogger';
    const target = isBlogger ? info.blogger : info.name;
    const sizeTxt = info.size ? fmtSize(info.size) : (info.bytes ? fmtSize(info.bytes) : '');
    const sizeLine = sizeTxt ? `<div class="del-row"><span>大小</span><b>${sizeTxt}</b></div>` : '';
    const videoLine = isBlogger
      ? `<div class="del-row"><span>将删除</span><b class="del-danger">${info.videos} 个视频</b></div>` : '';
    const embyLine = isBlogger && info.in_emby
      ? `<div class="del-row warn"><span>Emby</span><b>将从媒体库移除该目录</b></div>` : '';

    mask.innerHTML = `
      <div class="modal del-modal">
        <h3>${isBlogger ? '删除整个博主' : '删除单个视频'}</h3>
        <div class="del-target">${esc(target)}</div>
        <div class="del-info">
          ${videoLine}
          ${sizeLine}
          <div class="del-row"><span>磁盘路径</span><code>${esc(info.path || '')}</code></div>
          ${embyLine}
        </div>
        <div class="del-note">
          ${isBlogger
            ? '此操作将永久删除磁盘上的整个博主目录（含全部视频、海报、元数据），不可恢复。'
            : '此操作将永久删除该视频目录（含海报、元数据），不可恢复。'}
          ${isBlogger ? '请输入博主名以确认：' : '请勾选下方确认框：'}
        </div>
        ${isBlogger
          ? `<input class="del-input" id="del-input" autocomplete="off"
                   placeholder="输入 ${esc(target)} 以解锁删除">`
          : `<label class="del-check"><input type="checkbox" id="del-check">
             我确认删除该视频，并了解不可恢复</label>`}
        <div class="modal-foot">
          <button class="btn" id="del-cancel">取消</button>
          <button class="btn btn-danger" id="del-ok" disabled>
            ${isBlogger ? '永久删除博主' : '永久删除视频'}</button>
          </div>
      </div>`;

    const okBtn = mask.querySelector('#del-ok');
    const cancelBtn = mask.querySelector('#del-cancel');
    const gate = isBlogger
      ? mask.querySelector('#del-input')
      : mask.querySelector('#del-check');
    const sync = () => {
      okBtn.disabled = isBlogger
        ? (gate.value.trim() !== target)
        : !gate.checked;
    };
    gate.addEventListener(isBlogger ? 'input' : 'change', sync);
    if (isBlogger) setTimeout(() => gate.focus(), 50);

    okBtn.onclick = () => { mask.style.display = 'none'; resolve(true); };
    cancelBtn.onclick = () => { mask.style.display = 'none'; resolve(null); };
    mask.onclick = e => { if (e.target === mask) { mask.style.display = 'none'; resolve(null); } };
  });
}

// ---------- 删除功能开关门控 ----------
let _delEnabled = null;   // null=尚未查询；true/false=已确认

/** 页面加载时查询开关：关闭则给 body 加 del-hidden，隐藏全部删除按钮。
 *  查询失败时保守不隐藏，点击删除时仍会实时检查并有 403 兜底。 */
async function applyDeleteGate() {
  try {
    const d = await fetch(`${API}/settings`).then(x => x.json());
    _delEnabled = String(d.enable_delete || '').trim().toLowerCase() === 'true';
  } catch (e) { return; }
  document.body.classList.toggle('del-hidden', !_delEnabled);
}

/** 删除动作最终检查：已确认开启则直接放行；未确认/关闭时重新查询，
 *  关闭时弹提示框（可跳设置页）并同步按钮可见性。 */
async function ensureDeleteEnabled() {
  if (_delEnabled !== true) {
    try {
      const d = await fetch(`${API}/settings`).then(x => x.json());
      _delEnabled = String(d.enable_delete || '').trim().toLowerCase() === 'true';
    } catch (e) { _delEnabled = false; }
    document.body.classList.toggle('del-hidden', !_delEnabled);
    if (!_delEnabled) { alertDeleteDisabled(); return false; }
  }
  return true;
}

/** 未启用删除功能的模态提示：说明原因 + 前往设置按钮 */
function alertDeleteDisabled() {
  const mask = _delModal();
  mask.innerHTML = `
    <div class="modal del-modal">
      <h3>删除功能未开启</h3>
      <div class="del-note" style="margin-top:2px">
        为防止误删，删除功能默认关闭。请到「设置」页勾选
        <b>「启用删除功能」</b>并保存后再试。
      </div>
      <div class="modal-foot">
        <button class="btn" id="del-cancel">知道了</button>
        <button class="btn primary" id="del-ok">前往设置</button>
      </div>
    </div>`;
  mask.style.display = 'flex';
  mask.querySelector('#del-cancel').onclick = () => { mask.style.display = 'none'; };
  mask.querySelector('#del-ok').onclick = () => {
    mask.style.display = 'none';
    location.href = '/settings.html';
  };
  mask.onclick = e => { if (e.target === mask) mask.style.display = 'none'; };
}

// ---------- init（首页执行概览；所有页面统一做删除开关门控） ----------
if (document.getElementById('emby-overview')) {
  loadOverview();
}
applyDeleteGate();
