// Phone camera app: fullscreen viewfinder streaming frames to the Mac for
// live recognition (+ auto-capture), and a shutter for Identify / Add
// to library. Installed to the iPhone home screen as a PWA.
import { $, el, api, toast, photoWithFaces } from '/static/app.js?v=2';

const SEND_WIDTH = 640;    // streamed analysis frames (kept small for speed)
const JPEG_QUALITY = 0.7;

let stream = null;
let ws = null;
let running = false;
let facing = 'user';
let video, overlay, sendCanvas;
let lastFaces = [];
let frameSize = { w: 0, h: 0 };

export async function mount(root) {
  document.body.classList.add('phone-mode');

  video = el('video', { class: 'phone-video', autoplay: '', playsinline: '', muted: '' });
  overlay = el('canvas', { class: 'phone-canvas' });
  sendCanvas = document.createElement('canvas');

  root.append(el('div', { class: 'phone-wrap' },
    video, overlay,

    el('div', { class: 'phone-topbar' },
      el('span', { class: 'phone-status' },
        el('span', { id: 'phoneDot', class: 'dot' }), el('span', { id: 'phoneState' }, 'connecting…')),
      el('span', { id: 'phoneFaces', class: 'phone-status' }, ''),
      el('a', { class: 'phone-link', href: '#/people', onclick: () => document.body.classList.remove('phone-mode') }, 'People ▸')),

    el('div', { class: 'phone-bar' },
      el('button', { class: 'phone-side', title: 'Flip camera', onclick: flipCamera }, '⟳'),
      el('button', { class: 'shutter', title: 'Take photo', onclick: takePhoto }),
      el('a', { class: 'phone-side', title: 'Full app', href: '#/live', onclick: () => document.body.classList.remove('phone-mode') }, '⌂')),

    el('div', { id: 'phoneReview', class: 'phone-review', style: 'display:none' })));

  await startCamera();
  connectWS();
}

/* ------------------------------- camera --------------------------------- */

async function startCamera() {
  stopCamera();
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: facing, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    });
    video.srcObject = stream;
    video.dataset.facing = facing; // CSS mirrors the selfie preview
    await video.play().catch(() => {});
    running = true;
    pumpFrames();
  } catch (err) {
    setState('camera blocked', false);
    toast(`Camera error: ${err.message}. On iPhone this page must be opened over HTTPS (see /pair).`, 'err');
  }
}

function stopCamera() {
  running = false;
  if (stream) { stream.getTracks().forEach((t) => t.stop()); stream = null; }
}

function flipCamera() {
  facing = facing === 'user' ? 'environment' : 'user';
  startCamera();
}

/* ------------------------------ streaming -------------------------------- */

