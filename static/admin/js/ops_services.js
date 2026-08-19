/* Services control centre.
 * SECURITY: probe detail text can contain messages from external systems.
 * textContent / createElement only — never innerHTML with dynamic values.
 */
import { fetchAuth, showMsg } from './utils.js';

const el = (id) => document.getElementById(id);
const BADGE = { healthy: 'bg-green', degraded: 'bg-orange', down: 'bg-red',
                disabled: 'bg-secondary', unknown: 'bg-secondary-lt' };

async function load(force) {
  const res = await fetchAuth('/admin/api/ops/services' + (force ? '?force=true' : ''));
  if (!res.ok) return;
  const d = await res.json();

  el('svc-score').textContent = (d.health.score).toLocaleString('fa-IR');
  el('svc-score-label').textContent = d.health.label_fa;

  // Honest statement about what this deployment can and cannot control.
  const note = el('process-note');
  note.replaceChildren();
  if (!d.process_control.available) {
    note.className = 'alert alert-info';
    const strong = document.createElement('strong');
    strong.textContent = 'کنترل پروسهٔ برنامه در این نصب در دسترس نیست. ';
    const span = document.createElement('span');
    span.textContent = d.process_control.reason_fa;
    note.append(strong, span);
  }

  const wrap = el('svc-list');
  wrap.replaceChildren();
  d.services.forEach((s) => {
    const col = document.createElement('div');
    col.className = 'col-md-6 col-xl-4';
    const card = document.createElement('div');
    card.className = 'card';
    const body = document.createElement('div');
    body.className = 'card-body';

    const head = document.createElement('div');
    head.className = 'd-flex align-items-center mb-2';
    const title = document.createElement('h3');
    title.className = 'card-title m-0';
    title.textContent = s.label_fa;
    const badge = document.createElement('span');
    badge.className = 'badge ms-auto ' + (BADGE[s.status] || 'bg-secondary');
    badge.textContent = s.status_fa;
    head.append(title, badge);

    const detail = document.createElement('div');
    detail.className = 'text-muted small';
    detail.textContent = s.detail_fa;

    const meta = document.createElement('div');
    meta.className = 'text-muted small mt-1';
    meta.textContent = `تأخیر بررسی: ${s.latency_ms} ms`
      + (s.critical ? ' · حیاتی' : '')
      + (s.dependencies.length ? ' · وابسته به: ' + s.dependencies.join('، ') : '');

    body.append(head, detail, meta);

    if (s.read_only_reason) {
      const ro = document.createElement('div');
      ro.className = 'text-muted small mt-2 fst-italic';
      ro.textContent = 'فقط خواندنی — ' + s.read_only_reason;
      body.append(ro);
    }

    if (s.actions && s.actions.length) {
      const bar = document.createElement('div');
      bar.className = 'mt-3 d-flex flex-wrap gap-2';
      s.actions.forEach((a) => {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-sm ' + (a.destructive ? 'btn-outline-danger' : 'btn-outline-primary');
        btn.textContent = a.label_fa;
        btn.addEventListener('click', () => runAction(a, btn));
        bar.append(btn);
      });
      body.append(bar);
    }

    card.append(body); col.append(card); wrap.append(col);
  });
}

async function runAction(action, btn) {
  if (action.destructive &&
      !window.confirm(`«${action.label_fa}» اجرا شود؟ این عملیات در رخدادهای حساس ثبت می‌شود.`)) {
    return;
  }
  btn.disabled = true;
  const original = btn.textContent;
  btn.textContent = 'در حال اجرا…';
  try {
    const res = await fetchAuth('/admin/api/ops/services/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action.name }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      showMsg('svc-msg', `${data.label_fa}: ${data.message_fa} (${data.duration_ms} ms)`, 'success');
      load(true);
    } else {
      showMsg('svc-msg', data.detail || 'اجرای عملیات ناموفق بود.', 'danger');
    }
  } catch {
    showMsg('svc-msg', 'خطای ارتباط با سرور', 'danger');
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

export function initOpsServices() {
  el('btn-refresh').addEventListener('click', () => load(true));
  load(false);
}
