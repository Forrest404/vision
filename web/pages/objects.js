// Object segmentation (YOLO11-seg / FastSAM) — the original tool as a page.
import { $, el, api, status } from '/static/app.js?v=3';

const send = (url) => fetch(url).catch(() => {});
const chipColor = (i) => `hsl(${(i * 137.508) % 360} 85% 55%)`; // same wheel as the masks

const S = { mode: 'yolo', size: 's', conf: 0.35, boxes: false, classes: new Set() };
let lastObjectMode = 'yolo'; // survives page switches within the session
let statusFn = null;
let confDragging = false;
let confSendTimer = null;

function syncControls() {
  const modeBtn = $('#modeBtn');
  if (!modeBtn) return;
  modeBtn.textContent = S.mode === 'sam' ? 'FastSAM' : 'YOLO Seg';
  modeBtn.classList.toggle('active', S.mode === 'sam');

  document.querySelectorAll('#sizeRow button').forEach((b) =>
    b.classList.toggle('active', b.dataset.size === S.size));

  $('#confOut').textContent = S.conf.toFixed(2);
  if (!confDragging) $('#confSlider').value = S.conf;

  $('#boxesBtn').textContent = `Bounding boxes: ${S.boxes ? 'On' : 'Off'}`;
  $('#boxesBtn').classList.toggle('active', S.boxes);

  document.querySelectorAll('.chip').forEach((c) =>
    c.classList.toggle('active', S.classes.has(+c.dataset.id)));

  const sam = S.mode === 'sam';
  $('#sizeGroup').classList.toggle('disabled', sam);
  $('#classGroup').classList.toggle('disabled', sam);
}

function setMode(mode) {
  S.mode = mode;
  lastObjectMode = mode;
  send(`/set_mode?mode=${mode}`);
  syncControls();
}

function setSize(size) {
  S.size = size;
  send(`/set_model_size?size=${size}`);
  syncControls();
}

function setConf(value) {
  S.conf = Math.min(0.95, Math.max(0.1, Math.round(value * 20) / 20));
  clearTimeout(confSendTimer);
  confSendTimer = setTimeout(() => {
    confSendTimer = null;
    send(`/set_confidence?value=${S.conf}`);
  }, 120);
  syncControls();
}

function toggleClass(id) {
  S.classes.has(id) ? S.classes.delete(id) : S.classes.add(id);
  send(`/set_classes?ids=${[...S.classes].join(',')}`);
  syncControls();
}

