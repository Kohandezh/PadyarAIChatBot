/* Conversations, transcripts, and the queue of answers the bot got wrong.
 *
 * SECURITY RULE — DO NOT UNDO:
 * Every message on this screen was typed by a member of the public, and it is
 * rendered inside an authenticated admin session. A stored XSS here would let
 * any visitor run script as the administrator. So row data NEVER enters the
 * DOM through innerHTML — textContent or createElement, always. Same rule,
 * same reason, as static/admin/js/logs.js.
 */
import { fetchAuth } from './utils.js';

const el = (id) => document.getElementById(id);
const FILTER_KEYS = ['since', 'until', 'registered', 'source', 'q'];

let state = {
  view: 'list',
  limit: 50,
  offset: 0,
  hasMore: false,
  visitorId: '',
  sources: {},
  // Overwritten by the server on the first load. Defaults only decide what
  // the confidence dropdown means during the very first fetch.
  bounds: { weak: 0.45, trusted: 0.70 },
};

let datasetEntries = null;   // loaded once, for the "fix this answer" box
let fixTarget = { question: '', entryId: '' };

/* ── Small builders ─────────────────────────────────────────────────── */

function td(value, opts = {}) {
  const cell = document.createElement('td');
  cell.textContent = value === null || value === undefined || value === '' ? '—' : String(value);
  if (opts.ltr) { cell.dir = 'ltr'; cell.style.fontFamily = 'monospace'; cell.style.fontSize = '12px'; }
  if (opts.wrap) { cell.style.maxWidth = '320px'; cell.style.whiteSpace = 'normal'; cell.style.wordBreak = 'break-word'; }
  return cell;
}

function badge(text, cls) {
  const span = document.createElement('span');
  span.className = 'badge ' + (cls || 'bg-secondary-lt');
  span.textContent = text;
  return span;
}

function when(value) {
  return String(value || '').replace('T', ' ').slice(0, 16) || '—';
}

function percent(confidence) {
  if (confidence === null || confidence === undefined) return '—';
  return Math.round(Number(confidence) * 100) + '٪';
}

