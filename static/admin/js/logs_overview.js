/* Logging overview.
 *
 * Same security rule as logs.js: every string here originates in a log row and
 * is therefore attacker-controlled. textContent only — never innerHTML.
 */
import { fetchAuth } from './utils.js';

const el = (id) => document.getElementById(id);
const fa = (n) => (n || 0).toLocaleString('fa-IR');

function setTile(id, value) {
  const node = el(id);
  if (node) node.textContent = fa(value);
}

function sumLevels(byCategory, category, levels) {
  const bucket = byCategory[category] || {};
  return levels.reduce((acc, l) => acc + (bucket[l] || 0), 0);
}

function bytes(n) {
  if (!n) return '۰';
  const units = ['بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'];
  let i = 0, v = n;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1).replace('.', '٫')} ${units[i]}`;
}

async function load(days) {
  const res = await fetchAuth('/admin/api/logs/summary?days=' + days);
  if (!res.ok) return;
  const d = await res.json();
  const cat = d.by_category || {};
  const lvl = d.by_level || {};
  const ERRORS = ['error', 'critical', 'alert', 'emergency'];

  setTile('t-total', Object.values(d.totals || {}).reduce((a, b) => a + b, 0));
  setTile('t-window', Object.values(lvl).reduce((a, b) => a + b, 0));
  setTile('t-errors', ERRORS.reduce((a, l) => a + (lvl[l] || 0), 0));
  setTile('t-warnings', lvl.warning || 0);
  setTile('t-auth-failed', sumLevels(cat, 'security', ['warning', 'critical']));
  setTile('t-llm-failed', sumLevels(cat, 'llm', ERRORS));
  setTile('t-sms-failed', sumLevels(cat, 'sms', ERRORS));
  setTile('t-api-failed', sumLevels(cat, 'api', ERRORS));
  setTile('t-audit', (d.totals || {}).audit_logs);
  setTile('t-security', (d.totals || {}).security_events);

  el('t-storage').textContent = bytes(d.storage_bytes);
  const r = d.retention || {};
  el('t-retention').textContent =
    `عملیاتی ${fa(r.operational_days)} روز · حساس ${fa(r.audit_days)} روز · امنیتی ${fa(r.security_days)} روز`;
  el('t-oldest').textContent = (d.oldest || '—').replace('T', ' ').slice(0, 19);
  el('t-newest').textContent = (d.newest || '—').replace('T', ' ').slice(0, 19);

  // ── per-category table ──
  const catBody = el('cat-body');
  catBody.replaceChildren();
  Object.keys(d.categories || {}).forEach((slug) => {
    const bucket = cat[slug];
    if (!bucket) return;
    const total = Object.values(bucket).reduce((a, b) => a + b, 0);
    const errs = ERRORS.reduce((a, l) => a + (bucket[l] || 0), 0);
    const tr = document.createElement('tr');
    const name = document.createElement('td');
    const link = document.createElement('a');
    link.href = '/secure-panel-inotex/logs?category=' + encodeURIComponent(slug);
    link.textContent = d.categories[slug];
    name.append(link);
    const c1 = document.createElement('td'); c1.textContent = fa(total);
    const c2 = document.createElement('td'); c2.textContent = fa(errs);
    if (errs) c2.className = 'text-danger fw-bold';
    tr.append(name, c1, c2);
    catBody.append(tr);
  });

  // ── top errors ──
  const errBody = el('err-body');
  errBody.replaceChildren();
  (d.top_errors || []).forEach((e) => {
    const tr = document.createElement('tr');
    [e.event_name, e.error_type || '—', d.categories[e.category] || e.category, fa(e.n)]
      .forEach((v, i) => {
        const cell = document.createElement('td');
        cell.textContent = v;
        if (i === 0) { cell.dir = 'ltr'; cell.style.fontFamily = 'monospace'; cell.style.fontSize = '12px'; }
        tr.append(cell);
      });
    errBody.append(tr);
  });
  if (!(d.top_errors || []).length) {
    const tr = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 4; cell.className = 'text-muted text-center py-3';
    cell.textContent = 'در این بازه خطایی ثبت نشده است.';
    tr.append(cell); errBody.append(tr);
  }

  // ── providers ──
  const provBody = el('prov-body');
  provBody.replaceChildren();
  (d.providers || []).forEach((p) => {
    const tr = document.createElement('tr');
    const avg = p.avg_ms ? Math.round(p.avg_ms) + ' ms' : '—';
    [p.provider, fa(p.n), avg, fa(p.errors), fa(p.tokens)].forEach((v) => {
      const cell = document.createElement('td');
      cell.textContent = v;
      tr.append(cell);
    });
    provBody.append(tr);
  });
  if (!(d.providers || []).length) {
    const tr = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 5; cell.className = 'text-muted text-center py-3';
    cell.textContent = 'فراخوانی سرویس بیرونی در این بازه ثبت نشده است.';
    tr.append(cell); provBody.append(tr);
  }
}

export function initLogsOverview() {
  const picker = el('window-days');
  picker.addEventListener('change', () => load(picker.value));
  load(picker.value);
}
