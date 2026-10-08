/* Infrastructure → Backups.
 *
 * Every cell in this table is built with createElement + textContent. Nothing
 * here ever assigns innerHTML from a value that came off the wire: a backup id,
 * a manifest file name and a verification problem string are all data, and the
 * moment one of them reaches innerHTML this page becomes an injection point in
 * the admin panel.
 *
 * Both destructive actions are gated the same way: a phrase the operator has to
 * TYPE, compared exactly, with the button disabled until it matches. The server
 * checks the same phrase again — this is convenience, not the security control.
 */
import { fetchAuth, showMsg } from './utils.js';
import { loadProfile } from './settings.js';

const API = '/admin/api/infra/backups';
const el = (id) => document.getElementById(id);

let restoreTarget = '';
let deleteTarget = '';
// Told the off-site state after each load (the destination card shows its
// own warning from it). Set by initBackups().
let onOffsiteState = () => {};

/* ── formatting ─────────────────────────────────────────────────────── */

function formatBytes(n) {
    if (!n && n !== 0) return '—';
    const units = ['بایت', 'کیلوبایت', 'مگابایت', 'گیگابایت'];
    let value = n;
    let unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit += 1;
    }
    const shown = unit === 0 ? Math.round(value) : value.toFixed(1);
    return `${shown} ${units[unit]}`;
}

function formatDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('fa-IR', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
}

const KIND_LABELS = {
    manual: 'دستی',
    scheduled: 'خودکار',
    safety: 'ایمنی (پیش از بازگردانی)',
};

/* ── element helpers (no innerHTML, ever) ───────────────────────────── */

function cell(text, className) {
    const td = document.createElement('td');
    td.textContent = text;
    if (className) td.className = className;
    return td;
}

function button(label, className, onClick) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = className;
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
}

function verificationBadge(verification) {
    const span = document.createElement('span');
    const state = (verification && verification.state) || 'unknown';
    if (state === 'verified') {
        span.className = 'badge bg-success';
        span.textContent = 'سالم';
    } else if (state === 'failed') {
        span.className = 'badge bg-danger';
        span.textContent = 'خراب';
        // The reasons are shown as a title, not injected as markup.
        const problems = (verification && verification.problems) || [];
        if (problems.length) span.title = problems.join(' • ');
    } else {
        span.className = 'badge bg-secondary';
        span.textContent = 'بررسی‌نشده';
    }
    if (verification && verification.checked_at && state !== 'unknown') {
        const when = document.createElement('div');
        when.className = 'text-muted';
        when.style.fontSize = '.75rem';
        when.textContent = formatDate(verification.checked_at);
        const wrap = document.createElement('div');
        wrap.appendChild(span);
        wrap.appendChild(when);
        return wrap;
    }
    return span;
}

/* ── restore drill ──────────────────────────────────────────────────── */

/* The three results a drill can have, in the words the operator reads.
 * Colours are Bootstrap classes: green, red, gray. */
const DRILL_STATUS = {
    passed: { label: 'موفق', badge: 'bg-success', text: 'text-success' },
    failed: { label: 'ناموفق', badge: 'bg-danger', text: 'text-danger' },
    skipped: { label: 'انجام نشد', badge: 'bg-secondary', text: 'text-muted' },
};

const CHECK_LABELS = [
    ['row_counts', 'تعداد ردیف‌ها'],
    ['schema_migrations', 'نسخه‌های پایگاه داده'],
    ['validation', 'سلامت پایگاه داده'],
];

const CHECK_RESULTS = {
    passed: { label: 'درست', text: 'text-success' },
    failed: { label: 'نادرست', text: 'text-danger' },
    skipped: { label: 'بررسی نشد', text: 'text-muted' },
};

function faNumber(n) {
    return typeof n === 'number' ? n.toLocaleString('fa-IR') : '—';
}

/* "۴ دقیقه و ۲۳ ثانیه", or "کمتر از یک دقیقه". Minutes are the unit that
 * matters for a drill, so seconds are dropped under a minute. */
function formatDuration(ms) {
    if (typeof ms !== 'number' || ms < 0) return '—';
    const total = Math.round(ms / 1000);
    if (total < 60) return 'کمتر از یک دقیقه';
    const minutes = Math.floor(total / 60);
    const seconds = total % 60;
    const m = `${faNumber(minutes)} دقیقه`;
    return seconds ? `${m} و ${faNumber(seconds)} ثانیه` : m;
}

