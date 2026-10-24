// ---------- 目录映射指引页 ----------
let _diag = null;

function gset(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function stepIcon(ok) { return ok ? '✅' : '⚠️'; }

function renderDiag(d) {
  _diag = d;

  // 路径总览
  gset('g-local', (d.local_root || '/data') + (d.media_subdir ? '/' + d.media_subdir : ''));
  gset('g-emby', d.emby_prefix || '（未配置）');
  gset('g-formula', d.formula || '…');
  document.getElementById('g-box-emby').classList.toggle('warn', !d.host_video_root);

  // 三步
  document.getElementById('g-steps').innerHTML = d.steps.map(s => `
    <div class="gs-item ${s.ok ? 'ok' : 'warn'}">
      <span class="gs-ico">${stepIcon(s.ok)}</span>
      <div class="gs-body"><b>${esc(s.title)}</b><span>${esc(s.text)}</span></div>
    </div>`).join('');

  // 建议与按钮
  const det = d.detected || {};
  const fix = document.getElementById('g-fix');
  const msg = document.getElementById('g-msg');
  if (d.can_autofix) {
    fix.disabled = false;
    fix.textContent = d.host_video_root
      ? `一键校准为 ${det.root}` : `一键填入 ${det.root}`;
    msg.className = 'guide-msg warn';
    msg.innerHTML = `检测到现有 ${det.votes}/${det.total} 条库路径指向 <code>${esc(det.root)}</code>`
      + (d.host_video_root ? `，与当前配置 <code>${esc(d.host_video_root)}</code> 不一致。` : '，但配置里还是空的。')
      + ' 点上面的按钮即可写入（只改配置，不动任何文件）。';
  } else {
    fix.disabled = true;
    fix.textContent = '已是最优配置';
    if (det.root && det.root === d.host_video_root) {
      msg.className = 'guide-msg ok';
      msg.innerHTML = `Emby 侧根已与现有库路径一致（<code>${esc(det.root)}</code>），不需要调整。`;
    } else if (!det.root) {
      msg.className = 'guide-msg';
      msg.innerHTML = '还没有可推断的库路径。先在「浏览」页加入一次 Emby，再回来点「重新检测」。';
    } else {
      msg.className = 'guide-msg ok';
      msg.innerHTML = '配置已就绪。';
    }
  }

  // 问题列表
  document.getElementById('g-issues').innerHTML = (d.issues || []).map(i => `
    <div class="guide-issue ${i.level}">${i.level === 'err' ? '✖' : i.level === 'warn' ? '!' : 'i'}
      <span>${esc(i.text)}</span></div>`).join('');

  // 校验表
  const libs = d.libraries || [];
  document.getElementById('g-libs').innerHTML = libs.length
    ? libs.map(lib => {
      const rows = lib.dirs.slice(0, 8).map(x => `
        <div class="gl-row ${x.mapped ? 'ok' : 'warn'}">
          <span class="gl-ico">${x.mapped ? '✅' : '⚠️'}</span>
          <code class="gl-emby">${esc(x.emby_path)}</code>
          <span class="gl-arrow">→</span>
          <code class="gl-local">${esc(x.local_path)}</code>
          <span class="gl-note">${x.indexed ? (x.videos != null ? x.videos + ' 视频' : '已索引') : '未索引'}</span>
        </div>`).join('');
      const more = lib.dirs.length > 8
        ? `<div class="gl-more">…另有 ${lib.dirs.length - 8} 个目录</div>` : '';
      return `<div class="gl-lib"><div class="gl-lib-name">${esc(lib.name)}
        <span>${lib.dirs.length} 个目录</span></div>${rows}${more}</div>`;
    }).join('')
    : '<div class="gl-empty">还没有 DY_ 媒体库</div>';
}

async function detect() {
  const btn = document.getElementById('g-fix');
  if (btn) btn.disabled = true;
  try {
    const d = await fetch(`${API}/mapping/diagnose`).then(x => x.json());
    if (d.error) { toast(d.error, 'err'); return; }
    renderDiag(d);
  } catch (e) {
    toast('检测失败: ' + e.message, 'err');
  }
}

async function autofix() {
  if (!_diag || !_diag.can_autofix) return;
  const btn = document.getElementById('g-fix');
  btn.disabled = true;
  btn.textContent = '正在写入…';
  try {
    const r = await fetch(`${API}/mapping/autofix`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ apply: true }) });
    const d = await r.json();
    if (d.error) { toast(d.error, 'err'); await detect(); return; }
    if (!d.applied) { toast(d.reason || '没有可校准的目标', 'err'); await detect(); return; }
    toast(`已把 Emby 侧根设为 ${d.host_video_root}`, 'ok');
    if (d.diagnosis && !d.diagnosis.error) renderDiag(d.diagnosis);
  } catch (e) {
    toast('校准失败: ' + e.message, 'err');
    await detect();
  }
}

detect();
