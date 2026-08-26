// The organizer's exhibitor book. Everything on this page is data we hold
// ABOUT a company; the company's own words live on the dataset page and are
// never edited from here.

import { fetchAuth, escapeHtml } from './utils.js';

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

let currentCompany = null;   // {id, title}
let modal = null;
let searchTimer = null;

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
    const data = await post(`/admin/api/company-profiles?q=${encodeURIComponent(q)}`) ;
    if (!data) return;
    const rows = data.companies;
    const withProfile = rows.filter(c => c.has_profile).length;
    document.getElementById('profile-count').textContent = fa(withProfile);
    const body = document.getElementById('companies');
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="7" class="text-center text-muted py-4">'
            + 'شرکتی پیدا نشد.</td></tr>';
        return;
    }
    body.innerHTML = rows.map(c => `
        <tr data-id="${esc(c.id)}">
          <td class="ps-4">${esc(c.title)}</td>
          <td>${orDash(c.contact_name)}${c.contact_position
              ? `<div class="text-muted small">${esc(c.contact_position)}</div>` : ''}</td>
          <td dir="ltr">${orDash(c.contact_mobile || c.email || c.website)}</td>
          <td>${orDash(c.province)}</td>
          <td>${orDash(c.activity_field)}</td>
          <td>${c.has_profile
              ? '<span class="badge bg-success has-profile">دارد</span>'
              : '<span class="badge bg-secondary has-profile">ندارد</span>'}</td>
          <td class="text-end">
            <button class="btn btn-sm btn-outline-primary" data-edit="1">
              <i class="fas fa-folder-open me-1"></i>پرونده
            </button>
          </td>
        </tr>`).join('');
}

function fillForm(profile) {
    FIELDS.forEach(f => {
        const el = document.getElementById(`f-${f}`);
        if (el) el.value = profile[f] || '';
    });
    document.getElementById('profile-msg').textContent = '';
}

function openModal(company, profile) {
    currentCompany = company;
    document.getElementById('profile-modal-title').textContent =
        `پروندهٔ «${company.title}»`;
    fillForm(profile);
    modal.show();
}

export function initCompanies() {
    modal = new bootstrap.Modal(document.getElementById('profile-modal'));

    load().then(() => {
        document.getElementById('companies').addEventListener('click', async (ev) => {
            const btn = ev.target.closest('[data-edit]');
            if (!btn) return;
            const row = btn.closest('[data-id]');
            const id = row.dataset.id;
            const data = await post(`/admin/api/company-profiles/${encodeURIComponent(id)}`);
            if (!data) return;
            openModal({ id, title: row.cells[0].textContent.trim() }, data.profile);
        });
    });

    document.getElementById('company-search').addEventListener('input', (ev) => {
        clearTimeout(searchTimer);
        const q = ev.target.value.trim();
        searchTimer = setTimeout(() => load(q), 250);
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
        btn.disabled = false;
        if (!data) return;
        const msg = document.getElementById('profile-msg');
        msg.className = 'fw-bold mt-2 small text-success';
        msg.textContent = 'پرونده ذخیره شد.';
        await load(document.getElementById('company-search').value.trim());
    });
}
