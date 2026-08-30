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
    'address', 'address_en', 'province',
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

async function load(q = '') {
    currentSearch = q;
    const { offset, limit } = companiesPager.state;
    const data = await post(
        `/admin/api/company-profiles?q=${encodeURIComponent(q)}&limit=${limit}&offset=${offset}`);
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
        body.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">'
            + (onlyMissing ? 'شرکتِ بی‌اطلاعِ باقی‌مانده‌ای در این صفحه نیست.'
                           : 'شرکتی پیدا نشد.') + '</td></tr>';
        return;
    }
    body.innerHTML = rows.map(c => {
        const state = LEAD_STATES[c.lead_status];
        const stateBadge = state
            ? `<span class="badge bg-${state[1]}">${esc(state[0])}</span>`
            : '<span class="text-muted small">نرفته‌ایم</span>';
        return `
        <tr data-id="${esc(c.id)}">
          <td class="ps-4">${esc(c.title)}</td>
          <td>${orDash(c.contact_name)}${c.contact_position
              ? `<div class="text-muted small">${esc(c.contact_position)}</div>` : ''}</td>
          <td dir="ltr">${orDash(c.contact_mobile || c.email || c.website)}</td>
          <td>${orDash(c.province)}</td>
          <td>${orDash(c.activity_field)}</td>
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

function openModal(company, profile, videoUrl, content) {
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

export function initCompanies() {
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
            openModal({ id, title: row.cells[0].textContent.trim() }, data.profile, data.video_url, data.content);
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
        btn.disabled = false;
        if (!data || !contentOk || !videoOk) return;
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