export async function mount(root) {
  send(`/set_mode?mode=${lastObjectMode}`);

  const camInput = el('input', { type: 'checkbox', id: 'camToggle' });
  camInput.checked = true;
  camInput.addEventListener('change', () => send(`/set_camera?on=${camInput.checked}`));

  root.append(el('div', { class: 'live-layout' },
    el('div', { class: 'live-feed' },
      el('span', { id: 'liveBadge' }, el('span', { id: 'liveDot' }), 'LIVE'),
      el('img', { id: 'liveImg', src: '/video_feed', alt: 'Live segmentation stream' })),

    el('aside', { class: 'live-panel' },
      el('div', { class: 'statgrid' },
        el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Objects'), el('div', { class: 'v', id: 'objCount' }, '0')),
        el('div', { class: 'stat' }, el('div', { class: 'k' }, 'FPS'), el('div', { class: 'v', id: 'fpsStat' }, '0'))),

      el('div', { class: 'card' },
        el('h2', {}, 'Camera'),
        el('label', { class: 'switch' }, camInput, el('span', { class: 'track' }), 'Camera on'),
        el('p', { class: 'muted', style: 'margin:6px 0 0' },
          'The camera also turns off whenever you leave this page.')),

      el('div', { class: 'card' },
        el('h2', {}, 'Mode ', el('span', { class: 'hint', id: 'modelName' }, '')),
        el('button', { id: 'modeBtn', class: 'wide', onclick: () => setMode(S.mode === 'yolo' ? 'sam' : 'yolo') }, 'YOLO Seg')),

      el('div', { class: 'card', id: 'sizeGroup' },
        el('h2', {}, 'Model size'),
        el('div', { class: 'seg-row', id: 'sizeRow' },
          el('button', { 'data-size': 'n', onclick: () => setSize('n') }, 'Nano'),
          el('button', { 'data-size': 's', onclick: () => setSize('s') }, 'Small'),
          el('button', { 'data-size': 'm', onclick: () => setSize('m') }, 'Medium'))),

      el('div', { class: 'card' },
        el('h2', {}, 'Confidence ', el('output', { id: 'confOut' }, '0.35')),
        el('input', { type: 'range', id: 'confSlider', min: '0.1', max: '0.95', step: '0.05', value: '0.35' })),

      el('div', { class: 'card' },
        el('button', { id: 'boxesBtn', class: 'wide', onclick: () => { S.boxes = !S.boxes; send('/toggle_boxes'); syncControls(); } }, 'Bounding boxes: Off')),

      el('div', { class: 'card', id: 'classGroup' },
        el('h2', {}, 'Class filter ', el('span', { class: 'hint' }, 'none = all')),
        el('div', { class: 'chipgrid', id: 'classGrid' })),

      el('button', { id: 'snapBtn', class: 'wide primary', onclick: snapshot }, 'Snapshot'),
      el('p', { class: 'muted', style: 'text-align:center;margin:0' },
        'M mode · 1/2/3 size · [ ] confidence · B boxes'),
    )));

  const slider = $('#confSlider');
  slider.addEventListener('pointerdown', () => { confDragging = true; });
  window.addEventListener('pointerup', pointerUp);
  slider.addEventListener('input', () => setConf(+slider.value));
  window.addEventListener('keydown', keys);

  try {
    const info = await api.get('/api/info');
    if (typeof info.state?.camera_on === 'boolean') camInput.checked = info.state.camera_on;
    const grid = $('#classGrid');
    if (grid) {
      info.classes.forEach((name, id) => {
        const chip = el('button', { class: 'chip', 'data-id': id, onclick: () => toggleClass(id) }, name);
        chip.style.setProperty('--chip', chipColor(id));
        grid.append(chip);
      });
    }
  } catch { /* server warming up; stats poll keeps things in sync */ }

  statusFn = (st) => {
    if (!$('#objCount')) return;
    $('#objCount').textContent = st.objects;
    $('#fpsStat').textContent = st.fps.toFixed(1);
    $('#modelName').textContent = st.model.replace('.pt', '');
    if (typeof st.camera_on === 'boolean') camInput.checked = st.camera_on;
    if (st.mode === 'faces') return; // another page just switched modes
    S.mode = st.mode;
    S.size = st.size;
    S.boxes = st.boxes;
    if (!confDragging && !confSendTimer) S.conf = st.conf;
    S.classes = new Set(st.classes);
    syncControls();
  };
  status.listeners.add(statusFn);
  syncControls();
}

function pointerUp() { confDragging = false; }

const SIZE_KEYS = { 1: 'n', 2: 's', 3: 'm' };
function keys(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (e.key === 'm' || e.key === 'M') setMode(S.mode === 'yolo' ? 'sam' : 'yolo');
  else if (SIZE_KEYS[e.key]) setSize(SIZE_KEYS[e.key]);
  else if (e.key === '[') setConf(S.conf - 0.05);
  else if (e.key === ']') setConf(S.conf + 0.05);
  else if (e.key === 'b' || e.key === 'B') { S.boxes = !S.boxes; send('/toggle_boxes'); syncControls(); }
}

function snapshot() {
  const img = $('#liveImg');
  if (!img || !img.naturalWidth) return;
  const canvas = document.createElement('canvas');
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
}

export function unmount() {
  status.listeners.delete(statusFn);
  window.removeEventListener('keydown', keys);
  window.removeEventListener('pointerup', pointerUp);
  const img = $('#liveImg');
  if (img) img.src = '';
}