function drillBadge(row) {
    const drill = row.drill;
    const info = drill && DRILL_STATUS[drill.status];
    if (!info) return cell('—', 'text-muted');
    const td = document.createElement('td');
    // A button, not a span, so the keyboard can reach it and Enter opens it.
    const b = document.createElement('button');
    b.type = 'button';
    b.className = `badge border-0 drill-badge ${info.badge}`;
    b.textContent = info.label;
    b.title = 'دیدن نتیجهٔ تمرین بازیابی';
    b.addEventListener('click', () => openDrill(row.backup_id, drill));
    td.appendChild(b);
    return td;
}

/* Remember the newest drill time we have seen. After the operator starts a
 * drill we watch for this to change: that is how the page knows it finished. */
let lastDrillAt = null;
let watchFrom = null;
let watchUntil = 0;
let watchTimer = null;

function startWatching() {
    watchFrom = lastDrillAt;
    // A drill may restore a large database. Stop looking after 45 minutes.
    watchUntil = Date.now() + 45 * 60 * 1000;
    el('drill-running').hidden = false;
    if (!watchTimer) watchTimer = setInterval(poll, 20000);
}

/* True while a Bootstrap dialog is open or opening. Bootstrap adds
 * `modal-open` to the body at once, and `.show` to the dialog only after its
 * fade, so both are checked. */
function dialogOpen() {
    return document.body.classList.contains('modal-open')
        || Boolean(document.querySelector('.modal.show'));
}

/* The 20 second refresh after a manual drill. load() rebuilds the rows, and
 * that clears the ticked backups. An operator with the bulk-delete dialog open
 * would lose the choice while typing the confirmation. So the poll does
 * nothing while a dialog is open, and tries again on the next tick. */
function poll() {
    if (dialogOpen()) return;
    load(true);
}

function stopWatching() {
    el('drill-running').hidden = true;
    if (watchTimer) clearInterval(watchTimer);
    watchTimer = null;
}

function renderDrillStatus(latest) {
    const box = el('drill-status');
    const reason = el('drill-reason');
    box.replaceChildren();
    reason.textContent = '';
    lastDrillAt = (latest && latest.checked_at) || null;
    if (watchTimer && (lastDrillAt !== watchFrom || Date.now() > watchUntil)) {
        stopWatching();
    }

    const info = latest && DRILL_STATUS[latest.status];
    if (!info) {
        box.textContent = 'هنوز هیچ تمرین بازیابی انجام نشده است.';
        box.className = 'fw-bold text-muted';
        return;
    }
    box.className = 'fw-bold';
    box.appendChild(document.createTextNode('آخرین تمرین بازیابی: '));
    const status = document.createElement('span');
    status.className = info.text;
    status.textContent = info.label;
    box.appendChild(status);
    const when = formatDate(latest.checked_at);
    box.appendChild(document.createTextNode(
        ` · ${when} · مدت ${formatDuration(latest.duration_ms)} `));
    const more = button('دیدن جزئیات', 'btn btn-link btn-sm p-0 align-baseline',
        () => openDrill(latest.backup_id, latest));
    box.appendChild(more);
    reason.textContent = latest.reason || '';
}

function setText(id, text) {
    el(id).textContent = text;
}

function openDrill(backupId, drill) {
    const info = DRILL_STATUS[drill.status] || DRILL_STATUS.skipped;
    const status = el('drill-m-status');
    status.textContent = info.label;
    status.className = `col-sm-8 fw-bold ${info.text}`;
    setText('drill-m-backup', backupId || '—');
    setText('drill-m-date', formatDate(drill.checked_at));
    setText('drill-m-total', formatDuration(drill.duration_ms));
    setText('drill-m-restore', formatDuration(drill.restore_duration_ms));
    setText('drill-m-reason', drill.reason || '');

    const checks = el('drill-m-checks');
    checks.replaceChildren();
    CHECK_LABELS.forEach(([key, label]) => {
        const result = CHECK_RESULTS[(drill.checks || {})[key]] || CHECK_RESULTS.skipped;
        const li = document.createElement('li');
        li.appendChild(document.createTextNode(`${label}: `));
        const mark = document.createElement('b');
        mark.className = result.text;
        mark.textContent = result.label;
        li.appendChild(mark);
        checks.appendChild(li);
    });

    // Table names come from the backup, so they go in with textContent only.
    const tables = drill.tables || {};
    const names = Object.keys(tables).sort();
    const body = el('drill-m-tables');
    body.replaceChildren();
    names.forEach((name) => {
        const counts = tables[name] || {};
        const tr = document.createElement('tr');
        if (counts.expected !== counts.actual) tr.className = 'table-danger';
        tr.appendChild(cell(name, 'backup-id'));
        tr.appendChild(cell(faNumber(counts.expected)));
        tr.appendChild(cell(faNumber(counts.actual)));
        body.appendChild(tr);
    });
    el('drill-m-tables-wrap').hidden = names.length === 0;
    el('drill-m-no-tables').hidden = names.length !== 0;

    new bootstrap.Modal(el('drillModal')).show();
}

