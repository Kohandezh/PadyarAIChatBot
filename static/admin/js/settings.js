import { fetchAuth, showMsg } from './utils.js';

export async function loadProfile() {
    try {
        const res = await fetchAuth('/admin/api/profile');
        if (!res.ok) return;
        const data = await res.json();
        // current-question only exists on the account page; username topbar on all.
        const q = document.getElementById('current-question');
        if (q) q.textContent = data.security_question || 'تعریف نشده';
        const u = document.getElementById('display-username');
        if (u) u.textContent = data.username;
    } catch {
        // ignore — page still usable
    }
}

// ---- Database backups ----

function fmtSize(bytes) {
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + ' MB';
    if (bytes >= 1024) return (bytes / 1024).toFixed(0) + ' KB';
    return bytes + ' B';
}

function fmtDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString('fa-IR');
}

function renderBackups(backups) {
    const tbody = document.getElementById('backup-list');
    if (!backups || backups.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted">هنوز پشتیبانی گرفته نشده است</td></tr>';
        return;
    }
    tbody.innerHTML = backups.map(b => `
        <tr>
            <td>${fmtDate(b.created)}</td>
            <td>${fmtSize(b.size)}</td>
            <td class="text-end">
                <button class="btn btn-sm btn-outline-success me-1" data-restore="${b.name}">
                    <i class="fas fa-rotate-left"></i> بازگردانی
                </button>
                <button class="btn btn-sm btn-outline-primary me-1" data-download="${b.name}">
                    <i class="fas fa-download"></i> دانلود
                </button>
                <button class="btn btn-sm btn-outline-danger" data-delete="${b.name}">
                    <i class="fas fa-trash"></i>
                </button>
            </td>
        </tr>`).join('');
}

function applySchedule(s) {
    if (!s) return;
    document.getElementById('backup-enabled').checked = s.enabled;
    document.getElementById('backup-enabled-label').textContent = s.enabled ? 'روشن' : 'خاموش';
    document.getElementById('backup-interval').value = String(s.interval_hours);
    document.getElementById('backup-time').value = s.time || '03:00';
    document.getElementById('backup-last').textContent = fmtDate(s.last_run);
    document.getElementById('backup-next').textContent = s.enabled ? fmtDate(s.next_run) : 'غیرفعال';
    toggleTimeVisibility();
}

function toggleTimeVisibility() {
    const interval = parseInt(document.getElementById('backup-interval').value, 10);
    document.getElementById('backup-time-wrap').style.display = interval >= 24 ? '' : 'none';
}

async function loadBackups() {
    try {
        const res = await fetchAuth('/admin/api/backups');
        if (!res.ok) return;
        const data = await res.json();
        applySchedule(data.schedule);
        renderBackups(data.backups);
    } catch { /* page still usable */ }
}

async function downloadBackup(name) {
    try {
        const res = await fetchAuth(`/admin/api/backups/download/${encodeURIComponent(name)}`);
        if (!res.ok) { showMsg('schedule-msg', 'خطا در دانلود', 'danger'); return; }
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url; a.download = name;
        document.body.appendChild(a); a.click(); a.remove();
        URL.revokeObjectURL(url);
    } catch { showMsg('schedule-msg', 'خطای ارتباط با سرور', 'danger'); }
}

// A restore replaces the whole DB (including admin accounts and sessions), so
// the current login may no longer be valid afterwards — send the user to login.
async function doRestore(fetchPromise) {
    showMsg('restore-msg', 'در حال بازگردانی... صفحه را نبندید', 'primary');
    try {
        const res = await fetchPromise;
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch { /* no body */ }
        if (res.ok) {
            alert('اطلاعات با موفقیت بازگردانی شد. لطفاً دوباره وارد شوید.');
            window.location.href = '/secure-panel-inotex/login';
        } else {
            showMsg('restore-msg', detail || 'خطا در بازگردانی', 'danger');
        }
    } catch {
        showMsg('restore-msg', 'خطای ارتباط با سرور', 'danger');
    }
}

