// Exhibition lead capture — the operator's screen.
//
// The day, in order: read the three numbers, clear the review queue, free the
// companies nobody can reach, and hand out or rotate a field visitor's link.
//
// Everything rendered here came from outside the building. The proposed answer
// text is written by a company contact we have never met and the reviewer's
// browser shares an origin with the whole admin panel, so every interpolated
// value goes through esc() with no exceptions.

import { fetchAuth, escapeHtml } from './utils.js';

// A registration sits in exactly one of these three. They are places, not
// stages, so the numbers do not nest and the row does not have to descend.
const STATES = [
    ['unverified', 'منتظر تأیید شماره', 'کد پیامک شده، مخاطب هنوز آن را نخوانده', 'secondary'],
    ['verified',   'شمارهٔ تأیید شده',   'مخاطب هنوز هیچ متنی نفرستاده',           'primary'],
    ['completed',  'متن شرکت رسیده',    'مخاطب کارش را تمام کرده',                'success'],
];

const STATUS = {
    unverified: ['منتظر تأیید شماره', 'secondary'],
    verified:   ['شمارهٔ تأیید شده',   'primary'],
    completed:  ['متن شرکت رسیده',    'success'],
};

const fa = (n) => Number(n || 0).toLocaleString('fa-IR');

// escapeHtml() hands back whatever falsy value it was given, so wrap it once
// here and let every template hole assume a string.
const esc = (v) => escapeHtml(v === null || v === undefined ? '' : String(v)) || '';

const fullName = (r) => `${r.first_name || ''} ${r.last_name || ''}`.trim();

function faDate(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (isNaN(d)) return '';
    return d.toLocaleString('fa-IR', {
        year: 'numeric', month: 'long', day: 'numeric',
        hour: '2-digit', minute: '2-digit',
    });
}

// "How long has this been sitting there" in words, because an operator reading
// a stuck list needs to feel the age, not convert a timestamp.
function waitedFor(row) {
    let hours = row.waiting_hours;
    if (hours === undefined || hours === null) {
        const since = new Date(row.verified_at || '').getTime();
        if (isNaN(since)) return '—';
        hours = (Date.now() - since) / 3600000;
    }
    const h = Math.max(0, Math.floor(Number(hours) || 0));
    if (h < 1) return 'کمتر از یک ساعت';
    if (h < 24) return `${fa(h)} ساعت`;
    const days = Math.floor(h / 24);
    const rest = h % 24;
    return rest ? `${fa(days)} روز و ${fa(rest)} ساعت` : `${fa(days)} روز`;
}

function alertBox(text) {
    const el = document.getElementById('leads-alert');
    if (!text) { el.classList.add('d-none'); el.textContent = ''; return; }
    el.textContent = text;
    el.classList.remove('d-none');
}

function note(id, text, tone) {
    const el = document.getElementById(id);
    el.className = `fw-bold mt-2 small text-${tone}`;
    el.textContent = text;
}

// Every mutation goes through here so a refused action says why instead of
// silently doing nothing.
async function post(url, body) {
    alertBox('');
    const res = await fetchAuth(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await res.json(); } catch { /* empty body is fine */ }
    if (!res.ok) {
        alertBox(data.detail || 'این کار انجام نشد. دوباره تلاش کنید.');
        return null;
    }
    return data;
}

async function get(url) {
    const res = await fetchAuth(url);
    if (!res.ok) throw new Error(url);
    return res.json();
}

// Same refusal discipline as post(), for the one DELETE on this page.
async function del(url) {
    alertBox('');
    const res = await fetchAuth(url, { method: 'DELETE' });
    let data = {};
    try { data = await res.json(); } catch { /* empty body is fine */ }
    if (!res.ok) {
        alertBox(data.detail || 'این کار انجام نشد. دوباره تلاش کنید.');
        return false;
    }
    return true;
}

