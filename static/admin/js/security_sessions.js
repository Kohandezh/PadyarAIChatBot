/* Active admin sessions.
 * The server returns only an 8-character fingerprint, never the session token,
 * so this listing cannot be replayed as a cookie if it leaks.
 * SECURITY: textContent only — usernames are operator-supplied text.
 */
import { fetchAuth, showMsg } from './utils.js';

const el = (id) => document.getElementById(id);

async function load() {
  const res = await fetchAuth('/admin/api/security/sessions');
  if (!res.ok) return;
  const { sessions } = await res.json();
  const body = el('sessions-body');
  body.replaceChildren();
  sessions.forEach((s) => {
    const tr = document.createElement('tr');
    [s.fingerprint, s.username, (s.expiry || '').replace('T', ' ').slice(0, 19)]
      .forEach((v, i) => {
        const cell = document.createElement('td');
        cell.textContent = v;
        if (i === 0 || i === 2) { cell.dir = 'ltr'; cell.style.fontFamily = 'monospace'; }
        tr.append(cell);
      });
    const state = document.createElement('td');
    if (s.is_current) {
      const b = document.createElement('span');
      b.className = 'badge bg-green';
      b.textContent = 'نشست جاری';
      state.append(b);
    }
    tr.append(state);

    const act = document.createElement('td');
    if (!s.is_current) {
      const btn = document.createElement('button');
      btn.className = 'btn btn-sm btn-outline-danger';
      btn.textContent = 'ابطال';
      btn.addEventListener('click', () => revoke({ fingerprint: s.fingerprint }));
      act.append(btn);
    }
    tr.append(act);
    body.append(tr);
  });
  el('session-count').textContent = sessions.length.toLocaleString('fa-IR');
}

async function revoke(payload) {
  const res = await fetchAuth('/admin/api/security/sessions/revoke', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    showMsg('sessions-msg', `${data.revoked} نشست باطل شد. این اقدام ثبت شد.`, 'success');
    load();
  } else {
    showMsg('sessions-msg', data.detail || 'ابطال نشست ناموفق بود.', 'danger');
  }
}

async function loadAdmins() {
  const res = await fetchAuth('/admin/api/security/admins');
  if (!res.ok) return;
  const { admins } = await res.json();
  const body = el('admins-body');
  body.replaceChildren();
  admins.forEach((a) => {
    const tr = document.createElement('tr');
    [a.username,
     a.has_security_question ? 'دارد' : 'ندارد',
     a.active_sessions.toLocaleString('fa-IR'),
     a.failed_logins.toLocaleString('fa-IR'),
     (a.last_login || '—').replace('T', ' ').slice(0, 19)].forEach((v) => {
      const cell = document.createElement('td');
      cell.textContent = v;
      tr.append(cell);
    });
    body.append(tr);
  });
}

export function initSessions() {
  el('btn-revoke-others').addEventListener('click', () => {
    if (window.confirm('همهٔ نشست‌های دیگر باطل شوند؟ سایر مدیران باید دوباره وارد شوند.')) {
      revoke({ all_others: true });
    }
  });
  load();
  loadAdmins();
}
