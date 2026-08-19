/* Log settings + the destructive zone.
 *
 * The truncate flow ALWAYS previews first and shows the real count. A "clear
 * logs" button that deletes an unknown number of rows on one click is how
 * evidence disappears by accident.
 */
import { fetchAuth, showMsg } from './utils.js';

const el = (id) => document.getElementById(id);
const fa = (n) => (n || 0).toLocaleString('fa-IR');

async function loadSettings() {
  const res = await fetchAuth('/admin/api/logs/settings');
  if (!res.ok) return;
  const d = await res.json();
  el('s-retention').value = d.retention_days;
  el('s-audit').value = d.audit_retention_days;
  el('s-security').value = d.security_retention_days;
  el('s-min-level').value = d.min_level;
  el('s-policy').value = d.content_policy;
  el('s-debug').checked = !!d.debug_enabled;
  policyWarning();
}

function policyWarning() {
  const warn = el('policy-warning');
  warn.classList.toggle('d-none', el('s-policy').value !== 'full');
}

function truncateFilters() {
  const f = {};
  if (el('t-category').value) f.category = el('t-category').value;
  if (el('t-level').value) f.level = el('t-level').value;
  if (el('t-older').value) f.older_than_days = el('t-older').value;
  return f;
}

let pending = null;

async function preview() {
  const f = truncateFilters();
  const res = await fetchAuth('/admin/api/logs/truncate/preview?' + new URLSearchParams(f));
  const data = await res.json().catch(() => ({}));
  const n = data.matching || 0;
  pending = n ? f : null;
  const box = el('t-confirm');
  if (!n) {
    box.className = 'alert alert-secondary mt-3';
    box.textContent = 'با این فیلترها رکوردی برای حذف وجود ندارد.';
    el('t-execute').disabled = true;
    return;
  }
  box.className = 'alert alert-danger mt-3';
  box.textContent = `${fa(n)} رکورد حذف می‌شود. این عمل بازگشت‌پذیر نیست.`;
  el('t-execute').disabled = false;
}

async function execute() {
  if (!pending) return;
  const res = await fetchAuth('/admin/api/logs/truncate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(pending),
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok) {
    showMsg('logs-msg', `${fa(data.deleted)} رکورد حذف شد. این اقدام در رخدادهای حساس ثبت شد.`, 'success');
  } else {
    showMsg('logs-msg', data.detail || 'حذف لاگ‌ها ناموفق بود.', 'danger');
  }
  pending = null;
  el('t-execute').disabled = true;
  el('t-confirm').className = 'd-none';
  preview();
}

export function initLogsSettings() {
  loadSettings();
  el('s-policy').addEventListener('change', policyWarning);

  el('logs-settings-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const body = {
      retention_days: parseInt(el('s-retention').value, 10),
      audit_retention_days: parseInt(el('s-audit').value, 10),
      security_retention_days: parseInt(el('s-security').value, 10),
      debug_enabled: el('s-debug').checked,
      min_level: el('s-min-level').value,
      content_policy: el('s-policy').value,
    };
    const res = await fetchAuth('/admin/api/logs/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { showMsg('logs-msg', 'تنظیمات لاگ ذخیره شد', 'success'); loadSettings(); }
    else { showMsg('logs-msg', data.detail || 'ذخیره تنظیمات ناموفق بود', 'danger'); }
  });

  el('t-preview').addEventListener('click', preview);
  el('t-execute').addEventListener('click', execute);
  ['t-category', 't-level', 't-older'].forEach((id) => {
    el(id).addEventListener('change', () => {
      el('t-execute').disabled = true;
      el('t-confirm').className = 'd-none';
      pending = null;
    });
  });
}
