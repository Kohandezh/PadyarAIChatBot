// The organizer's exhibitor book. Most of this page is data we hold ABOUT a
// company — the profile fields below. The company's own words (title/text,
// in Persian and English) and its intro video are dataset-style PUBLIC
// content instead, and the dataset page can't reach a company any more
// (migrations/0013_companies.sql moved companies out of `dataset`), so both
// are edited here via their own endpoints
// (PUT /admin/api/company-profiles/{id}/content and .../video) — see
// app/services/company_profiles.py:set_public_content and :set_video for
// why those stay structurally separate from the profile fields below.

import { API_BASE } from './state.js';
import { fetchAuth, escapeHtml } from './utils.js';
import { createPager } from './pager.js';

const fa = (n) => Number(n || 0).toLocaleString('fa-IR');
const esc = (v) => escapeHtml(v === null || v === undefined ? '' : String(v)) || '';
const orDash = (v) => esc(v) || '<span class="text-muted">—</span>';

// Mirrors PROFILE_FIELDS in app/services/company_profiles.py: the ids are
// `f-<field>`, so one list drives both the load and the save.
const FIELDS = [
    'contact_name', 'contact_position', 'contact_mobile',
    'email', 'website', 'company_phone', 'fax',
    'address', 'address_en', 'province', 'booth_number', 'hall',
    'company_type', 'org_stage', 'activity_field', 'participation',
    'notes',
];

// Mirrors PUBLIC_CONTENT_FIELDS in app/services/company_profiles.py: the
// chatbot's own words about the company, kept structurally separate from
// FIELDS/PROFILE_FIELDS above (see company_profiles.py:set_public_content
// for why) — its ids are `c-content-<field>`, saved through its own PUT.
const CONTENT_FIELDS = ['title', 'title_en', 'text', 'text_en'];

// The sales lens: where this company stands in "go find the ones we don't
// know". Keyed by the API's lead_status; null = untouched. The wording is
// the operator's day, not the database's vocabulary.
const LEAD_STATES = {
    verified:   ['در انتظار متن', 'warning'],
    completed:  ['متن رسیده', 'success'],
};

let currentCompany = null;   // {id, title}
let modal = null;
let mediaBrowserModal = null;
let searchTimer = null;
let onlyMissing = false;
let companiesPager = null;
let currentSearch = '';

function alertBox(text) {
    const el = document.getElementById('companies-alert');
    if (!text) { el.classList.add('d-none'); el.textContent = ''; return; }
    el.textContent = text;
    el.classList.remove('d-none');
}

async function post(url, options = {}) {
    const res = await fetchAuth(url, options);
    let data = {};
    try { data = await res.json(); } catch { /* empty body is fine */ }
    if (!res.ok) {
        alertBox(data.detail || 'این کار انجام نشد. دوباره تلاش کنید.');
        return null;
    }
    return data;
}

// The organizer's private sales signal (migrations/0019) — never shown to
// visitors, never sent to the AI; this table is the only public face it has.
const WARMTH = {
    low:    ['سرد',    'secondary'],
    medium: ['معمولی', 'info'],
    high:   ['داغ',    'warning text-dark'],
};

let warmthFilter = '';

