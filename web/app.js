'use strict';

const $ = (sel) => document.querySelector(sel);
const send = (url) => fetch(url).catch(() => {});

// Mirrors the server's state; the 1s /stats poll keeps it honest.
const S = { mode: 'yolo', size: 'm', conf: 0.35, boxes: false, classes: new Set() };

const chipColor = (i) => `hsl(${(i * 137.508) % 360} 85% 55%)`; // same wheel as the masks

/* ------------------------------- controls ------------------------------- */

function syncControls() {
  $('#modeBtn').textContent = S.mode === 'yolo' ? 'YOLO Seg' : 'FastSAM';
  $('#modeBtn').classList.toggle('active', S.mode === 'sam');

  document.querySelectorAll('#sizeRow button').forEach((b) =>
    b.classList.toggle('active', b.dataset.size === S.size));

  $('#confOut').textContent = S.conf.toFixed(2);
  if (!confDragging) $('#confSlider').value = S.conf;

  $('#boxesBtn').textContent = `Bounding Boxes: ${S.boxes ? 'On' : 'Off'}`;
  $('#boxesBtn').classList.toggle('active', S.boxes);

  document.querySelectorAll('.chip').forEach((c) =>
    c.classList.toggle('active', S.classes.has(+c.dataset.id)));

  // size + class filter only apply to YOLO mode
  const sam = S.mode === 'sam';
  $('#sizeGroup').classList.toggle('disabled', sam);
  $('#classGroup').classList.toggle('disabled', sam);
}

function setMode(mode) {
  S.mode = mode;
  send(`/set_mode?mode=${mode}`);
  syncControls();
}

function setSize(size) {
  S.size = size;
  send(`/set_model_size?size=${size}`);
  syncControls();
}

let confSendTimer = null;
function setConf(value) {
  S.conf = Math.min(0.95, Math.max(0.1, Math.round(value * 20) / 20));
  clearTimeout(confSendTimer); // throttle while sliding/holding a key
  confSendTimer = setTimeout(() => {
    confSendTimer = null;
    send(`/set_confidence?value=${S.conf}`);
  }, 120);
  syncControls();
}

function toggleBoxes() {
  S.boxes = !S.boxes;
  send('/toggle_boxes');
  syncControls();
}

function toggleClass(id) {
  S.classes.has(id) ? S.classes.delete(id) : S.classes.add(id);
  send(`/set_classes?ids=${[...S.classes].join(',')}`);
  syncControls();
}

$('#modeBtn').addEventListener('click', () => setMode(S.mode === 'yolo' ? 'sam' : 'yolo'));

document.querySelectorAll('#sizeRow button').forEach((b) =>
  b.addEventListener('click', () => setSize(b.dataset.size)));

let confDragging = false;
const slider = $('#confSlider');
slider.addEventListener('pointerdown', () => { confDragging = true; });
window.addEventListener('pointerup', () => { confDragging = false; });
slider.addEventListener('input', () => setConf(+slider.value));

$('#boxesBtn').addEventListener('click', toggleBoxes);

/* ------------------------------ class chips ----------------------------- */

async function buildChips() {
  try {
    const info = await fetch('/api/info').then((r) => r.json());
    const grid = $('#classGrid');
    info.classes.forEach((name, id) => {
      const chip = document.createElement('button');
      chip.className = 'chip';
      chip.textContent = name;
      chip.dataset.id = id;
      chip.style.setProperty('--chip', chipColor(id));
      chip.addEventListener('click', () => toggleClass(id));
      grid.appendChild(chip);
    });
    applyStats(info.state);
  } catch {
    setTimeout(buildChips, 1500); // server still warming up
  }
}

/* ------------------------------ stats poll ------------------------------ */

function applyStats(st) {
  if (!st) return;
  $('#fpsLabel').textContent = `${st.fps.toFixed(1)} FPS`;
  $('#modelName').textContent = st.model.replace('.pt', '');
  $('#objectsCount').textContent = st.objects;
  $('#liveDot').style.background = st.error ? '#f59e0b' : '';

  S.mode = st.mode;
  S.size = st.size;
  S.boxes = st.boxes;
  if (!confDragging && !confSendTimer) S.conf = st.conf;
  S.classes = new Set(st.classes);
  syncControls();
}

setInterval(async () => {
  try {
    applyStats(await fetch('/stats').then((r) => r.json()));
  } catch { /* server restarting; keep polling */ }
}, 1000);

/* ------------------------------- snapshot ------------------------------- */

$('#snapBtn').addEventListener('click', () => {
  const img = $('#video');
  if (!img.naturalWidth) return;
  const canvas = $('#snapCanvas');
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  canvas.getContext('2d').drawImage(img, 0, 0);
  canvas.toBlob((blob) => {
    if (!blob) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `segmentation-${Date.now()}.png`;
    a.click();
    URL.revokeObjectURL(a.href);
  }, 'image/png');
});

/* --------------------------- keyboard shortcuts ------------------------- */

const SIZE_KEYS = { 1: 'n', 2: 's', 3: 'm' };

window.addEventListener('keydown', (e) => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' && document.activeElement.type === 'text') return;

  if (e.key === 'm' || e.key === 'M') setMode(S.mode === 'yolo' ? 'sam' : 'yolo');
  else if (SIZE_KEYS[e.key]) setSize(SIZE_KEYS[e.key]);
  else if (e.key === '[') setConf(S.conf - 0.05);
  else if (e.key === ']') setConf(S.conf + 0.05);
  else if (e.key === 'b' || e.key === 'B') toggleBoxes();
});

/* --------------------------------- init --------------------------------- */

buildChips();
syncControls();
