// ── WebSocket ────────────────────────────────────────
let ws;

function connectWS() {
  ws = new WebSocket(`ws://${location.host}/ws`);

  ws.onopen = () => {
    setWsDot(true);
    log('서버 연결됨', 'ok');
  };

  ws.onclose = () => {
    setWsDot(false);
    log('서버 연결 끊김', 'error');
    setTimeout(connectWS, 3000);
  };

  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    handleWsMessage(msg);
  };
}

function handleWsMessage(msg) {
  switch (msg.type) {
    case 'init':
    case 'ble_connected':
      updateDevicePanel(msg);
      log(`BLE 연결됨: ${msg.address || ''}`, 'ok');
      break;
    case 'ble_disconnected':
      updateBleState('disconnected');
      log('BLE 연결 해제 — 자동 재연결 대기 중...', 'warn');
      break;
    case 'log':
      log(msg.msg, msg.level || 'info');
      break;
    case 'ble_notify':
      if (msg.battery != null) updateBattery(msg.battery);
      if (msg.media_count) updateMediaCount(msg.media_count);
      if (msg.wifi_ip) {
        document.getElementById('wifi-ip').textContent = msg.wifi_ip;
        log(`Wi-Fi IP 수신: ${msg.wifi_ip}`, 'info');
      }
      break;
    case 'sync_progress':
      updateSyncProgress(msg);
      break;
    case 'sync_complete':
      log(`싱크 완료: ${msg.count}개 파일`, 'ok');
      loadMediaList();
      break;
    case 'stt_result':
      showSttResult(msg.file, msg.text);
      break;
    case 'stt_progress':
      log(`STT 청크 ${msg.chunk + 1}/${msg.total}...`, 'info');
      break;
  }
}

// ── UI 상태 업데이트 ─────────────────────────────────
function setWsDot(on) {
  const dot = document.getElementById('ws-dot');
  dot.className = 'dot' + (on ? ' on' : '');
  document.getElementById('ws-label').textContent = on ? '연결됨' : '연결 끊김';
}

function updateDevicePanel(data) {
  updateBleState(data.state);
  document.getElementById('device-address').textContent = data.address || '-';
  document.getElementById('firmware').textContent = data.firmware || '-';
  if (data.battery != null) updateBattery(data.battery);
  if (data.media_count) updateMediaCount(data.media_count);
  if (data.wifi_ip) document.getElementById('wifi-ip').textContent = data.wifi_ip;
  setControlsEnabled(data.state === 'connected');
}

function updateBleState(state) {
  const el = document.getElementById('ble-state');
  const map = {
    connected:    ['badge-green',  '● 연결됨'],
    disconnected: ['badge-red',    '○ 연결 안됨'],
    connecting:   ['badge-yellow', '◌ 연결 중...'],
    scanning:     ['badge-blue',   '◌ 스캔 중...'],
  };
  const [cls, text] = map[state] || ['badge-red', '알 수 없음'];
  el.className = `badge ${cls}`;
  el.textContent = text;
}

function updateBattery(level) {
  document.getElementById('battery-value').textContent = level + '%';
  const fill = document.getElementById('battery-fill');
  fill.style.width = level + '%';
  fill.className = 'battery-fill' + (level <= 15 ? ' low' : level <= 30 ? ' mid' : '');
}

function updateMediaCount(c) {
  document.getElementById('photo-count').textContent = c.photo ?? '-';
  document.getElementById('video-count').textContent = c.video ?? '-';
  document.getElementById('audio-count').textContent = c.audio ?? '-';
}

function setControlsEnabled(on) {
  document.querySelectorAll('.needs-ble').forEach(el => el.disabled = !on);
}

function updateSyncProgress(msg) {
  const bar = document.getElementById('sync-bar');
  const label = document.getElementById('sync-label');
  bar.style.width = msg.pct + '%';
  label.textContent = `${msg.filename} (${msg.pct}%)`;
}