async function load(q = '') {
    currentSearch = q;
    const { offset, limit } = companiesPager.state;
    const data = await post(
        `/admin/api/company-profiles?q=${encodeURIComponent(q)}`
        + `&limit=${limit}&offset=${offset}`
        + (warmthFilter ? `&warmth=${encodeURIComponent(warmthFilter)}` : ''));
    if (!data) return;
    let rows = data.companies;
    companiesPager.setResult({ shown: rows.length, total: data.total, hasMore: data.has_more });
    // The one filter that answers the day's question: "who is left to find?"
    // Missing means no verified capture AND no profile — untouched or
    // spreadsheet-only, either way the booth still has work to do there.
    // It only narrows the page already fetched, same as the search box does.
    if (onlyMissing) {
        rows = rows.filter(c => !c.has_profile && !c.lead_status);
    }
    const withProfile = rows.filter(c => c.has_profile).length;
    document.getElementById('profile-count').textContent = fa(withProfile);
    const body = document.getElementById('companies');
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="10" class="text-center text-muted py-4">'
            + (onlyMissing ? 'شرکتِ بی‌اطلاعِ باقی‌مانده‌ای در این صفحه نیست.'
                           : 'شرکتی پیدا نشد.') + '</td></tr>';
        return;
    }
    body.innerHTML = rows.map(c => {
        const state = LEAD_STATES[c.lead_status];
        const stateBadge = state
            ? `<span class="badge bg-${state[1]}">${esc(state[0])}</span>`
            : '<span class="text-muted small">نرفته‌ایم</span>';
        const warmth = WARMTH[c.marketing_warmth];
        const warmthBadge = warmth
            ? `<span class="badge bg-${warmth[1]}">${esc(warmth[0])}</span>`
            : '<span class="text-muted small">—</span>';
        return `
        <tr data-id="${esc(c.id)}">
          <td class="ps-4">${esc(c.title)}</td>
          <td>${orDash(c.contact_name)}${c.contact_position
              ? `<div class="text-muted small">${esc(c.contact_position)}</div>` : ''}</td>
          <td dir="ltr">${orDash(c.contact_mobile || c.email || c.website)}</td>
          <td>${orDash(c.province)}</td>
          <td>${orDash(c.activity_field)}</td>
          <td>${warmthBadge}</td>
          <td>${stateBadge}</td>
          <td>${c.has_profile
              ? '<span class="badge bg-success has-profile">دارد</span>'
              : '<span class="badge bg-secondary has-profile">ندارد</span>'}</td>
          <td>${c.video_url
              ? '<i class="fas fa-check text-success"></i>'
              : '<i class="fas fa-times text-muted"></i>'}</td>
          <td class="text-end">
            <button class="btn btn-sm btn-outline-primary" data-edit="1">
              <i class="fas fa-folder-open me-1"></i>پرونده
            </button>
          </td>
        </tr>`;
    }).join('');
}

function fillForm(profile) {
    FIELDS.forEach(f => {
        const el = document.getElementById(`f-${f}`);
        if (el) el.value = profile[f] || '';
    });
    document.getElementById('profile-msg').textContent = '';
}

function fillContentForm(content) {
    CONTENT_FIELDS.forEach(f => {
        const el = document.getElementById(`c-content-${f}`);
        if (el) el.value = (content && content[f]) || '';
    });
}

function updateVideoPreview() {
    const field = document.getElementById('c-edit-video');
    if (!field) return;   // video module not enabled — fieldset isn't rendered
    const url = field.value;
    const previewDiv = document.getElementById('c-video-preview');
    const emptyDiv = document.getElementById('c-video-empty');
    if (url) {
        document.getElementById('c-preview-player').src = url;
        previewDiv.style.display = '';
        emptyDiv.style.display = 'none';
    } else {
        document.getElementById('c-preview-player').src = '';
        previewDiv.style.display = 'none';
        emptyDiv.style.display = '';
    }
}

function removeCompanyVideo() {
    document.getElementById('c-edit-video').value = '';
    updateVideoPreview();
}

function openModal(company, profile, videoUrl, content, priorityBoost) {
    currentCompany = company;
    document.getElementById('profile-modal-title').textContent =
        `پروندهٔ «${company.title}»`;
    fillForm(profile);
    fillContentForm(content);
    const videoField = document.getElementById('c-edit-video');
    if (videoField) {
        videoField.value = videoUrl || '';
        updateVideoPreview();
    }
    const boostField = document.getElementById('c-priority-boost');
    if (boostField) boostField.checked = !!priorityBoost;
    modal.show();
}

// --- Media Browser (same pattern as static/admin/js/dataset.js) ---

async function openCompanyMediaBrowser() {
    if (!mediaBrowserModal) {
        mediaBrowserModal = new bootstrap.Modal(document.getElementById('mediaBrowserModal'));
    }
    document.getElementById('media-search').value = '';
    document.getElementById('media-upload-status').innerText = '';
    mediaBrowserModal.show();
    await loadMediaGrid();
}

async function loadMediaGrid() {
    const grid = document.getElementById('media-grid');
    grid.innerHTML = '<div class="text-center py-4 text-muted w-100"><div class="spinner-border spinner-border-sm me-1"></div> در حال بارگذاری...</div>';
    try {
        const res = await fetchAuth(API_BASE + '/videos');
        if (!res.ok) throw new Error('خطا در دریافت لیست ویدیوها');
        const videos = await res.json();
        renderMediaGrid(videos);
    } catch (err) {
        grid.innerHTML = `<div class="text-center py-4 text-danger w-100">${err.message}</div>`;
    }
}

