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
             onclick="location.href='/browse.html?blogger=${encodeURIComponent(x.blogger)}'"
             title="点击浏览该博主的视频">
          <span class="ov-blogger" title="${esc(x.emby_path)}">${esc(x.blogger)}</span>
          <span class="ov-arrow">→</span>
          <code class="ov-local" title="${esc(x.local_path)}">${esc(x.local_path)}</code>
          <span class="ov-count ${x.videos == null ? 'warn' : 'ok'}">${x.videos == null ? '未索引' : x.videos + ' 视频'}</span>
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

// ---------- init（仅首页执行；浏览/设置/日志/预览页同样引入本文件） ----------
if (document.getElementById('emby-overview')) {
  loadOverview();
}
