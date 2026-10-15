// ---------- 设置页 ----------
async function openSettings() {
  try {
    const d = await fetch(`${API}/settings`).then(x => x.json());
    document.getElementById('s-emby-url').value = d.emby_url || '';
    document.getElementById('s-emby-key').value = '';
    document.getElementById('s-key-hint').textContent = d.emby_api_key ? '已配置 API Key' : '尚未配置';
    document.getElementById('s-prefix').value = d.emby_library_prefix || 'DY_';
    document.getElementById('s-host-root').value = d.host_video_root || '';
    document.getElementById('s-scan-interval').value = d.scan_interval || '0';
    document.getElementById('s-enable-delete').checked =
      String(d.enable_delete || '').trim().toLowerCase() === 'true';
    refreshDirs(d.media_subdir || '');
  } catch (e) {
    toast('读取设置失败: ' + e, 'err');
  }
}
async function refreshDirs(selected = '') {
  const sel = document.getElementById('s-subdir');
  sel.innerHTML = '<option value="">/（/data 根目录）</option>';
  try {
    const d = await fetch(`${API}/dirs`).then(x => x.json());
    if (d.error) { sel.insertAdjacentHTML('beforeend', `<option disabled>${esc(d.error)}</option>`); return; }
    for (const dir of d.dirs) {
      sel.insertAdjacentHTML('beforeend',
        `<option value="${esc(dir.name)}" ${dir.name === selected ? 'selected' : ''}>/${esc(dir.name)}（${dir.subdirs} 个子目录）</option>`);
    }
  } catch (e) {
    sel.insertAdjacentHTML('beforeend', '<option disabled>读取目录失败</option>');
  }
}
async function saveSettings() {
  const values = {
    emby_url: val('s-emby-url').trim(),
    emby_library_prefix: val('s-prefix').trim() || 'DY_',
    media_subdir: val('s-subdir'),
    host_video_root: val('s-host-root').trim(),
    scan_interval: String(Math.max(0, parseInt(val('s-scan-interval'), 10) || 0)),
    enable_delete: document.getElementById('s-enable-delete').checked
      ? 'true' : 'false',
  };
  const key = val('s-emby-key').trim();
  if (key) values.emby_api_key = key;
  const r = await fetch(`${API}/bind`, { method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ media_subdir: values.media_subdir }) });
  const b = await r.json();
  if (b.error) { toast(b.error, 'err'); return; }
  const r2 = await fetch(`${API}/settings`, { method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ values }) });
  const d = await r2.json();
  if (!d.ok) { toast('保存失败', 'err'); return; }
  const mins = parseInt(values.scan_interval, 10) || 0;
  const tip = b.rescan ? '已换绑目录，正在重建索引' : '设置已保存';
  toast(`${tip}；定时扫描${mins > 0 ? `：每 ${mins} 分钟` : '已关闭'}`, 'ok');
}
openSettings();