function _formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function renderMediaGrid(videos) {
    const grid = document.getElementById('media-grid');
    const search = document.getElementById('media-search').value.trim().toLowerCase();
    const filtered = search ? videos.filter(v => v.filename.toLowerCase().includes(search)) : videos;

    if (filtered.length === 0) {
        grid.innerHTML = '<div class="text-center py-4 text-muted w-100">ویدیویی یافت نشد</div>';
        return;
    }

    grid.innerHTML = filtered.map(v => `
        <div class="col-6 col-md-4 col-lg-3">
            <div class="card h-100" style="cursor:pointer;border:2px solid transparent;transition:border-color .15s"
                 onmouseenter="this.style.borderColor='#4e73df'" onmouseleave="this.style.borderColor='transparent'"
                 onclick="window.selectCompanyVideo('${v.url}')">
                <div class="position-relative" style="padding-top:56%;background:#000;border-radius:6px 6px 0 0;overflow:hidden">
                    <video src="${v.url}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover" muted preload="metadata"></video>
                    <span class="position-absolute bottom-0 end-0 badge bg-dark bg-opacity-75 m-1" style="font-size:10px">${_formatSize(v.size)}</span>
                    <button class="btn btn-sm btn-danger position-absolute top-0 start-0 m-1 py-0 px-1" style="font-size:11px;line-height:1.2"
                            onclick="event.stopPropagation();window.deleteCompanyMediaVideo('${escapeHtml(v.filename)}')">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </div>
                <div class="card-body p-2">
                    <small class="text-truncate d-block" title="${escapeHtml(v.filename)}">${escapeHtml(v.filename)}</small>
                </div>
            </div>
        </div>
    `).join('');
}

function selectCompanyVideo(url) {
    document.getElementById('c-edit-video').value = url;
    updateVideoPreview();
    mediaBrowserModal.hide();
}

async function deleteCompanyMediaVideo(filename) {
    if (!confirm(`آیا از حذف "${filename}" مطمئن هستید؟ این عمل قابل بازگشت نیست.`)) return;
    try {
        const res = await fetchAuth(API_BASE + '/videos/' + encodeURIComponent(filename), { method: 'DELETE' });
        if (res.ok) {
            await loadMediaGrid();
        } else {
            const data = await res.json();
            alert('خطا: ' + (data.detail || 'عملیات ناموفق'));
        }
    } catch {
        alert('خطای ارتباط با سرور');
    }
}

async function uploadFromCompanyMediaBrowser(input) {
    const file = input.files[0];
    if (!file) return;

    const status = document.getElementById('media-upload-status');
    status.className = 'text-muted small';
    status.innerText = '⏳ در حال آپلود ' + file.name + '...';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetchAuth(API_BASE + '/upload_video', { method: 'POST', body: formData });
        if (res.ok) {
            const data = await res.json();
            status.className = 'text-success small';
            status.innerText = '✅ ' + data.filename + ' آپلود شد';
            await loadMediaGrid();
        } else {
            const data = await res.json();
            status.className = 'text-danger small';
            status.innerText = '❌ ' + (data.detail || 'خطا در آپلود');
        }
    } catch {
        status.className = 'text-danger small';
        status.innerText = '❌ خطای ارتباط با سرور';
    }
    input.value = '';
}

// ── Company autofill ──────────────────────────────────────────────────────
// One button, one loop: the server fills at most 10 companies per POST (so
// one request never outlives the proxy timeout), and this side keeps calling
// until nothing is pending — the operator sees progress, not a spinner that
// may or may not still be working.
async function refreshAutofillCount() {
    const btn = document.getElementById('autofill-btn');
    const badge = document.getElementById('autofill-count');
    if (!btn || !badge) return;
    try {
        const res = await fetchAuth('/admin/api/company-profiles/autofill');
        if (!res.ok) { badge.textContent = '—'; return; }
        const data = await res.json();
        badge.textContent = fa(data.fillable);
        btn.disabled = !data.fillable;
        btn.title = data.fillable
            ? `${fa(data.fillable)} شرکت متن معرفی دارد ولی اطلاعاتش ناقص است`
            : (data.no_text
                ? `${fa(data.no_text)} شرکت متن معرفی هم ندارد — این‌ها را باید دستی پر کنید`
                : 'خالی‌ای نمانده است');
    } catch { badge.textContent = '—'; }
}

