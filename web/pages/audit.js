// Audit log + recent watchlist events (operator/admin).
import { $, el, api, toast, hasRole } from '/static/app.js?v=3';

export async function mount(root) {
  if (!hasRole('operator')) {
    root.append(el('div', { class: 'empty' }, 'Operators and admins only.'));
    return;
  }
  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Audit & events'),
      el('span', { class: 'sub' }, 'Who did what, and every watchlist sighting')),
    el('div', { class: 'page-body' },
      el('h2', { class: 'section-h' }, 'Recent watchlist events'),
      el('div', { class: 'gallery', id: 'eventsGrid' }),
      el('h2', { class: 'section-h', style: 'margin-top:24px' }, 'Audit log'),
      el('div', { id: 'auditTable', class: 'audit-table' })));

  loadEvents();
  loadAudit();
}

async function loadEvents() {
  try {
    const events = await api.get('/api/events?limit=40');
    const grid = $('#eventsGrid');
    if (!grid) return;
    if (!events.length) {
      grid.append(el('div', { class: 'empty', style: 'grid-column:1/-1' },
        el('div', { class: 'icon' }, '🔔'), 'No watchlist sightings recorded yet.'));
      return;
    }
    for (const ev of events) {
      grid.append(el('div', { class: 'tile' },
        el('div', { class: 'thumbwrap', style: 'aspect-ratio:1' },
          ev.snapshot ? el('img', { src: ev.snapshot, alt: '' })
            : el('div', { class: 'avatar placeholder', style: 'width:100%;height:100%;border-radius:0' }, '?')),
        el('div', { class: 'meta' },
          el('span', { class: 'name' }, ev.person_name || '—'),
          el('span', { class: `scorebadge cat-${ev.category}` }, ev.category)),
        el('div', { class: 'meta', style: 'padding-top:0' },
          el('span', { class: 'muted', style: 'font-size:.72rem' }, ev.ts),
          el('span', { class: 'muted', style: 'font-size:.72rem' }, ev.camera || ''))));
    }
  } catch (err) { toast(err.message, 'err'); }
}

async function loadAudit() {
  try {
    const rows = await api.get('/api/audit?limit=300');
    const box = $('#auditTable');
    if (!box) return;
    if (!rows.length) { box.append(el('div', { class: 'empty' }, 'No audit entries yet.')); return; }
    const table = el('table', { class: 'grid-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, 'Time'), el('th', {}, 'User'), el('th', {}, 'Action'),
        el('th', {}, 'Target'), el('th', {}, 'Detail'), el('th', {}, 'IP'))),
      el('tbody', {}, rows.map((r) => el('tr', {},
        el('td', {}, r.ts), el('td', {}, r.username || '—'),
        el('td', {}, el('span', { class: 'tag-pill' }, r.action)),
        el('td', {}, r.target || ''), el('td', {}, r.detail || ''),
        el('td', { class: 'muted' }, r.ip || '')))));
    box.append(el('div', { style: 'overflow-x:auto' }, table));
  } catch (err) { toast(err.message, 'err'); }
}

export function unmount() {}
