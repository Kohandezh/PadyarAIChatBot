/* Infrastructure → Database (PostgreSQL).
 *
 * SECURITY: values come from the database server and from log rows, both of
 * which can carry text this admin did not author. textContent / createElement
 * only — never innerHTML with dynamic values.
 *
 * This page shows the PRODUCTION engine. SQLite-era controls (PRAGMA integrity
 * check, WAL checkpoint, SQLite VACUUM) are deliberately absent: after the
 * cutover they describe a store the application no longer uses, and a green
 * tick from the wrong database is worse than no check at all.
 */
import { fetchAuth, showMsg } from './utils.js';

const el = (id) => document.getElementById(id);
const fa = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('fa-IR'));

function bytes(n) {
  if (!n) return '۰';
  const u = ['بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'];
  let i = 0, v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1).replace('.', '٫')} ${u[i]}`;
}

function put(id, value) { const n = el(id); if (n) n.textContent = value; }

function row(cells, opts = {}) {
  const tr = document.createElement('tr');
  cells.forEach((c, i) => {
    const td = document.createElement('td');
    td.textContent = c === null || c === undefined || c === '' ? '—' : String(c);
    if (opts.ltr && opts.ltr.includes(i)) { td.dir = 'ltr'; td.style.fontFamily = 'monospace'; td.style.fontSize = '12px'; }
    if (opts.danger && opts.danger(c, i)) td.className = 'text-danger fw-bold';
    tr.append(td);
  });
  return tr;
}

async function load() {
  const t0 = performance.now();
  const res = await fetchAuth('/admin/api/infra/database/pg');
  if (!res.ok) {
    showMsg('db-msg', 'اطلاعات پایگاه داده در دسترس نیست.', 'danger');
    return;
  }
  const d = await res.json();
  const latency = Math.round(performance.now() - t0);

  put('pg-version', d.version);
  put('pg-database', d.database);
  put('pg-size', bytes(d.size_bytes));
  put('pg-latency', `${fa(latency)} ms`);
  put('pg-migration', (d.migrations && d.migrations.length)
    ? d.migrations[d.migrations.length - 1].version : 'نامشخص');

  const pool = d.pool || {};
  put('pg-pool', `${fa(pool.size)} از ${fa(pool.max)}`);
  put('pg-pool-available', fa(pool.available));
  put('pg-pool-waiting', fa(pool.waiting));

  const act = d.activity || {};
  put('pg-conn-total', fa(act.total));
  put('pg-conn-active', fa(act.active));
  put('pg-idle-txn', fa(act.idle_in_txn));
  put('pg-lock-waits', fa(act.waiting_on_lock));
  put('pg-longest', act.longest_query_seconds
    ? `${Number(act.longest_query_seconds).toFixed(1).replace('.', '٫')} ثانیه` : '—');

  // idle-in-transaction and lock waits are the two numbers that actually
  // predict trouble; surface them rather than burying them in a table.
  const warn = el('pg-warn');
  warn.replaceChildren();
  const problems = [];
  if ((act.idle_in_txn || 0) > 0) problems.push(`${fa(act.idle_in_txn)} اتصال در تراکنش بی‌کار`);
  if ((act.waiting_on_lock || 0) > 0) problems.push(`${fa(act.waiting_on_lock)} اتصال منتظر قفل`);
  if (problems.length) {
    warn.className = 'alert alert-warning';
    warn.textContent = problems.join(' · ');
  }

  const schemas = el('pg-schemas');
  schemas.replaceChildren();
  (d.schemas || []).forEach((s) => schemas.append(row([s.schema, fa(s.tables), bytes(s.bytes)], { ltr: [0] })));

  const settings = el('pg-settings');
  settings.replaceChildren();
  Object.entries(d.settings || {}).forEach(([k, v]) => settings.append(row([k, v], { ltr: [0, 1] })));

  const actions = el('pg-actions');
  actions.replaceChildren();
  (d.actions || []).forEach((a) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'btn btn-sm btn-outline-primary';
    b.textContent = a.label_fa;
    b.addEventListener('click', () => runAction(a, b));
    actions.append(b);
  });
}

async function loadTables() {
  const res = await fetchAuth('/admin/api/infra/database/pg/tables');
  if (!res.ok) return;
  const { tables, indexes } = await res.json();

  const tb = el('pg-tables');
  tb.replaceChildren();
  (tables || []).forEach((t) => tb.append(row(
    [`${t.schema}.${t.table_name}`, fa(t.live_rows), fa(t.dead_rows), bytes(t.bytes),
     (t.last_autovacuum || '—').toString().slice(0, 19).replace('T', ' '),
     (t.last_autoanalyze || '—').toString().slice(0, 19).replace('T', ' ')],
    { ltr: [0, 4, 5], danger: (c, i) => i === 2 && Number(String(c).replace(/\D/g, '')) > 1000 })));

  const ib = el('pg-indexes');
  ib.replaceChildren();
  (indexes || []).forEach((x) => ib.append(row(
    [`${x.schema}.${x.table_name}`, x.index_name, fa(x.scans), bytes(x.bytes)],
    { ltr: [0, 1], danger: (c, i) => i === 2 && String(c) === '۰' })));
}

async function runAction(action, btn) {
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'در حال اجرا…';
  try {
    const res = await fetchAuth('/admin/api/infra/database/pg/maintenance', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action.name }),
    });
    const data = await res.json().catch(() => ({}));
    showMsg('db-msg', res.ok ? `${data.label_fa}: ${data.message_fa}` : (data.detail || 'ناموفق'),
            res.ok ? 'success' : 'danger');
    if (res.ok) { load(); loadTables(); }
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

export function initInfraDatabase() {
  el('btn-refresh-db').addEventListener('click', () => { load(); loadTables(); });
  load();
  loadTables();
}
