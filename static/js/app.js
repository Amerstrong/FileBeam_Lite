/**
 * FileBeam Lite (免费开源版) - 纯前端交互逻辑
 * 支持单文件 <= 10MB 极速互传，超出自动引导升级 Pro 专业版
 */

// ─── 全局状态 ─────────────────────────────────────────────────────────────────
const state = {
  maxSizeBytes: 10 * 1024 * 1024, // 10MB 限制
  serverUrl: '',
  proUpgradeUrl: 'https://wojingfang.cn/tools/filebeam.html',
  machineCode: '',
  activated: false,
  history: JSON.parse(localStorage.getItem('fblite_history') || '[]'),
  currentTab: 'received',
  isUploading: false
};

// ─── 页面启动初始化 ───────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initAppInfo();
  initDropZone();
  fetchFiles();
  renderHistory();
});

// ─── 获取服务端配置与局域网 IP ────────────────────────────────────────────────
async function initAppInfo() {
  try {
    const res = await fetch('/api/info');
    const data = await res.json();
    state.serverUrl = data.url;
    state.proUpgradeUrl = data.pro_upgrade_url || state.proUpgradeUrl;
    state.maxSizeBytes = data.max_size_bytes || state.maxSizeBytes;
    state.machineCode = data.machine_code || '';
    state.activated = Boolean(data.activated);

    const urlElem = document.getElementById('serverUrl');
    if (urlElem) urlElem.textContent = data.url;

    const proLinkElem = document.getElementById('btnUpgradeProLink');
    if (proLinkElem) proLinkElem.href = state.proUpgradeUrl;
    updateActivationUI();
  } catch (err) {
    console.error('获取服务端信息失败:', err);
    showToast('无法连接服务端，请确认服务已启动', 'error');
  }
}

// ─── 复制局域网地址 ───────────────────────────────────────────────────────────
function copyServerUrl() {
  if (!state.serverUrl) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(state.serverUrl).then(() => {
      showToast('已复制局域网网址，发给手机直接打开！');
    }).catch(() => fallbackCopy(state.serverUrl));
  } else {
    fallbackCopy(state.serverUrl);
  }
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  document.body.appendChild(ta);
  ta.select();
  document.execCommand('copy');
  ta.remove();
  showToast('已复制局域网网址');
}

// ─── 拖拽与文件选择事件 ───────────────────────────────────────────────────────
function initDropZone() {
  const dropZone = document.getElementById('dropZone');
  const fileInput = document.getElementById('fileInput');

  ['dragenter', 'dragover'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.add('dragover');
    });
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropZone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropZone.classList.remove('dragover');
    });
  });

  dropZone.addEventListener('drop', (e) => {
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      handleFiles(Array.from(files));
    }
  });

  fileInput.addEventListener('change', () => {
    if (fileInput.files && fileInput.files.length > 0) {
      handleFiles(Array.from(fileInput.files));
      fileInput.value = ''; // 清空以允许重复上传同一文件
    }
  });
}

function triggerFileInput() {
  if (state.isUploading) {
    showToast('当前有文件正在传输中，请稍候...', 'info');
    return;
  }
  document.getElementById('fileInput').click();
}

// ─── 批量文件预检与上传队列 ───────────────────────────────────────────────────
async function handleFiles(files) {
  if (!files || files.length === 0) return;

  // 1. 检查是否存在超过 10MB 的文件
  const oversizedFiles = files.filter(f => f.size > state.maxSizeBytes);
  if (oversizedFiles.length > 0) {
    const firstOver = oversizedFiles[0];
    showOversizedPrompt(firstOver.name, formatSize(firstOver.size));
    return;
  }

  // 2. 顺序上传各个文件
  state.isUploading = true;
  document.getElementById('progressContainer').style.display = 'block';

  let successCount = 0;
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    try {
      await uploadSingleFile(file, i + 1, files.length);
      successCount++;
      addHistoryItem({
        name: file.name,
        size: formatSize(file.size),
        type: 'send',
        time: formatNow()
      });
    } catch (err) {
      showToast(`上传失败 (${file.name}): ${err.message}`, 'error');
    }
  }

  state.isUploading = false;
  setTimeout(() => {
    document.getElementById('progressContainer').style.display = 'none';
    resetProgress();
  }, 1000);

  if (successCount > 0) {
    showToast(`🎉 成功发送 ${successCount} 个文件！`);
    fetchFiles();
  }
}

