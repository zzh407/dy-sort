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
    document.getElementById('modal-title').textContent = '选择要加入的 Emby 媒体库（点击直接选择）';
    document.getElementById('modal-body').innerHTML =
      `<div class="modal-grid">${libs.map((l, i) => `
        <button type="button" class="modal-item" data-lib="${esc(l.name)}">
          <span class="modal-idx">${i + 1}</span>
          <span class="modal-name">${esc(l.name)}${l.over ? ' ⚠' : ''}</span>
          <span class="modal-sub">${(l.paths || []).length} 个目录 · ${l.videos == null ? '视频数未知' : `${l.videos}/${l.limit} 视频`}</span>
        </button>`).join('')}</div>
      <div class="hint" style="margin-top:10px">开启自动管理时，超过 ${libs[0].limit} 个视频的库不再追加：先填同族还有空位的序号库，都没有空位才新建；目录不会被拆分。</div>`;
    mask.querySelectorAll('.modal-item').forEach(b => {
      b.onclick = () => closeModal(b.dataset.lib);
    });
    document.getElementById('modal-ok').onclick = () => closeModal(null);
    document.getElementById('modal-ok').style.display = 'none';
    document.getElementById('modal-cancel').onclick = () => closeModal(null);
    mask.style.display = 'flex';
    _modalResolve = resolve;
  });
}

// ---------- Emby 媒体库概览（首页） ----------
const OV_COLLAPSE = 20;   // 每个库默认展示的目录行数
const OV_HIDE_KEY = 'dysort-hide-names';
let _ovData = null;                                   // 最近一次概览数据（切换隐藏时复用）
let ovHidden = localStorage.getItem(OV_HIDE_KEY) === '1';

/** 名称是否打码（眼睛按钮控制，状态存 localStorage） */
function ovMask(s) { return ovHidden ? '••••' : s; }
/** 路径只遮挡最后一段（那一段就是博主目录名） */
function ovMaskPath(p) {
  const s = String(p || '');
  if (!ovHidden) return s;
  const i = s.lastIndexOf('/');
  return i < 0 ? '••••' : s.slice(0, i + 1) + '••••';
}

async function loadOverview() {
  const sec = document.getElementById('emby-overview');
  try {
    const d = await fetch(`${API}/emby/overview`).then(x => x.json());
    if (d.error || !d.libraries.length) {
      sec.style.display = 'none';
      document.getElementById('home-empty').style.display = 'block';
      return;
    }
    _ovData = d;
    renderOverview();
    sec.style.display = 'block';
    document.getElementById('home-empty').style.display = 'none';
  } catch (e) {
    console.error(e);
    sec.style.display = 'none';
    document.getElementById('home-empty').style.display = 'block';
    toast('加载映射信息失败: ' + e.message, 'err');
  }
}

