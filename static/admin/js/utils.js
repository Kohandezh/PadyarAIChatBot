import { API_BASE } from './state.js';

export function escapeHtml(text) {
    if (!text) return text;
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ── CSRF ────────────────────────────────────────────────────────────────
// Every admin mutation goes through fetchAuth(), so attaching the token here
// covers all of them — and any endpoint added later gets it for free rather
// than being forgotten.
let _csrfToken = null;

async function csrfToken() {
    if (_csrfToken) return _csrfToken;
    try {
        const res = await fetch('/admin/csrf', { credentials: 'same-origin' });
        if (res.ok) _csrfToken = (await res.json()).csrf_token;
    } catch { /* leave null; the server will reject and the UI reports it */ }
    return _csrfToken;
}

export function resetCsrfToken() { _csrfToken = null; }

export async function fetchAuth(url, options = {}) {
    const method = (options.method || 'GET').toUpperCase();
    const opts = { ...options, credentials: 'include' };
    // Attach the CSRF token to every state-changing call. Reads need none.
    if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        const token = await csrfToken();
        opts.headers = { ...(options.headers || {}) };
        if (token) opts.headers['X-CSRF-Token'] = token;
    }
    let res = await fetch(url, opts);
    // A 403 can mean a stale token after re-login: refetch once and retry.
    if (res.status === 403 && ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
        resetCsrfToken();
        const token = await csrfToken();
        if (token) {
            opts.headers = { ...(opts.headers || {}), 'X-CSRF-Token': token };
            res = await fetch(url, opts);
        }
    }
    if (res.status === 401) {
        window.location.href = '/secure-panel-inotex/login';
    }
    return res;
}

export function showMsg(elementId, text, type) {
    const el = document.getElementById(elementId);
    el.className = `text-center fw-bold mt-2 text-${type}`;
    el.innerText = text;
    setTimeout(() => el.innerText = '', 3000);
}

// --- Import / Export (shared by dataset & questions pages) ---

// Download an authenticated export as a file. resource = 'dataset' | 'questions'.
export async function exportResource(resource, format) {
    const res = await fetchAuth(`${API_BASE}/${resource}/export?format=${format}`);
    if (!res.ok) {
        alert('خطا در دریافت خروجی');
        return;
    }
    const blob = await res.blob();
    let fname = `${resource}.${format}`;
    const cd = res.headers.get('Content-Disposition');
    const m = cd && cd.match(/filename="?([^"]+)"?/);
    if (m) fname = m[1];
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fname;
    document.body.appendChild(a);
    a.click();
    a.remove();
    // Defer revocation: revoking synchronously after click() can abort the
    // download in Safari and some Chrome/Firefox builds before the blob resolves.
    setTimeout(() => URL.revokeObjectURL(url), 100);
}

// Reset and show the shared import modal (markup lives in each page template).
export function openImportModal() {
    document.getElementById('import-file').value = '';
    document.getElementById('import-msg').innerText = '';
    const checked = document.querySelector('input[name="import-mode"]:checked');
    if (checked) checked.checked = false;
    new bootstrap.Modal(document.getElementById('importModal')).show();
}

// Submit the import form. Calls onSuccess() (e.g. reload the table) on success.
export async function submitImportResource(resource, onSuccess) {
    const fileInput = document.getElementById('import-file');
    const msg = document.getElementById('import-msg');
    const mode = document.querySelector('input[name="import-mode"]:checked');

    if (!fileInput.files.length) {
        msg.className = 'text-danger small';
        msg.innerText = 'ابتدا یک فایل انتخاب کنید.';
        return;
    }
    if (!mode) {
        msg.className = 'text-danger small';
        msg.innerText = 'لطفاً یکی از حالت‌ها را انتخاب کنید.';
        return;
    }
    if (mode.value === 'replace' &&
        !confirm('همه داده‌های فعلی پاک و فقط با محتوای این فایل جایگزین می‌شوند. مطمئن هستید؟')) {
        return;
    }

    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    fd.append('mode', mode.value);

    msg.className = 'text-muted small';
    msg.innerText = '⏳ در حال وارد کردن...';

    try {
        const res = await fetchAuth(`${API_BASE}/${resource}/import`, { method: 'POST', body: fd });
        const data = await res.json();
        if (res.ok) {
            let txt = `✅ انجام شد — ${data.imported} مورد جدید`;
            if (data.updated) txt += `، ${data.updated} به‌روزرسانی`;
            if (data.skipped) txt += `، ${data.skipped} رد شده`;
            if (data.unknown_dataset_ids && data.unknown_dataset_ids.length) {
                txt += ` — ⚠️ ${data.unknown_dataset_ids.length} سوال به شناسه دیتاستی اشاره دارند که وجود ندارد.`;
            }
            msg.className = 'text-success small';
            msg.innerText = txt;
            if (onSuccess) onSuccess();
        } else {
            msg.className = 'text-danger small';
            msg.innerText = '❌ ' + (data.detail || 'خطا در وارد کردن');
        }
    } catch {
        msg.className = 'text-danger small';
        msg.innerText = '❌ خطای ارتباط با سرور';
    }
}
