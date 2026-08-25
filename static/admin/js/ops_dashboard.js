/* Main operations dashboard.
 * SECURITY: values here derive from log rows and probe output, both of which
 * can contain attacker-influenced text. textContent / createElement only —
 * never innerHTML with dynamic values.
 */
import { fetchAuth, showMsg } from './utils.js';

const el = (id) => document.getElementById(id);
const fa = (n) => (n === null || n === undefined ? '—' : Number(n).toLocaleString('fa-IR'));

function bytes(n) {
  if (!n) return '۰';
  const u = ['بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(1).replace('.', '٫')} ${u[i]}`;
}

/* Every KPI links to the page that explains it. A number with no drill-down
   forces the operator to guess where to look next. */
function tile(id, value, href) {
  const node = el(id);
  if (!node) return;
  node.textContent = value;
  if (href) {
    const card = node.closest('.card');
    if (card) {
      card.style.cursor = 'pointer';
      card.onclick = () => { window.location = href; };
    }
  }
}

async function load() {
  // Rides the same 30s cycle as the KPIs so a toggle flipped in another
  // tab shows up here too (the state read is uncached server-side).
  loadMaintenance();
  const res = await fetchAuth('/admin/api/ops/dashboard');
  if (!res.ok) return;
  const d = await res.json();

  // health
  const h = d.health;
  el('health-score').textContent = fa(h.score);
  el('health-label').textContent = h.label_fa;
  const ring = el('health-ring');
  ring.className = 'h1 m-0 ' + (h.score >= 90 ? 'text-green' : h.score >= 60 ? 'text-orange' : 'text-red');

  tile('t-svc-healthy', fa(d.services.healthy), '/secure-panel-inotex/ops/services');
  tile('t-svc-degraded', fa(d.services.degraded), '/secure-panel-inotex/ops/services');
  tile('t-svc-down', fa(d.services.down), '/secure-panel-inotex/ops/services');

  tile('t-messages', fa(d.traffic.messages_24h), '/secure-panel-inotex/logs?category=chat');
  tile('t-api', fa(d.traffic.api_requests_24h), '/secure-panel-inotex/logs?category=api');
  tile('t-api-err', fa(d.traffic.api_errors_24h), '/secure-panel-inotex/logs?category=api&level=error');

  tile('t-llm', fa(d.ai.requests_24h), '/secure-panel-inotex/logs?category=llm');
  tile('t-llm-err', fa(d.ai.errors_24h), '/secure-panel-inotex/logs?category=llm&level=error');
  el('t-llm-rate').textContent = d.ai.error_rate.toLocaleString('fa-IR') + '٪';
  el('t-llm-latency').textContent = d.ai.avg_latency_ms ? fa(d.ai.avg_latency_ms) + ' ms' : '—';
  tile('t-tokens', fa(d.ai.tokens_24h), '/secure-panel-inotex/logs?category=llm');

  tile('t-sms', fa(d.sms.events_24h), '/secure-panel-inotex/logs?category=sms');
  tile('t-sms-err', fa(d.sms.failures_24h), '/secure-panel-inotex/logs?category=sms&level=error');

  tile('t-failed-logins', fa(d.security.failed_logins_24h), '/secure-panel-inotex/logs?category=security');
  tile('t-security', fa(d.security.events_24h), '/secure-panel-inotex/logs?category=security');
  tile('t-sessions', fa(d.security.active_sessions), '/secure-panel-inotex/security/sessions');
  tile('t-audit', fa(d.security.audit_24h), '/secure-panel-inotex/logs?category=audit');

  tile('t-log-total', fa(d.logs.total_events), '/secure-panel-inotex/logs');
  el('t-log-storage').textContent = bytes(d.logs.storage_bytes);
  el('t-uptime').textContent = d.process.uptime_fa;
  const engine = d.process.db_engine === 'postgres' ? 'PostgreSQL' : 'SQLite';
  el('t-runtime').textContent = `Python ${d.process.python} · ${engine}`;
}

/* ── Maintenance mode ─────────────────────────────────────────────────
 * The one switch that silences the public chatbot. State shapes mirror
 * maintenance.state()/set_maintenance exactly — the POST response IS the
 * new state, so a successful toggle re-renders from it with no extra GET
 * and no stale read. enabled_by/enabled_at are admin-entered text, so
 * they reach the page through textContent only.
 */
let maintenancePending = false;

function renderMaintenance(m) {
  const toggle = el('maintenance-toggle');
  // Skip the checkbox while the operator is on it or a POST is in flight:
  // the 30s poll otherwise snaps the switch back to the old server state
  // mid-click and the card lies about what was just changed.
  if (toggle && document.activeElement !== toggle && !maintenancePending) {
    toggle.checked = m.enabled;
  }
  const status = el('maintenance-status');
  if (status) status.textContent = m.enabled ? 'موقتاً تعطیل' : 'فعال';
  const meta = el('maintenance-meta');
  if (meta) {
    meta.textContent = (m.enabled && m.enabled_by)
      ? `روشن‌کننده: ${m.enabled_by}` +
        (m.enabled_at ? ` · از ${new Date(m.enabled_at).toLocaleString('fa-IR')}` : '')
      : '';
  }
}

async function loadMaintenance() {
  // Install without the ops module renders no card — nothing to refresh.
  if (!el('maintenance-toggle')) return;
  const res = await fetchAuth('/admin/api/ops/maintenance');
  if (res.ok) renderMaintenance(await res.json());
}

async function toggleMaintenance(e) {
  const enabled = e.target.checked;
  // Only the consequential direction asks: turning ON silences every
  // visitor. Turning OFF stays frictionless — an operator must always be
  // able to undo what they did without the UI arguing.
  if (enabled &&
      !window.confirm('چت‌بات برای همهٔ بازدیدکنندگان موقتاً تعطیل می‌شود. ادامه می‌دهید؟')) {
    e.target.checked = false;
    return;
  }
  maintenancePending = true;
  try {
    const res = await fetchAuth('/admin/api/ops/maintenance', {
      method: 'POST', body: JSON.stringify({ enabled, reason: '' }) });
    if (res.ok) {
      renderMaintenance(await res.json());
      showMsg('maintenance-msg',
              enabled ? 'حالت تعمیرات روشن شد' : 'حالت تعمیرات خاموش شد', 'success');
    } else {
      // The change never landed — put the switch back and surface the
      // server's Persian detail instead of a generic shrug.
      e.target.checked = !enabled;
      const data = await res.json().catch(() => ({}));
      showMsg('maintenance-msg', data.detail || 'تغییر ممکن نشد', 'danger');
    }
  } catch {
    e.target.checked = !enabled;
    showMsg('maintenance-msg', 'خطای ارتباط با سرور', 'danger');
  } finally {
    maintenancePending = false;
  }
}

export function initOpsDashboard() {
  const toggle = el('maintenance-toggle');
  if (toggle) toggle.onchange = toggleMaintenance;
  load();
  // 30s: the underlying probes are cached server-side, so this is cheap.
  setInterval(load, 30000);
}