function sourceLabel(source) {
  return state.sources[source] || source || '—';
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

/* ── Filters ────────────────────────────────────────────────────────── */

function filters() {
  const f = {};
  FILTER_KEYS.forEach((k) => {
    const node = el('f-' + k);
    if (node && node.value.trim()) f[k] = node.value.trim();
  });
  // The operator picks a word, not a number. "پاسخ ضعیف" means "this session
  // contains a turn the bot itself would not have trusted".
  const band = el('f-band').value;
  if (band === 'good') f.min_confidence = state.bounds.trusted;
  if (band === 'mid') { f.min_confidence = state.bounds.weak; f.max_confidence = state.bounds.trusted; }
  if (band === 'weak') f.max_confidence = state.bounds.weak;
  if (state.visitorId) f.visitor_id = state.visitorId;
  return f;
}

function syncUrl() {
  const qs = new URLSearchParams(filters());
  if (state.view === 'weak') qs.set('view', 'weak');
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
  state.visitorId = qs.get('visitor_id') || '';
  state.view = qs.get('view') === 'weak' ? 'weak' : 'list';
  if (state.visitorId) el('btn-clear-visitor').classList.remove('d-none');
}

/* ── View switching ─────────────────────────────────────────────────── */

function switchView(view) {
  state.view = view;
  el('view-list').classList.toggle('d-none', view !== 'list');
  el('view-weak').classList.toggle('d-none', view !== 'weak');
  // The CSV button belongs to the conversation list; on the wrong-answer
  // queue it would export something the operator is not looking at.
  el('btn-csv').classList.toggle('d-none', view !== 'list');
  document.querySelectorAll('#view-tabs [data-view]').forEach((tab) => {
    const on = tab.dataset.view === view;
    const colour = tab.dataset.view === 'weak' ? 'warning' : 'primary';
    tab.className = 'btn ' + (on ? 'btn-' + colour : 'btn-outline-' + colour);
  });
  el('page-title').textContent = view === 'weak' ? 'پاسخ‌های اشتباه' : 'گفتگوها';
  el('page-hint').textContent = view === 'weak'
    ? 'سوال‌هایی که ربات درست جواب نداده. آن‌ها را همین‌جا درست کنید.'
    : 'هر گفتگو یک بار نشستن یک بازدیدکننده پای چت‌بات است.';
  syncUrl();
  if (view === 'weak') loadWeak(); else loadList();
}

/* ── View 1: the conversation list ──────────────────────────────────── */

async function loadList() {
  syncUrl();
  const qs = new URLSearchParams({ ...filters(), limit: state.limit, offset: state.offset });
  const body = el('conv-body');
  body.replaceChildren(emptyRow('در حال بارگذاری…', 6));
  try {
    const res = await fetchAuth('/admin/api/conversations?' + qs);
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    state.sources = data.sources || {};
    state.hasMore = data.has_more;
    if (data.weak_below) state.bounds = { weak: data.weak_below, trusted: data.trusted_above };
    renderList(data.rows || []);
    el('conv-range').textContent = (data.rows || []).length
      ? `${state.offset + 1} تا ${state.offset + data.rows.length}`
      : 'موردی نیست';
    el('btn-prev').disabled = state.offset <= 0;
    el('btn-next').disabled = !state.hasMore;
  } catch (e) {
    body.replaceChildren(emptyRow('خطا در دریافت گفتگوها', 6));
  }
}

function renderList(rows) {
  const body = el('conv-body');
  body.replaceChildren();
  if (!rows.length) {
    body.append(emptyRow('هیچ گفتگویی با این فیلترها پیدا نشد.', 6));
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.addEventListener('click', () => openTranscript(r.id));

    tr.append(td(when(r.last_message_at), { ltr: true }));

    const name = [r.first_name, r.last_name].filter(Boolean).join(' ');
    const who = document.createElement('td');
    who.append(name ? badge(name, 'bg-green-lt') : badge('ناشناس', 'bg-secondary-lt'));
    tr.append(who);

    tr.append(td(r.phone, { ltr: true }));
    tr.append(td(r.message_count));

    const status = document.createElement('td');
    if (r.weak_count > 0) {
      status.append(badge(`${r.weak_count} پاسخ ضعیف`, 'bg-red-lt'));
    } else {
      status.append(badge('سالم', 'bg-green-lt'));
    }
    tr.append(status);

    const action = document.createElement('td');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-sm btn-outline-secondary';
    btn.textContent = 'خواندن';
    action.append(btn);
    tr.append(action);

    body.append(tr);
  });
}

/* ── View 2: the wrong answers ──────────────────────────────────────── */

async function loadWeak() {
  const qs = new URLSearchParams({
    threshold: el('w-threshold').value,
    limit: el('w-limit').value,
  });
  const body = el('weak-body');
  body.replaceChildren(emptyRow('در حال بارگذاری…', 6));
  try {
    const res = await fetchAuth('/admin/api/conversations/weak?' + qs);
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    state.sources = data.sources || {};
    renderWeak(data.rows || []);
  } catch (e) {
    body.replaceChildren(emptyRow('خطا در دریافت پاسخ‌های اشتباه', 6));
  }
}

function renderWeak(rows) {
  const body = el('weak-body');
  body.replaceChildren();
  if (!rows.length) {
    body.append(emptyRow('هیچ پاسخ ضعیفی نیست. ربات همه را درست جواب داده.', 6));
    return;
  }
  rows.forEach((r) => {
    const tr = document.createElement('tr');
    tr.append(td(when(r.created_at), { ltr: true }));
    tr.append(td(r.question, { wrap: true }));
    tr.append(td(r.text, { wrap: true }));

    const src = document.createElement('td');
    src.append(badge(sourceLabel(r.source), r.no_answer ? 'bg-red-lt' : 'bg-orange-lt'));
    tr.append(src);

    tr.append(td(percent(r.confidence)));

    const action = document.createElement('td');
    action.style.whiteSpace = 'nowrap';

    const fix = document.createElement('button');
    fix.type = 'button';
    fix.className = 'btn btn-sm btn-primary ms-1';
    fix.textContent = 'درست کردن';
    fix.addEventListener('click', () => openFix(r.question, r.entry_id));
    action.append(fix);

    const read = document.createElement('button');
    read.type = 'button';
    read.className = 'btn btn-sm btn-outline-secondary';
    read.textContent = 'گفتگو';
    read.addEventListener('click', () => openTranscript(r.conversation_id));
    action.append(read);

    tr.append(action);
    body.append(tr);
  });
}