// ── Risky content in a proposed answer ──────────────────────────────────
//
// An approved edit becomes the answer the chatbot gives every visitor, and the
// damage that actually happens needs no markup: a competitor's address, a bank
// account, a phone number that is not the company's. The server extracts these
// at submit time; this is the fallback so no card is ever shown without its
// risky tokens named, and a reviewer approving the fortieth diff of the
// afternoon still sees them called out rather than buried in prose.

const RISK_PATTERNS = [
    [/\bIR[ -]?\d{2}(?:[ -]?\d){22}\b/gi,                              'شماره شبا'],
    [/\b(?:\d[ -]?){15}\d\b/g,                                         'شمارهٔ کارت یا حساب'],
    [/(?:https?:\/\/|www\.)[^\s<>"'«»،؛]+/gi,                          'نشانی اینترنتی'],
    [/\b[a-z0-9-]{2,}\.(?:ir|com|net|org|co|io|me|info|biz|app|dev)\b(?:\/[^\s<>"'«»،؛]*)?/gi, 'نشانی اینترنتی'],
    [/(?:\+?98|0)?9\d{2}[ -]?\d{3}[ -]?\d{4}\b/g,                      'شمارهٔ تماس'],
    [/\b0\d{2}[ -]?\d{8}\b/g,                                          'شمارهٔ تماس'],
    [/@[A-Za-z0-9_]{4,}\b/g,                                           'آی‌دی شبکهٔ اجتماعی'],
];

// Persian and Arabic digits map one char to one char, so matching on the
// converted copy still gives indices that are valid in the original.
function latinDigits(text) {
    return text.replace(/[۰-۹]/g, c => String.fromCharCode(c.charCodeAt(0) - 1728))
               .replace(/[٠-٩]/g, c => String.fromCharCode(c.charCodeAt(0) - 1584));
}

function findRisky(text) {
    if (!text) return [];
    const probe = latinDigits(String(text));
    const taken = [];
    const found = [];
    const seen = new Set();
    for (const [pattern, label] of RISK_PATTERNS) {
        pattern.lastIndex = 0;
        let m;
        while ((m = pattern.exec(probe)) !== null) {
            const start = m.index;
            const end = start + m[0].length;
            if (m[0].length === 0) { pattern.lastIndex++; continue; }
            // Earlier patterns win, so a bank number is not also reported as a
            // phone number sitting inside it.
            if (taken.some(([s, e]) => start < e && end > s)) continue;
            taken.push([start, end]);
            const value = String(text).slice(start, end);
            if (seen.has(value)) continue;
            seen.add(value);
            found.push({ label, value });
        }
    }
    return found;
}

function riskyHtml(edit) {
    // Prefer what the server extracted: only it knows the allowed-domain list.
    const items = Array.isArray(edit.risky) && edit.risky.length
        ? edit.risky.map(r => ({
            label: r.label || r.kind || 'مورد قابل بررسی',
            value: r.value,
            allowed: !!r.allowed,
        }))
        : findRisky(edit.new_text);
    if (!items.length) return '';
    return `
      <div class="alert alert-warning py-2 px-3 mb-3">
        <div class="fw-bold small mb-2">
          <i class="fas fa-triangle-exclamation me-1"></i>
          در متن پیشنهادی این موارد پیدا شد. قبل از تأیید هر کدام را بخوانید:
        </div>
        <ul class="mb-0 small ps-3">
          ${items.map(i => `<li>
              <span class="text-muted">${esc(i.label)}:</span>
              <code dir="ltr">${esc(i.value)}</code>
              ${i.allowed ? '<span class="badge bg-success ms-1">دامنهٔ مجاز</span>' : ''}
            </li>`).join('')}
        </ul>
      </div>`;
}

// ── Rendering ───────────────────────────────────────────────────────────

function senderLine(r) {
    const bits = [fullName(r), r.position, r.phone].filter(Boolean);
    return bits.map(b => esc(b)).join(' · ');
}

// Old beside new, because approving is writing the chatbot's answer: the
// reviewer has to see the CHANGE, not just the replacement.
function diffHtml(oldText, newText) {
    const cell = (t) => t
        ? `<div class="bg-light rounded p-2 small" style="white-space:pre-wrap">${esc(t)}</div>`
        : '<div class="bg-light rounded p-2 small text-muted">این شرکت هنوز متنی نداشت.</div>';
    return `
      <div class="row g-2 mb-3">
        <div class="col-md-6">
          <div class="text-muted small mb-1">متن فعلی</div>
          ${cell(oldText)}
        </div>
        <div class="col-md-6">
          <div class="text-muted small mb-1">متن پیشنهادی شرکت</div>
          ${cell(newText)}
        </div>
      </div>`;
}

async function loadFunnel() {
    const data = await get('/admin/api/leads/funnel');
    document.getElementById('funnel').innerHTML = `
        <div class="col-12 col-md-3">
          <div class="content-card h-100 border-warning">
            <div class="card-body text-center py-3">
              <div class="display-6 mb-0 text-warning">${fa(data.pending_review)}</div>
              <div class="fw-bold small">در انتظار بررسی شما</div>
              <div class="text-muted" style="font-size:.75rem">متنی که هنوز روی چت‌بات ننشسته</div>
            </div>
          </div>
        </div>` +
        STATES.map(([key, label, hint, tone]) => `
        <div class="col-12 col-md-3">
          <div class="content-card h-100">
            <div class="card-body text-center py-3">
              <div class="display-6 mb-0 text-${tone}">${fa(data[key])}</div>
              <div class="fw-bold small">${label}</div>
              <div class="text-muted" style="font-size:.75rem">${hint}</div>
            </div>
          </div>
        </div>`).join('');

    const side = [`همهٔ ثبت‌ها: ${fa(data.total)}`];
    if (data.overrides !== undefined) side.push(`ثبت با شمارهٔ تکراری: ${fa(data.overrides)}`);
    if (data.released !== undefined) side.push(`شرکت‌های آزادشده: ${fa(data.released)}`);
    document.getElementById('funnel-side').textContent = side.join(' · ');
    document.getElementById('pending-count').textContent = fa(data.pending_review);
}

async function loadEdits() {
    const { edits } = await get('/admin/api/leads/edits');
    const box = document.getElementById('edits');
    if (!edits.length) {
        box.innerHTML = '<p class="text-muted mb-0">چیزی در انتظار بررسی نیست.</p>';
        return;
    }
    box.innerHTML = edits.map(e => `
        <div class="border rounded p-3 mb-3" data-edit="${esc(e.id)}">
          <div class="d-flex justify-content-between flex-wrap gap-2 mb-2">
            <strong>${esc(e.company_name || e.dataset_id)}</strong>
            <span class="text-muted small">${senderLine(e)}</span>
          </div>
          ${riskyHtml(e)}
          ${diffHtml(e.old_text, e.new_text)}
          <button class="btn btn-success btn-sm" data-approve="1">
            <i class="fas fa-check me-1"></i>تأیید و انتشار روی چت‌بات
          </button>
          <button class="btn btn-outline-danger btn-sm" data-approve="0">
            <i class="fas fa-times me-1"></i>رد و اطلاع به مخاطب
          </button>
        </div>`).join('');
}

async function loadApproved() {
    const { edits } = await get('/admin/api/leads/edits?status=approved');
    const box = document.getElementById('approved');
    if (!edits.length) {
        box.innerHTML = '<p class="text-muted mb-0">هنوز متنی تأیید نشده است.</p>';
        return;
    }
    box.innerHTML = edits.map(e => `
        <div class="border rounded p-3 mb-3" data-edit="${esc(e.id)}">
          <div class="d-flex justify-content-between flex-wrap gap-2 mb-2">
            <strong>${esc(e.company_name || e.dataset_id)}</strong>
            <span class="text-muted small">
              تأیید شده در ${esc(faDate(e.reviewed_at))}
              ${e.reviewed_by ? '· ' + esc(e.reviewed_by) : ''}
            </span>
          </div>
          ${diffHtml(e.old_text, e.new_text)}
          <button class="btn btn-outline-warning btn-sm" data-revert="1">
            <i class="fas fa-rotate-left me-1"></i>برگرداندن به متن قبلی
          </button>
        </div>`).join('');
}

async function loadStuck() {
    const { stuck } = await get('/admin/api/leads/stuck');
    document.getElementById('stuck-count').textContent = fa(stuck.length);
    const body = document.getElementById('stuck');
    if (!stuck.length) {
        body.innerHTML = '<tr><td colspan="6" class="text-center text-muted py-4">'
            + 'هیچ شرکتی منتظر نمانده است.</td></tr>';
        return;
    }
    body.innerHTML = stuck.map(s => `
        <tr data-lead="${esc(s.id)}">
          <td class="ps-4">${esc(s.company_name)}</td>
          <td>${esc(fullName(s))}</td>
          <td dir="ltr">${esc(s.phone)}</td>
          <td>${esc(s.visitor_name) || '<span class="text-muted">—</span>'}</td>
          <td>${esc(waitedFor(s))}</td>
          <td class="text-end">
            <button class="btn btn-sm btn-outline-primary" data-release="1">
              آزاد کردن و برگرداندن به فهرست
            </button>
          </td>
        </tr>`).join('');
}

async function loadVisitors() {
    const { visitors } = await get('/admin/api/leads/visitors');
    document.getElementById('visitors').innerHTML = visitors.map(v => `
        <tr class="${v.active ? '' : 'opacity-50'}" data-visitor="${esc(v.id)}">
          <td class="ps-4">${esc(v.name) || '<span class="text-muted">بی‌نام</span>'}</td>
          <td>${fa(v.total)}</td>
          <td>${fa(v.verified)}</td>
          <td class="text-end">
            <button class="btn btn-sm btn-outline-secondary" data-rotate="1">ساخت لینک تازه</button>
            <button class="btn btn-sm ${v.active ? 'btn-outline-danger' : 'btn-outline-success'}"
                    data-active="${v.active ? '0' : '1'}">
              ${v.active ? 'غیرفعال' : 'فعال'}
            </button>
            <button class="btn btn-sm btn-outline-danger" data-remove="1">
              <i class="fas fa-trash me-1"></i>حذف
            </button>
          </td>
        </tr>`).join('');
}

async function loadLeads() {
    const { leads } = await get('/admin/api/leads');
    // The number a visitor waved through is only a control if it stays visible
    // afterwards, so the override rides on the registration itself.
    const nameOf = {};
    leads.forEach(l => { nameOf[l.id] = l.company_name; });

    document.getElementById('leads').innerHTML = leads.map(l => {
        const [label, tone] = STATUS[l.status] || [l.status, 'secondary'];
        const prior = l.duplicate_override_company
            || nameOf[l.duplicate_override_of]
            || '';
        const override = l.duplicate_override_at ? `
            <div class="small text-danger mt-1">
              <i class="fas fa-triangle-exclamation me-1"></i>
              این شماره از قبل برای ${prior ? `«${esc(prior)}»` : 'یک شرکت دیگر'}
              ثبت شده بود و همکار غرفه هشدار را رد کرد
              (${esc(faDate(l.duplicate_override_at))}).
            </div>` : '';
        const released = l.released_at
            ? `<span class="badge bg-light text-dark ms-1">آزاد شده</span>` : '';
        return `<tr data-lead="${esc(l.id)}">
            <td class="ps-4">${esc(l.company_name)}${override}</td>
            <td>${esc(fullName(l))}</td>
            <td dir="ltr">${esc(l.phone)}</td>
            <td><span class="badge bg-${tone}">${esc(label)}</span>${released}</td>
          </tr>`;
    }).join('');
}

async function loadSettings() {
    const s = await get('/admin/api/leads/settings');
    // The SMS option depends on a gateway permission this account may not have.
    // If it is missing, the operator has to read that here, at the moment of
    // choosing, and not afterwards in a log.
    const smsOff = s.sms_available === false;
    // When it IS available, the same field carries the dev-outbox note: the
    // channel works, and the operator still deserves to know where the
    // message really goes on a test install.
    const smsNote = s.sms_reason && !smsOff
        ? `<div class="text-muted small mt-1">${esc(s.sms_reason)}</div>` : '';
    const smsReason = smsOff
        ? `<div class="text-danger small mt-1">
             سامانهٔ پیامک این حساب اجازهٔ فرستادن لینک ندارد، پس این گزینه الان کار نمی‌کند.
             ${s.sms_reason ? esc(s.sms_reason) : ''}
           </div>`
        : '';
    document.getElementById('channel').innerHTML = `
        <div class="form-check mb-3">
          <input class="form-check-input" type="radio" name="channel" id="channel-qr" value="qr"
                 ${s.invite_channel !== 'sms' ? 'checked' : ''}>
          <label class="form-check-label" for="channel-qr">
            <span class="fw-bold">نمایش QR روی گوشی همکار غرفه</span>
            <div class="text-muted small">
              مخاطب همان‌جا اسکن می‌کند. هزینهٔ پیامک ندارد و به اجازهٔ هیچ سامانهٔ بیرونی وابسته نیست.
            </div>
          </label>
        </div>
        <div class="form-check">
          <input class="form-check-input" type="radio" name="channel" id="channel-sms" value="sms"
                 ${s.invite_channel === 'sms' ? 'checked' : ''} ${smsOff ? 'disabled' : ''}>
          <label class="form-check-label" for="channel-sms">
            <span class="fw-bold">فرستادن لینک با پیامک</span>
             <div class="text-muted small">
               لینک به همان شماره‌ای می‌رود که تأیید شده. مخاطب لازم نیست همان لحظه پای غرفه بماند.
             </div>
             ${smsReason}
             ${smsNote}
          </label>
        </div>`;
    document.getElementById('consent-script').value = s.consent_script || '';
}

function refresh() {
    alertBox('');
    return Promise.all([
        loadFunnel(), loadEdits(), loadStuck(), loadVisitors(), loadLeads(), loadSettings(),
    ].map(p => p.catch(() => alertBox('بعضی بخش‌های این صفحه بارگذاری نشد. صفحه را تازه کنید.'))));
}

// ── Wiring ──────────────────────────────────────────────────────────────

// The link and its QR are shown exactly once, right here. Nothing is emailed
// and nothing is texted, so the operator hands it over face to face.
function showNewLink(title, data) {
    document.getElementById('new-visitor-title').textContent = title;
    document.getElementById('new-visitor-qr').innerHTML = data.qr;
    document.getElementById('new-visitor-link').textContent = data.link;
    document.getElementById('new-visitor').classList.remove('d-none');
}

export function initLeads() {
    document.getElementById('add-visitor').addEventListener('click', async () => {
        const input = document.getElementById('visitor-name');
        const data = await post('/admin/api/leads/visitors', { name: input.value.trim() });
        if (!data) return;
        input.value = '';
        showNewLink('لینک اختصاصی این همکار:', data);
        await refresh();
    });

    document.getElementById('edits').addEventListener('click', async (ev) => {
        const btn = ev.target.closest('[data-approve]');
        if (!btn) return;
        const approve = btn.dataset.approve === '1';
        const ask = approve
            ? 'این متن از همین لحظه پاسخی است که چت‌بات به همهٔ بازدیدکننده‌ها می‌دهد. تأیید می‌کنید؟'
            : 'متن رد می‌شود و یک پیامک به مخاطب می‌رود که دوباره متن را بفرستد. ادامه می‌دهید؟';
        if (!confirm(ask)) return;
        btn.disabled = true;
        const id = btn.closest('[data-edit]').dataset.edit;
        const data = await post(`/admin/api/leads/edits/${encodeURIComponent(id)}`, { approve });
        // Rejecting notifies the contact by SMS, and that can fail on its own.
        if (data && data.notified === false) {
            alertBox('متن رد شد، ولی پیامک اطلاع‌رسانی به مخاطب نرفت'
                + (data.notify_error ? `: ${data.notify_error}` : '.'));
        }
        await refresh();
        await loadApproved().catch(() => {});
    });

    document.getElementById('approved').addEventListener('click', async (ev) => {
        const btn = ev.target.closest('[data-revert]');
        if (!btn) return;
        if (!confirm('متن قبلی دوباره روی چت‌بات می‌نشیند و متن تأییدشده برداشته می‌شود. ادامه می‌دهید؟')) return;
        btn.disabled = true;
        const id = btn.closest('[data-edit]').dataset.edit;
        if (await post(`/admin/api/leads/edits/${encodeURIComponent(id)}/revert`)) {
            await refresh();
            await loadApproved().catch(() => {});
        } else {
            btn.disabled = false;
        }
    });

    document.getElementById('approved-toggle').addEventListener('click', async (ev) => {
        const body = document.getElementById('approved-body');
        const opening = body.classList.contains('d-none');
        body.classList.toggle('d-none', !opening);
        ev.currentTarget.textContent = opening ? 'بستن' : 'نمایش';
        if (opening) await loadApproved().catch(() => alertBox('فهرست متن‌های تأییدشده بارگذاری نشد.'));
    });

    document.getElementById('stuck').addEventListener('click', async (ev) => {
        const btn = ev.target.closest('[data-release]');
        if (!btn) return;
        if (!confirm('نام این شرکت به فهرست جستجوی همکاران غرفه برمی‌گردد و لینک قبلی مخاطب از کار می‌افتد. ادامه می‌دهید؟')) return;
        btn.disabled = true;
        const id = btn.closest('[data-lead]').dataset.lead;
        if (await post(`/admin/api/leads/${encodeURIComponent(id)}/release`)) await refresh();
        else btn.disabled = false;
    });

    document.getElementById('visitors').addEventListener('click', async (ev) => {
        const row = ev.target.closest('[data-visitor]');
        if (!row) return;
        const id = row.dataset.visitor;

        const rotate = ev.target.closest('[data-rotate]');
        if (rotate) {
            if (!confirm('لینک قبلی این همکار از همین لحظه از کار می‌افتد و یک لینک تازه ساخته می‌شود. ادامه می‌دهید؟')) return;
            const data = await post(`/admin/api/leads/visitors/${encodeURIComponent(id)}/rotate`);
            if (!data) return;
            showNewLink('لینک تازهٔ این همکار (لینک قبلی دیگر کار نمی‌کند):', data);
            await refresh();
            return;
        }

        const remove = ev.target.closest('[data-remove]');
        if (remove) {
            if (!confirm('این همکار از فهرست بیرون می‌رود و لینک شخصی‌اش از همین لحظه از کار می‌افتد. ثبت‌هایی که انجام داده سر جایشان می‌مانند. ادامه می‌دهید؟')) return;
            remove.disabled = true;
            if (await del(`/admin/api/leads/visitors/${encodeURIComponent(id)}`)) await refresh();
            else remove.disabled = false;
            return;
        }

        const toggle = ev.target.closest('[data-active]');
        if (!toggle) return;
        const turningOff = toggle.dataset.active === '0';
        if (turningOff && !confirm('این همکار از همین لحظه نمی‌تواند ثبت تازه‌ای انجام دهد. ادامه می‌دهید؟')) return;
        await post(`/admin/api/leads/visitors/${encodeURIComponent(id)}/active`,
                   { active: !turningOff });
        await refresh();
    });

    document.getElementById('settings-save').addEventListener('click', async () => {
        const picked = document.querySelector('input[name="channel"]:checked');
        const body = {
            invite_channel: picked ? picked.value : 'qr',
            consent_script: document.getElementById('consent-script').value,
        };
        if (body.invite_channel === 'sms'
            && !confirm('از این لحظه لینک ویرایش با پیامک به مخاطب فرستاده می‌شود و QR نمایش داده نمی‌شود. ادامه می‌دهید؟')) {
            return;
        }
        const data = await post('/admin/api/leads/settings', body);
        if (!data) { note('settings-msg', 'ذخیره نشد.', 'danger'); return; }
        note('settings-msg', 'ذخیره شد.', 'success');
        await loadSettings().catch(() => {});
    });

    refresh();
}
