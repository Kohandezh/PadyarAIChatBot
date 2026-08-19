/* Log explorer.
 *
 * SECURITY RULE — DO NOT UNDO:
 * Every value in a log row is ATTACKER-CONTROLLED. A visitor chooses their own
 * user-agent; a visitor writes the chat message that ends up in an error; a
 * visitor picks the phone number in a failed OTP. Those strings are rendered
 * here, inside an authenticated admin session.
 *
 * Therefore: NEVER assign row data with innerHTML. Use textContent or
 * createElement, always. A stored XSS in this viewer would let any visitor run
 * script as the administrator — the worst possible outcome for a log page.
 * The server also strips control characters, but that is defence in depth, not
 * a licence to interpolate HTML here.
 */
import { fetchAuth } from './utils.js';

const el = (id) => document.getElementById(id);

const LEVEL_CLASS = {
  debug: 'bg-secondary-lt', info: 'bg-blue-lt', notice: 'bg-azure-lt',
  warning: 'bg-orange-lt', error: 'bg-red-lt', critical: 'bg-red',
  alert: 'bg-red', emergency: 'bg-red',
};
const LEVEL_FA = {
  debug: 'اشکال‌زدایی', info: 'اطلاع', notice: 'توجه', warning: 'هشدار',
  error: 'خطا', critical: 'بحرانی', alert: 'اضطرار', emergency: 'فوری',
};

let CATEGORIES = {};
let state = { limit: 50, offset: 0, total: 0, sort: 'created_at', direction: 'desc' };

/* Build a text node safely. This is the only way row data enters the DOM. */
function td(value, opts = {}) {
  const cell = document.createElement('td');
  cell.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  if (opts.ltr) { cell.dir = 'ltr'; cell.style.fontFamily = 'monospace'; cell.style.fontSize = '12px'; }
  if (opts.wrap) { cell.style.maxWidth = '340px'; cell.style.whiteSpace = 'normal'; cell.style.wordBreak = 'break-word'; }
  return cell;
}

function badge(text, cls) {
  const span = document.createElement('span');
  span.className = 'badge ' + (cls || 'bg-secondary-lt');
  span.textContent = text;          // textContent, never innerHTML
  return span;
}

/* Read the filter bar. Also what gets written to the URL so a filtered view
   is shareable — an operator investigating an incident can send the link. */
function filters() {
  const f = {};
  ['q', 'category', 'level', 'since', 'until', 'actor', 'ip', 'provider',
   'request_id', 'correlation_id', 'min_duration'].forEach((k) => {
    const node = el('f-' + k);
    if (node && node.value.trim()) f[k] = node.value.trim();
  });
  return f;
}

function syncUrl(f) {
  const qs = new URLSearchParams(f);
  if (state.offset) qs.set('offset', state.offset);
  history.replaceState(null, '', location.pathname + (qs.toString() ? '?' + qs : ''));
}

function applyUrlToForm() {
  const qs = new URLSearchParams(location.search);
  qs.forEach((value, key) => {
    const node = el('f-' + key);
    if (node) node.value = value;
  });
  state.offset = parseInt(qs.get('offset') || '0', 10) || 0;
}

async function load() {
  const f = filters();
  syncUrl(f);
  const qs = new URLSearchParams({ ...f, limit: state.limit, offset: state.offset,
                                   sort: state.sort, direction: state.direction });
  const body = el('logs-body');
  body.replaceChildren(td('در حال بارگذاری…'));
  try {
    const res = await fetchAuth('/admin/api/logs?' + qs);
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    CATEGORIES = data.categories || {};
    state.total = data.total;
    render(data.rows);
    el('logs-count').textContent = data.total.toLocaleString('fa-IR');
    el('logs-range').textContent = data.total
      ? `${(state.offset + 1).toLocaleString('fa-IR')} تا ${Math.min(state.offset + state.limit, data.total).toLocaleString('fa-IR')}`
      : '—';
    el('btn-prev').disabled = state.offset <= 0;
    el('btn-next').disabled = state.offset + state.limit >= data.total;
  } catch (e) {
    body.replaceChildren(td('خطا در دریافت لاگ‌ها'));
  }
}

function render(rows) {
  const body = el('logs-body');
  body.replaceChildren();
  if (!rows.length) {
    const tr = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = 8;
    cell.className = 'text-center text-muted py-4';
    cell.textContent = 'رکوردی با این فیلترها پیدا نشد.';
    tr.append(cell); body.append(tr);
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => openDetail(r.id));

    const when = document.createElement('td');
    when.dir = 'ltr';
    when.style.fontSize = '12px';
    when.textContent = (r.created_at || '').replace('T', ' ').slice(0, 19);
    tr.append(when);

    const lvl = document.createElement('td');
    lvl.append(badge(LEVEL_FA[r.level] || r.level, LEVEL_CLASS[r.level]));
    tr.append(lvl);

    const cat = document.createElement('td');
    cat.append(badge(CATEGORIES[r.category] || r.category, 'bg-secondary-lt'));
    tr.append(cat);

    tr.append(td(r.event_name, { ltr: true }));
    tr.append(td(r.message, { wrap: true }));
    tr.append(td(r.actor));
    tr.append(td(r.ip, { ltr: true }));
    tr.append(td(r.duration_ms !== null && r.duration_ms !== undefined ? r.duration_ms + ' ms' : ''));
    body.append(tr);
  });
}

