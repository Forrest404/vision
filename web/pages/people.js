// People: browse/search the face database; person detail with rename,
// merge, delete, face strip and photo gallery.
import { $, el, api, toast, modal, confirmModal, lightbox, handoff } from '/static/app.js?v=2';

export async function mount(root, params) {
  if (params[0]) return personDetail(root, +params[0]);
  return peopleList(root);
}

/* ------------------------------- list view ------------------------------ */

async function peopleList(root) {
  const searchInput = el('input', {
    type: 'search', placeholder: 'Search people by name…',
    oninput: () => renderGrid(searchInput.value),
  });

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'People'),
      el('span', { class: 'sub', id: 'peopleCount' }, '')),
    el('div', { class: 'page-body' },
      el('div', { class: 'searchbar' },
        searchInput,
        el('button', {
          class: 'danger', title: 'Delete every person, photo and face',
          onclick: () => confirmModal(
            'Delete EVERYTHING? Every person, every photo and every face is ' +
            'permanently removed from this device. This cannot be undone.',
            async () => {
              try {
                const res = await api.del('/api/library');
                toast(`Deleted ${res.deleted.persons} people, ${res.deleted.photos} photos`, 'ok');
                renderGrid(searchInput.value);
              } catch (err) { toast(err.message, 'err'); }
            }, 'Delete everything'),
        }, 'Delete all')),
      el('div', { class: 'people-grid', id: 'peopleGrid' })));

  let timer = null;
  async function renderGrid(q = '') {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const persons = await api.get(`/api/persons?q=${encodeURIComponent(q)}`).catch(() => []);
      const grid = $('#peopleGrid');
      if (!grid) return;
      grid.innerHTML = '';
      $('#peopleCount').textContent = `${persons.length} ${persons.length === 1 ? 'person' : 'people'}`;
      if (!persons.length) {
        grid.append(el('div', { class: 'empty', style: 'grid-column:1/-1' },
          el('div', { class: 'icon' }, '☺'),
          q ? 'No people match that search.' : 'No people yet — upload photos in Enroll and name the faces.'));
        return;
      }
      for (const p of persons) {
        grid.append(el('div', {
          class: 'person-card',
          onclick: () => { location.hash = `#/people/${p.id}`; },
        },
          p.cover_url
            ? el('img', { class: 'avatar', src: p.cover_url, alt: p.name })
            : el('div', { class: 'avatar placeholder' }, p.name[0]?.toUpperCase() || '?'),
          el('div', { class: 'pname' }, p.name),
          el('div', { class: 'pmeta' }, /^\d+$/.test(p.name)
            ? 'auto-captured · open to rename'
            : `${p.face_count} faces · ${p.photo_count} photos`)));
      }
    }, q ? 200 : 0);
  }
  renderGrid();
}

/* ------------------------------ detail view ----------------------------- */

async function personDetail(root, personId) {
  let person;
  try {
    person = await api.get(`/api/persons/${personId}`);
  } catch {
    root.append(el('div', { class: 'empty' }, 'Person not found.'));
    return;
  }

  const nameEl = el('h1', { title: 'Click to rename', style: 'cursor:text' }, person.name);
  nameEl.addEventListener('click', () => renameInline(nameEl, person));

  root.append(
    el('div', { class: 'page-head' },
      el('a', { class: 'btn small ghost', href: '#/people' }, '← People'),
      nameEl,
      el('span', { class: 'sub' }, `${person.faces.length} faces · ${person.photos.length} photos`)),
    el('div', { class: 'btnrow', style: 'margin:14px 0' },
      el('a', { class: 'btn', href: `#/search?person=${person.id}`, onclick: (e) => { e.preventDefault(); findPhotos(person); } }, 'Find photos'),
      el('button', { onclick: () => mergeModal(person) }, 'Merge into…'),
      el('button', {
        class: 'danger',
        onclick: () => confirmModal(
          `Delete "${person.name}"? Their photos stay in the library; the faces become unlabeled.`,
          async () => {
            try {
              await api.del(`/api/persons/${person.id}`);
              toast('Person deleted', 'ok');
              location.hash = '#/people';
            } catch (e) { toast(e.message, 'err'); }
          }),
      }, 'Delete person')),
    el('div', { class: 'page-body', style: 'display:flex;flex-direction:column;gap:20px' },
      el('div', { class: 'card' },
        el('h2', {}, 'Faces ', el('span', { class: 'hint' }, 'hover to remove a wrong label')),
        el('div', { class: 'crop-strip' },
          person.faces.map((f) => el('div', { class: 'crop-item' },
            el('img', {
              src: f.crop_url, alt: '',
              onclick: () => openPhotoOfFace(person, f),
            }),
            el('button', {
              class: 'x', title: 'Unlabel this face',
              onclick: async () => {
                await api.post('/api/faces/label', { labels: [{ face_id: f.face_id, person_id: null }] })
                  .catch((e) => toast(e.message, 'err'));
                toast('Face unlabeled', 'ok');
                remount(root, personId);
              },
            }, '✕'))))),
      el('div', {},
        el('h2', { style: 'font-size:.76rem;text-transform:uppercase;letter-spacing:.05em;color:var(--text-dim);margin:0 0 10px' }, 'Photos'),
        el('div', { class: 'gallery' },
          person.photos.map((photo) => photoTile(photo, person, () => remount(root, personId)))))));
}

