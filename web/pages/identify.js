// Identify: upload one photo; known faces get named, nothing is stored.
import { $, el, api, toast, photoWithFaces, handoff, trackUrl } from '/static/app.js?v=2';

let lastFile = null;

export async function mount(root) {
  const fileInput = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
  fileInput.addEventListener('change', () => fileInput.files[0] && identify(fileInput.files[0]));

  const drop = el('div', { class: 'dropzone' },
    el('div', { class: 'big' }, 'Drop a photo to identify who is in it'),
    el('div', { class: 'small' }, 'Matched against your on-device face database. The photo is not stored.'));
  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('drag');
    const f = [...e.dataTransfer.files].find((f) => f.type.startsWith('image/'));
    if (f) identify(f);
  });

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Identify'),
      el('span', { class: 'sub' }, 'Who is in this photo?')),
    el('div', { class: 'page-body' },
      drop, fileInput,
      el('div', { id: 'idResult', style: 'margin-top:20px' })));
}

async function identify(file) {
  lastFile = file;
  const out = $('#idResult');
  out.innerHTML = '';
  out.append(el('p', { class: 'muted' }, el('span', { class: 'spinner' }), ' Analyzing…'));

  try {
    const res = await api.upload('/api/identify', file, 'file');
    out.innerHTML = '';

    const known = res.faces.filter((f) => f.match);
    const faces = res.faces.map((f) => ({
      ...f,
      cls: f.match ? 'named' : 'unknown',
      tag: f.match ? `${f.match.name} ${f.match.score.toFixed(2)}` : 'Unknown',
    }));

    const url = trackUrl(URL.createObjectURL(file));
    const summary = res.faces.length === 0
      ? 'No faces detected.'
      : `${res.faces.length} face${res.faces.length === 1 ? '' : 's'} — ${known.length} recognized.`;

    out.append(
      el('div', { class: 'card', style: 'max-width:900px' },
        el('div', { style: 'display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;gap:10px;flex-wrap:wrap' },
          el('strong', {}, summary),
          el('div', { class: 'btnrow' },
            ...known.map((f) => el('a', {
              class: 'btn small', href: `#/people/${f.match.person_id}`,
            }, f.match.name)),
            el('button', { class: 'small primary', onclick: addToLibrary }, 'Add to library'))),
        photoWithFaces(url, { w: res.width, h: res.height }, faces)));
  } catch (err) {
    out.innerHTML = '';
    out.append(el('p', { class: 'muted' }, `Failed: ${err.message}`));
    toast(err.message, 'err');
  }
}

async function addToLibrary() {
  if (!lastFile) return;
  try {
    const res = await api.upload('/api/photos', lastFile);
    handoff.enrollResults = res.results; // Enroll renders these on mount
    toast('Added to library — name the faces', 'ok');
    location.hash = '#/enroll';
  } catch (err) {
    toast(err.message, 'err');
  }
}

export function unmount() {
  lastFile = null;
}
