// Enroll: batch-upload photos, click each detected face, name it.
// Names save to the database IMMEDIATELY on OK — nothing is staged.
import { $, el, api, toast, photoWithFaces, handoff } from '/static/app.js?v=2';

let personCache = [];

export async function mount(root) {
  personCache = await api.get('/api/persons').catch(() => []);

  const fileInput = el('input', { type: 'file', accept: 'image/*', multiple: '', style: 'display:none' });
  fileInput.addEventListener('change', () => upload([...fileInput.files]));

  const drop = el('div', { class: 'dropzone' },
    el('div', { class: 'big' }, 'Drop photos here or click to browse'),
    el('div', { class: 'small' }, 'Faces are detected on-device. Up to 50 images per batch, 20 MB each.'));
  drop.addEventListener('click', () => fileInput.click());
  drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag'); });
  drop.addEventListener('dragleave', () => drop.classList.remove('drag'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('drag');
    upload([...e.dataTransfer.files].filter((f) => f.type.startsWith('image/')));
  });

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Enroll faces'),
      el('span', { class: 'sub' }, 'Upload photos, then click each face to name it — names save instantly')),
    el('div', { class: 'page-body' },
      drop, fileInput,
      el('div', { class: 'progressbar', id: 'upProgress', style: 'display:none' }, el('div')),
      el('div', { id: 'results', style: 'display:flex;flex-direction:column;gap:22px;margin-top:18px' })));

  if (handoff.enrollResults) { // arrived via Identify's "Add to library"
    for (const r of handoff.enrollResults) renderPhoto(r);
    delete handoff.enrollResults;
  }

  // Library photos that still have unnamed faces — so nothing gets stranded.
  const existing = await api.get('/api/photos?page_size=48').catch(() => null);
  const unnamed = (existing?.items || []).filter((p) => p.faces.some((f) => !f.person_id));
  if (unnamed.length) {
    for (const p of unnamed.reverse()) renderPhoto(p);
    $('#results').prepend(el('p', { class: 'muted', style: 'margin:6px 0 0' },
      'Already in your library with unnamed faces — click a face to name it:'));
  }
}

async function upload(files) {
  if (!files.length) return;
  const bar = $('#upProgress');
  bar.style.display = '';
  bar.firstChild.style.width = '2%';

  const CHUNK = 5;
  let done = 0;
  for (let i = 0; i < files.length; i += CHUNK) {
    const chunk = files.slice(i, i + CHUNK);
    try {
      const res = await api.upload('/api/photos', chunk);
      for (const r of res.results) renderPhoto(r);
    } catch (err) {
      toast(`Upload failed: ${err.message}`, 'err');
    }
    done += chunk.length;
    bar.firstChild.style.width = `${(done / files.length) * 100}%`;
  }
  setTimeout(() => { bar.style.display = 'none'; }, 600);
  personCache = await api.get('/api/persons').catch(() => personCache);
}

function renderPhoto(r) {
  const results = $('#results');
  if (!results) return;
  if (r.error) {
    results.prepend(el('div', { class: 'card' },
      el('strong', {}, r.original_name || 'file'), ` — ${r.error}`));
    return;
  }

  const faces = r.faces.map((f) => ({
    ...f,
    cls: f.person_name ? 'named' : '',
    tag: labelFor(f),
  }));

  const card = el('div', { class: 'card' });
  const head = el('div', { style: 'display:flex;justify-content:space-between;align-items:baseline;margin-bottom:12px;gap:10px' },
    el('div', {},
      el('strong', {}, r.original_name || `photo #${r.photo_id}`),
      r.duplicate ? el('span', { class: 'muted' }, '  (already in library)') : null),
    el('span', { class: 'muted' }, `${faces.length} face${faces.length === 1 ? '' : 's'} detected — click a face to name it`));

  const pb = photoWithFaces(r.url, { w: r.width, h: r.height }, faces, {
    onFaceClick: (f, node) => namePopover(f, node),
  });

  card.append(head, pb);
  if (!faces.length) {
    card.append(el('p', { class: 'muted' }, 'No faces found in this photo. It is stored in the library anyway.'));
  }
  results.prepend(card);
}

