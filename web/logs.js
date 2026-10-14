// ---------- 日志页 ----------
let logSeq = 0, logTimer = null, follow = true, minLevel = '';

const LEVEL_RANK = { DEBUG: 10, INFO: 20, WARNING: 30, ERROR: 40, CRITICAL: 50 };

function setFollow(on) { follow = on; }

function reloadLogs() {
  minLevel = document.getElementById('log-level').value;
  logSeq = 0;
  document.getElementById('log-view').innerHTML = '';
  pollLogs();
}

async function pollLogs() {
  try {
    const d = await fetch(`${API}/logs?after=${logSeq}`).then(x => x.json());
    const view = document.getElementById('log-view');
    const stick = follow &&
      view.scrollHeight - view.scrollTop - view.clientHeight < 40;
    if (d.logs && d.logs.length) {
      logSeq = d.seq;
      const rows = d.logs
        .filter(l => !minLevel || (LEVEL_RANK[l.level] || 20) >= LEVEL_RANK[minLevel])
        .map(fmtLog);
      if (rows.length) view.insertAdjacentHTML('beforeend', rows.join(''));
      while (view.children.length > 800) view.removeChild(view.firstChild);
    }
    if (stick) view.scrollTop = view.scrollHeight;
  } catch (e) { /* 忽略轮询错误 */ }
}

function fmtLog(l) {
  const t = new Date(l.ts * 1000).toLocaleTimeString('zh-CN', {hour12: false});
  const lv = l.level === 'ERROR' ? 'err' : (l.level === 'WARNING' ? 'warn' : '');
  return `<div class="log-line"><span class="lt">${t}</span>` +
    `<span class="lv ${lv}">${esc(l.level)}</span>` +
    `<span class="ln">${esc(l.logger.replace('dy-sort.', ''))}</span>` +
    `<span class="lm">${esc(l.msg)}</span></div>`;
}

reloadLogs();
logTimer = setInterval(pollLogs, 2000);