/* ── The transcript ─────────────────────────────────────────────────── */

function bubble(message, weakBelow) {
  const wrap = document.createElement('div');
  const visitor = message.role === 'visitor';
  wrap.className = 'card mb-2 ' + (visitor ? 'bg-white' : 'bg-azure-lt');
  wrap.style.marginInlineStart = visitor ? '0' : '28px';
  wrap.style.marginInlineEnd = visitor ? '28px' : '0';

  const body = document.createElement('div');
  body.className = 'card-body p-2';

  const who = document.createElement('div');
  who.className = 'small text-muted mb-1';
  who.textContent = visitor ? 'بازدیدکننده' : 'ربات';
  body.append(who);

  const text = document.createElement('div');
  text.style.whiteSpace = 'pre-wrap';
  text.style.wordBreak = 'break-word';
  text.textContent = message.text || '';
  body.append(text);

  if (!visitor) {
    const meta = document.createElement('div');
    meta.className = 'mt-2';
    meta.append(badge(sourceLabel(message.source), 'bg-secondary-lt'));
    const weak = message.confidence !== null && message.confidence !== undefined
                 && Number(message.confidence) < weakBelow;
    if (message.confidence !== null && message.confidence !== undefined) {
      const conf = badge('اطمینان ' + percent(message.confidence),
                         weak ? 'bg-red-lt' : 'bg-green-lt');
      conf.classList.add('ms-1');
      meta.append(conf);
    }
    body.append(meta);
  }

  wrap.append(body);
  return wrap;
}

async function openTranscript(conversationId) {
  const panel = el('transcript-body');
  panel.replaceChildren(document.createTextNode('در حال بارگذاری…'));
  new bootstrap.Offcanvas(el('transcript-panel')).show();
  try {
    const res = await fetchAuth('/admin/api/conversations/' + encodeURIComponent(conversationId));
    if (!res.ok) throw new Error('http ' + res.status);
    const data = await res.json();
    state.sources = data.sources || state.sources;
    panel.replaceChildren();

    const head = document.createElement('div');
    head.className = 'mb-3';
    const c = data.conversation || {};
    const name = [c.first_name, c.last_name].filter(Boolean).join(' ');
    const title = document.createElement('h3');
    title.className = 'mb-1';
    title.textContent = name || 'بازدیدکنندهٔ ناشناس';
    const sub = document.createElement('div');
    sub.className = 'text-muted small';
    sub.textContent = `${when(c.started_at)} · ${c.message_count || 0} پیام`
                      + (c.phone ? ' · ' + c.phone : '');
    head.append(title, sub);
    panel.append(head);

    const weakBelow = data.weak_below || state.bounds.weak;
    const messages = data.messages || [];
    if (!messages.length) {
      panel.append(document.createTextNode('این گفتگو پیامی ندارد.'));
      return;
    }
    let lastQuestion = '';
    messages.forEach((m) => {
      panel.append(bubble(m, weakBelow));
      if (m.role === 'visitor') { lastQuestion = m.text || ''; return; }
      const weak = m.confidence !== null && m.confidence !== undefined
                   && Number(m.confidence) < weakBelow;
      if (!weak) return;
      // The same one-click fix as the wrong-answer queue, offered where the
      // mistake is being read.
      const fix = document.createElement('button');
      fix.type = 'button';
      fix.className = 'btn btn-sm btn-primary mb-3';
      fix.textContent = 'این پاسخ درست نیست — اصلاحش کن';
      const question = lastQuestion;
      fix.addEventListener('click', () => openFix(question, m.entry_id));
      panel.append(fix);
    });
  } catch (e) {
    panel.replaceChildren(document.createTextNode('خطا در دریافت متن گفتگو'));
  }
}