// ── Bulk confirm campaigns (migrations/0024) ─────────────────────────────
// Text every company with a mobile on file, each with its own one-time edit
// link. The send is paced on the server (~1/second) inside a background
// task, so launching returns immediately and this panel polls the report.
// Delivery ("did it arrive") is the outbox's word, polled by the button and
// the background loop — a green "sent" here means the gateway took it, not
// that a phone rang.

const SMS_STATUS = {
    delivered:    ['رسیده', 'success'],
    queued:       ['در صف ارسال', 'warning'],
    unknown:      ['وضعیت نامعلوم', 'secondary'],
    failed:       ['ناموفق', 'danger'],
    skipped:      ['رد شد (پیش‌نویس در بررسی)', 'secondary'],
    send_failed:  ['ارسال نشد', 'danger'],
};

async function getJSON(url) {
    const res = await fetchAuth(url);
    if (!res.ok) return null;
    return res.json().catch(() => null);
}

async function loadCampaigns() {
    const data = await getJSON('/admin/api/leads/campaigns');
    if (!data) return;
    const cap = data.capability || {};
    const capEl = document.getElementById('campaign-capability');
    capEl.textContent = cap.available
        ? `${fa(cap.audience)} شرکت شمارهٔ موبایل در پرونده دارند` +
          (cap.dev ? ' — حالت آزمایشی: پیام‌ها در صندوق آزمایشی می‌نشینند' : '')
        : (cap.reason || 'ارسال ممکن نیست.');
    const textEl = document.getElementById('campaign-text');
    if (!textEl.value.trim() && cap.text) textEl.value = cap.text;
    document.getElementById('campaign-send').disabled = !cap.available;

    const box = document.getElementById('campaign-history');
    if (!(data.campaigns || []).length) {
        box.innerHTML = '<p class="text-muted small mb-0">هنوز کمپینی ارسال نشده است.</p>';
        return;
    }
    const statusFa = { running: ['در حال ارسال', 'warning'], done: ['تمام شد', 'success'], stopped: ['متوقف شد', 'danger'] };
    box.innerHTML = data.campaigns.map(c => {
        const [label, tone] = statusFa[c.status] || [c.status, 'secondary'];
        const d = c.delivery || {};
        const counts = [
            `${fa(c.sent)} ارسال`,
            c.skipped ? `${fa(c.skipped)} رد` : '',
            c.failed ? `${fa(c.failed)} ناموفق` : '',
            `${fa(d.delivered)} رسیده`,
            `${fa(d.queued)} در صف`,
        ].filter(Boolean).join('، ');
        return `
          <div class="border rounded p-2 mb-2" data-campaign="${esc(c.id)}">
            <div class="d-flex justify-content-between flex-wrap gap-2 align-items-center">
              <span class="small">
                <span class="badge bg-${tone} ms-1">${label}</span>
                ${esc(new Date(c.created_at).toLocaleString('fa-IR'))}
              </span>
              <span class="small text-muted">${counts}</span>
              <button class="btn btn-outline-secondary btn-sm" data-campaign-detail="${esc(c.id)}">
                جزئیات
              </button>
            </div>
            ${c.stop_reason ? `<div class="small text-danger mt-1">${esc(c.stop_reason)}</div>` : ''}
            <div class="campaign-detail mt-2" id="campaign-detail-${esc(c.id)}" hidden></div>
          </div>`;
    }).join('');
    box.querySelectorAll('[data-campaign-detail]').forEach(btn => {
        btn.addEventListener('click', () => toggleCampaignDetail(btn.dataset.campaignDetail));
    });
}

async function toggleCampaignDetail(campaignId) {
    const box = document.getElementById(`campaign-detail-${campaignId}`);
    if (!box.hidden) { box.hidden = true; return; }
    box.hidden = false;
    box.innerHTML = '<span class="text-muted small">در حال بارگذاری…</span>';
    const data = await getJSON(`/admin/api/leads/campaigns/${encodeURIComponent(campaignId)}`);
    if (!data) { box.innerHTML = '<span class="text-muted small">خواندن جزئیات ناموفق بود.</span>'; return; }
    const rows = (data.messages || []).map(m => {
        const [label, tone] = SMS_STATUS[m.status] || [m.status, 'secondary'];
        return `
          <tr>
            <td class="small">${esc(m.status_detail || m.reference || '—')}</td>
            <td><span class="badge bg-${tone}">${label}</span></td>
            <td class="small text-muted">${esc(m.status_checked_at
                ? new Date(m.status_checked_at).toLocaleString('fa-IR') : '—')}</td>
          </tr>`;
    }).join('');
    box.innerHTML = rows
        ? `<table class="table table-sm mb-0"><tbody>${rows}</tbody></table>`
        : '<span class="text-muted small">پیامی ثبت نشده است.</span>';
}

