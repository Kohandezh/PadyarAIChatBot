/* The registered visitors screen.
 *
 * SECURITY RULE — DO NOT UNDO:
 * Every value on this page was typed by a member of the public: a name, a job
 * title, a free-text interest. It is rendered inside an authenticated admin
 * session, so it NEVER goes in with innerHTML. textContent or createElement,
 * always. Same rule, same reason, as static/admin/js/logs.js.
 */
import { fetchAuth, initBulkSelection } from './utils.js';

const el = (id) => document.getElementById(id);
const FILTER_KEYS = ['since', 'until', 'job', 'interest', 'q'];

let state = { limit: 50, offset: 0, hasMore: false };
let bulkSelection = null;

/* Who the side panel is showing right now. The "sign out everywhere" button
 * lives in the template and is bound once, so it has to read the visitor from
 * somewhere; a stale id here would end the WRONG person's sessions, which is
 * why openVisitor() sets it and nothing else writes it. */
let openRow = null;

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
    body.append(emptyRow('هیچ بازدیدکننده‌ای با این فیلترها پیدا نشد.', 9));
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => openVisitor(r));

    const checkCell = document.createElement('td');
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.className = 'form-check-input row-check';
    check.value = r.id;
    check.addEventListener('click', (e) => e.stopPropagation());
    checkCell.append(check);
    tr.append(checkCell);

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

  if (bulkSelection) bulkSelection.clear();
}

/* ── Bulk delete ────────────────────────────────────────────────────── */

