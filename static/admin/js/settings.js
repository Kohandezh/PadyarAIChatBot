import { fetchAuth, showMsg } from './utils.js';

// --- Idle-time avatar videos (Settings → دستیار هوشمند) ------------------
// One main clip plus up to 3 extras that core.js rotates through at random
// while nobody is chatting. `extra` is always kept compact (no gaps) — the 3
// boxes are "the next N clips", not fixed slots, so removing one shifts the
// rest left; the boxes past the first empty one are locked until it fills.

const IDLE_EXTRA_MAX = 3;
let idleVideoState = { main: '', extra: [] };

function _idleSlot(key) {
    return document.querySelector(`#idle-video-slots [data-slot="${key}"]`);
}

function _renderIdleVideoSlots() {
    const keys = ['main', 'extra-0', 'extra-1', 'extra-2'];
    keys.forEach((key, i) => {
        const slot = _idleSlot(key);
        if (!slot) return;
        const url = key === 'main' ? idleVideoState.main : (idleVideoState.extra[i - 1] || '');
        const video = slot.querySelector('.idle-video-preview');
        const removeBtn = slot.querySelector('.idle-video-remove-btn');
        if (url) {
            video.src = url;
            slot.classList.add('has-video');
            removeBtn.style.display = '';
        } else {
            video.src = '';
            slot.classList.remove('has-video');
            removeBtn.style.display = 'none';
        }
        // Extra slots fill in order: box N is locked until box N-1 has a clip.
        if (key !== 'main') {
            const extraIndex = i - 1;
            const locked = extraIndex > idleVideoState.extra.length;
            slot.classList.toggle('locked', locked && !url);
        }
    });
}

async function _loadIdleVideos() {
    try {
        const res = await fetchAuth('/admin/api/idle-videos');
        if (!res.ok) return; // video module disabled — card stays empty
        const data = await res.json();
        idleVideoState = { main: data.main || '', extra: (data.extra || []).slice(0, IDLE_EXTRA_MAX) };
        _renderIdleVideoSlots();
    } catch {
        // ignore — card stays empty and still usable
    }
}

async function _saveIdleVideos() {
    try {
        const res = await fetchAuth('/admin/api/idle-videos', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ main: idleVideoState.main || '', extra: idleVideoState.extra }),
        });
        const data = await res.json();
        if (!res.ok) {
            showMsg('idle-video-msg', data.detail || 'خطا در ذخیره', 'danger');
        }
    } catch {
        showMsg('idle-video-msg', 'خطای ارتباط با سرور', 'danger');
    }
}