async function launchCampaign() {
    const btn = document.getElementById('campaign-send');
    const textEl = document.getElementById('campaign-text');
    const progress = document.getElementById('campaign-progress');
    if (!textEl.value.includes('{magic_link}')) {
        alertBox('متن پیامک باید عبارت {magic_link} را داشته باشد؛ همان‌جا لینک هر شرکت جایگزین می‌شود.');
        return;
    }
    const cap = (await getJSON('/admin/api/leads/campaigns') || {}).capability;
    if (!cap || !cap.available) { alertBox(cap && cap.reason); return; }
    if (!confirm(`به ${fa(cap.audience)} شرکت پیامک «بررسی و تأیید اطلاعات» ارسال شود؟ هر شرکت لینک یک‌بارمصرف خودش را می‌گیرد.`)) return;
    btn.disabled = true;
    progress.textContent = 'کمپین آغاز شد؛ در حال ارسال…';
    const data = await post('/admin/api/leads/campaigns', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: textEl.value.trim() }),
    });
    if (!data) { progress.textContent = ''; btn.disabled = false; return; }
    // The send runs paced in the background; poll the report until it ends.
    const timer = setInterval(async () => {
        await loadCampaigns();
        const state = (await getJSON('/admin/api/leads/campaigns') || {}).campaigns || [];
        if (!state.some(c => c.status === 'running')) {
            clearInterval(timer);
            progress.textContent = 'ارسال تمام شد.';
            btn.disabled = false;
        }
    }, 5000);
}

async function initCampaigns() {
    const btn = document.getElementById('campaign-send');
    if (!btn) return;
    await loadCampaigns();
    btn.addEventListener('click', launchCampaign);
    document.getElementById('campaign-refresh').addEventListener('click', async () => {
        const progress = document.getElementById('campaign-progress');
        progress.textContent = 'در حال پرسیدن وضعیت از سامانهٔ پیامک…';
        await post('/admin/api/sms/refresh-statuses');
        progress.textContent = '';
        await loadCampaigns();
    });
}

async function initAutofill() {
    await refreshAutofillCount();
    const btn = document.getElementById('autofill-btn');
    if (!btn) return;
        btn.addEventListener('click', async () => {
        const badge = document.getElementById('autofill-count');
        const progress = document.getElementById('autofill-progress');
        const total = Number(badge.textContent.replace(/[۰-۹]/g, d => '۰۱۲۳۴۵۶۷۸۹'.indexOf(d))) || 0;
        if (total && !confirm(`برای ${fa(total)} شرکت، اطلاعات خالی از متن معرفی به‌طور خودکار پر شود؟`)) return;
        btn.disabled = true;
        progress.classList.remove('d-none');
        let filled = 0, failed = 0, done = 0, fieldCount = 0, cursor = null;
        try {
            for (;;) {
                progress.textContent = `در حال پر کردن… ${fa(done)} شرکت انجام شد`;
                const res = await post('/admin/api/company-profiles/autofill', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    // The cursor resumes AFTER the last company this pass
                    // examined — without it every batch restarts at the
                    // queue head and re-asks the same no-yield companies.
                    body: JSON.stringify(cursor ? { cursor } : {}),
                });
                if (!res) return;                       // post() already alerted
                filled += res.filled.length;
                failed += res.failed.length;
                done += res.filled.length + res.failed.length;
                fieldCount += res.filled.reduce((n, f) => n + (f.fields ? f.fields.length : 0), 0);
                cursor = res.cursor || null;
                // The pass reached the queue's end: stop. A fresh pass is a
                // fresh human click, not this loop's decision.
                if (res.pass_complete) break;
                if (!res.remaining) break;
            }
            const noText = await fetchAuth('/admin/api/company-profiles/autofill')
                .then(r => r.ok ? r.json() : null).catch(() => null);
            const parts = [`${fa(fieldCount)} فیلد در ${fa(filled)} شرکت پر شد`];
            if (failed) parts.push(`${fa(failed)} شرکت نتوانست پر شود`);
            if (noText && noText.no_text) parts.push(`${fa(noText.no_text)} شرکت متن معرفی ندارد`);
            alertBox('');
            progress.textContent = parts.join('، ') + '؛ جزئیات در لاگ‌ها.';
            await load(document.getElementById('company-search').value.trim());
            await refreshAutofillCount();
        } finally {
            progress.classList.add('d-none');
            btn.disabled = false;
            await refreshAutofillCount();
        }
    });
}

