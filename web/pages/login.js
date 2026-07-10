// Login page — shown full-screen (nav hidden) until a session exists.
import { $, el, api, toast, session, afterLogin } from '/static/app.js?v=3';

export async function mount(root) {
  document.body.classList.add('auth-gate');

  const user = el('input', { type: 'text', placeholder: 'Username', autocomplete: 'username' });
  const pass = el('input', { type: 'password', placeholder: 'Password', autocomplete: 'current-password' });
  const submit = el('button', { class: 'primary wide', onclick: doLogin }, 'Sign in');

  async function doLogin() {
    submit.disabled = true;
    try {
      const me = await api.post('/api/login', { username: user.value, password: pass.value });
      session.user = me.username; session.role = me.role;
      toast(`Welcome, ${me.username}`, 'ok');
      afterLogin(me);
    } catch (err) {
      toast(err.message || 'Login failed', 'err');
      submit.disabled = false;
    }
  }
  [user, pass].forEach((i) => i.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') doLogin();
  }));

  root.append(el('div', { class: 'login-wrap' },
    el('div', { class: 'login-card' },
      el('div', { class: 'login-brand' },
        el('svg', { viewBox: '0 0 24 24', width: '34', height: '34', fill: 'none',
          stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round',
          html: '<rect x="3" y="3" width="18" height="18" rx="5"/><circle cx="9" cy="10" r="1.2" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1.2" fill="currentColor" stroke="none"/><path d="M8.5 14.5c1 1.2 2.2 1.8 3.5 1.8s2.5-.6 3.5-1.8"/>' }),
        el('span', {}, 'FaceVision')),
      el('p', { class: 'muted', style: 'margin:0 0 18px' }, 'Sign in to continue'),
      el('div', { class: 'field' }, el('label', {}, 'Username'), user),
      el('div', { class: 'field' }, el('label', {}, 'Password'), pass),
      submit,
      el('p', { class: 'muted', style: 'margin:16px 0 0;font-size:.76rem' },
        'First run? The server terminal printed a one-time admin password.'))));
}

export function unmount() {
  document.body.classList.remove('auth-gate');
}