// ── BLE 스캔 / 연결 ──────────────────────────────────
async function scanDevices() {
  log('BLE 스캔 중...', 'info');
  const btn = document.getElementById('btn-scan');
  btn.disabled = true;
  btn.textContent = '스캔 중...';
  try {
    const res = await api('POST', '/api/ble/scan');
    const list = document.getElementById('device-list');
    list.innerHTML = '';
    if (!res.devices.length) {
      list.innerHTML = '<div class="empty-state"><div class="icon">📡</div>기기 없음</div>';
      return;
    }
    res.devices.forEach(d => {
      const item = document.createElement('div');
      item.className = 'device-item';
      item.innerHTML = `
        <div>
          <div class="device-name">${d.name || 'Unknown'}</div>
          <div class="device-addr">${d.address}</div>
        </div>
        <button class="btn-primary btn-sm" onclick="connectDevice('${d.address}')">연결</button>
      `;
      list.appendChild(item);
    });
    log(`${res.devices.length}개 기기 발견`, 'ok');
  } catch (e) {
    log('스캔 실패: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '스캔';
  }
}

async function connectDevice(address) {
  log(`연결 중: ${address}`, 'info');
  updateBleState('connecting');
  try {
    const res = await api('POST', `/api/ble/connect/${address}`);
    updateDevicePanel(res);
    log('BLE 연결 완료', 'ok');
  } catch (e) {
    updateBleState('disconnected');
    log('연결 실패: ' + e.message, 'error');
  }
}

async function disconnectBLE() {
  await api('POST', '/api/ble/disconnect');
  setControlsEnabled(false);
}

// ── 기기 제어 ────────────────────────────────────────
async function takePhoto() {
  await api('POST', '/api/device/photo');
  log('📷 사진 촬영', 'ok');
}

let videoRecording = false;
async function toggleVideo() {
  const btn = document.getElementById('btn-video');
  if (!videoRecording) {
    await api('POST', '/api/device/video/start');
    videoRecording = true;
    btn.textContent = '⏹ 영상 중지';
    btn.className = 'btn-danger needs-ble';
    log('🎥 영상 녹화 시작', 'ok');
  } else {
    await api('POST', '/api/device/video/stop');
    videoRecording = false;
    btn.textContent = '🎥 영상 녹화';
    btn.className = 'btn-primary needs-ble';
    log('⏹ 영상 녹화 중지', 'ok');
  }
}

let audioRecording = false;
async function toggleAudio() {
  const btn = document.getElementById('btn-audio');
  if (!audioRecording) {
    await api('POST', '/api/device/audio/start');
    audioRecording = true;
    btn.textContent = '⏹ 녹음 중지';
    btn.className = 'btn-danger needs-ble';
    log('🎙 오디오 녹음 시작', 'ok');
  } else {
    await api('POST', '/api/device/audio/stop');
    audioRecording = false;
    btn.textContent = '🎙 오디오 녹음';
    btn.className = 'btn-primary needs-ble';
    log('⏹ 오디오 녹음 중지', 'ok');
  }
}

async function triggerAiPhoto() {
  await api('POST', '/api/device/ai_photo');
  log('🤖 AI 이미지 생성 요청', 'info');
}

// ── Wi-Fi 싱크 ───────────────────────────────────────
async function enableWifi() {
  log('Wi-Fi 핫스팟 활성화 중...', 'info');
  const res = await api('POST', '/api/wifi/enable');
  if (res.ip) {
    document.getElementById('wifi-ip').textContent = res.ip;
    log(`Wi-Fi IP: ${res.ip}`, 'ok');
  } else {
    log('IP 수신 실패 — 안경 Wi-Fi 연결 후 수동 입력', 'warn');
  }
}

async function probeEndpoints() {
  log('미문서 엔드포인트 탐색 중...', 'info');
  const res = await api('POST', '/api/wifi/probe');
  Object.entries(res).forEach(([path, info]) => {
    const status = info.status;
    const type = (status === 200) ? 'ok' : 'error';
    log(`${path} → ${status} ${info.content_type || ''}`, type);
  });
}

async function syncFiles() {
  log('파일 싱크 시작...', 'info');
  document.getElementById('sync-bar').style.width = '0%';
  document.getElementById('sync-label').textContent = '싱크 중...';
  const btn = document.getElementById('btn-sync');
  btn.disabled = true;
  try {
    const res = await api('POST', '/api/wifi/sync');
    log(`싱크 완료: ${res.count}개`, 'ok');
    loadMediaList();
  } catch (e) {
    log('싱크 실패: ' + e.message, 'error');
  } finally {
    btn.disabled = false;
    document.getElementById('sync-label').textContent = '';
  }
}

// ── UUID 확인 ────────────────────────────────────────
async function checkUUIDs() {
  const res = await api('GET', '/api/ble/uuids');
  const log_box = document.getElementById('log-box');
  res.forEach(svc => {
    log(`[SVC] ${svc.uuid}`, 'info');
    svc.chars.forEach(c => {
      log(`  [CHAR] ${c.uuid} [${c.properties.join(',')}]${c.value ? ' = ' + c.value : ''}`, 'ok');
    });
  });
}

// ── 파일 브라우저 ────────────────────────────────────
let mediaData = { photos: [], videos: [], audio: [] };

async function loadMediaList() {
  const res = await api('GET', '/api/media/list');
  mediaData = res;
  renderCurrentTab();
}

let currentTab = 'photos';

function switchTab(tab) {
  currentTab = tab;
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === tab);
  });
  renderCurrentTab();
}