function labelFor(f) {
  if (f.person_name) return f.person_name;
  if (f.suggestion) return `${f.suggestion.name}? ${f.suggestion.score.toFixed(2)}`;
  return 'name…';
}

function namePopover(face, node) {
  // one popover at a time
  document.querySelectorAll('.name-pop').forEach((n) => n.remove());

  const input = el('input', { type: 'text', placeholder: 'Type a name…', autocomplete: 'off' });
  if (face.person_name) input.value = face.person_name;
  else if (face.suggestion) input.value = face.suggestion.name;

  const sugWrap = el('div', { style: 'position:relative' });
  const list = el('div', { class: 'suggest-list', style: 'display:none' });
  sugWrap.append(input, list);

  const refresh = () => {
    const q = input.value.trim().toLowerCase();
    const hits = personCache.filter((p) => p.name.toLowerCase().includes(q)).slice(0, 6);
    list.innerHTML = '';
    if (!hits.length) { list.style.display = 'none'; return; }
    list.style.display = '';
    for (const p of hits) {
      list.append(el('button', {
        type: 'button',
        onclick: () => { input.value = p.name; list.style.display = 'none'; input.focus(); },
      }, p.name, el('span', { class: 'muted', style: 'margin-left:auto' }, `${p.face_count} faces`)));
    }
  };
  input.addEventListener('input', refresh);
  input.addEventListener('focus', refresh);

  const save = async () => {
    const name = input.value.trim();
    if (!name) { pop.remove(); return; }
    okBtn.disabled = true;
    try {
      // saves straight to the on-device database
      await api.post('/api/faces/label', { labels: [{ face_id: face.face_id, name }] });
      face.person_name = name;
      node.classList.add('named');
      node.classList.remove('unknown');
      let tag = node.querySelector('.tag');
      if (!tag) { tag = el('span', { class: 'tag' }); node.append(tag); }
      tag.textContent = name;
      pop.remove();
      toast(`Saved "${name}"`, 'ok');
      personCache = await api.get('/api/persons').catch(() => personCache);
    } catch (err) {
      okBtn.disabled = false;
      toast(`Could not save: ${err.message}`, 'err');
    }
  };

  const okBtn = el('button', { class: 'small primary', type: 'button', onclick: save }, 'Save');
  const pop = el('div', { class: 'modal name-pop', style: 'position:absolute;z-index:30;width:280px;padding:14px' },
    el('h3', { style: 'margin-bottom:10px' },
      face.suggestion ? `Looks like ${face.suggestion.name} (${face.suggestion.score.toFixed(2)})` : 'Who is this?'),
    sugWrap,
    el('div', { class: 'btnrow', style: 'margin-top:12px;justify-content:flex-end' },
      el('button', { class: 'small danger', type: 'button', onclick: () => removeFace(face, node, pop) }, 'Not a face'),
      el('button', { class: 'small', type: 'button', onclick: () => pop.remove() }, 'Cancel'),
      okBtn));

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); save(); }
    if (e.key === 'Escape') pop.remove();
  });

  // position under the face box, inside the photobox
  const parent = node.parentElement;
  parent.append(pop);
  const nb = node.getBoundingClientRect();
  const pbx = parent.getBoundingClientRect();
  pop.style.left = `${Math.max(0, Math.min(nb.left - pbx.left, pbx.width - 290))}px`;
  pop.style.top = `${nb.bottom - pbx.top + 6}px`;
  input.focus();
  input.select();
}

async function removeFace(face, node, pop) {
  try {
    await api.del(`/api/faces/${face.face_id}`);
    node.remove();
    pop.remove();
    toast('Detection removed', 'ok');
  } catch (err) {
    toast(err.message, 'err');
  }
}

export function unmount() {}
