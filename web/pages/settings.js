// Settings: recognition, detection, overlay style, camera. Persisted in the
// on-device DB and pushed into the live pipeline immediately.
import { el, api, toast } from '/static/app.js?v=2';

let saveTimer = null;

function push(partial) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(async () => {
    try { await api.post('/api/settings', partial); }
    catch (err) { toast(`Could not save: ${err.message}`, 'err'); }
  }, 200);
}

export async function mount(root) {
  let s;
  try {
    s = await api.get('/api/settings');
  } catch (err) {
    root.append(el('div', { class: 'empty' }, `Could not load settings: ${err.message}`));
    return;
  }

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Settings'),
      el('span', { class: 'sub' }, 'Stored on this device · applied live')),
    el('div', { class: 'page-body' },
      el('div', { class: 'settings-grid' },

        el('div', { class: 'card' },
          el('h2', {}, 'Recognition'),
          slider('Match threshold', s.rec_threshold, 0.2, 0.6, 0.01,
            (v) => push({ rec_threshold: v }),
            'A face matches a person when cosine similarity is at or above this. Lower = more matches but more mistakes. SFace default: 0.363.'),
          slider('Detector confidence', s.det_score, 0.3, 0.95, 0.01,
            (v) => push({ det_score: v }),
            'Minimum score for YuNet to call something a face. Lower finds smaller/harder faces but risks false positives.')),

        el('div', { class: 'card' },
          el('h2', {}, 'Auto-capture'),
          toggle('Save new faces from the live feed', s.auto_enroll?.enabled,
            (v) => push({ auto_enroll: { enabled: v } })),
          el('p', { class: 'muted', style: 'margin:0 0 12px;font-size:.76rem' },
            'Clear unknown faces are stored as numbered people (1000, 2000, …) with a snapshot. Rename them on the People page.'),
          slider('Capture quality', s.auto_enroll?.min_score ?? 0.8, 0.5, 0.95, 0.01,
            (v) => push({ auto_enroll: { min_score: v } }),
            'How confident the detector must be before a stranger is saved. Higher = fewer but cleaner captures.'),
          slider('Min face size (px)', s.auto_enroll?.min_size ?? 80, 40, 200, 5,
            (v) => push({ auto_enroll: { min_size: v } }),
            'Faces smaller than this on screen are too far away to enroll reliably.')),

        el('div', { class: 'card' },
          el('h2', {}, 'Overlay style'),
          colorRow('Known face box', s.overlay.box_color, (v) => push({ overlay: { box_color: v } })),
          colorRow('Unknown face box', s.overlay.unknown_color, (v) => push({ overlay: { unknown_color: v } })),
          slider('Box thickness', s.overlay.box_thickness, 1, 6, 1,
            (v) => push({ overlay: { box_thickness: v } })),
          slider('Label size', s.overlay.label_scale, 0.4, 1.2, 0.05,
            (v) => push({ overlay: { label_scale: v } })),
          toggle('Show 5-point landmarks', s.overlay.show_landmarks, (v) => push({ overlay: { show_landmarks: v } })),
          toggle('Show match score', s.overlay.show_score, (v) => push({ overlay: { show_score: v } }))),

        el('div', { class: 'card' },
          el('h2', {}, 'Camera'),
          el('div', { class: 'field' },
            el('label', {}, 'Device'),
            el('div', { id: 'cameraSelect' }, el('span', { class: 'muted' }, 'Detecting cameras…'))),
          el('div', { class: 'field' },
            el('label', {}, 'Resolution'),
            select(`${s.camera.width}x${s.camera.height}`,
              ['640x480', '1280x720', '1920x1080'],
              (v) => {
                const [width, height] = v.split('x').map(Number);
                push({ camera: { width, height } });
                toast('Camera restarts with the new size', 'ok');
              })),
          el('p', { class: 'muted', style: 'margin:4px 0 0' },
            'Higher resolutions find smaller faces but lower the frame rate.')),

        el('div', { class: 'card', id: 'aboutCard' },
          el('h2', {}, 'About'),
          el('p', { class: 'muted', style: 'margin:0 0 8px' },
            'Everything runs on this device: YuNet face detection, SFace embeddings, YOLO11-seg and FastSAM. ' +
            'The face database lives in the project\'s data/ folder.'),
          el('p', { class: 'muted', style: 'margin:0' },
            'Delete data/ to wipe all stored faces and photos.')),
      )));

  loadCameras(s.camera.index ?? 0);
}

async function loadCameras(currentIndex) {
  const holder = document.getElementById('cameraSelect');
  try {
    const { cameras } = await api.get('/api/cameras');
    if (!holder) return; // navigated away while probing
    holder.innerHTML = '';
    if (!cameras.length) {
      holder.append(el('span', { class: 'muted' }, 'No cameras found.'));
      return;
    }
    const sel = el('select', {},
      cameras.map((c) => {
        const opt = el('option', { value: c.index }, c.name || `Camera ${c.index}`);
        if (c.index === currentIndex) opt.selected = true;
        return opt;
      }));
    sel.addEventListener('change', () => {
      push({ camera: { index: +sel.value } });
      toast('Switching camera…', 'ok');
    });
    holder.append(sel);
  } catch (err) {
    if (holder) {
      holder.innerHTML = '';
      holder.append(el('span', { class: 'muted' }, `Could not list cameras: ${err.message}`));
    }
  }
}

/* ------------------------------ controls -------------------------------- */

function slider(label, value, min, max, step, onChange, hint = '') {
  const out = el('output', {}, (+value).toFixed(step >= 1 ? 0 : 2));
  const input = el('input', { type: 'range', min, max, step, value });
  input.addEventListener('input', () => {
    out.textContent = (+input.value).toFixed(step >= 1 ? 0 : 2);
    onChange(+input.value);
  });
  return el('div', { class: 'field' },
    el('label', { style: 'display:flex;justify-content:space-between' }, label, out),
    input,
    hint ? el('span', { class: 'muted', style: 'font-size:.76rem' }, hint) : null);
}

function colorRow(label, value, onChange) {
  const input = el('input', { type: 'color', value });
  input.addEventListener('input', () => onChange(input.value));
  return el('div', { class: 'field' },
    el('div', { class: 'row', style: 'justify-content:space-between' },
      el('label', {}, label), input));
}

function toggle(label, initial, onChange) {
  const input = el('input', { type: 'checkbox' });
  input.checked = !!initial;
  input.addEventListener('change', () => onChange(input.checked));
  return el('label', { class: 'switch', style: 'margin-bottom:10px' },
    input, el('span', { class: 'track' }), label);
}

function select(current, options, onChange) {
  const sel = el('select', {},
    options.map((o) => {
      const opt = el('option', { value: o }, o.replace('x', ' × '));
      if (o === current) opt.selected = true;
      return opt;
    }));
  sel.addEventListener('change', () => onChange(sel.value));
  return sel;
}

export function unmount() {
  clearTimeout(saveTimer);
}
