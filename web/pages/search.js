// Search: upload a face photo -> every stored photo containing that person,
// with the matching face highlighted and a link to the full photo.
import { $, el, api, toast, photoWithFaces, lightbox, handoff, trackUrl } from '/static/app.js?v=3';

let queryFile = null;

export async function mount(root) {
  const fileInput = el('input', { type: 'file', accept: 'image/*', style: 'display:none' });
  fileInput.addEventListener('change', () => fileInput.files[0] && search(fileInput.files[0]));

  const drop = el('div', { class: 'dropzone' },
    el('div', { class: 'big' }, 'Drop a photo of a face to find them everywhere'),
    el('div', { class: 'small' }, 'Searches every face stored on this device — labeled or not.'));
  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('drag');
    const f = [...e.dataTransfer.files].find((f) => f.type.startsWith('image/'));
    if (f) search(f);
  });

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Face search'),
      el('span', { class: 'sub' }, 'Find every photo of a person')),
    el('div', { class: 'page-body' },
      drop, fileInput,
      el('div', { id: 'searchOut', style: 'margin-top:20px' })));

  if (handoff.searchPersonId) { // arrived via People -> "Find photos"
    const pid = handoff.searchPersonId;
    delete handoff.searchPersonId;
    await personPhotos(pid);
  }
}

/* -------------------- search by an uploaded face photo ------------------ */

async function search(file, faceIndex = null) {
  queryFile = file;
  const out = $('#searchOut');
  out.innerHTML = '';
  out.append(el('p', { class: 'muted' }, el('span', { class: 'spinner' }), ' Searching…'));

  try {
    const extra = faceIndex === null ? {} : { face_index: faceIndex };
    const res = await api.upload('/api/search/face', file, 'file', extra);
    out.innerHTML = '';

    if (!res.query_faces.length) {
      out.append(el('div', { class: 'empty' }, el('div', { class: 'icon' }, '🔍'), 'No face found in that photo.'));
      return;
    }

    if (res.needs_selection) {
      out.append(el('div', { class: 'card', style: 'max-width:760px' },
        el('h2', {}, 'Multiple faces — pick the one to search for'),
        photoWithFaces(trackUrl(URL.createObjectURL(file)), { w: res.width, h: res.height },
          res.query_faces.map((f, i) => ({ ...f, tag: `${i + 1}`, _idx: i })),
          { onFaceClick: (f) => search(file, f._idx) })));
      return;
    }

    renderMatches(out, res.matches, res.person
      ? el('span', {}, 'Best match: ',
          el('a', { class: 'plain', href: `#/people/${res.person.id}` }, res.person.name),
          ` (${res.person.score.toFixed(2)})`)
      : 'No labeled person matched — showing similar stored faces.');
  } catch (err) {
    out.innerHTML = '';
    out.append(el('p', { class: 'muted' }, `Search failed: ${err.message}`));
    toast(err.message, 'err');
  }
}

/* -------------------- all photos of a known person ---------------------- */

async function personPhotos(personId) {
  const out = $('#searchOut');
  out.innerHTML = '';
  out.append(el('p', { class: 'muted' }, 'Loading…'));
  try {
    const person = await api.get(`/api/persons/${personId}`);
    out.innerHTML = '';
    const matches = person.photos.map((photo) => ({
      ...photo,
      best_score: null,
      faces: photo.faces
        .filter((f) => f.person_id === personId)
        .map((f) => ({ ...f, match_score: null })),
    }));
    renderMatches(out, matches,
      el('span', {}, 'All photos of ',
        el('a', { class: 'plain', href: `#/people/${personId}` }, person.name)));
  } catch (err) {
    out.innerHTML = '';
    out.append(el('p', { class: 'muted' }, `Failed: ${err.message}`));
  }
}

/* ------------------------------ result grid ----------------------------- */

function renderMatches(out, matches, headline) {
  out.append(el('p', { style: 'margin:0 0 14px' }, headline));

  if (!matches.length) {
    out.append(el('div', { class: 'empty' },
      el('div', { class: 'icon' }, '🕳'), 'No stored photos contain this face.'));
    return;
  }

  out.append(el('div', { class: 'gallery' },
    matches.map((m) => el('div', { class: 'tile' },
      el('div', { class: 'thumbwrap' },
        el('img', {
          src: m.thumb_url, alt: '',
          onclick: async () => {
            // full photo with ALL its faces; matched ones highlighted
            const photo = await api.get(`/api/photos/${m.photo_id}`).catch(() => null);
            const matchedIds = new Set(m.faces.map((f) => f.face_id));
            const faces = (photo?.faces || m.faces).map((f) => ({
              ...f,
              cls: matchedIds.has(f.face_id) ? 'highlight' : (f.person_name ? 'named' : ''),
              tag: matchedIds.has(f.face_id)
                ? (f.person_name || 'match')
                : (f.person_name || null),
            }));
            lightbox(m.url, faces, photo ? { w: photo.width, h: photo.height } : { w: m.width, h: m.height });
          },
        })),
      el('div', { class: 'meta' },
        el('span', { class: 'name' }, m.original_name || `#${m.photo_id}`),
        el('span', { style: 'display:flex;gap:6px;align-items:center' },
          m.best_score !== null && m.best_score !== undefined
            ? el('span', { class: 'scorebadge' }, m.best_score.toFixed(2)) : null,
          el('a', { class: 'plain', href: m.url, target: '_blank', title: 'Open original photo' }, '↗'))))),
  ));
}

export function unmount() {
  queryFile = null;
}