function renderCurrentTab() {
  const container = document.getElementById('file-container');
  const data = mediaData[currentTab] || [];
  container.innerHTML = '';

  if (!data.length) {
    container.innerHTML = '<div class="empty-state"><div class="icon">📂</div>파일 없음<br><small>싱크 후 파일이 표시됩니다</small></div>';
    return;
  }

  if (currentTab === 'photos') {
    container.className = 'photo-grid';
    data.forEach(f => {
      const el = document.createElement('div');
      el.className = 'photo-thumb';
      el.innerHTML = `
        <img src="${f.url}" loading="lazy" alt="${f.name}">
        <div class="overlay">${f.name}</div>
      `;
      el.onclick = () => showPhotoModal(f.url, f.name);
      container.appendChild(el);
    });
  } else {
    container.className = 'file-list';
    data.forEach(f => {
      const isAudio = currentTab === 'audio';
      const icon = isAudio ? '🎵' : '🎬';
      const el = document.createElement('div');
      el.className = 'file-item';
      el.innerHTML = `
        <div class="file-icon ${currentTab === 'audio' ? 'audio' : 'video'}">${icon}</div>
        <div class="file-info">
          <div class="file-name">${f.name}</div>
          <div class="file-meta">${formatBytes(f.size)}</div>
        </div>
        <div class="file-actions">
          ${isAudio ? `
            <button class="btn-ghost btn-sm" onclick="playAudio('${f.url}', '${f.name}')">▶</button>
            <button class="btn-primary btn-sm" onclick="convertAndTranscribe('${f.path}', '${f.name}')">STT</button>
          ` : `
            <a href="${f.url}" target="_blank"><button class="btn-ghost btn-sm">▶</button></a>
          `}
        </div>
      `;
      container.appendChild(el);
    });
  }
}

// ── 오디오 플레이어 ──────────────────────────────────
function playAudio(url, name) {
  const player = document.getElementById('audio-player');
  const title = document.getElementById('now-playing');
  player.src = url;
  title.textContent = name;
  player.play();
  log(`▶ 재생: ${name}`, 'info');
}

// ── OPUS 변환 + STT ──────────────────────────────────
async function convertAndTranscribe(path, name) {
  log(`변환 중: ${name}`, 'info');
  try {
    const converted = await api('POST', `/api/audio/convert/${path}`);
    log(`WAV 변환 완료: ${converted.wav_path}`, 'ok');
    log(`STT 실행 중: ${name}`, 'info');
    const res = await api('POST', `/api/audio/transcribe/${converted.wav_path}`);
    showSttResult(name, res.text);
  } catch (e) {
    log('변환/STT 실패: ' + e.message, 'error');
  }
}

function showSttResult(file, text) {
  document.getElementById('stt-file').textContent = file;
  document.getElementById('stt-text').textContent = text || '(텍스트 없음)';
  log(`STT 완료: ${file}`, 'ok');
}

// ── 사진 모달 ────────────────────────────────────────
function showPhotoModal(url, name) {
  const modal = document.getElementById('photo-modal');
  document.getElementById('modal-img').src = url;
  document.getElementById('modal-name').textContent = name;
  modal.classList.add('show');
}

function closeModal() {
  document.getElementById('photo-modal').classList.remove('show');
}

// ── 로그 ─────────────────────────────────────────────
function log(msg, level = 'info') {
  const box = document.getElementById('log-box');
  const now = new Date().toLocaleTimeString('ko-KR', { hour12: false });
  const el = document.createElement('div');
  el.className = `log-entry ${level}`;
  el.textContent = `[${now}] ${msg}`;
  box.appendChild(el);
  box.scrollTop = box.scrollHeight;
  // 최대 200줄
  while (box.children.length > 200) box.removeChild(box.firstChild);
}

// ── 유틸 ─────────────────────────────────────────────
async function api(method, url, body) {
  const opts = {
    method,
    headers: { 'Content-Type': 'application/json' },
  };
  if (body) opts.body = JSON.stringify(body);
  const res = await fetch(url, opts);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / 1048576).toFixed(1) + ' MB';
}

// ── 초기화 ───────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  connectWS();
  setControlsEnabled(false);

  const status = await api('GET', '/api/status').catch(() => null);
  if (status) updateDevicePanel(status);

  await loadMediaList();
});