export function initCompanies() {
    initCampaigns();
    modal = new bootstrap.Modal(document.getElementById('profile-modal'));
    companiesPager = createPager({
        pageSizeEl: document.getElementById('companies-page-size'),
        prevBtnEl: document.getElementById('companies-btn-prev'),
        nextBtnEl: document.getElementById('companies-btn-next'),
        rangeEl: document.getElementById('companies-range'),
        defaultLimit: 25,
        onPage: () => load(currentSearch),
    });

    load().then(() => {
        document.getElementById('companies').addEventListener('click', async (ev) => {
            const btn = ev.target.closest('[data-edit]');
            if (!btn) return;
            const row = btn.closest('[data-id]');
            const id = row.dataset.id;
            const data = await post(`/admin/api/company-profiles/${encodeURIComponent(id)}`);
            if (!data) return;
            openModal({ id, title: row.cells[0].textContent.trim() }, data.profile,
                     data.video_url, data.content, data.priority_boost);
        });
    });

    document.getElementById('company-search').addEventListener('input', (ev) => {
        clearTimeout(searchTimer);
        const q = ev.target.value.trim();
        searchTimer = setTimeout(() => { companiesPager.reset(); load(q); }, 250);
    });

    document.getElementById('only-missing').addEventListener('change', (ev) => {
        onlyMissing = ev.target.checked;
        companiesPager.reset();
        load(document.getElementById('company-search').value.trim());
    });

    document.getElementById('warmth-filter').addEventListener('change', (ev) => {
        warmthFilter = ev.target.value;
        companiesPager.reset();
        load(document.getElementById('company-search').value.trim());
    });

    initAutofill();

    document.getElementById('profile-save').addEventListener('click', async (ev) => {
        if (!currentCompany) return;
        const btn = ev.currentTarget;
        const body = {};
        FIELDS.forEach(f => {
            const el = document.getElementById(`f-${f}`);
            if (el) body[f] = el.value.trim();
        });
        btn.disabled = true;
        const data = await post(
            `/admin/api/company-profiles/${encodeURIComponent(currentCompany.id)}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
        let contentOk = true;
        const contentBody = {};
        CONTENT_FIELDS.forEach(f => {
            const el = document.getElementById(`c-content-${f}`);
            if (el) contentBody[f] = el.value.trim();
        });
        if (data) {
            const contentData = await post(
                `/admin/api/company-profiles/${encodeURIComponent(currentCompany.id)}/content`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(contentBody),
                });
            contentOk = !!contentData;
        }
        let videoOk = true;
        const videoField = document.getElementById('c-edit-video');
        if (data && videoField) {
            const videoData = await post(
                `/admin/api/company-profiles/${encodeURIComponent(currentCompany.id)}/video`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ video_url: videoField.value.trim() }),
                });
            videoOk = !!videoData;
        }
        let boostOk = true;
        const boostField = document.getElementById('c-priority-boost');
        if (data && boostField) {
            const boostData = await post(
                `/admin/api/company-profiles/${encodeURIComponent(currentCompany.id)}/priority-boost`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ priority_boost: boostField.checked }),
                });
            boostOk = !!boostData;
        }
        btn.disabled = false;
        if (!data || !contentOk || !videoOk || !boostOk) return;
        const msg = document.getElementById('profile-msg');
        msg.className = 'fw-bold mt-2 small text-success';
        msg.textContent = 'پرونده ذخیره شد.';
        await load(document.getElementById('company-search').value.trim());
    });

    window.removeCompanyVideo = removeCompanyVideo;
    window.openCompanyMediaBrowser = openCompanyMediaBrowser;
    window.selectCompanyVideo = selectCompanyVideo;
    window.deleteCompanyMediaVideo = deleteCompanyMediaVideo;
    window.uploadFromCompanyMediaBrowser = uploadFromCompanyMediaBrowser;

    document.getElementById('media-search')?.addEventListener('input', async () => {
        const res = await fetchAuth(API_BASE + '/videos');
        if (res.ok) {
            renderMediaGrid(await res.json());
        }
    });
}