async function runDrill(id, btn) {
    btn.disabled = true;
    showMsg('backups-msg', '⏳ در حال شروع تمرین بازیابی...', 'muted');
    try {
        const res = await fetchAuth(`${API}/${encodeURIComponent(id)}/drill`,
            { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            showMsg('backups-msg', data.message || 'تمرین بازیابی شروع شد', 'success');
            startWatching();
            // Reload soon so the page shows the drill has started.
            setTimeout(load, 2000);
        } else {
            showMsg('backups-msg', await detail(res, 'شروع تمرین بازیابی ناموفق بود'),
                'danger');
        }
    } catch {
        showMsg('backups-msg', 'خطای ارتباط با سرور', 'danger');
    } finally {
        btn.disabled = false;
    }
}

function filesCell(row) {
    const td = document.createElement('td');
    td.className = 'backup-files small';
    if (!row.files.length) {
        td.textContent = 'ندارد';
        return td;
    }
    row.files.forEach((f) => {
        const line = document.createElement('div');
        const link = document.createElement('a');
        link.href = '#';
        link.textContent = `${f.label || f.name} (${formatBytes(f.bytes)})`;
        link.addEventListener('click', (e) => {
            e.preventDefault();
            downloadFile(row.backup_id, f.name);
        });
        line.appendChild(link);
        td.appendChild(line);
    });
    return td;
}

/* ── data ───────────────────────────────────────────────────────────── */

function renderSchedule(schedule) {
    const s = schedule || {};
    el('sched-enabled').textContent = s.enabled ? 'روشن' : 'خاموش';
    el('sched-interval').textContent = s.interval_hours
        ? `${s.interval_hours} ساعت` : '—';
    el('sched-keep').textContent = s.keep ? `حداکثر ${s.keep} نسخه` : '—';
    el('sched-last').textContent = formatDate(s.last_run);
    el('sched-next').textContent = formatDate(s.next_run);
}

/* Off-site copy. With no target set, every backup sits on this server, so
 * losing the server loses them all: say that, in plain words. */
function renderOffsite(offsite) {
    const o = offsite || {};
    const box = el('offsite-status');
    onOffsiteState(o);
    // A destination whose copies cannot be encrypted sends nothing, so it is
    // not "configured": say both facts (review fix 3).
    if (o.state === 'not_ready') {
        box.className = 'small mb-1 text-danger fw-bold';
        box.textContent = 'مقصد تنظیم شده، ولی رمزگذاری نسخه‌ها روی خود سرور آماده نیست، پس هیچ نسخه‌ای بیرون از سرور نیست.';
        return;
    }
    if (!o.configured) {
        box.className = 'small mb-1 text-danger fw-bold';
        box.textContent = 'هیچ نسخه‌ای بیرون از سرور نیست.';
        return;
    }
    if (o.state === 'copied') {
        box.className = 'small mb-1 text-success';
        box.textContent = `نسخهٔ بیرون از سرور: آخرین کپی موفق بود (${formatDate(o.attempted_at)}).`;
    } else if (o.state === 'failed') {
        box.className = 'small mb-1 text-danger fw-bold';
        box.textContent = `نسخهٔ بیرون از سرور: آخرین کپی ناموفق بود (${formatDate(o.attempted_at)}). جزئیات در بخش گزارش‌ها ثبت شد.`;
    } else {
        box.className = 'small mb-1 text-warning';
        box.textContent = 'نسخهٔ بیرون از سرور: جدیدترین پشتیبان هنوز بیرون کپی نشده است.';
    }
}

/* Row checkboxes for bulk delete. Kept in a Set of backup ids; rebuilt from
 * scratch on every render so it can never hold an id that is no longer on
 * disk. */
const selected = new Set();

function syncBulkButton() {
    const btn = el('bulk-delete-btn');
    if (!btn) return;
    const n = selected.size;
    btn.disabled = n === 0;
    btn.textContent = n ? `حذف ${n} نسخهٔ انتخاب‌شده` : 'حذف انتخاب‌شده‌ها';
    const all = el('select-all');
    const boxes = document.querySelectorAll('#backups-body input[type="checkbox"]');
    if (all) {
        all.checked = boxes.length > 0 && n === boxes.length;
        all.indeterminate = n > 0 && n < boxes.length;
    }
}

function checkboxCell(row) {
    const td = document.createElement('td');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.className = 'form-check-input';
    box.setAttribute('aria-label', `انتخاب ${row.backup_id}`);
    box.dataset.backupId = row.backup_id;
    box.checked = selected.has(row.backup_id);
    box.addEventListener('change', () => {
        if (box.checked) selected.add(row.backup_id);
        else selected.delete(row.backup_id);
        syncBulkButton();
    });
    td.appendChild(box);
    return td;
}

function renderRows(rows, isPg) {
    const body = el('backups-body');
    body.replaceChildren();
    selected.clear();

    if (!rows.length) {
        const tr = document.createElement('tr');
        const td = cell('هنوز هیچ نسخهٔ پشتیبانی گرفته نشده است.',
            'text-center text-muted');
        td.colSpan = 8;
        tr.appendChild(td);
        body.appendChild(tr);
        syncBulkButton();
        return;
    }

    rows.forEach((row) => {
        const tr = document.createElement('tr');

        tr.appendChild(checkboxCell(row));

        const dateTd = cell(formatDate(row.created_at));
        const kind = document.createElement('div');
        kind.className = 'text-muted';
        kind.style.fontSize = '.75rem';
        kind.textContent = KIND_LABELS[row.kind] || row.kind || '';
        dateTd.appendChild(kind);
        tr.appendChild(dateTd);

        tr.appendChild(cell(row.backup_id, 'backup-id text-muted'));
        tr.appendChild(cell(formatBytes(row.total_bytes)));
        tr.appendChild(filesCell(row));

        const healthTd = document.createElement('td');
        healthTd.appendChild(verificationBadge(row.verification));
        tr.appendChild(healthTd);

        tr.appendChild(drillBadge(row));

        const actions = document.createElement('td');
        actions.className = 'text-end';
        const group = document.createElement('div');
        group.className = 'd-flex gap-2 justify-content-end flex-wrap';
        group.appendChild(button('بررسی سلامت', 'btn btn-sm btn-outline-primary',
            () => verifyBackup(row.backup_id)));
        // The drill exists only for PostgreSQL. On SQLite the server says 409,
        // so the button is not shown at all.
        if (isPg) {
            const drillBtn = button('تمرین بازیابی', 'btn btn-sm btn-outline-secondary',
                () => runDrill(row.backup_id, drillBtn));
            group.appendChild(drillBtn);
        }
        group.appendChild(button('حذف نسخه', 'btn btn-sm btn-outline-danger',
            () => openDelete(row.backup_id)));
        // Restore replaces the WHOLE database — it must never read like just
        // another row action. Label says so, icon warns, and it sits last.
        // Warning, not danger: red is reserved for irreversible DELETE, and a
        // red بازگردانی reads as the delete button (owner report 2026-08-30).
        group.appendChild(button('⚠ بازگردانی کل پایگاه‌داده', 'btn btn-sm btn-outline-warning',
            () => openRestore(row)));
        actions.appendChild(group);
        tr.appendChild(actions);

        body.appendChild(tr);
    });
    syncBulkButton();
}

/* `fromPoll` is true only for the timer. A normal load() always draws. The
 * timer's request may have been sent before a dialog opened, so the dialog is
 * checked again when the answer arrives. */
async function load(fromPoll = false) {
    try {
        const res = await fetchAuth(API);
        if (!res.ok) {
            showMsg('backups-msg', 'خواندن فهرست نسخه‌های پشتیبان ناموفق بود', 'danger');
            return;
        }
        const data = await res.json();
        if (fromPoll && dialogOpen()) return;
        renderSchedule(data.schedule);
        renderOffsite(data.offsite);
        const isPg = data.engine === 'postgresql';
        // The drill exists only for PostgreSQL, so SQLite shows no drill box.
        el('drill-box').hidden = !isPg;
        renderRows(data.backups || [], isPg);
        renderDrillStatus(data.latest_drill);
    } catch {
        showMsg('backups-msg', 'خطای ارتباط با سرور', 'danger');
    }
}

async function detail(res, fallback) {
    try {
        const body = await res.json();
        return body.detail || fallback;
    } catch {
        return fallback;
    }
}

/* ── actions ────────────────────────────────────────────────────────── */

async function createBackup() {
    const btn = el('create-btn');
    btn.disabled = true;
    showMsg('backups-msg', '⏳ در حال گرفتن نسخهٔ پشتیبان...', 'muted');
    try {
        const res = await fetchAuth(API, { method: 'POST' });
        if (res.ok) {
            showMsg('backups-msg', 'نسخهٔ پشتیبان گرفته شد', 'success');
            load();
        } else {
            showMsg('backups-msg', await detail(res, 'پشتیبان‌گیری ناموفق بود'), 'danger');
        }
    } catch {
        showMsg('backups-msg', 'خطای ارتباط با سرور', 'danger');
    } finally {
        btn.disabled = false;
    }
}

async function verifyBackup(id) {
    showMsg('backups-msg', '⏳ در حال بررسی سلامت...', 'muted');
    try {
        const res = await fetchAuth(`${API}/${encodeURIComponent(id)}/verify`,
            { method: 'POST' });
        if (res.ok) {
            const data = await res.json();
            showMsg('backups-msg',
                data.ok ? 'این نسخه سالم است' : 'این نسخه سالم نیست و برای بازگردانی مناسب نیست',
                data.ok ? 'success' : 'danger');
            load();
        } else {
            showMsg('backups-msg', await detail(res, 'بررسی ناموفق بود'), 'danger');
        }
    } catch {
        showMsg('backups-msg', 'خطای ارتباط با سرور', 'danger');
    }
}

async function downloadFile(id, name) {
    const url = `${API}/${encodeURIComponent(id)}/download?file=${encodeURIComponent(name)}`;
    try {
        const res = await fetchAuth(url);
        if (!res.ok) {
            showMsg('backups-msg', await detail(res, 'دریافت فایل ناموفق بود'), 'danger');
            return;
        }
        const blob = await res.blob();
        const href = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = href;
        a.download = `${id}_${name}`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 100);
    } catch {
        showMsg('backups-msg', 'خطای ارتباط با سرور', 'danger');
    }
}