export function initIdleVideos() {
    const container = document.getElementById('idle-video-slots');
    if (!container) return;

    ['main', 'extra-0', 'extra-1', 'extra-2'].forEach((key, i) => {
        const slot = _idleSlot(key);
        if (!slot) return;
        const input = slot.querySelector('.idle-video-input');
        const removeBtn = slot.querySelector('.idle-video-remove-btn');
        const extraIndex = i - 1; // -1 for the main slot, unused there

        input.addEventListener('change', async () => {
            const file = input.files[0];
            input.value = '';
            if (!file) return;
            showMsg('idle-video-msg', 'در حال آپلود...', 'muted');
            const formData = new FormData();
            formData.append('file', file);
            try {
                const res = await fetchAuth('/admin/api/upload_video', { method: 'POST', body: formData });
                const data = await res.json();
                if (!res.ok) {
                    showMsg('idle-video-msg', data.detail || 'خطا در آپلود', 'danger');
                    return;
                }
                if (key === 'main') {
                    idleVideoState.main = data.video_url;
                } else if (extraIndex < idleVideoState.extra.length) {
                    idleVideoState.extra[extraIndex] = data.video_url; // replacing an existing clip
                } else {
                    idleVideoState.extra.push(data.video_url); // filling the next open slot
                }
                _renderIdleVideoSlots();
                await _saveIdleVideos();
                showMsg('idle-video-msg', 'ذخیره شد', 'success');
            } catch {
                showMsg('idle-video-msg', 'خطای ارتباط با سرور', 'danger');
            }
        });

        removeBtn.addEventListener('click', async () => {
            if (key === 'main') {
                idleVideoState.main = '';
            } else {
                idleVideoState.extra.splice(extraIndex, 1); // compacts — the rest shift left
            }
            _renderIdleVideoSlots();
            await _saveIdleVideos();
            showMsg('idle-video-msg', 'حذف شد', 'success');
        });
    });

    _loadIdleVideos();
}

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
    // On PostgreSQL this page shows only the schedule; the list/upload
    // controls live on the infrastructure page. loadBackups() is what feeds
    // them, so it is the one thing to skip — nothing below may assume the
    // buttons exist.
    const hasList = !!document.getElementById('backup-list');
    if (hasList) loadBackups();

    // Restore from an uploaded backup file.
    const uploadBtn = document.getElementById('restore-upload-btn');
    if (uploadBtn) uploadBtn.addEventListener('click', () => {
        document.getElementById('restore-file-input').click();
    });
    const fileInput = document.getElementById('restore-file-input');
    if (fileInput) fileInput.addEventListener('change', (e) => {
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

    // The three controls below exist only on the SQLite page (see
    // settings_backup.html); on PostgreSQL the list lives on the
    // infrastructure page.
    const createBtn = document.getElementById('create-backup-btn');
    if (createBtn) createBtn.addEventListener('click', async (e) => {
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

    const listEl = document.getElementById('backup-list');
    if (listEl) listEl.addEventListener('click', async (e) => {
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
let originalRefusal = '';
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

function setIfPresent(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = (value === null || value === undefined) ? '' : value;
}

function valueOf(id) {
    const el = document.getElementById(id);
    return el ? el.value.trim() : null;
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
        // The keys that make a new deployment in a different category a data
        // job instead of a Python edit. Guarded, so an older cached page that
        // lacks the inputs still loads the rest of the form.
        setIfPresent('assistant-domain', d.domain);
        setIfPresent('assistant-domain-en', d.domain_en);
        setIfPresent('refusal-fa', d.refusal_fa);
        setIfPresent('refusal-en', d.refusal_en);
        setIfPresent('collection-noun-fa', d.collection_noun_fa);
        setIfPresent('collection-noun-en', d.collection_noun_en);
        setIfPresent('options-shown', d.options_shown);
        setIfPresent('chat-log-retention', d.chat_log_retention_days);
        originalMedical = (d.medical_safety || '').trim();
        originalRefusal = [(d.refusal_fa || '').trim(), (d.refusal_en || '').trim()].join('\u0000');
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
        domain: valueOf('assistant-domain'),
        domain_en: valueOf('assistant-domain-en'),
        refusal_fa: valueOf('refusal-fa'),
        refusal_en: valueOf('refusal-en'),
        collection_noun_fa: valueOf('collection-noun-fa'),
        collection_noun_en: valueOf('collection-noun-en'),
        options_shown: numberOf('options-shown'),
        chat_log_retention_days: numberOf('chat-log-retention'),
    };
}

function numberOf(id) {
    const raw = valueOf(id);
    if (raw === null || raw === '') return null;
    const n = parseInt(raw, 10);
    return Number.isNaN(n) ? null : n;
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
        // Key presence alone used to read as "AI works" while zero eligible
        // route targets meant every ambiguous query failed. `routes` carries
        // the same eligible counts the health probe reports: when a key
        // exists but either count is zero, say so in plain Persian. The fix
        // is pressing save on this very form (the server then rebuilds the
        // missing routing), and the submit handler reloads this state right
        // after a save — so the warning clears itself the moment it is fixed.
        const routes = d.routes || {};
        const unrouted = d.has_key && (!(routes.chat > 0) || !(routes.classify > 0));
        document.getElementById('ai-conn-key-state').textContent = unrouted
            ? 'کلید ذخیره شده اما مسیر پاسخ‌گویی هوش مصنوعی فعال نیست — در تنظیمات هوش مصنوعی دوباره ذخیره کنید.'
            : (d.has_key ? 'کلید ذخیره شده است.' : 'هنوز کلیدی ذخیره نشده.');
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
        // Changing the red lines OR the out-of-scope refusal sentence requires
        // the admin password: both are safety wording, and the server enforces
        // the same rule.
        const refusalNow = [(body.refusal_fa || ''), (body.refusal_en || '')].join('\u0000');
        if (body.medical_safety !== originalMedical || refusalNow !== originalRefusal) {
            pendingBody = body;
            document.getElementById('medical-confirm-password').value = '';
            document.getElementById('medical-confirm-msg').textContent = '';
            medicalModal.show();
            return;
        }
        const r = await postAssistant(body);
        if (r.ok) {
            originalMedical = body.medical_safety;
            originalRefusal = refusalNow;
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
            originalRefusal = [(pendingBody.refusal_fa || ''),
                               (pendingBody.refusal_en || '')].join('\u0000');
            pendingBody = null;
            showMsg('assistant-msg', 'محتوای دستیار ذخیره شد', 'success');
        } else {
            document.getElementById('medical-confirm-msg').textContent = r.detail || 'رمز عبور نادرست است';
        }
    });
}

// ---- White-label branding ----

function applyBranding(d) {
    if (!d) return;
    document.getElementById('brand-app-name').value = d.whitelabel_app_name || '';
    document.getElementById('brand-primary').value = d.whitelabel_primary_color || '#2D5CA7';
    document.getElementById('brand-accent').value = d.whitelabel_accent_color || '#FCB715';
    document.getElementById('brand-welcome').value = d.whitelabel_welcome_text || '';
    document.getElementById('brand-logo').value = d.whitelabel_logo_url || '';
    updateLogoPreview();
}

// The preview mirrors the input live — the operator sees the logo they are
// about to save, not the one already stored. A broken URL just hides it.
function updateLogoPreview() {
    const url = document.getElementById('brand-logo').value.trim();
    const img = document.getElementById('brand-logo-preview');
    if (!url) { img.hidden = true; img.removeAttribute('src'); return; }
    img.hidden = false;
    img.src = url;
}

async function loadBranding() {
    try {
        const res = await fetchAuth('/admin/api/branding');
        if (!res.ok) return;
        applyBranding(await res.json());
    } catch { /* form keeps its server-rendered values */ }
}

export function initBranding() {
    loadProfile();
    const form = document.getElementById('branding-form');
    if (!form) return;
    loadBranding();
    document.getElementById('brand-logo').addEventListener('input', updateLogoPreview);

    // Logo upload: pick a file → validated server-side (magic bytes) → the
    // URL lands in the field → the operator still presses «ذخیره برندینگ».
    const uploadBtn = document.getElementById('logo-upload-btn');
    const fileInput = document.getElementById('logo-file-input');
    if (uploadBtn && fileInput) {
        uploadBtn.addEventListener('click', () => fileInput.click());
        fileInput.addEventListener('change', async () => {
            const file = fileInput.files[0];
            fileInput.value = '';
            if (!file) return;
            if (!['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(file.type)) {
                showMsg('branding-msg', 'فقط تصویر (PNG، JPG، GIF، WebP) قابل بارگذاری است', 'danger');
                return;
            }
            if (file.size > 2 * 1024 * 1024) {
                showMsg('branding-msg', 'حجم لوگو حداکثر می‌تواند ۲ مگابایت باشد', 'danger');
                return;
            }
            uploadBtn.disabled = true;
            showMsg('branding-msg', '⏳ در حال بارگذاری...', 'muted');
            try {
                const fd = new FormData();
                fd.append('file', file);
                const res = await fetchAuth('/admin/api/upload_logo', { method: 'POST', body: fd });
                const data = await res.json().catch(() => ({}));
                if (res.ok) {
                    document.getElementById('brand-logo').value = data.url || '';
                    updateLogoPreview();
                    showMsg('branding-msg', 'تصویر بارگذاری شد — برای اعمال، «ذخیره برندینگ» را بزنید', 'success');
                } else {
                    showMsg('branding-msg', data.detail || 'بارگذاری ناموفق بود', 'danger');
                }
            } catch {
                showMsg('branding-msg', 'خطای ارتباط با سرور', 'danger');
            } finally {
                uploadBtn.disabled = false;
            }
        });
    }

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('save-branding-btn');
        btn.disabled = true;
        try {
            const res = await fetchAuth('/admin/api/branding', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    app_name: document.getElementById('brand-app-name').value.trim(),
                    logo_url: document.getElementById('brand-logo').value.trim(),
                    primary_color: document.getElementById('brand-primary').value,
                    accent_color: document.getElementById('brand-accent').value,
                    welcome_text: document.getElementById('brand-welcome').value.trim(),
                }),
            });
            let detail = '';
            try { detail = (await res.json()).detail || ''; } catch { /* no body */ }
            if (res.ok) showMsg('branding-msg', 'برندینگ ذخیره شد', 'success');
            else showMsg('branding-msg', detail || 'خطا در ذخیره برندینگ', 'danger');
        } catch {
            showMsg('branding-msg', 'خطای ارتباط با سرور', 'danger');
        } finally {
            btn.disabled = false;
        }
    });
}