/* ── Fixing an answer ───────────────────────────────────────────────── */

async function loadDatasetEntries() {
  if (datasetEntries) return datasetEntries;
  const res = await fetchAuth('/admin/api/dataset');
  datasetEntries = res.ok ? await res.json() : [];
  return datasetEntries;
}

function fillEntries(term) {
  const select = el('fix-entry');
  select.replaceChildren();
  const needle = (term || '').trim().toLowerCase();
  (datasetEntries || [])
    .filter((d) => !needle || ((d.title || '') + ' ' + (d.id || '')).toLowerCase().includes(needle))
    .slice(0, 300)
    .forEach((d) => {
      const option = document.createElement('option');
      option.value = d.id;
      option.textContent = d.title || d.id;   // textContent, never innerHTML
      if (d.id === fixTarget.entryId) option.selected = true;
      select.append(option);
    });
  if (!select.options.length) {
    const option = document.createElement('option');
    option.disabled = true;
    option.textContent = 'پاسخی پیدا نشد';
    select.append(option);
  }
}

async function openFix(question, entryId) {
  fixTarget = { question: question || '', entryId: entryId || '' };
  el('fix-question').value = fixTarget.question;
  el('fix-search').value = '';
  el('fix-msg').textContent = '';
  el('fix-msg').className = 'small';
  new bootstrap.Modal(el('fix-modal')).show();
  await loadDatasetEntries();
  fillEntries('');
}

async function saveFix() {
  const question = el('fix-question').value.trim();
  const datasetId = el('fix-entry').value;
  const msg = el('fix-msg');
  if (!question || !datasetId) {
    msg.className = 'small text-danger';
    msg.textContent = 'هم سوال و هم پاسخ باید انتخاب شوند.';
    return;
  }
  msg.className = 'small text-muted';
  msg.textContent = 'در حال ذخیره…';
  try {
    const res = await fetchAuth('/admin/api/questions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, dataset_id: datasetId }),
    });
    if (!res.ok) throw new Error('http ' + res.status);
    msg.className = 'small text-success';
    msg.textContent = 'ذخیره شد. از این به بعد ربات این سوال را درست جواب می‌دهد.';
    setTimeout(() => {
      const modal = bootstrap.Modal.getInstance(el('fix-modal'));
      if (modal) modal.hide();
    }, 1200);
  } catch (e) {
    msg.className = 'small text-danger';
    msg.textContent = 'ذخیره نشد. دوباره تلاش کنید.';
  }
}

/* ── Wiring ─────────────────────────────────────────────────────────── */

export function initConversations() {
  applyUrlToForm();

  el('conv-filter').addEventListener('submit', (e) => {
    e.preventDefault(); state.offset = 0; loadList();
  });
  el('btn-reset').addEventListener('click', () => {
    el('conv-filter').reset(); state.offset = 0; loadList();
  });
  el('btn-clear-visitor').addEventListener('click', () => {
    state.visitorId = '';
    el('btn-clear-visitor').classList.add('d-none');
    state.offset = 0; loadList();
  });
  el('btn-prev').addEventListener('click', () => {
    state.offset = Math.max(0, state.offset - state.limit); loadList();
  });
  el('btn-next').addEventListener('click', () => {
    if (state.hasMore) { state.offset += state.limit; loadList(); }
  });
  el('page-size').addEventListener('change', (e) => {
    state.limit = parseInt(e.target.value, 10) || 50; state.offset = 0; loadList();
  });
  el('btn-csv').addEventListener('click', () => {
    window.location = '/admin/api/conversations/export?' + new URLSearchParams(filters());
  });

  el('w-threshold').addEventListener('change', loadWeak);
  el('w-limit').addEventListener('change', loadWeak);
  el('btn-weak-reload').addEventListener('click', loadWeak);

  el('fix-search').addEventListener('input', (e) => fillEntries(e.target.value));
  el('fix-save').addEventListener('click', saveFix);

  document.querySelectorAll('#view-tabs [data-view]').forEach((tab) => {
    tab.addEventListener('click', (e) => { e.preventDefault(); switchView(tab.dataset.view); });
  });

  switchView(state.view);
}