/* ── typed-confirmation gates ───────────────────────────────────────── */

function armWhenTyped(inputId, buttonId, phrase) {
    const input = el(inputId);
    const btn = el(buttonId);
    input.value = '';
    btn.disabled = true;
    input.oninput = () => { btn.disabled = input.value.trim() !== phrase; };
}

function openRestore(row) {
    restoreTarget = row.backup_id;
    el('restore-target').textContent =
        `${formatDate(row.created_at)} — ${row.backup_id}`;
    const phrase = `RESTORE BACKUP ${row.backup_id}`;
    el('restore-phrase').textContent = phrase;
    el('restore-msg').textContent = '';
    armWhenTyped('restore-input', 'restore-confirm-btn', phrase);
    new bootstrap.Modal(el('restoreModal')).show();
}

function openDelete(id) {
    deleteTarget = id;
    const phrase = `DELETE BACKUP ${id}`;
    el('delete-phrase').textContent = phrase;
    el('delete-msg').textContent = '';
    armWhenTyped('delete-input', 'delete-confirm-btn', phrase);
    new bootstrap.Modal(el('deleteModal')).show();
}

function closeModal(id) {
    const instance = bootstrap.Modal.getInstance(el(id));
    if (instance) instance.hide();
}

async function doRestore() {
    const id = restoreTarget;
    const btn = el('restore-confirm-btn');
    const msg = el('restore-msg');
    btn.disabled = true;
    msg.className = 'text-center fw-bold mt-2 text-muted';
    msg.textContent = '⏳ در حال بازگردانی...';
    try {
        const res = await fetchAuth(`${API}/${encodeURIComponent(id)}/restore`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirm: el('restore-input').value.trim() }),
        });
        if (res.ok) {
            const data = await res.json();
            closeModal('restoreModal');
            showMsg('backups-msg',
                data.message || 'بازگردانی انجام شد', 'success');
            load();
        } else {
            msg.className = 'text-center fw-bold mt-2 text-danger';
            msg.textContent = await detail(res, 'بازگردانی ناموفق بود');
            btn.disabled = false;
        }
    } catch {
        msg.className = 'text-center fw-bold mt-2 text-danger';
        msg.textContent = 'خطای ارتباط با سرور';
        btn.disabled = false;
    }
}