export function initBackup() {
    loadProfile();
    loadBackups();

    // Restore from an uploaded backup file.
    document.getElementById('restore-upload-btn').addEventListener('click', () => {
        document.getElementById('restore-file-input').click();
    });
    document.getElementById('restore-file-input').addEventListener('change', (e) => {
        const fileEl = e.target;
        const file = fileEl.files[0];
        if (!file) return;
        if (!confirm(`بازگردانی از «${file.name}»؟\n\nتمام اطلاعات فعلی با این نسخه جایگزین می‌شود. (یک پشتیبان ایمنی از وضعیت فعلی به‌صورت خودکار گرفته می‌شود.)`)) {
            fileEl.value = '';
            return;
        }
        const fd = new FormData();
        fd.append('file', file);
        doRestore(fetchAuth('/admin/api/backups/restore-upload', { method: 'POST', body: fd }));
        fileEl.value = '';
    });

    document.getElementById('backup-interval').addEventListener('change', toggleTimeVisibility);
    document.getElementById('backup-enabled').addEventListener('change', (e) => {
        document.getElementById('backup-enabled-label').textContent = e.target.checked ? 'روشن' : 'خاموش';
    });

    document.getElementById('save-schedule-btn').addEventListener('click', async () => {
        const body = {
            enabled: document.getElementById('backup-enabled').checked,
            interval_hours: parseInt(document.getElementById('backup-interval').value, 10),
            time: document.getElementById('backup-time').value || '03:00',
        };
        try {
            const res = await fetchAuth('/admin/api/backup-schedule', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            const data = await res.json();
            if (res.ok) { applySchedule(data); showMsg('schedule-msg', 'زمان‌بندی ذخیره شد', 'success'); }
            else showMsg('schedule-msg', data.detail || 'خطا در ذخیره', 'danger');
        } catch { showMsg('schedule-msg', 'خطای ارتباط با سرور', 'danger'); }
    });

    document.getElementById('create-backup-btn').addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        btn.disabled = true;
        try {
            const res = await fetchAuth('/admin/api/backups/create', { method: 'POST' });
            const data = await res.json();
            if (res.ok) { renderBackups(data.backups); showMsg('schedule-msg', 'پشتیبان جدید ساخته شد', 'success'); loadBackups(); }
            else showMsg('schedule-msg', data.detail || 'خطا در ساخت پشتیبان', 'danger');
        } catch { showMsg('schedule-msg', 'خطای ارتباط با سرور', 'danger'); }
        finally { btn.disabled = false; }
    });

    document.getElementById('backup-list').addEventListener('click', async (e) => {
        const dl = e.target.closest('[data-download]');
        if (dl) { downloadBackup(dl.dataset.download); return; }
        const restore = e.target.closest('[data-restore]');
        if (restore) {
            const name = restore.dataset.restore;
            if (!confirm(`بازگردانی به نسخه «${name}»؟\n\nتمام اطلاعات فعلی با این نسخه جایگزین می‌شود. (یک پشتیبان ایمنی از وضعیت فعلی به‌صورت خودکار گرفته می‌شود.)`)) return;
            doRestore(fetchAuth(`/admin/api/backups/restore/${encodeURIComponent(name)}`, { method: 'POST' }));
            return;
        }
        const del = e.target.closest('[data-delete]');
        if (del) {
            if (!confirm('این نسخه پشتیبان حذف شود؟')) return;
            try {
                const res = await fetchAuth(`/admin/api/backups/${encodeURIComponent(del.dataset.delete)}`, { method: 'DELETE' });
                const data = await res.json();
                if (res.ok) { renderBackups(data.backups); showMsg('schedule-msg', 'پشتیبان حذف شد', 'success'); }
                else showMsg('schedule-msg', data.detail || 'خطا در حذف', 'danger');
            } catch { showMsg('schedule-msg', 'خطای ارتباط با سرور', 'danger'); }
        }
    });
}

// ---- Assistant content ----

let selectedTone = 'professional';
let medicalPresets = [];
let originalMedical = '';      // last-saved medical text, to detect changes
let medicalModal = null;
let pendingBody = null;        // form data awaiting password confirmation

function renderTone(presets, current) {
    selectedTone = current;
    const box = document.getElementById('tone-options');
    box.innerHTML = (presets || []).map(p =>
        `<button type="button" class="btn tone-btn ${p.key === current ? 'active' : ''}" data-tone="${p.key}">${p.label}</button>`
    ).join('');
}

function highlightTone() {
    document.querySelectorAll('#tone-options [data-tone]').forEach(b => {
        b.classList.toggle('active', b.dataset.tone === selectedTone);
    });
}

function renderMedicalPresets(presets) {
    medicalPresets = presets || [];
    const box = document.getElementById('medical-presets');
    box.innerHTML = medicalPresets.map((p, i) =>
        `<button type="button" class="btn btn-sm btn-outline-secondary" data-medical="${i}">+ ${p.label}</button>`
    ).join('');
}

