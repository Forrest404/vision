// Gallery: browse every photo in the library, newest first, with paging.
import { $, el, api, toast, confirmModal, lightbox } from '/static/app.js?v=2';

const PAGE_SIZE = 48;
let page = 1;
let total = 0;
let loading = false;

export async function mount(root) {
  page = 1;
  total = 0;

  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Gallery'),
      el('span', { class: 'sub', id: 'galleryCount' }, '')),
    el('div', { class: 'page-body' },
      el('div', { class: 'gallery', id: 'galleryGrid' }),
      el('div', { style: 'text-align:center;margin-top:18px' },
        el('button', { id: 'moreBtn', style: 'display:none', onclick: loadMore }, 'Load more'))));

  await loadMore();
}

async function loadMore() {
  if (loading) return;
  loading = true;
  const btn = $('#moreBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Loading…'; }
  try {
    const res = await api.get(`/api/photos?page=${page}&page_size=${PAGE_SIZE}`);
    total = res.total;
    const grid = $('#galleryGrid');
    if (!grid) return; // navigated away
    if (total === 0 && page === 1) {
      grid.append(el('div', { class: 'empty', style: 'grid-column:1/-1' },
        el('div', { class: 'icon' }, '🖼'),
        'No photos yet — add some in Enroll, or let auto-capture collect them from the live feed.'));
    }
    for (const p of res.items) grid.append(tile(p));
    page += 1;
    const shown = grid.querySelectorAll('.tile').length;
    $('#galleryCount').textContent = `${total} photo${total === 1 ? '' : 's'}`;
    if (btn) {
      btn.style.display = shown < total ? '' : 'none';
      btn.disabled = false;
      btn.textContent = 'Load more';
    }
  } catch (err) {
    toast(err.message, 'err');
  }
  loading = false;
}

function tile(photo) {
  const named = photo.faces.filter((f) => f.person_name);
  const t = el('div', { class: 'tile' },
    el('div', { class: 'thumbwrap' },
      el('img', {
        src: photo.thumb_url, alt: photo.original_name || '',
        loading: 'lazy',
        onclick: () => lightbox(photo.url,
          photo.faces.map((f) => ({
            ...f,
            cls: f.person_name ? 'named' : 'unknown',
            tag: f.person_name || null,
          })),
          { w: photo.width, h: photo.height }),
      })),
    el('div', { class: 'meta' },
      el('span', { class: 'name', title: photo.original_name || '' },
        named.length ? named.map((f) => f.person_name).join(', ')
          : (photo.original_name || `#${photo.photo_id}`)),
      el('span', { style: 'display:flex;gap:6px;align-items:center' },
        photo.faces.length
          ? el('span', { class: 'scorebadge', title: 'faces in photo' }, photo.faces.length)
          : null,
        el('a', { class: 'plain', href: photo.url, target: '_blank', title: 'Open original' }, '↗'),
        el('button', {
          class: 'small ghost danger', title: 'Delete photo',
          onclick: (e) => {
            e.stopPropagation();
            confirmModal('Delete this photo and its faces from the library?', async () => {
              try {
                await api.del(`/api/photos/${photo.photo_id}`);
                t.remove();
                total -= 1;
                $('#galleryCount') && ($('#galleryCount').textContent =
                  `${total} photo${total === 1 ? '' : 's'}`);
                toast('Photo deleted', 'ok');
              } catch (err) { toast(err.message, 'err'); }
            });
          },
        }, '🗑'))));
  return t;
}

export function unmount() {
  loading = false;
}