async function doDelete() {
    const id = deleteTarget;
    const btn = el('delete-confirm-btn');
    const msg = el('delete-msg');
    btn.disabled = true;
    msg.className = 'text-center fw-bold mt-2 text-muted';
    msg.textContent = '⏳ در حال حذف...';
    try {
        const res = await fetchAuth(`${API}/${encodeURIComponent(id)}`,
            { method: 'DELETE' });
        if (res.ok) {
            closeModal('deleteModal');
            showMsg('backups-msg', 'نسخهٔ پشتیبان حذف شد', 'success');
            load();
        } else {
            msg.className = 'text-center fw-bold mt-2 text-danger';
            msg.textContent = await detail(res, 'حذف ناموفق بود');
            btn.disabled = false;
        }
    } catch {
        msg.className = 'text-center fw-bold mt-2 text-danger';
        msg.textContent = 'خطای ارتباط با سرور';
        btn.disabled = false;
    }
}

/* ── bulk delete ────────────────────────────────────────────────────── */

/* One phrase for the whole batch — the operator typed their intent once for
 * N known ids; per-id phrases would make deleting 5 backups impossible UX.
 * The phrase embeds the COUNT, so a stale "DELETE 5 BACKUPS" phrase cannot
 * delete a different number of sets than the operator confirmed. */
