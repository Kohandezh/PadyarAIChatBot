/* Main operations dashboard.
 * SECURITY: values here derive from log rows and probe output, both of which
 * can contain attacker-influenced text. textContent / createElement only —
 * never innerHTML with dynamic values.
 */
import { fetchAuth } from './utils.js';

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

export function initOpsDashboard() {
  load();
  // 30s: the underlying probes are cached server-side, so this is cheap.
  setInterval(load, 30000);
}
