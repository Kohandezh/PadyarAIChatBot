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
    el('sched-last').textContent = formatDate(s.last_run);
    el('sched-next').textContent = formatDate(s.next_run);
}

function renderRows(rows) {
    const body = el('backups-body');
    body.replaceChildren();

    if (!rows.length) {
        const tr = document.createElement('tr');
        const td = cell('هنوز هیچ نسخهٔ پشتیبانی گرفته نشده است.',
            'text-center text-muted');
        td.colSpan = 6;
        tr.appendChild(td);
        body.appendChild(tr);
        return;
    }

    rows.forEach((row) => {
        const tr = document.createElement('tr');

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

        const actions = document.createElement('td');
        actions.className = 'text-end';
        const group = document.createElement('div');
        group.className = 'd-flex gap-2 justify-content-end flex-wrap';
        group.appendChild(button('بررسی سلامت', 'btn btn-sm btn-outline-primary',
            () => verifyBackup(row.backup_id)));
        group.appendChild(button('بازگردانی', 'btn btn-sm btn-outline-danger',
            () => openRestore(row.backup_id)));
        group.appendChild(button('حذف', 'btn btn-sm btn-outline-secondary',
            () => openDelete(row.backup_id)));
        actions.appendChild(group);
        tr.appendChild(actions);

        body.appendChild(tr);
    });
}

async function load() {
    try {
        const res = await fetchAuth(API);
        if (!res.ok) {
            showMsg('backups-msg', 'خواندن فهرست نسخه‌های پشتیبان ناموفق بود', 'danger');
            return;
        }
        const data = await res.json();
        renderSchedule(data.schedule);
        renderRows(data.backups || []);
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

function openRestore(id) {
    restoreTarget = id;
    const phrase = `RESTORE BACKUP ${id}`;
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

export function initBackups() {
    loadProfile();
    el('create-btn').addEventListener('click', createBackup);
    el('restore-confirm-btn').addEventListener('click', doRestore);
    el('delete-confirm-btn').addEventListener('click', doDelete);
    load();
}