function openBulkDelete() {
    const ids = [...selected];
    if (!ids.length) return;
    el('bulk-count').textContent = String(ids.length);
    el('bulk-phrase').textContent = `DELETE ${ids.length} BACKUPS`;
    el('bulk-msg').textContent = '';
    armWhenTyped('bulk-input', 'bulk-confirm-btn', `DELETE ${ids.length} BACKUPS`);
    new bootstrap.Modal(el('bulkDeleteModal')).show();
}

async function doBulkDelete() {
    const ids = [...selected];
    const btn = el('bulk-confirm-btn');
    const msg = el('bulk-msg');
    btn.disabled = true;
    msg.className = 'text-center fw-bold mt-2 text-muted';
    msg.textContent = '⏳ در حال حذف...';
    try {
        const res = await fetchAuth(`${API}/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ids,
                confirm: `DELETE ${ids.length} BACKUPS`,
            }),
        });
        if (res.ok) {
            const data = await res.json();
            closeModal('bulkDeleteModal');
            showMsg('backups-msg',
                data.deleted?.length
                    ? `${data.deleted.length} نسخهٔ پشتیبان حذف شد`
                    : 'هیچ نسخه‌ای حذف نشد',
                'success');
            load();
        } else {
            msg.className = 'text-center fw-bold mt-2 text-danger';
            msg.textContent = await detail(res, 'حذف ناموفق بود');
            btn.disabled = false;
        }
    } catch {
        msg.className = 'text-center fw-bold mt-2 text-danger';
        msg.textContent = 'خطای ارتباط با سرور';
        btn.disabled = false;
    }
}

export { load as reloadBackups };

export function initBackups(options = {}) {
    if (options.onOffsiteState) onOffsiteState = options.onOffsiteState;
    loadProfile();
    el('create-btn').addEventListener('click', createBackup);
    el('restore-confirm-btn').addEventListener('click', doRestore);
    el('delete-confirm-btn').addEventListener('click', doDelete);
    el('bulk-delete-btn').addEventListener('click', openBulkDelete);
    el('bulk-confirm-btn').addEventListener('click', doBulkDelete);
    el('select-all').addEventListener('change', (e) => {
        document.querySelectorAll('#backups-body input[type="checkbox"]').forEach((box) => {
            box.checked = e.target.checked;
            const id = box.dataset.backupId;
            if (box.checked) selected.add(id);
            else selected.delete(id);
        });
        syncBulkButton();
    });
    load();
}
