const canvas = document.getElementById('previewCanvas');
const ctx = canvas.getContext('2d');
const patternInfo = document.getElementById('patternInfo');
const jobInfo = document.getElementById('jobInfo');
const deviceInfo = document.getElementById('deviceInfo');
const statusLog = document.getElementById('statusLog');
const patternInput = document.getElementById('patternInput');

async function api(path, options) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json();
}

function appendLog(line) {
  const now = new Date().toLocaleTimeString();
  statusLog.textContent = `[${now}] ${line}\n` + statusLog.textContent;
}

function clearCanvas() {
  ctx.fillStyle = '#0f172a';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
}

function drawPattern(data) {
  clearCanvas();
  const strokes = data.strokes || [];
  if (!strokes.length) {
    appendLog('No strokes to display.');
    return;
  }
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const stroke of strokes) {
    for (const [x, y] of stroke.points) {
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }
  const padding = 10;
  const w = maxX - minX || 1;
  const h = maxY - minY || 1;
  const scale = Math.min((canvas.width - 2 * padding) / w, (canvas.height - 2 * padding) / h);

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.save();
  ctx.translate(padding, canvas.height - padding);
  ctx.scale(scale, -scale);
  ctx.translate(-minX, -minY);

  const palette = ['#111111', '#e41a1c', '#377eb8', '#4daf4a', '#984ea3', '#ff7f00', '#a65628', '#f781bf', '#999999'];
  for (const stroke of strokes) {
    const color = stroke.color || palette[stroke.pen % palette.length] || '#38bdf8';
    ctx.strokeStyle = color;
    ctx.lineWidth = (stroke.width || 1.5) / Math.max(scale, 0.001);
    ctx.beginPath();
    const [first, ...rest] = stroke.points;
    if (!first) continue;
    ctx.moveTo(first[0], first[1]);
    for (const [x, y] of rest) {
      ctx.lineTo(x, y);
    }
    ctx.stroke();
  }
  ctx.restore();
}

async function refreshPreview() {
  try {
    const data = await api('/api/pattern');
    drawPattern(data);
  } catch (err) {
    appendLog(`Preview error: ${err.message}`);
  }
}

async function updateStatus() {
  try {
    const data = await api('/api/status');
    const { pattern, job, device } = data;
    patternInfo.textContent = `${pattern.count} strokes, ${pattern.total_length_mm.toFixed(1)} mm`;
    jobInfo.textContent = `${job.job_state} (${job.progress.current}/${job.progress.total})`;
    if (job.last_status) {
      appendLog(job.last_status);
    }
    if (job.last_error) {
      appendLog(`Error: ${job.last_error}`);
    }
    if (device.error) {
      deviceInfo.textContent = `Error: ${device.error}`;
    } else if (device.wpos) {
      const [x, y] = device.wpos;
      deviceInfo.textContent = `${device.state || 'unknown'} @ (${x.toFixed(2)}, ${y.toFixed(2)})`;
    } else {
      deviceInfo.textContent = device.state || 'unknown';
    }
  } catch (err) {
    appendLog(`Status error: ${err.message}`);
  }
}

function bindControls() {
  document.getElementById('refreshPreview').addEventListener('click', refreshPreview);
  document.getElementById('startJob').addEventListener('click', async () => {
    try {
      await api('/api/job/start', { method: 'POST', body: JSON.stringify({}) });
      appendLog('Job started');
    } catch (err) {
      appendLog(`Start failed: ${err.message}`);
    }
  });
  document.getElementById('stopJob').addEventListener('click', async () => {
    await api('/api/job/stop', { method: 'POST', body: '{}' });
    appendLog('Stop requested');
  });
  document.getElementById('gotoBtn').addEventListener('click', async () => {
    const x = parseFloat(document.getElementById('gotoX').value);
    const y = parseFloat(document.getElementById('gotoY').value);
    await api('/api/device/goto', { method: 'POST', body: JSON.stringify({ x, y }) });
    appendLog(`Goto ${x}, ${y}`);
  });
  document.getElementById('jogBtn').addEventListener('click', async () => {
    const dx = parseFloat(document.getElementById('jogDx').value);
    const dy = parseFloat(document.getElementById('jogDy').value);
    await api('/api/device/jog', { method: 'POST', body: JSON.stringify({ dx, dy }) });
    appendLog(`Jog ${dx}, ${dy}`);
  });
  document.getElementById('penUp').addEventListener('click', async () => {
    await api('/api/device/pen', { method: 'POST', body: JSON.stringify({ pos: 1.0 }) });
    appendLog('Pen up');
  });
  document.getElementById('penDown').addEventListener('click', async () => {
    await api('/api/device/pen', { method: 'POST', body: JSON.stringify({ pos: 0.0 }) });
    appendLog('Pen down');
  });
  document.getElementById('penSet').addEventListener('click', async () => {
    const pos = parseFloat(document.getElementById('penPos').value);
    await api('/api/device/pen', { method: 'POST', body: JSON.stringify({ pos }) });
    appendLog(`Pen height ${pos}`);
  });
  document.getElementById('origin').addEventListener('click', async () => {
    await api('/api/device/origin', { method: 'POST', body: '{}' });
    appendLog('Origin set');
  });
  document.getElementById('uploadPattern').addEventListener('click', async () => {
    try {
      const payload = JSON.parse(patternInput.value || '{}');
      const res = await api('/api/pattern', { method: 'POST', body: JSON.stringify(payload) });
      appendLog(`Pattern uploaded (${res.count} items)`);
      await refreshPreview();
    } catch (err) {
      appendLog(`Upload failed: ${err.message}`);
    }
  });
  document.getElementById('clearPattern').addEventListener('click', async () => {
    await api('/api/pattern', { method: 'DELETE' });
    appendLog('Pattern cleared');
    clearCanvas();
  });
}

bindControls();
refreshPreview();
updateStatus();
setInterval(updateStatus, 2000);