// ─── 单文件带进度上传 (XHR) ───────────────────────────────────────────────────
function uploadSingleFile(file, index, total) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const formData = new FormData();
    formData.append('file', file);

    const nameLabel = total > 1 ? `[${index}/${total}] ${file.name}` : file.name;
    document.getElementById('progressFileName').textContent = nameLabel;

    let startTime = Date.now();
    let loadedBytes = 0;

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        const percent = Math.round((e.loaded / e.total) * 100);
        document.getElementById('progressBar').style.width = `${percent}%`;
        document.getElementById('progressPercent').textContent = `${percent}%`;

        // 计算速率
        const now = Date.now();
        const durationSec = (now - startTime) / 1000;
        if (durationSec > 0.3) {
          const speed = (e.loaded - loadedBytes) / durationSec;
          document.getElementById('progressSpeed').textContent = `${formatSize(speed)}/s`;
          startTime = now;
          loadedBytes = e.loaded;
        }
      }
    };

    xhr.onload = () => {
      if (xhr.status === 200) {
        try {
          const res = JSON.parse(xhr.responseText);
          if (res.success) {
            resolve(res);
          } else {
            reject(new Error(res.error || '上传失败'));
          }
        } catch (e) {
          reject(new Error('解析服务端响应异常'));
        }
      } else if (xhr.status === 413) {
        showOversizedPrompt(file.name, formatSize(file.size));
        reject(new Error('文件超过10MB免费版上限'));
      } else {
        reject(new Error(`HTTP ${xhr.status}`));
      }
    };

    xhr.onerror = () => reject(new Error('网络连接中断'));
    xhr.open('POST', '/api/upload', true);
    xhr.send(formData);
  });
}

function resetProgress() {
  document.getElementById('progressBar').style.width = '0%';
  document.getElementById('progressPercent').textContent = '0%';
  document.getElementById('progressSpeed').textContent = '0 KB/s';
}

// ─── 超限引导弹窗 ─────────────────────────────────────────────────────────────
function showOversizedPrompt(fileName, sizeStr) {
  showToast(`文件超过 10MB 限制: ${sizeStr}`, 'warning');
  openProModal();
}

// ─── 获取并渲染文件列表 ───────────────────────────────────────────────────────
async function fetchFiles() {
  try {
    const res = await fetch('/api/files');
    const data = await res.json();
    renderFileList('receivedList', data.received || [], 'received');
    renderFileList('sharedList', data.shared || [], 'shared');

    document.getElementById('receivedCount').textContent = (data.received || []).length;
    document.getElementById('sharedCount').textContent = (data.shared || []).length;
  } catch (err) {
    console.error('获取文件列表失败:', err);
  }
}

function renderFileList(containerId, files, folderType) {
  const container = document.getElementById(containerId);
  if (!files || files.length === 0) {
    container.innerHTML = `<div class="empty-state">${folderType === 'received' ? '暂无接收的文件' : '电脑 shared 目录中暂无文件'}</div>`;
    return;
  }

  container.innerHTML = files.map(f => `
    <div class="file-item">
      <div class="file-main">
        <div class="file-icon">${getFileIcon(f.name)}</div>
        <div class="file-details">
          <div class="file-name" title="${escapeHtml(f.name)}">${escapeHtml(f.name)}</div>
          <div class="file-sub">${f.size_str} · ${f.modified}</div>
        </div>
      </div>
      <div class="file-actions">
        <a class="btn-download" href="/api/download/${folderType}/${encodeURIComponent(f.name)}" download="${escapeHtml(f.name)}" onclick="onDownloadClick('${escapeHtml(f.name)}', '${f.size_str}')">
          ⬇ 下载
        </a>
      </div>
    </div>
  `).join('');
}

function onDownloadClick(name, size) {
  addHistoryItem({
    name: name,
    size: size,
    type: 'download',
    time: formatNow()
  });
}

// ─── 历史传输记录 ─────────────────────────────────────────────────────────────
function addHistoryItem(item) {
  state.history.unshift(item);
  if (state.history.length > 100) state.history = state.history.slice(0, 100);
  localStorage.setItem('fblite_history', JSON.stringify(state.history));
  renderHistory();
}

function renderHistory() {
  const container = document.getElementById('historyList');
  document.getElementById('historyCount').textContent = state.history.length;

  if (state.history.length === 0) {
    container.innerHTML = `<div class="empty-state">暂无历史传输记录</div>`;
    return;
  }

  const icons = { send: '📤 发送', download: '⬇ 下载' };
  container.innerHTML = state.history.map(h => `
    <div class="file-item">
      <div class="file-main">
        <div class="file-icon">${getFileIcon(h.name)}</div>
        <div class="file-details">
          <div class="file-name">${escapeHtml(h.name)}</div>
          <div class="file-sub">${icons[h.type] || '📄'} · ${h.size} · ${h.time}</div>
        </div>
      </div>
    </div>
  `).join('');
}

function clearHistory() {
  if (state.history.length === 0) {
    showToast('当前暂无传输记录', 'info');
    return;
  }
  if (confirm('确定要清空全部传输日志吗？\n\n💡 提示：仅清除界面上的记录日志，电脑和手机本地已存的实体文件不受任何影响。')) {
    state.history = [];
    localStorage.removeItem('fblite_history');
    renderHistory();
    showToast('传输记录已成功清除（文件依然保留）');
  }
}

// ─── 选项卡切换 ───────────────────────────────────────────────────────────────
function switchTab(tab) {
  state.currentTab = tab;
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(content => content.classList.remove('active'));

  if (tab === 'received') {
    document.querySelector('.tab-btn:nth-child(1)').classList.add('active');
    document.getElementById('tabReceived').classList.add('active');
  } else if (tab === 'shared') {
    document.querySelector('.tab-btn:nth-child(2)').classList.add('active');
    document.getElementById('tabShared').classList.add('active');
  } else if (tab === 'history') {
    document.querySelector('.tab-btn:nth-child(3)').classList.add('active');
    document.getElementById('tabHistory').classList.add('active');
  }
}