async function bulkDeleteVisitors() {
  const ids = bulkSelection.getSelected();
  if (ids.length === 0) return;
  if (!confirm(`${ids.length} بازدیدکننده و همهٔ گفتگوهای آن‌ها برای همیشه حذف شود؟ این عمل قابل بازگشت نیست.`)) return;

  const res = await fetchAuth('/admin/api/visitors/bulk-delete', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (res.ok) {
    load();
  } else {
    alert('خطا در حذف بازدیدکنندگان');
  }
}

/* ── One person, in full ───────────────────────────────────────────── */
const FIELD_FA = {
  first_name: 'نام', last_name: 'نام خانوادگی', phone: 'شمارهٔ تماس',
  job: 'شغل', position: 'سمت', interests: 'زمینه‌های مورد علاقه',
  conversation_count: 'تعداد گفتگو', created_at: 'زمان ثبت‌نام',
  last_seen_at: 'آخرین بازدید',
};

function openVisitor(row) {
  openRow = row;
  el('revoke-msg').textContent = '';   // last person's result is not this one's
  el('delete-visitor-msg').textContent = '';
  renderVisitorView(row);
  new bootstrap.Offcanvas(el('visitor-panel')).show();
}

function renderVisitorView(row) {
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

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.className = 'btn btn-outline-secondary w-100 mt-3';
  editBtn.textContent = 'ویرایش اطلاعات';
  editBtn.addEventListener('click', () => renderVisitorEditForm(row));
  panel.append(editBtn);

  const go = document.createElement('a');
  go.className = 'btn btn-primary w-100 mt-2';
  go.textContent = 'گفتگوهای این نفر';
  go.href = '/secure-panel-inotex/conversations?visitor_id=' + encodeURIComponent(row.id);
  panel.append(go);
}

/* ── Editing a visitor's own profile fields ────────────────────────────
 *
 * Phone is intentionally not here: it is the OTP identity key
 * (phone_hash), and letting an operator change it would desync a person's
 * record from the number they actually verified.
 */
const EDITABLE_FIELDS = [
  ['first_name', 'نام'],
  ['last_name', 'نام خانوادگی'],
  ['job', 'شغل'],
  ['position', 'سمت'],
  ['interests', 'زمینه‌های مورد علاقه'],
];

function renderVisitorEditForm(row) {
  const panel = el('visitor-body');
  panel.replaceChildren();

  const inputs = {};
  EDITABLE_FIELDS.forEach(([key, label]) => {
    const group = document.createElement('div');
    group.className = 'mb-2';
    const lbl = document.createElement('label');
    lbl.className = 'form-label';
    lbl.textContent = label;
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control';
    input.value = row[key] || '';
    inputs[key] = input;
    group.append(lbl, input);
    panel.append(group);
  });

  const msg = document.createElement('div');
  msg.className = 'small mt-2';

  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'btn btn-primary w-100 mt-2';
  saveBtn.textContent = 'ذخیره';
  saveBtn.addEventListener('click', async () => {
    const payload = {};
    EDITABLE_FIELDS.forEach(([key]) => { payload[key] = inputs[key].value.trim(); });
    saveBtn.disabled = true;
    msg.className = 'small mt-2 text-muted';
    msg.textContent = 'در حال ذخیره…';
    try {
      const res = await fetchAuth('/admin/api/visitors/' + encodeURIComponent(row.id), {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error('http ' + res.status);
      const data = await res.json();
      openVisitor(data.visitor);
      load();
    } catch (e) {
      msg.className = 'small mt-2 text-danger';
      msg.textContent = 'ذخیره نشد. دوباره تلاش کنید.';
      saveBtn.disabled = false;
    }
  });

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'btn btn-outline-secondary w-100 mt-2';
  cancelBtn.textContent = 'انصراف';
  cancelBtn.addEventListener('click', () => renderVisitorView(row));

  panel.append(saveBtn, cancelBtn, msg);
}

/* ── Delete this visitor ─────────────────────────────────────────────── */

async function deleteVisitor() {
  if (!openRow) return;
  const name = [openRow.first_name, openRow.last_name].filter(Boolean).join(' ')
    || 'این بازدیدکننده';
  if (!confirm(`${name} و همهٔ گفتگوهایش برای همیشه حذف شود؟ این عمل قابل بازگشت نیست.`)) return;

  const msg = el('delete-visitor-msg');
  const button = el('btn-delete-visitor');
  button.disabled = true;
  msg.className = 'small mt-2 text-muted';
  msg.textContent = 'در حال حذف…';
  try {
    const res = await fetchAuth(
      '/admin/api/visitors/' + encodeURIComponent(openRow.id), { method: 'DELETE' });
    if (!res.ok) throw new Error('http ' + res.status);
    bootstrap.Offcanvas.getInstance(el('visitor-panel'))?.hide();
    load();
  } catch (e) {
    msg.className = 'small mt-2 text-danger';
    msg.textContent = 'حذف نشد. دوباره تلاش کنید.';
    button.disabled = false;
  }
}

/* ── Sign this person out everywhere ───────────────────────────────────
 *
 * The stolen-phone control. The session cookie in that phone IS the
 * credential, so whoever holds the phone is this visitor to the install until
 * the sessions are deleted. This is the only way an operator can delete them.
 *
 * fetchAuth, never a bare fetch: it attaches X-CSRF-Token, and without that
 * header app/auth/csrf.py answers 403 before the endpoint runs, so the
 * button would look like it worked and change nothing.
 */
async function revokeSessions() {
  if (!openRow) return;
  const name = [openRow.first_name, openRow.last_name].filter(Boolean).join(' ')
    || 'این نفر';
  // Two steps on purpose. Ending somebody's access must not be a single
  // mis-click, and the message names the person so a wrong row is caught here.
  if (!confirm(`${name} از همهٔ دستگاه‌ها خارج شود؟ برای ادامهٔ گفتگو باید دوباره شماره‌اش را تأیید کند.`)) return;

  const msg = el('revoke-msg');
  const button = el('btn-revoke-sessions');
  button.disabled = true;
  msg.className = 'small mt-2 text-muted';
  msg.textContent = 'در حال انجام…';
  try {
    const res = await fetchAuth(
      '/admin/api/visitors/' + encodeURIComponent(openRow.id) + '/sessions/revoke',
      { method: 'POST' });
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    msg.className = 'small mt-2 text-success';
    // The count matters: 0 means they were already signed out everywhere, and
    // an operator who was told "انجام شد" would not know that.
    msg.textContent = data.revoked
      ? `انجام شد. ${data.revoked} دستگاه از حساب خارج شد.`
      : 'این نفر روی هیچ دستگاهی وارد نبود.';
  } catch (e) {
    msg.className = 'small mt-2 text-danger';
    msg.textContent = 'انجام نشد. دوباره تلاش کنید.';
  } finally {
    button.disabled = false;
  }
}

export function initVisitors() {
  applyUrlToForm();
  bulkSelection = initBulkSelection({
    selectAllEl: el('visitors-select-all'),
    toolbarEl: el('visitors-bulk-toolbar'),
    countEl: el('visitors-bulk-count'),
  });
  bulkSelection.attach(el('visitors-body'));
  el('btn-bulk-delete-visitors').addEventListener('click', bulkDeleteVisitors);
  el('btn-delete-visitor').addEventListener('click', deleteVisitor);
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
  el('btn-revoke-sessions').addEventListener('click', revokeSessions);
  el('btn-csv').addEventListener('click', () => {
    window.location = '/admin/api/visitors/export?' + new URLSearchParams(filters());
  });
  load();
}
