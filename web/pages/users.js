// User management (admin only).
import { $, el, api, toast, confirmModal, hasRole, session } from '/static/app.js?v=3';

const ROLES = ['viewer', 'operator', 'admin'];

export async function mount(root) {
  if (!hasRole('admin')) {
    root.append(el('div', { class: 'empty' }, 'Admins only.'));
    return;
  }
  root.append(
    el('div', { class: 'page-head' },
      el('h1', {}, 'Users'),
      el('span', { class: 'sub' }, 'Accounts and roles')),
    el('div', { class: 'page-body' },
      el('div', { class: 'card', style: 'max-width:520px;margin-bottom:20px' },
        el('h2', {}, 'Add user'),
        newUserForm()),
      el('div', { id: 'usersList' })));
  loadUsers();
}

function newUserForm() {
  const u = el('input', { type: 'text', placeholder: 'Username', autocomplete: 'off' });
  const p = el('input', { type: 'password', placeholder: 'Password (min 8 chars)', autocomplete: 'new-password' });
  const role = el('select', {}, ROLES.map((r) => el('option', { value: r }, r)));
  const add = el('button', {
    class: 'primary', onclick: async () => {
      add.disabled = true;
      try {
        await api.post('/api/users', { username: u.value, password: p.value, role: role.value });
        toast(`Created ${u.value}`, 'ok');
        u.value = ''; p.value = '';
        loadUsers();
      } catch (err) { toast(err.message, 'err'); }
      add.disabled = false;
    },
  }, 'Create');
  return el('div', { class: 'field', style: 'gap:10px' },
    u, p, el('div', { class: 'row' }, el('label', {}, 'Role'), role), add);
}

async function loadUsers() {
  try {
    const list = await api.get('/api/users');
    const box = $('#usersList');
    if (!box) return;
    box.innerHTML = '';
    const table = el('table', { class: 'grid-table' },
      el('thead', {}, el('tr', {},
        el('th', {}, 'User'), el('th', {}, 'Role'), el('th', {}, 'Last login'), el('th', {}, ''))),
      el('tbody', {}, list.map((usr) => userRow(usr))));
    box.append(el('div', { style: 'overflow-x:auto' }, table));
  } catch (err) { toast(err.message, 'err'); }
}

function userRow(usr) {
  const roleSel = el('select', {},
    ROLES.map((r) => {
      const o = el('option', { value: r }, r);
      if (r === usr.role) o.selected = true;
      return o;
    }));
  roleSel.addEventListener('change', async () => {
    try { await api.patch(`/api/users/${usr.id}`, { role: roleSel.value }); toast('Role updated', 'ok'); }
    catch (err) { toast(err.message, 'err'); }
  });
  const self = usr.username === session.user;
  return el('tr', {},
    el('td', {}, usr.username, self ? el('span', { class: 'muted' }, ' (you)') : null,
      usr.must_change ? el('span', { class: 'scorebadge', style: 'margin-left:6px' }, 'must change') : null),
    el('td', {}, roleSel),
    el('td', { class: 'muted' }, usr.last_login || 'never'),
    el('td', {}, self ? null : el('button', {
      class: 'small danger', onclick: () => confirmModal(
        `Delete user "${usr.username}"?`, async () => {
          try { await api.del(`/api/users/${usr.id}`); toast('User deleted', 'ok'); loadUsers(); }
          catch (err) { toast(err.message, 'err'); }
        }),
    }, 'Delete')));
}

export function unmount() {}
