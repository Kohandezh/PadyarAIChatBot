/* The registered visitors screen.
 *
 * SECURITY RULE — DO NOT UNDO:
 * Every value on this page was typed by a member of the public: a name, a job
 * title, a free-text interest. It is rendered inside an authenticated admin
 * session, so it NEVER goes in with innerHTML. textContent or createElement,
 * always. Same rule, same reason, as static/admin/js/logs.js.
 */
import { fetchAuth } from './utils.js';

const el = (id) => document.getElementById(id);
const FILTER_KEYS = ['since', 'until', 'job', 'interest', 'q'];

let state = { limit: 50, offset: 0, hasMore: false };

/* A table cell holding text and nothing else. */
function td(value, opts = {}) {
  const cell = document.createElement('td');
  const text = value === null || value === undefined || value === '' ? '—' : String(value);
  cell.textContent = text;
  if (opts.ltr) { cell.dir = 'ltr'; cell.style.fontFamily = 'monospace'; }
  if (opts.wrap) { cell.style.maxWidth = '260px'; cell.style.whiteSpace = 'normal'; cell.style.wordBreak = 'break-word'; }
  return cell;
}

function when(value) {
  return String(value || '').replace('T', ' ').slice(0, 16) || '—';
}

function filters() {
  const f = {};
  FILTER_KEYS.forEach((k) => {
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
  const qs = new URLSearchParams({ ...f, limit: state.limit, offset: state.offset });
  const body = el('visitors-body');
  body.replaceChildren(td('در حال بارگذاری…'));
  try {
    const res = await fetchAuth('/admin/api/visitors?' + qs);
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    state.hasMore = data.has_more;
    render(data.rows || []);
    el('visitors-range').textContent = (data.rows || []).length
      ? `${state.offset + 1} تا ${state.offset + data.rows.length}`
      : 'موردی نیست';
    el('btn-prev').disabled = state.offset <= 0;
    el('btn-next').disabled = !state.hasMore;
  } catch (e) {
    body.replaceChildren(td('خطا در دریافت فهرست بازدیدکنندگان'));
  }
}

function emptyRow(text, columns) {
  const tr = document.createElement('tr');
  const cell = document.createElement('td');
  cell.colSpan = columns;
  cell.className = 'text-center text-muted py-4';
  cell.textContent = text;
  tr.append(cell);
  return tr;
}

function render(rows) {
  const body = el('visitors-body');
  body.replaceChildren();
  if (!rows.length) {
    body.append(emptyRow('هیچ بازدیدکننده‌ای با این فیلترها پیدا نشد.', 8));
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => openVisitor(r));

    const name = [r.first_name, r.last_name].filter(Boolean).join(' ');
    tr.append(td(name));
    tr.append(td(r.phone, { ltr: true }));
    tr.append(td(r.job));
    tr.append(td(r.position));
    tr.append(td(r.interests, { wrap: true }));
    tr.append(td(r.conversation_count));
    tr.append(td(when(r.last_seen_at), { ltr: true }));

    // The one thing this screen is for: from a person to what they asked.
    const action = document.createElement('td');
    const link = document.createElement('a');
    link.className = 'btn btn-sm btn-outline-primary';
    link.textContent = 'گفتگوها';
    link.href = '/secure-panel-inotex/conversations?visitor_id=' + encodeURIComponent(r.id);
    link.addEventListener('click', (e) => e.stopPropagation());
    action.append(link);
    tr.append(action);

    body.append(tr);
  });
}

/* ── One person, in full ───────────────────────────────────────────── */
const FIELD_FA = {
  first_name: 'نام', last_name: 'نام خانوادگی', phone: 'شمارهٔ تماس',
  job: 'شغل', position: 'سمت', interests: 'زمینه‌های مورد علاقه',
  conversation_count: 'تعداد گفتگو', created_at: 'زمان ثبت‌نام',
  last_seen_at: 'آخرین بازدید',
};

function openVisitor(row) {
  const panel = el('visitor-body');
  panel.replaceChildren();

  const table = document.createElement('table');
  table.className = 'table table-sm';
  Object.keys(FIELD_FA).forEach((key) => {
    const value = row[key];
    if (value === null || value === undefined || value === '') return;
    const tr = document.createElement('tr');
    const th = document.createElement('th');
    th.style.width = '45%';
    th.textContent = FIELD_FA[key];
    const isTime = key.endsWith('_at');
    tr.append(th, td(isTime ? when(value) : value,
                     { ltr: key === 'phone' || isTime, wrap: true }));
    table.append(tr);
  });
  panel.append(table);

  // Anything the chatbot itself asked and recorded later. Empty for most
  // people, which is why it is a section that appears rather than a column.
  const answers = row.answers || {};
  const keys = Object.keys(answers);
  if (keys.length) {
    const head = document.createElement('h4');
    head.className = 'mt-4';
    head.textContent = 'پاسخ‌هایی که در گفتگو داده';
    const extra = document.createElement('table');
    extra.className = 'table table-sm';
    keys.forEach((key) => {
      const tr = document.createElement('tr');
      const th = document.createElement('th');
      th.style.width = '45%';
      th.textContent = key;
      tr.append(th, td(String(answers[key]), { wrap: true }));
      extra.append(tr);
    });
    panel.append(head, extra);
  }

  const go = document.createElement('a');
  go.className = 'btn btn-primary w-100 mt-3';
  go.textContent = 'گفتگوهای این نفر';
  go.href = '/secure-panel-inotex/conversations?visitor_id=' + encodeURIComponent(row.id);
  panel.append(go);

  new bootstrap.Offcanvas(el('visitor-panel')).show();
}

export function initVisitors() {
  applyUrlToForm();
  el('visitors-filter').addEventListener('submit', (e) => {
    e.preventDefault(); state.offset = 0; load();
  });
  el('btn-reset').addEventListener('click', () => {
    el('visitors-filter').reset(); state.offset = 0; load();
  });
  el('btn-prev').addEventListener('click', () => {
    state.offset = Math.max(0, state.offset - state.limit); load();
  });
  el('btn-next').addEventListener('click', () => {
    if (state.hasMore) { state.offset += state.limit; load(); }
  });
  el('page-size').addEventListener('change', (e) => {
    state.limit = parseInt(e.target.value, 10) || 50; state.offset = 0; load();
  });
  el('btn-csv').addEventListener('click', () => {
    window.location = '/admin/api/visitors/export?' + new URLSearchParams(filters());
  });
  load();
}