function connectWS() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/phone`);
  ws.binaryType = 'arraybuffer';
  ws.onopen = () => { setState('live', true); pumpFrames(); };
  ws.onclose = () => {
    setState('reconnecting…', false);
    if (document.body.classList.contains('phone-mode')) setTimeout(connectWS, 1500);
  };
  ws.onerror = () => {};
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.error) { setState(msg.error, false); }
      else {
        lastFaces = msg.faces;
        frameSize = { w: msg.w, h: msg.h };
        setState('live', true);
        const n = msg.faces.length;
        $('#phoneFaces') && ($('#phoneFaces').textContent =
          n ? `${n} face${n === 1 ? '' : 's'}` : '');
        drawOverlay();
      }
    } catch { /* not JSON */ }
    sendFrame(); // ping-pong: reply received -> next frame
  };
}

function pumpFrames() {
  if (ws?.readyState === WebSocket.OPEN) sendFrame();
}

function sendFrame() {
  if (!running || !video.videoWidth || ws?.readyState !== WebSocket.OPEN) {
    setTimeout(pumpFrames, 200);
    return;
  }
  const scale = SEND_WIDTH / video.videoWidth;
  sendCanvas.width = SEND_WIDTH;
  sendCanvas.height = Math.round(video.videoHeight * scale);
  sendCanvas.getContext('2d').drawImage(video, 0, 0, sendCanvas.width, sendCanvas.height);
  sendCanvas.toBlob((blob) => {
    if (blob && ws?.readyState === WebSocket.OPEN) {
      blob.arrayBuffer().then((buf) => ws.send(buf));
    } else {
      setTimeout(pumpFrames, 200);
    }
  }, 'image/jpeg', JPEG_QUALITY);
}

/* ------------------------------- overlay --------------------------------- */

function drawOverlay() {
  if (!overlay || !video.videoWidth) return;
  const dw = overlay.clientWidth, dh = overlay.clientHeight;
  if (overlay.width !== dw) overlay.width = dw;
  if (overlay.height !== dh) overlay.height = dh;
  const ctx = overlay.getContext('2d');
  ctx.clearRect(0, 0, dw, dh);
  if (!frameSize.w) return;

  // the <video> uses object-fit: cover — map frame coords to display coords
  const s = Math.max(dw / frameSize.w, dh / frameSize.h);
  const ox = (dw - frameSize.w * s) / 2;
  const oy = (dh - frameSize.h * s) / 2;
  const mirrored = facing === 'user'; // selfie preview is mirrored

  ctx.font = '600 14px -apple-system, system-ui, sans-serif';
  ctx.textBaseline = 'bottom';
  ctx.lineWidth = 2.5;

  for (const f of lastFaces) {
    let x = f.bbox.x * s + ox;
    const y = f.bbox.y * s + oy, w = f.bbox.w * s, h = f.bbox.h * s;
    if (mirrored) x = dw - x - w;
    const known = !!f.name;
    const color = known ? '#34d399' : '#f87171';
    ctx.strokeStyle = color;
    ctx.strokeRect(x, y, w, h);

    const label = known ? f.name : 'Unknown';
    const tw = ctx.measureText(label).width + 12;
    ctx.fillStyle = color;
    ctx.fillRect(x - 1, y - 22, tw, 22);
    ctx.fillStyle = '#0b0c0e';
    ctx.fillText(label, x + 5, y - 4);
  }
}

/* ----------------------------- photo capture ----------------------------- */

function setState(text, ok) {
  const dot = $('#phoneDot'), st = $('#phoneState');
  if (dot) dot.className = `dot ${ok ? 'ok' : ''}`;
  if (st) st.textContent = text;
}

function takePhoto() {
  if (!video.videoWidth) return;
  const c = document.createElement('canvas');
  c.width = video.videoWidth;
  c.height = video.videoHeight;
  c.getContext('2d').drawImage(video, 0, 0);
  c.toBlob((blob) => {
    if (blob) reviewPhoto(new File([blob], `phone-${Date.now()}.jpg`, { type: 'image/jpeg' }));
  }, 'image/jpeg', 0.92);
}

function reviewPhoto(file) {
  const review = $('#phoneReview');
  const url = URL.createObjectURL(file);
  review.innerHTML = '';
  review.style.display = '';
  const imgBox = el('div', { class: 'phone-review-img' }, el('img', { src: url, alt: '' }));

  const actions = el('div', { class: 'phone-review-bar' },
    el('button', { class: 'wide', onclick: () => identify(file, imgBox, actions) }, 'Identify'),
    el('button', { class: 'wide primary', onclick: () => addToLibrary(file, imgBox, actions) }, 'Add to library'),
    el('button', { class: 'ghost wide', onclick: closeReview }, 'Back to camera'));

  review.append(imgBox, actions);
}

function closeReview() {
  const review = $('#phoneReview');
  if (review) { review.style.display = 'none'; review.innerHTML = ''; }
}

async function identify(file, imgBox, actions) {
  setBusy(actions, 'Identifying…');
  try {
    const res = await api.upload('/api/identify', file, 'file');
    const faces = res.faces.map((f) => ({
      ...f,
      cls: f.match ? 'named' : 'unknown',
      tag: f.match ? `${f.match.name} ${f.match.score.toFixed(2)}` : 'Unknown',
    }));
    imgBox.innerHTML = '';
    imgBox.append(photoWithFaces(URL.createObjectURL(file), { w: res.width, h: res.height }, faces));
    const known = faces.filter((f) => f.match);
    toast(known.length ? `Recognized: ${known.map((f) => f.match.name).join(', ')}` : 'No one recognized', known.length ? 'ok' : '');
  } catch (err) {
    toast(err.message, 'err');
  }
  setBusy(actions, null);
}

async function addToLibrary(file, imgBox, actions) {
  setBusy(actions, 'Saving…');
  try {
    const res = await api.upload('/api/photos', file);
    const r = res.results[0];
    if (r.error) throw new Error(r.error);
    const faces = r.faces.map((f) => ({
      ...f,
      cls: f.person_name ? 'named' : '',
      tag: f.person_name || (f.suggestion ? `${f.suggestion.name}?` : 'tap to name'),
    }));
    imgBox.innerHTML = '';
    imgBox.append(photoWithFaces(r.url, { w: r.width, h: r.height }, faces, {
      onFaceClick: (f, node) => nameFace(f, node),
    }));
    toast(`Saved — ${faces.length} face${faces.length === 1 ? '' : 's'} detected. Tap a face to name it.`, 'ok');
  } catch (err) {
    toast(err.message, 'err');
  }
  setBusy(actions, null);
}

function nameFace(face, node) {
  const sheet = el('div', { class: 'phone-sheet' });
  const input = el('input', {
    type: 'text', placeholder: 'Name this face…', autocomplete: 'off',
    value: face.person_name || face.suggestion?.name || '',
  });
  const save = async () => {
    const name = input.value.trim();
    if (!name) { sheet.remove(); return; }
    try {
      await api.post('/api/faces/label', { labels: [{ face_id: face.face_id, name }] });
      face.person_name = name;
      node.classList.add('named');
      let tag = node.querySelector('.tag');
      if (!tag) { tag = el('span', { class: 'tag' }); node.append(tag); }
      tag.textContent = name;
      toast(`Saved "${name}"`, 'ok');
    } catch (err) { toast(err.message, 'err'); }
    sheet.remove();
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter') save(); });
  sheet.append(input,
    el('button', { class: 'primary', onclick: save }, 'Save'),
    el('button', { class: 'ghost', onclick: () => sheet.remove() }, 'Cancel'));
  document.body.append(sheet);
  input.focus();
}

function setBusy(actions, text) {
  actions.querySelectorAll('button').forEach((b) => { b.disabled = !!text; });
}

export function unmount() {
  document.body.classList.remove('phone-mode');
  stopCamera();
  if (ws) { ws.onclose = null; ws.close(); ws = null; }
  lastFaces = [];
}