/* ── Detail drawer ─────────────────────────────────────────────────── */
const FIELD_FA = {
  created_at: 'زمان', level: 'سطح', category: 'دسته', subcategory: 'زیردسته',
  event_name: 'نام رخداد', message: 'پیام', outcome: 'نتیجه', actor: 'عامل',
  actor_type: 'نوع عامل', target: 'هدف', ip: 'نشانی IP', user_agent: 'مرورگر',
  provider: 'سرویس‌دهنده', model: 'مدل', route: 'مسیر', http_method: 'متد',
  http_status: 'کد وضعیت', duration_ms: 'مدت (ms)', tokens_in: 'توکن ورودی',
  tokens_out: 'توکن خروجی', cost: 'هزینه', retry_count: 'تلاش مجدد',
  error_type: 'نوع خطا', error_code: 'کد خطا', request_id: 'شناسه درخواست',
  correlation_id: 'شناسه همبستگی', conversation_id: 'شناسه گفتگو', source: 'جدول',
};

async function openDetail(id) {
  const panel = el('detail-body');
  panel.replaceChildren(document.createTextNode('در حال بارگذاری…'));
  new bootstrap.Offcanvas(el('detail-panel')).show();
  try {
    const res = await fetchAuth('/admin/api/logs/' + encodeURIComponent(id));
    if (!res.ok) throw new Error('http ' + res.status);
    const { row, related, related_key } = await res.json();
    panel.replaceChildren();

    const table = document.createElement('table');
    table.className = 'table table-sm';
    Object.keys(FIELD_FA).forEach((key) => {
      const value = row[key];
      if (value === null || value === undefined || value === '') return;
      const tr = document.createElement('tr');
      const th = document.createElement('th');
      th.style.width = '40%';
      th.textContent = FIELD_FA[key];
      tr.append(th, td(value, { ltr: /_id$|^ip$|^route$/.test(key), wrap: true }));
      table.append(tr);
    });
    panel.append(table);

    ['request_id', 'correlation_id'].forEach((key) => {
      if (!row[key]) return;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-sm btn-outline-secondary ms-2 mb-2';
      btn.textContent = 'کپی ' + FIELD_FA[key];
      btn.addEventListener('click', () => navigator.clipboard.writeText(row[key]));
      panel.append(btn);
    });

    if (row.stack) {
      const details = document.createElement('details');
      details.className = 'mt-3';
      const sum = document.createElement('summary');
      sum.textContent = 'دنبالهٔ خطا (stack)';
      const pre = document.createElement('pre');
      pre.dir = 'ltr';
      pre.style.cssText = 'white-space:pre-wrap;word-break:break-word;font-size:11px;max-height:280px;overflow:auto';
      pre.textContent = row.stack;                 // textContent
      details.append(sum, pre); panel.append(details);
    }

    if (row.metadata) {
      const details = document.createElement('details');
      details.className = 'mt-3';
      const sum = document.createElement('summary');
      sum.textContent = 'پیشرفته (metadata)';
      const pre = document.createElement('pre');
      pre.dir = 'ltr';
      pre.style.cssText = 'white-space:pre-wrap;word-break:break-word;font-size:11px;max-height:280px;overflow:auto';
      try { pre.textContent = JSON.stringify(JSON.parse(row.metadata), null, 2); }
      catch { pre.textContent = row.metadata; }
      details.append(sum, pre); panel.append(details);
    }

    if (related && related.length > 1) {
      const head = document.createElement('h4');
      head.className = 'mt-4';
      head.textContent = `رخدادهای مرتبط (${FIELD_FA[related_key] || related_key})`;
      panel.append(head);
      const list = document.createElement('div');
      list.className = 'list-group';
      related.forEach((r) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'list-group-item list-group-item-action';
        const line = document.createElement('div');
        line.textContent = `${(r.created_at || '').slice(11, 19)} · ${r.event_name}`;
        const sub = document.createElement('small');
        sub.className = 'text-muted';
        sub.textContent = r.message || '';
        item.append(line, sub);
        item.addEventListener('click', () => openDetail(r.id));
        list.append(item);
      });
      panel.append(list);
    }
  } catch (e) {
    panel.replaceChildren(document.createTextNode('خطا در دریافت جزئیات'));
  }
}

function exportAs(format) {
  const qs = new URLSearchParams({ ...filters(), format });
  window.location = '/admin/api/logs/export?' + qs;
}

export function initLogs() {
  applyUrlToForm();
  el('logs-filter').addEventListener('submit', (e) => { e.preventDefault(); state.offset = 0; load(); });
  el('btn-reset').addEventListener('click', () => {
    el('logs-filter').reset(); state.offset = 0; load();
  });
  el('btn-prev').addEventListener('click', () => { state.offset = Math.max(0, state.offset - state.limit); load(); });
  el('btn-next').addEventListener('click', () => { state.offset += state.limit; load(); });
  el('page-size').addEventListener('change', (e) => {
    state.limit = parseInt(e.target.value, 10) || 50; state.offset = 0; load();
  });
  el('btn-csv').addEventListener('click', () => exportAs('csv'));
  el('btn-json').addEventListener('click', () => exportAs('json'));
  load();
}
