// FaceVision SPA: hash router + shared helpers. Pages live in /static/pages/.
'use strict';

import * as live from '/static/pages/live.js';
import * as enroll from '/static/pages/enroll.js';
import * as identify from '/static/pages/identify.js';
import * as search from '/static/pages/search.js';
import * as people from '/static/pages/people.js';
import * as objects from '/static/pages/objects.js';
import * as settings from '/static/pages/settings.js';
import * as phone from '/static/pages/phone.js';

/* -------------------------------- helpers ------------------------------- */

export const $ = (sel, root = document) => root.querySelector(sel);

// cross-page handoff (e.g. Identify -> "Add to library" -> Enroll)
export const handoff = {};

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c.nodeType ? c : document.createTextNode(c));
  }
  return node;
}

export function toast(msg, type = '') {
  const t = el('div', { class: `toast ${type}` }, msg);
  $('#toasts').append(t);
  setTimeout(() => { t.style.opacity = '0'; t.style.transition = 'opacity .3s'; }, 3200);
  setTimeout(() => t.remove(), 3600);
}

async function handle(resp) {
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail || detail; } catch { /* not json */ }
    throw new Error(detail);
  }
  return resp.json();
}

export const api = {
  get: (url) => fetch(url).then(handle),
  post: (url, body) => fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(handle),
  patch: (url, body) => fetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }).then(handle),
  del: (url) => fetch(url, { method: 'DELETE' }).then(handle),
  // multipart upload; `files` may be a File or an array of Files
  upload: (url, files, field = 'files', extra = {}) => {
    const fd = new FormData();
    for (const f of [].concat(files)) fd.append(field, f);
    for (const [k, v] of Object.entries(extra)) fd.append(k, v);
    return fetch(url, { method: 'POST', body: fd }).then(handle);
  },
};

/* ----------------------- face overlays on an image ---------------------- */

// Absolutely-positioned boxes in % of the image's natural size, so they
// track responsive resizing for free. Returns the .photobox wrapper.
export function photoWithFaces(src, natural, faces, opts = {}) {
  const box = el('div', { class: 'photobox' });
  const img = el('img', { src, alt: '' });
  box.append(img);
  const place = () => {
    const w = natural?.w || img.naturalWidth;
    const h = natural?.h || img.naturalHeight;
    if (!w || !h) return;
    for (const f of faces) {
      const b = f.bbox;
      const fb = el('button', {
        class: `facebox ${f.cls || ''}`,
        style: `left:${(b.x / w) * 100}%;top:${(b.y / h) * 100}%;` +
               `width:${(b.w / w) * 100}%;height:${(b.h / h) * 100}%;`,
        title: f.title || '',
      });
      if (f.tag) fb.append(el('span', { class: 'tag' }, f.tag));
      if (opts.onFaceClick) fb.addEventListener('click', (e) => { e.stopPropagation(); opts.onFaceClick(f, fb); });
      box.append(fb);
      f._node = fb;
    }
  };
  if (natural?.w) place();
  else if (img.complete && img.naturalWidth) place();
  else img.addEventListener('load', place, { once: true });
  if (opts.onClick) { box.style.cursor = 'zoom-in'; img.addEventListener('click', () => opts.onClick()); }
  return box;
}

/* --------------------------------- modal -------------------------------- */

export function modal(build) {
  const root = $('#modalRoot');
  const backdrop = el('div', { class: 'modal-backdrop' });
  const box = el('div', { class: 'modal' });
  backdrop.append(box);
  const close = () => backdrop.remove();
  backdrop.addEventListener('click', (e) => { if (e.target === backdrop) close(); });
  window.addEventListener('keydown', function esc(e) {
    if (e.key === 'Escape') { close(); window.removeEventListener('keydown', esc); }
  });
  build(box, close);
  root.append(backdrop);
  return close;
}

export function confirmModal(text, onYes, yesLabel = 'Delete') {
  modal((box, close) => {
    box.append(
      el('h3', {}, 'Are you sure?'),
      el('p', { class: 'muted' }, text),
      el('div', { class: 'btnrow', style: 'justify-content:flex-end;margin-top:16px' },
        el('button', { onclick: close }, 'Cancel'),
        el('button', { class: 'danger', onclick: () => { close(); onYes(); } }, yesLabel)),
    );
  });
}

export function lightbox(src, faces = [], natural = null) {
  const lb = el('div', { class: 'lightbox' });
  const pb = photoWithFaces(src, natural, faces);
  pb.style.cursor = 'default';
  lb.append(pb);
  lb.append(el('div', { class: 'actions' },
    el('a', { class: 'btn', href: src, target: '_blank' }, 'Open original'),
    el('button', { onclick: () => lb.remove() }, 'Close')));
  lb.addEventListener('click', (e) => { if (e.target === lb) lb.remove(); });
  document.body.append(lb);
}

/* --------------------------------- router ------------------------------- */

const routes = [
  { match: /^#\/live$/, page: live, id: 'live' },
  { match: /^#\/enroll$/, page: enroll, id: 'enroll' },
  { match: /^#\/identify$/, page: identify, id: 'identify' },
  { match: /^#\/search$/, page: search, id: 'search' },
  { match: /^#\/people$/, page: people, id: 'people' },
  { match: /^#\/people\/(\d+)$/, page: people, id: 'people' },
  { match: /^#\/objects$/, page: objects, id: 'objects' },
  { match: /^#\/settings$/, page: settings, id: 'settings' },
  { match: /^#\/phone$/, page: phone, id: 'phone' },
];

let current = null;

async function route() {
  const hash = location.hash || '#/live';
  const r = routes.find((r) => r.match.test(hash)) || routes[0];
  const params = hash.match(r.match)?.slice(1) || [];

  if (current?.unmount) { try { current.unmount(); } catch { /* page cleanup */ } }
  current = r.page;

  document.querySelectorAll('.navlink').forEach((a) =>
    a.classList.toggle('active', a.dataset.page === r.id));

  const main = $('#page');
  main.innerHTML = '';
  main.scrollTop = 0;
  try {
    await r.page.mount(main, params);
  } catch (err) {
    main.append(el('div', { class: 'empty' },
      el('div', { class: 'icon' }, '⚠'), `Failed to load page: ${err.message}`));
  }
}

window.addEventListener('hashchange', route);

/* ----------------------------- status polling --------------------------- */

export const status = { state: null, faceReady: false, listeners: new Set() };

async function pollStatus() {
  try {
    const st = await api.get('/stats');
    status.state = st;
    const dot = $('#statusDot'), txt = $('#statusText');
    if (st.error) { dot.className = 'warn'; txt.textContent = st.error; }
    else { dot.className = 'ok'; txt.textContent = `${st.device} · ${st.fps.toFixed(0)} fps`; }
    status.listeners.forEach((fn) => { try { fn(st); } catch { /* listener */ } });
  } catch {
    $('#statusDot').className = '';
    $('#statusText').textContent = 'offline';
  }
}

setInterval(pollStatus, 1000);
pollStatus();

/* ---------------------------------- init -------------------------------- */

// phones land on the camera app; desktops on the Mac live view
if (!location.hash) {
  const phoney = matchMedia('(display-mode: standalone)').matches ||
    /iPhone|iPad|Android/i.test(navigator.userAgent);
  location.hash = phoney ? '#/phone' : '#/live';
}
route();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => { /* http context */ });
}