async function loadAssistant() {
    try {
        const res = await fetchAuth('/admin/api/assistant');
        if (!res.ok) return;
        const d = await res.json();
        document.getElementById('assistant-name').value = d.name || '';
        document.getElementById('assistant-org').value = d.org || '';
        document.getElementById('assistant-phone').value = d.phone || '';
        document.getElementById('assistant-website').value = d.website || '';
        document.getElementById('assistant-personality').value = d.personality || '';
        document.getElementById('assistant-medical').value = d.medical_safety || '';
        document.getElementById('assistant-knowledge').value = d.knowledge || '';
        originalMedical = (d.medical_safety || '').trim();
        renderTone(d.tone_presets, d.tone || 'professional');
        renderMedicalPresets(d.medical_presets);
    } catch { /* page still usable */ }
}

function collectAssistant() {
    return {
        name: document.getElementById('assistant-name').value.trim(),
        org: document.getElementById('assistant-org').value.trim(),
        phone: document.getElementById('assistant-phone').value.trim(),
        website: document.getElementById('assistant-website').value.trim(),
        personality: document.getElementById('assistant-personality').value.trim(),
        medical_safety: document.getElementById('assistant-medical').value.trim(),
        tone: selectedTone,
        knowledge: document.getElementById('assistant-knowledge').value.trim(),
    };
}

async function postAssistant(body) {
    try {
        const res = await fetchAuth('/admin/api/assistant', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        let detail = '';
        try { detail = (await res.json()).detail || ''; } catch { /* no body */ }
        return { ok: res.ok, status: res.status, detail };
    } catch {
        return { ok: false, status: 0, detail: 'خطای ارتباط با سرور' };
    }
}

async function loadAIConnection() {
    try {
        const res = await fetchAuth('/admin/api/ai-connection');
        if (!res.ok) return;
        const d = await res.json();
        document.getElementById('ai-conn-base').value = d.api_base || '';
        document.getElementById('ai-conn-base').placeholder = d.api_base_default || 'https://...';
        document.getElementById('ai-conn-key-state').textContent =
            d.has_key ? 'کلید ذخیره شده است.' : 'هنوز کلیدی ذخیره نشده.';
        // The chat / classification model inputs were removed from this page:
        // routing owns those now, and writing them here changed nothing at
        // runtime while telling the operator it had saved.
        document.getElementById('ai-conn-model-stt').value = d.model_stt || '';
        const sttNote = document.getElementById('ai-stt-status');
        if (sttNote && d.stt) {
            sttNote.textContent = d.stt.configured
                ? `${d.stt.detail_fa} ${d.stt.provider_display_name || ''}`.trim()
                : (d.stt.detail_fa || 'رونویسی تنظیم نشده است.');
        }
        document.getElementById('ai-conn-stt').checked = !!d.feature_stt;
        document.getElementById('ai-conn-tts').checked = !!d.feature_tts;
        const langSel = document.getElementById('ai-conn-lang');
        if (langSel) langSel.value = d.default_lang || 'fa';
        const search = document.getElementById('ai-conn-search');
        if (search) {
            search.value = d.search_backend || 'tfidf';
            if (!d.embedding_available) {
                search.querySelector('option[value="embedding"]').disabled = true;
                document.getElementById('ai-conn-search-note').textContent =
                    'برای موتور معنایی، بسته‌ی model2vec باید روی سرور نصب باشد (pip install model2vec).';
            }
        }
    } catch (e) { /* form keeps placeholders */ }
}

function initAIConnection() {
    const form = document.getElementById('ai-conn-form');
    if (!form) return;
    loadAIConnection();
    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const res = await fetchAuth('/admin/api/ai-connection', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                api_base: document.getElementById('ai-conn-base').value.trim(),
                api_key: document.getElementById('ai-conn-key').value.trim(),
                model_stt: document.getElementById('ai-conn-model-stt').value.trim(),
                feature_stt: document.getElementById('ai-conn-stt').checked,
                feature_tts: document.getElementById('ai-conn-tts').checked,
                search_backend: (document.getElementById('ai-conn-search') || {}).value || 'tfidf',
                default_lang: (document.getElementById('ai-conn-lang') || {}).value || 'fa',
            })
        });
        if (res.ok) {
            document.getElementById('ai-conn-key').value = '';
            showMsg('ai-conn-msg', 'اتصال ذخیره شد', 'success');
            loadAIConnection();
        } else {
            showMsg('ai-conn-msg', 'خطا در ذخیره اتصال', 'danger');
        }
    });
}