// ─── 弹窗控制 ─────────────────────────────────────────────────────────────────
function openProModal() {
  document.getElementById('proModal').style.display = 'flex';
  refreshActivationStatus();
}

function closeProModal() {
  document.getElementById('proModal').style.display = 'none';
}

function updateActivationUI(message = '', type = '') {
  const machineCodeElem = document.getElementById('machineCodeValue');
  const statusElem = document.getElementById('activationStatus');
  const button = document.getElementById('btnActivatePro');
  if (machineCodeElem) machineCodeElem.textContent = state.machineCode || '暂不可用';
  if (statusElem) {
    if (message) {
      statusElem.textContent = message;
      statusElem.className = `activation-status ${type}`.trim();
    } else if (state.activated) {
      statusElem.textContent = '✅ 本机已完成 Pro 授权验证，可前往官网获取专业版客户端。';
      statusElem.className = 'activation-status success';
    } else {
      statusElem.textContent = '付款后由后台将激活码绑定到本机机器码。';
      statusElem.className = 'activation-status';
    }
  }
  if (button && state.activated) {
    button.textContent = '已激活';
    button.disabled = true;
  }
}

async function refreshActivationStatus() {
  try {
    const res = await fetch('/api/activation-status', { cache: 'no-store' });
    const data = await res.json();
    state.machineCode = data.machine_code || state.machineCode;
    state.activated = Boolean(data.activated);
    updateActivationUI();
  } catch (err) {
    updateActivationUI('无法读取本机授权状态，请确认服务仍在运行。', 'error');
  }
}

async function copyMachineCode() {
  const machineCode = state.machineCode || document.getElementById('machineCodeValue')?.textContent || '';
  if (!machineCode || machineCode === '暂不可用' || machineCode === '读取中...') {
    showToast('机器码暂不可用', 'warning');
    return;
  }
  try {
    await navigator.clipboard.writeText(machineCode);
    showToast('机器码已复制');
  } catch (err) {
    fallbackCopy(machineCode);
    showToast('机器码已复制');
  }
}

async function activatePro(event) {
  event.preventDefault();
  const input = document.getElementById('activationCodeInput');
  const button = document.getElementById('btnActivatePro');
  const activationCode = input.value.trim().toUpperCase();
  if (!activationCode) {
    updateActivationUI('请先输入激活码。', 'error');
    input.focus();
    return;
  }
  button.disabled = true;
  button.textContent = '验证中...';
  updateActivationUI('正在连接授权服务，请稍候...');
  try {
    const res = await fetch('/api/activate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ activation_code: activationCode })
    });
    const data = await res.json();
    state.machineCode = data.machine_code || state.machineCode;
    state.activated = Boolean(data.success && data.activated);
    updateActivationUI(data.message || (data.success ? '激活成功。' : '激活失败。'), data.success ? 'success' : 'error');
    if (data.success) input.value = '';
  } catch (err) {
    updateActivationUI('无法连接本地服务，请稍后重试。', 'error');
  } finally {
    if (!state.activated) {
      button.disabled = false;
      button.textContent = '验证激活码';
    }
  }
}

function openHelpModal() {
  document.getElementById('helpModal').style.display = 'flex';
}

function closeHelpModal() {
  document.getElementById('helpModal').style.display = 'none';
}

// 点击遮罩外部关闭弹窗
window.addEventListener('click', (e) => {
  const proModal = document.getElementById('proModal');
  const helpModal = document.getElementById('helpModal');
  if (e.target === proModal) closeProModal();
  if (e.target === helpModal) closeHelpModal();
});

// ─── Toast 提示通知 ───────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(message, type = 'info') {
  const toast = document.getElementById('toast');
  toast.textContent = message;
  toast.className = 'toast show';
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    toast.className = 'toast';
  }, 2800);
}

// ─── 格式化工具函数 ───────────────────────────────────────────────────────────
function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  if (bytes < 1024 * 1024 * 1024) return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  return (bytes / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

function formatNow() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function escapeHtml(str) {
  if (!str) return '';
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function getFileIcon(filename) {
  const ext = (filename.split('.').pop() || '').toLowerCase();
  const map = {
    jpg: '🖼️', jpeg: '🖼️', png: '🖼️', gif: '🖼️', webp: '🖼️', bmp: '🖼️',
    mp4: '🎬', mov: '🎬', avi: '🎬', mkv: '🎬',
    mp3: '🎵', wav: '🎵', flac: '🎵', aac: '🎵',
    pdf: '📄', doc: '📝', docx: '📝', xls: '📊', xlsx: '📊', ppt: '📊', pptx: '📊',
    txt: '📃', md: '📃', json: '⚙️', zip: '📦', rar: '📦', '7z': '📦'
  };
  return map[ext] || '📁';
}
