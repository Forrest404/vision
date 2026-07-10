// Settings: recognition, detection, overlay style, camera. Persisted in the
// on-device DB and pushed into the live pipeline immediately.
import { el, api, toast } from '/static/app.js?v=3';

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

        el('div', { class: 'card', style: 'border-color:var(--accent)' },
          el('h2', {}, 'Compliance'),
          toggle('Watchlist mode (retail-safe)', s.watchlist?.enabled,
            (v) => push({ watchlist: { enabled: v } })),
          el('p', { class: 'muted', style: 'margin:0 0 12px;font-size:.76rem' },
            'When ON, the live feed NEVER stores new faces — it only alerts on '
            + 'people you have added to a watchlist. This is the lawful shape for '
            + 'monitoring customers. Turning it on disables auto-capture.'),
          slider('Keep watchlist events (days)', s.retention?.events_days ?? 30, 1, 365, 1,
            (v) => push({ retention: { events_days: v } })),
          slider('Keep unmatched captures (days)', s.retention?.unmatched_faces_days ?? 7, 0, 90, 1,
            (v) => push({ retention: { unmatched_faces_days: v } }),
            'Non-watchlist auto-captures older than this are auto-deleted. Watchlisted people are always kept.'),
          ackRow(s.compliance_ack),
          el('p', { class: 'muted', style: 'margin:10px 0 0;font-size:.72rem' },
            'Legal note: deploying face recognition on the public requires consent '
            + 'signage, a privacy/DPIA assessment, and sign-off for your jurisdiction. '
            + 'This software provides controls, not legal advice.')),

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
            'Clear unknown faces are stored as numbered people (0001, 0002, …) with a snapshot. Rename them on the People page.'),
          slider('Capture quality', s.auto_enroll?.min_score ?? 0.6, 0.3, 0.95, 0.01,
            (v) => push({ auto_enroll: { min_score: v } }),
            'How confident the detector must be before a stranger is saved. Lower captures more (a few soft shots included); higher = fewer but cleaner.'),
          slider('Min face size (px)', s.auto_enroll?.min_size ?? 60, 40, 200, 5,
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
          el('h2', {}, 'My account'),
          passwordForm(),
          el('p', { class: 'muted', style: 'margin:8px 0 0;font-size:.76rem' },
            'Manage all accounts on the ', el('a', { class: 'plain', href: '#/users' }, 'Users'),
            ' page (admins only).')),

        el('div', { class: 'card' },
          el('h2', {}, 'iPhone camera'),
          toggle('Allow phone pairing & streaming', s.phone?.enabled, async (v) => {
            clearTimeout(saveTimer); // apply instantly, then draw/hide the QR
            try {
              await api.post('/api/settings', { phone: { enabled: v } });
              renderQR(v);
            } catch (err) { toast(err.message, 'err'); }
          }),
          el('div', { id: 'qrArea', style: 'text-align:center' }),
          el('p', { class: 'muted', style: 'margin:10px 0 0;font-size:.76rem' },
            'Scan the code with the iPhone camera, then follow the steps to ' +
            'install FaceVision as an app. While off, phones cannot connect.')),

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
          el('h2', {}, 'Library'),
          el('div', { class: 'statgrid', id: 'libStats', style: 'margin-bottom:12px' }),
          el('a', { class: 'btn wide', href: '/api/library/export', download: '' }, 'Export backup (.zip)'),
          el('p', { class: 'muted', style: 'margin:10px 0 0;font-size:.76rem' },
            'Everything runs on this device: YuNet detection, SFace embeddings, ' +
            'YOLO11-seg and FastSAM. The backup zip contains the database and every photo — ' +
            'restore by unzipping it over the project\'s data/ folder.')),
      )));

  loadCameras(s.camera.index ?? 0);
  renderQR(!!s.phone?.enabled);
  loadStats();
}

async function loadStats() {
  const grid = document.getElementById('libStats');
  try {
    const s = await api.get('/api/library/stats');
    if (!grid || !grid.isConnected) return;
    const stat = (k, v) => el('div', { class: 'stat' },
      el('div', { class: 'k' }, k), el('div', { class: 'v' }, v));
    grid.append(
      stat('People', s.persons), stat('Photos', s.photos),
      stat('Faces', s.faces), stat('Unnamed', s.unlabeled_faces));
  } catch { /* server warming up */ }
}

async function renderQR(enabled) {
  const area = document.getElementById('qrArea');
  if (!area) return;
  area.innerHTML = '';
  if (!enabled) return;
  try {
    const info = await api.get('/api/pair/info');
    area.append(
      el('img', {
        src: `/api/pair/qr.png?t=${Date.now()}`, alt: 'Pairing QR code',
        style: 'width:200px;height:200px;border-radius:10px;margin-top:10px;background:#fff;padding:6px',
      }),
      el('p', { class: 'muted', style: 'margin:8px 0 0;font-size:.78rem' },
        'or open ', el('code', {}, info.url), ' on the phone'));
  } catch (err) {
    area.append(el('p', { class: 'muted' }, `QR unavailable: ${err.message}`));
  }
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
    if (holder && holder.isConnected) {
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

function passwordForm() {
  const cur = el('input', { type: 'password', placeholder: 'Current password', autocomplete: 'current-password' });
  const nw = el('input', { type: 'password', placeholder: 'New password (min 8)', autocomplete: 'new-password' });
  const btn = el('button', { class: 'primary', onclick: async () => {
    btn.disabled = true;
    try {
      await api.post('/api/password', { current: cur.value, new: nw.value });
      toast('Password changed', 'ok'); cur.value = ''; nw.value = '';
    } catch (err) { toast(err.message, 'err'); }
    btn.disabled = false;
  } }, 'Change password');
  return el('div', { class: 'field', style: 'gap:10px' }, cur, nw, btn);
}

function ackRow(initial) {
  const input = el('input', { type: 'checkbox' });
  input.checked = !!initial;
  input.addEventListener('change', () => push({ compliance_ack: input.checked }));
  return el('label', { class: 'switch', style: 'margin:6px 0 4px' },
    input, el('span', { class: 'track' }),
    'I have consent signage & a privacy assessment in place');
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
