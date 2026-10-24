// ---------- 设置页 ----------
async function openSettings() {
  try {
    const d = await fetch(`${API}/settings`).then(x => x.json());
    document.getElementById('s-emby-url').value = d.emby_url || '';
    document.getElementById('s-emby-key').value = '';
    document.getElementById('s-key-hint').textContent = d.emby_api_key ? '已配置 API Key' : '尚未配置';
    document.getElementById('s-prefix').value = d.emby_library_prefix || 'DY_';
    document.getElementById('s-auto-lib').checked =
      String(d.emby_auto_library ?? 'true').trim().toLowerCase() !== 'false';
    document.getElementById('s-max-videos').value = d.emby_library_max_videos || '2000';
    document.getElementById('s-vision-url').value = d.vision_base_url || '';
    document.getElementById('s-vision-key').value = '';
    document.getElementById('s-vision-model').value = d.vision_model || '';
    const eng = (d.recognition_engine || 'local').toLowerCase();
    document.getElementById('s-engine').value =
      ['local', 'llm', 'rules'].includes(eng) ? eng : 'local';
    document.getElementById('s-local-variant').value =
      (function(v){v=(v||'quantized').toLowerCase();return ['large','fp32'].includes(v)?v:'quantized';})(d.local_model_variant);
    document.getElementById('s-local-dir').value = d.local_model_dir || '';
    document.getElementById('s-local-granularity').value = (d.local_model_granularity || 'coarse').toLowerCase() === 'fine' ? 'fine' : 'coarse';
    document.getElementById('s-local-mirror').value = d.local_model_mirror || '';
    document.getElementById('s-local-auto').checked =
      String(d.local_model_auto_download ?? 'true').trim().toLowerCase() !== 'false';
    document.getElementById('s-label-keywords').value = d.label_keywords || '';
    document.getElementById('s-body-keywords').value = d.body_keywords || '';
    document.getElementById('s-style-labels').value =
      d.style_labels || '丝袜,瑜伽裤,裙装,短裤,牛仔裤,运动装,泳装,制服,其他';
    document.getElementById('s-body-labels').value =
      d.body_labels || '大胸,长腿,纤细,微胖,丰满,肌肉';
    document.getElementById('s-vision-sample').value = d.vision_sample_videos || '10';
    document.getElementById('s-vision-batch').value = d.vision_batch_images || '1';
    document.getElementById('s-vision-conc').value = d.vision_concurrency || '2';
    document.getElementById('s-vision-hint').textContent = d.vision_api_key
      ? '已配置 API Key（智谱默认模型可直接使用）'
      : '尚未配置：默认智谱 GLM-4V-Flash（免费），只需填 API Key 即可用';
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
    emby_auto_library: document.getElementById('s-auto-lib').checked
      ? 'true' : 'false',
    emby_library_max_videos: String(Math.max(1, parseInt(val('s-max-videos'), 10) || 2000)),
    vision_base_url: val('s-vision-url').trim(),
    vision_model: val('s-vision-model').trim(),
    recognition_engine: val('s-engine') || 'local',
    local_model_variant: val('s-local-variant') || 'quantized',
    local_model_dir: val('s-local-dir').trim() || '/app/.cache/models',
    local_model_mirror: val('s-local-mirror').trim() || 'https://hf-mirror.com',
    local_model_granularity: val('s-local-granularity') || 'coarse',
    local_model_auto_download: document.getElementById('s-local-auto').checked
      ? 'true' : 'false',
    label_keywords: val('s-label-keywords').trim(),
    body_keywords: val('s-body-keywords').trim(),
    style_labels: val('s-style-labels').trim(),
    vision_max_images: String(Math.min(50, Math.max(10, parseInt(val('s-vision-sample'), 10) || 10))),
    vision_sample_videos: String(Math.min(50, Math.max(10, parseInt(val('s-vision-sample'), 10) || 10))),
    body_labels: val('s-body-labels').trim(),
    vision_batch_images: String(Math.min(10, Math.max(1, parseInt(val('s-vision-batch'), 10) || 1))),
    vision_concurrency: String(Math.min(8, Math.max(1, parseInt(val('s-vision-conc'), 10) || 2))),
  };
  const vkey = val('s-vision-key').trim();
  if (vkey) values.vision_api_key = vkey;
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