function renderOverview() {
  const d = _ovData;
  if (!d) return;
  const body = document.getElementById('ov-body');
  const totalDirs = d.libraries.reduce((s, l) => s + l.dirs.length, 0);
  const totalIndexed = d.libraries.reduce(
    (s, l) => s + l.dirs.filter(x => x.videos != null).length, 0);
  const overCount = d.libraries.filter(l => l.over).length;
  document.getElementById('ov-root').textContent =
    `本地根 ${ovMaskPath(d.media_root)} ｜ ${d.libraries.length} 个库 / ${totalDirs} 个目录 / ${totalIndexed} 个已索引`
    + ` ｜ 单库上限 ${d.limit} 视频${d.auto ? '' : '（自动管理已关闭）'}`
    + (overCount ? ` ｜ ${overCount} 个库超限` : '');
  body.innerHTML = d.libraries.map(lib => {
    const rows = lib.dirs.map((x, i) => `
      <div class="ov-dir${ovHidden ? ' ov-dir-masked' : ''}"${i >= OV_COLLAPSE ? ' style="display:none"' : ''}
           ${ovHidden ? '' : `onclick="location.href='/browse.html?blogger=${encodeURIComponent(x.blogger)}&mapped=1'"`}
           title="${ovHidden ? '名称已隐藏（点右上角眼睛恢复）' : '点击浏览该博主的视频（已映射目录）'}">
        <span class="ov-blogger" title="${ovHidden ? '' : esc(x.emby_path)}">${esc(ovMask(x.blogger))}</span>
        <span class="ov-arrow">→</span>
        <code class="ov-local" title="${ovHidden ? '' : esc(x.local_path)}">${esc(ovMaskPath(x.local_path))}</code>
        <span class="ov-count ${x.videos == null ? 'warn' : 'ok'}">${x.videos == null ? '未索引' : x.videos + ' 视频'}</span>
        <button class="btn ov-move" data-move="${esc(x.blogger)}" data-lib="${esc(lib.name)}"
                title="把该目录整体移动到另一个媒体库（目录不会被拆分）">移动</button>
        <button class="btn ov-remove" data-remove="${esc(x.blogger)}"
                title="把该目录从 Emby 媒体库移除（不影响本地文件，之后可重新加入）">移出</button>
      </div>`).join('');
    const hidden = lib.dirs.length - OV_COLLAPSE;
    const cap = lib.videos == null ? '视频数未知' : `${lib.videos} / ${lib.limit} 视频`;
    const near = lib.videos != null && !lib.over && lib.videos > lib.limit * 0.9;
    return `
      <div class="ov-lib">
        <div class="ov-lib-head">
          <span class="ov-lib-name">${esc(ovMask(lib.name))}</span>
          ${lib.over ? '<span class="ov-over">超出上限</span>' : ''}
          <span class="ov-lib-count${near ? ' near' : ''}">${lib.dirs.length} 个目录 · ${cap}</span>
          ${lib.over ? `<button class="btn ov-fix" data-fix="${esc(lib.name)}"
            title="把该库放不下的目录整体迁到同族还有空位的库，都没有空位才新建">整理</button>` : ''}
        </div>
        <div class="ov-dirs">${rows || '<div class="ov-dir ov-empty">（空库）</div>'}</div>
        ${hidden > 0 ? `<button class="btn ov-more" data-open="0" onclick="toggleOvMore(this)">展开其余 ${hidden} 个目录</button>` : ''}
      </div>`;
  }).join('');
}

/** 眼睛按钮：隐藏 / 显示媒体库名与目录名（不重新请求，直接重绘） */
function toggleOvHide() {
  ovHidden = !ovHidden;
  localStorage.setItem(OV_HIDE_KEY, ovHidden ? '1' : '0');
  applyEyeState();
  renderOverview();
}