async function loadMenuSettings() {
    try {
        const res = await fetchAuth('/admin/api/menu-settings');
        if (!res.ok) return;
        const d = await res.json();
        document.getElementById('menu-show-language').checked = !!d.menu_show_language;
        document.getElementById('menu-show-theme-toggle').checked = !!d.menu_show_theme_toggle;
        document.getElementById('menu-show-text-size').checked = !!d.menu_show_text_size;
        document.getElementById('menu-show-logout').checked = !!d.menu_show_logout;
    } catch { /* form keeps its server-rendered values */ }
}

export function initMenuSettings() {
    const form = document.getElementById('menu-settings-form');
    if (!form) return;
    loadMenuSettings();

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const btn = document.getElementById('save-menu-settings-btn');
        btn.disabled = true;
        try {
            const res = await fetchAuth('/admin/api/menu-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    show_language: document.getElementById('menu-show-language').checked,
                    show_theme_toggle: document.getElementById('menu-show-theme-toggle').checked,
                    show_text_size: document.getElementById('menu-show-text-size').checked,
                    show_logout: document.getElementById('menu-show-logout').checked,
                }),
            });
            let detail = '';
            try { detail = (await res.json()).detail || ''; } catch { /* no body */ }
            if (res.ok) showMsg('menu-settings-msg', 'ذخیره شد', 'success');
            else showMsg('menu-settings-msg', detail || 'خطا در ذخیره', 'danger');
        } catch {
            showMsg('menu-settings-msg', 'خطای ارتباط با سرور', 'danger');
        } finally {
            btn.disabled = false;
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