function remount(root, personId) {
  root.innerHTML = '';
  personDetail(root, personId);
}

function photoTile(photo, person, refresh) {
  const mine = photo.faces.filter((f) => f.person_id === person.id);
  const tile = el('div', { class: 'tile' },
    el('div', { class: 'thumbwrap' },
      el('img', {
        src: photo.thumb_url, alt: '',
        onclick: () => lightbox(photo.url,
          photo.faces.map((f) => ({
            ...f,
            cls: f.person_id === person.id ? 'highlight' : (f.person_name ? 'named' : ''),
            tag: f.person_name || null,
          })),
          { w: photo.width, h: photo.height }),
      })),
    el('div', { class: 'meta' },
      el('span', { class: 'name' }, photo.original_name || `#${photo.photo_id}`),
      el('span', { style: 'display:flex;gap:6px;align-items:center' },
        mine.length > 1 ? el('span', { class: 'scorebadge' }, `×${mine.length}`) : null,
        el('a', { class: 'plain', href: photo.url, target: '_blank', title: 'Open original' }, '↗'),
        el('button', {
          class: 'small ghost danger', title: 'Delete photo',
          onclick: (e) => {
            e.stopPropagation();
            confirmModal('Delete this photo and all its faces from the library?', async () => {
              await api.del(`/api/photos/${photo.photo_id}`).catch((err) => toast(err.message, 'err'));
              toast('Photo deleted', 'ok');
              refresh();
            });
          },
        }, '🗑'))));
  return tile;
}

function openPhotoOfFace(person, face) {
  api.get(`/api/photos/${face.photo_id}`).then((photo) => {
    lightbox(photo.url,
      photo.faces.map((f) => ({
        ...f,
        cls: f.face_id === face.face_id ? 'highlight' : (f.person_name ? 'named' : ''),
        tag: f.person_name || null,
      })),
      { w: photo.width, h: photo.height });
  }).catch((e) => toast(e.message, 'err'));
}

function renameInline(nameEl, person) {
  const input = el('input', { type: 'text', value: person.name, style: 'font-size:1.1rem;max-width:280px' });
  const commit = async () => {
    const name = input.value.trim();
    input.replaceWith(nameEl);
    if (!name || name === person.name) return;
    try {
      await api.patch(`/api/persons/${person.id}`, { name });
      person.name = name;
      nameEl.textContent = name;
      toast('Renamed', 'ok');
    } catch (err) { toast(err.message, 'err'); }
  };
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') input.blur();
    if (e.key === 'Escape') { input.value = person.name; input.blur(); }
  });
  nameEl.replaceWith(input);
  input.focus();
  input.select();
}

function mergeModal(person) {
  modal(async (box, close) => {
    box.append(el('h3', {}, `Merge "${person.name}" into…`),
      el('p', { class: 'muted' }, 'All of their faces move to the person you pick; this entry is removed.'));
    const list = el('div', { style: 'display:flex;flex-direction:column;gap:6px;max-height:300px;overflow-y:auto' });
    box.append(list);
    const persons = await api.get('/api/persons').catch(() => []);
    const others = persons.filter((p) => p.id !== person.id);
    if (!others.length) list.append(el('p', { class: 'muted' }, 'No one else to merge into.'));
    for (const p of others) {
      list.append(el('button', {
        style: 'justify-content:flex-start',
        onclick: async () => {
          try {
            await api.post(`/api/persons/${person.id}/merge`, { target_id: p.id });
            toast(`Merged into ${p.name}`, 'ok');
            close();
            location.hash = `#/people/${p.id}`;
          } catch (err) { toast(err.message, 'err'); }
        },
      }, p.name, el('span', { class: 'muted', style: 'margin-left:auto' }, `${p.face_count} faces`)));
    }
  });
}

function findPhotos(person) {
  handoff.searchPersonId = person.id; // search page reads this on mount
  location.hash = '#/search';
}

export function unmount() {}
