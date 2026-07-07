// Live face recognition: MJPEG feed + recognition controls.
import { $, el, api, status, toast } from '/static/app.js?v=2';

let statusFn = null;
let settingsTimer = null;

function pushSettings(partial) {
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(() => {
    api.post('/api/settings', partial).catch((e) => toast(e.message, 'err'));
  }, 150);
}

export async function mount(root) {
  api.get('/set_mode?mode=faces').catch(() => {});

  const info = await api.get('/api/info').catch(() => ({ face_ready: true }));
  const settings = await api.get('/api/settings').catch(() => null);

  const camInput = el('input', { type: 'checkbox', id: 'camToggle' });
  camInput.checked = info.state?.camera_on ?? true;
  camInput.addEventListener('change', () =>
    api.get(`/set_camera?on=${camInput.checked}`).catch((e) => toast(e.message, 'err')));

  root.append(el('div', { class: 'live-layout' },
    el('div', { class: 'live-feed' },
      el('span', { id: 'liveBadge' }, el('span', { id: 'liveDot' }), 'LIVE'),
      el('img', { id: 'liveImg', src: '/video_feed', alt: 'Live face recognition stream' })),

    el('aside', { class: 'live-panel' },
      !info.face_ready && el('div', { class: 'card', style: 'border-color:var(--amber)' },
        el('h2', {}, 'Face models loading'),
        el('p', { class: 'muted', style: 'margin:0' },
          'Recognition is unavailable until the one-time model download finishes. The feed still runs.')),

      el('div', { class: 'statgrid' },
        el('div', { class: 'stat' }, el('div', { class: 'k' }, 'Faces'), el('div', { class: 'v', id: 'faceCount' }, '0')),
        el('div', { class: 'stat' }, el('div', { class: 'k' }, 'FPS'), el('div', { class: 'v', id: 'fpsStat' }, '0'))),

      el('div', { class: 'card' },
        el('h2', {}, 'Camera'),
        el('label', { class: 'switch' }, camInput, el('span', { class: 'track' }), 'Camera on'),
        el('p', { class: 'muted', style: 'margin:6px 0 0' },
          'The camera also turns off whenever you leave this page.')),

      el('div', { class: 'card' },
        el('h2', {}, 'Recognition threshold ', el('output', { id: 'thrOut' }, settings ? settings.rec_threshold.toFixed(2) : '—')),
        el('input', {
          type: 'range', min: '0.2', max: '0.6', step: '0.01',
          value: settings?.rec_threshold ?? 0.363,
          oninput: (e) => {
            $('#thrOut').textContent = (+e.target.value).toFixed(2);
            pushSettings({ rec_threshold: +e.target.value });
          },
        }),
        el('p', { class: 'muted', style: 'margin:8px 0 0' },
          'Lower = more matches (riskier). Default 0.36.')),

      el('div', { class: 'card' },
        el('h2', {}, 'Auto-capture'),
        toggle('Save new faces automatically', settings?.auto_enroll?.enabled, (v) => pushSettings({ auto_enroll: { enabled: v } })),
        el('p', { class: 'muted', style: 'margin:6px 0 0' },
          'Clear unknown faces are saved as 1000, 2000, … — rename them in ',
          el('a', { class: 'plain', href: '#/people' }, 'People'), '.')),

      el('div', { class: 'card' },
        el('h2', {}, 'Overlay'),
        toggle('Show landmarks', settings?.overlay?.show_landmarks, (v) => pushSettings({ overlay: { show_landmarks: v } })),
        toggle('Show match score', settings?.overlay?.show_score, (v) => pushSettings({ overlay: { show_score: v } })),
        el('p', { class: 'muted', style: 'margin:10px 0 0' },
          'Colors and label size are in ', el('a', { class: 'plain', href: '#/settings' }, 'Settings'), '.')),

      el('div', { class: 'card' },
        el('h2', {}, 'Enroll faces'),
        el('p', { class: 'muted', style: 'margin:0 0 10px' },
          'Names appear here once faces are added to the database.'),
        el('a', { class: 'btn primary wide', href: '#/enroll' }, 'Upload photos')),
    )));

  statusFn = (st) => {
    $('#faceCount') && ($('#faceCount').textContent = st.objects);
    $('#fpsStat') && ($('#fpsStat').textContent = st.fps.toFixed(1));
    if (typeof st.camera_on === 'boolean') camInput.checked = st.camera_on;
  };
  status.listeners.add(statusFn);
}

function toggle(label, initial, onChange) {
  const input = el('input', { type: 'checkbox' });
  input.checked = !!initial;
  input.addEventListener('change', () => onChange(input.checked));
  return el('label', { class: 'switch', style: 'margin-bottom:8px' },
    input, el('span', { class: 'track' }), label);
}

export function unmount() {
  status.listeners.delete(statusFn);
  const img = $('#liveImg');
  if (img) img.src = ''; // closes the MJPEG connection
}