export function initAi() {
    loadProfile();
    loadAssistant();
    initAIConnection();
    medicalModal = new bootstrap.Modal(document.getElementById('medicalConfirmModal'));

    document.getElementById('tone-options').addEventListener('click', (e) => {
        const b = e.target.closest('[data-tone]');
        if (!b) return;
        selectedTone = b.dataset.tone;
        highlightTone();
    });

    document.getElementById('medical-presets').addEventListener('click', (e) => {
        const b = e.target.closest('[data-medical]');
        if (!b) return;
        const preset = medicalPresets[parseInt(b.dataset.medical, 10)];
        if (!preset) return;
        const ta = document.getElementById('assistant-medical');
        const cur = ta.value.replace(/\s+$/, '');
        if (cur.includes(preset.text)) return;  // don't append twice
        ta.value = (cur ? cur + '\n' : '') + preset.text;
        ta.focus();
    });

    document.getElementById('assistant-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = collectAssistant();
        if (!body.name || !body.org) {
            showMsg('assistant-msg', 'نام دستیار و نام سازمان الزامی است', 'danger');
            return;
        }
        // Changing the medical-safety rules requires admin password confirmation.
        if (body.medical_safety !== originalMedical) {
            pendingBody = body;
            document.getElementById('medical-confirm-password').value = '';
            document.getElementById('medical-confirm-msg').textContent = '';
            medicalModal.show();
            return;
        }
        const r = await postAssistant(body);
        if (r.ok) {
            originalMedical = body.medical_safety;
            showMsg('assistant-msg', 'محتوای دستیار ذخیره شد', 'success');
        } else {
            showMsg('assistant-msg', r.detail || 'خطا در ذخیره', 'danger');
        }
    });

    document.getElementById('medical-confirm-btn').addEventListener('click', async () => {
        const pw = document.getElementById('medical-confirm-password').value;
        if (!pw) {
            document.getElementById('medical-confirm-msg').textContent = 'رمز عبور را وارد کنید';
            return;
        }
        const r = await postAssistant({ ...pendingBody, password: pw });
        if (r.ok) {
            medicalModal.hide();
            originalMedical = pendingBody.medical_safety;
            pendingBody = null;
            showMsg('assistant-msg', 'محتوای دستیار ذخیره شد', 'success');
        } else {
            document.getElementById('medical-confirm-msg').textContent = r.detail || 'رمز عبور نادرست است';
        }
    });
}

export function initAccount() {
    loadProfile();

    // Change Password form
    document.getElementById('change-password-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const current_password = document.getElementById('current-password').value;
        const new_password = document.getElementById('new-password').value;
        const confirm_password = document.getElementById('confirm-password').value;

        if (new_password !== confirm_password) {
            showMsg('password-msg', 'رمز عبور جدید و تکرار آن مطابقت ندارند', 'danger');
            return;
        }

        try {
            const res = await fetchAuth('/admin/api/change-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current_password, new_password, confirm_password })
            });
            const data = await res.json();
            if (res.ok) {
                showMsg('password-msg', 'رمز عبور با موفقیت تغییر کرد', 'success');
                document.getElementById('change-password-form').reset();
            } else {
                showMsg('password-msg', data.detail || 'خطا در تغییر رمز عبور', 'danger');
            }
        } catch {
            showMsg('password-msg', 'خطای ارتباط با سرور', 'danger');
        }
    });

    // Change Security Question form
    document.getElementById('change-security-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const current_answer = document.getElementById('current-answer').value.trim();
        const new_question = document.getElementById('new-question').value.trim();
        const new_answer = document.getElementById('new-answer').value.trim();

        if (!current_answer || !new_question || !new_answer) {
            showMsg('security-msg', 'تمام فیلدها الزامی است', 'danger');
            return;
        }

        try {
            const res = await fetchAuth('/admin/api/change-security-question', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ current_answer, new_question, new_answer })
            });
            const data = await res.json();
            if (res.ok) {
                showMsg('security-msg', 'سوال امنیتی با موفقیت تغییر کرد', 'success');
                document.getElementById('change-security-form').reset();
                loadProfile();
            } else {
                showMsg('security-msg', data.detail || 'خطا در تغییر سوال امنیتی', 'danger');
            }
        } catch {
            showMsg('security-msg', 'خطای ارتباط با سرور', 'danger');
        }
    });
}