function applyEyeState() {
  const btn = document.getElementById('ov-eye');
  if (!btn) return;
  btn.dataset.hidden = ovHidden ? '1' : '';
  btn.title = ovHidden ? '显示媒体库名与目录名' : '隐藏媒体库名与目录名';
  btn.setAttribute('aria-label', btn.title);
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
  const go = await confirmAction('移出 Emby 媒体库',
    `将把目录「${esc(blogger)}」从 Emby 媒体库移除。<br>` +
    '本地文件不会被删除，之后仍可重新加入。确定继续？',
    '移出', true);
  if (!go) return;
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

/** 选择移动目标：已有库 或 新建同家族序号库。resolve {to} / {create:true} / null。 */
function pickMoveTarget(blogger, fromLib) {
  return new Promise(async resolve => {
    let d;
    try { d = await fetch(`${API}/emby/libraries`).then(x => x.json()); }
    catch (e) { toast('读取 Emby 媒体库失败: ' + e, 'err'); return resolve(null); }
    if (d.error) { toast(d.error, 'err'); return resolve(null); }
    const others = (d.libraries || []).filter(l => l.name !== fromLib);
    const mask = _modal();
    document.getElementById('modal-title').textContent = `把「${blogger}」整体移动到…`;
    document.getElementById('modal-body').innerHTML =
      `<div class="modal-grid">
         <button type="button" class="modal-item" data-new="1">
           <span class="modal-idx">＋</span>
           <span class="modal-name">新建同名序号库</span>
           <span class="modal-sub">取下一个序号（如 ${esc(fromLib)}2）</span>
         </button>
         ${others.map((l, i) => `
         <button type="button" class="modal-item" data-lib="${esc(l.name)}">
           <span class="modal-idx">${i + 1}</span>
           <span class="modal-name">${esc(l.name)}${l.over ? ' ⚠' : ''}</span>
           <span class="modal-sub">${(l.paths || []).length} 个目录 · ${l.videos == null ? '视频数未知' : `${l.videos}/${l.limit} 视频`}</span>
         </button>`).join('')}
       </div>
       <div class="hint" style="margin-top:10px">目录整体移动（Emby 会重新扫描该目录），同一个博主的视频不会被拆到两个库。</div>`;
    mask.querySelectorAll('.modal-item').forEach(b => {
      b.onclick = () => closeModal(b.dataset.new ? { create: true } : { to: b.dataset.lib });
    });
    const ok = document.getElementById('modal-ok');
    ok.style.display = 'none';
    ok.onclick = () => closeModal(null);
    document.getElementById('modal-cancel').onclick = () => closeModal(null);
    mask.style.display = 'flex';
    _modalResolve = resolve;
  });
}

/** 把单个目录整体移动到另一个媒体库（或新建序号库） */
async function moveToLibrary(btn) {
  const blogger = btn.dataset.move, fromLib = btn.dataset.lib;
  if (!blogger) return;
  const pick = await pickMoveTarget(blogger, fromLib);
  if (!pick) return;
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/emby/move`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ blogger, to_library: pick.to || null,
                             create_new: !!pick.create }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); btn.disabled = false; return; }
    toast(`已把「${d.moved}」从「${d.from}」移到「${d.to}」${d.created ? '（新建库）' : ''}`, 'ok');
    await loadOverview();
  } catch (e) { toast('移动失败: ' + e, 'err'); btn.disabled = false; }
}

/** 整理超限库：预览 -> 确认 -> 执行（放不下的目录整体迁到新建序号库）。
 *  libName 为空 = 一键整理全部超限库；指定则只整理该库。 */
async function openRebalance(libName) {
  const scope = libName ? `?libraries=${encodeURIComponent(libName)}` : '';
  let d;
  try { d = await fetch(`${API}/emby/rebalance/preview${scope}`).then(x => x.json()); }
  catch (e) { return toast('读取整理方案失败: ' + e, 'err'); }
  if (d.error) return toast(d.error, 'err');
  const moves = d.moves || [];
  if (!moves.length) {
    return toast((d.warnings && d.warnings[0])
      || (libName ? `「${libName}」没有可整理的目录（未超限或目录不可拆）`
                  : '没有需要整理的媒体库（都在上限内）'), 'ok');
  }
  const shown = moves.slice(0, 40);
  const rows = shown.map(m => `<div class="del-row"><span>${esc(m.from)} → ${esc(m.to)}</span>`
    + `<b>${esc(m.blogger)}（${m.videos} 视频）</b></div>`).join('');
  const more = moves.length > shown.length
    ? `<div class="del-row"><span>…</span><b>另有 ${moves.length - shown.length} 个目录</b></div>` : '';
  const warn = (d.warnings || []).map(w => `<div class="del-row warn"><span>注意</span>`
    + `<b>${esc(w)}</b></div>`).join('');
  const title = libName ? `整理「${libName}」` : '一键整理全部超限库';
  const created = d.created || [];
  const ok = await confirmAction(title,
    `<div class="hint" style="margin-bottom:8px">`
    + `范围：${libName ? esc(libName) : `全部 ${(d.scope || []).length} 个库`}；`
    + `整体迁移 ${moves.length} 个目录`
    + (created.length
        ? `，其中新建 ${created.length} 个库（${created.map(esc).join('、')}），其余迁入已有空位库`
        : '，全部迁入已有空位库（不新建库）')
    + '；目录不会被拆分，本地文件不受影响。</div>'
    + rows + more + warn, '开始整理');
  if (!ok) return;
  try {
    const r = await fetch(`${API}/emby/rebalance`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm: true, libraries: libName ? [libName] : null }) });
    const res = await r.json();
    if (res.error) return toast(res.error, 'err');
    toast(`整理完成：新建 ${(res.created || []).length} 个库`
      + `${(res.created || []).length ? `（${res.created.join('、')}）` : ''}`
      + `，迁移 ${(res.moves || []).length} 个目录`, 'ok');
    await loadOverview();
  } catch (e) { toast('整理失败: ' + e, 'err'); }
}

// 捕获阶段委托：绑定映射行内「移出 / 移动」按钮与库头「整理」按钮。
// 必须用 capture（先于行的 onclick 跳转触发）并阻断冒泡，否则点击会先跳转浏览页。
document.addEventListener('click', e => {
  const rm = e.target.closest('.ov-remove');
  if (rm) { e.stopPropagation(); removeFromEmby(rm, rm.dataset.remove); return; }
  const mv = e.target.closest('.ov-move');
  if (mv) { e.stopPropagation(); moveToLibrary(mv); return; }
  const fx = e.target.closest('.ov-fix');
  if (fx) { e.stopPropagation(); openRebalance(fx.dataset.fix); }
}, true);

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
 * mode: 'video' 单视频 | 'blogger' 整个博主
 * 不设输入博主名/勾选等二次门控，确认按钮直接可用，点「取消」或遮罩即放弃。
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
        </div>
        <div class="modal-foot">
          <button class="btn" id="del-cancel">取消</button>
          <button class="btn btn-danger" id="del-ok">
            ${isBlogger ? '永久删除博主' : '永久删除视频'}</button>
        </div>
      </div>`;

    mask.style.display = 'flex';
    const okBtn = mask.querySelector('#del-ok');
    const cancelBtn = mask.querySelector('#del-cancel');
    okBtn.onclick = () => { mask.style.display = 'none'; resolve(true); };
    cancelBtn.onclick = () => { mask.style.display = 'none'; resolve(null); };
    mask.onclick = e => { if (e.target === mask) { mask.style.display = 'none'; resolve(null); } };
  });
}

/** 通用确认弹窗（替代 window.confirm）：原生 confirm 被浏览器
 *  「阻止此页面显示更多对话框」抑制后会静默返回 false，导致删除点击
 *  无反应且无报错；应用内弹窗不受影响。resolve true=确认。 */
function confirmAction(title, bodyHtml, okText, danger) {
  return new Promise(resolve => {
    const mask = _delModal();
    mask.innerHTML =
      '<div class="modal del-modal">' +
      '<h3>' + esc(title) + '</h3>' +
      '<div class="del-note">' + bodyHtml + '</div>' +
      '<div class="modal-foot">' +
      '<button class="btn" id="del-cancel">取消</button>' +
      '<button class="btn ' + (danger ? 'btn-danger' : 'primary') + '" id="del-ok">' + esc(okText || '确定') + '</button>' +
      '</div></div>';
    mask.style.display = 'flex';
    const done = v => { mask.style.display = 'none'; resolve(v); };
    mask.querySelector('#del-cancel').onclick = () => done(false);
    mask.querySelector('#del-ok').onclick = () => done(true);
    mask.onclick = e => { if (e.target === mask) done(false); };
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
  applyEyeState();     // 恢复上次的「隐藏名称」状态
  loadOverview();
}
applyDeleteGate();
