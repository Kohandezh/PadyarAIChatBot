// AI → Models page logic.
// Every value interpolated into innerHTML goes through escapeHtml() — model
// ids and display names come from the vendor's discovery API (or an admin
// typing a manual entry) and are untrusted. `source`/`status` are DB
// CHECK-constrained enums, but they are escaped too so the guarantee lives
// here rather than depending on a constraint in another file.
import { fetchAuth, showMsg, escapeHtml } from './utils.js';

// escapeHtml() only handles strings; numbers/null reach it from JSON.
const esc = (v) => (v === null || v === undefined) ? '' : escapeHtml(String(v));

const SOURCE_FA = { bootstrap: 'راه‌انداز', discovered: 'کشف‌شده', manual: 'دستی' };
const STATUS_FA = { available: 'موجود', preview: 'پیش‌نمایش', deprecated: 'منسوخ‌شده',
                    legacy: 'قدیمی', unavailable: 'از رده خارج', unknown: 'نامعلوم', manual: 'دستی' };
let instances = [];
let current = '';

export async function initAIModels() {
    loadOwnModel();
    document.getElementById('btn-add-model').onclick = openAdd;
    document.getElementById('btn-save-model').onclick = saveModel;
    document.getElementById('btn-refresh-models').onclick = refreshCatalog;
    const res = await fetchAuth('/admin/api/ai/routes');
    if (!res.ok) {
        showMsg('ai-models-msg', 'خطا در دریافت فهرست نمونه‌ها', 'danger');
        document.getElementById('models-body').innerHTML =
            '<tr><td colspan="9" class="text-center text-muted py-4">فهرست نمونه‌ها بارگذاری نشد.</td></tr>';
        return;
    }
    instances = (await res.json()).instances || [];
    const sel = document.getElementById('sel-instance');
    sel.innerHTML = instances.map(i =>
        `<option value="${esc(i.id)}">${esc(i.display_name)}</option>`).join('')
        || '<option value="">— نمونه‌ای نیست —</option>';
    sel.onchange = () => { current = sel.value; loadModels(); };
    current = sel.value;
    loadModels();
}

async function loadModels() {
    const body = document.getElementById('models-body');
    if (!current) { body.innerHTML = '<tr><td colspan="9" class="text-center text-muted py-4">—</td></tr>'; return; }
    const [modelsRes, pricingRes] = await Promise.all([
        fetchAuth(`/admin/api/ai/models?instance_id=${encodeURIComponent(current)}`),
        fetchAuth(`/admin/api/ai/providers/${encodeURIComponent(current)}`),
    ]);
    let pricing = {};
    if (pricingRes.ok) pricing = (await pricingRes.json()).pricing || {};
    const models = modelsRes.ok ? (await modelsRes.json()).models : [];
    body.innerHTML = models.map(m => {
        const p = pricing[m.model_id];
        const price = p
            ? `$${esc(p.input_per_million)} / $${esc(p.output_per_million)}`
            : '<span class="text-muted">ناموجود</span>';
        const caps = [
            m.supports_reasoning && 'استدلال', m.supports_tools && 'ابزار',
            m.supports_structured && 'ساخت‌یافته', m.supports_vision && 'تصویر',
        ].filter(Boolean).join('، ') || '—';
        return `<tr>
          <td dir="ltr">${esc(m.model_id)}</td>
          <td>${esc(m.display_name)}</td>
          <td><span class="badge bg-secondary-lt">${esc(SOURCE_FA[m.source] || m.source)}</span></td>
          <td><span class="badge ${m.status === 'available' ? 'bg-success-lt' : 'bg-warning-lt'}">${esc(STATUS_FA[m.status] || m.status)}</span></td>
          <td dir="ltr">${m.context_window ? esc(Number(m.context_window).toLocaleString()) : '—'}</td>
          <td dir="ltr">${m.max_output_tokens ? esc(Number(m.max_output_tokens).toLocaleString()) : '—'}</td>
          <td class="small">${esc(caps)}</td>
          <td dir="ltr" class="small">${price}</td>
          <td class="text-end"><button class="btn btn-sm btn-outline-danger" data-id="${esc(m.id)}">حذف</button></td>
        </tr>`;
    }).join('') || '<tr><td colspan="9" class="text-center text-muted py-4">مدلی ثبت نشده است.</td></tr>';
    body.querySelectorAll('button').forEach(b => {
        b.onclick = async () => {
            if (!confirm('این مدل از کاتالوگ حذف شود؟ تاریخ مصرف و لاگ‌ها دست‌نخورده می‌مانند.')) return;
            const res = await fetchAuth('/admin/api/ai/models/delete', {
                method: 'POST', body: JSON.stringify({ id: Number(b.dataset.id) }) });
            if (res.ok) { showMsg('ai-models-msg', 'حذف شد', 'success'); loadModels(); }
        };
    });
}

function openAdd() {
    if (!current) { showMsg('ai-models-msg', 'نمونه‌ای انتخاب نشده است', 'danger'); return; }
    ['m-model-id', 'm-display', 'm-ctx', 'm-maxout'].forEach(id => document.getElementById(id).value = '');
    new bootstrap.Modal('#model-modal').show();
}

async function saveModel() {
    const res = await fetchAuth('/admin/api/ai/models/manual', {
        method: 'POST',
        body: JSON.stringify({
            instance_id: current,
            model_id: document.getElementById('m-model-id').value.trim(),
            display_name: document.getElementById('m-display').value.trim(),
            context_window: document.getElementById('m-ctx').value || null,
            max_output_tokens: document.getElementById('m-maxout').value || null,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showMsg('model-form-msg', data.detail || 'خطا', 'danger'); return; }
    bootstrap.Modal.getInstance(document.getElementById('model-modal')).hide();
    showMsg('ai-models-msg', 'مدل دستی افزوده شد', 'success');
    loadModels();
}

async function refreshCatalog() {
    if (!current) return;
    showMsg('ai-models-msg', 'در حال به‌روزرسانی…', 'muted');
    const res = await fetchAuth('/admin/api/ai/models/refresh', {
        method: 'POST', body: JSON.stringify({ instance_id: current }) });
    const data = await res.json().catch(() => ({}));
    if (data.ok) showMsg('ai-models-msg',
        `انجام شد: ${data.added} جدید، ${data.updated} به‌روز، ${data.unavailable} از رده خارج`, 'success');
    else showMsg('ai-models-msg', data.detail || 'ناموفق', 'warning');
    loadModels();
}

// ── This install's own trained model (read-only card) ──────────────────
// Plain words first, for staff with no technical background. Every value
// from the JSON goes through esc(): the sidecar is a file on disk that
// anyone with write access to the directory could edit.

const fa = (n) => Number(n).toLocaleString('fa-IR');

function formatDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString('fa-IR', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
    });
}

// null means "not measured" (too few questions to hold some out). It must
// read as words, never as 0 or NaN, which would be a false statement.
function accuracyOutOf100(accuracy) {
    return (accuracy === null || accuracy === undefined) ? null : Math.round(accuracy * 100);
}

function accuracySentence(m) {
    const score = accuracyOutOf100(m.holdout_accuracy);
    if (score === null) {
        return 'دقت اندازه گرفته نشد، چون پرسش کافی برای آزمون نبود.';
    }
    // intent.train() measures a probe model on a held-out slice, then trains
    // the served model on ALL questions, that slice included. Say exactly that.
    return `در آزمون، از هر ${esc(fa(100))} پرسش حدود ${esc(fa(score))} پرسش درست تشخیص داده شد. `
        + `برای این آزمون ${esc(fa(m.holdout_size))} پرسش کنار گذاشته شد و یک مدل آزمایشی روی بقیهٔ پرسش‌ها آموزش دید. `
        + 'مدلی که الان کار می‌کند بعد از آزمون روی همهٔ پرسش‌ها آموزش دیده است.';
}

// The history never decides the card's state. A file that cannot be read
// hides only the table; skipped rows are named in one line under it.
function historySection(rows, status) {
    if (status === 'unreadable') {
        return '<p class="text-muted small mt-4 mb-0" id="own-model-history-note">تاریخچهٔ نسخه‌ها خوانده نشد.</p>';
    }
    const note = status === 'partial'
        ? '<p class="text-muted small mb-0" id="own-model-history-note">بعضی خط‌های تاریخچه خوانده نشد و اینجا نیامده است.</p>'
        : '';
    if (!rows.length) return note ? `<div class="mt-4">${note}</div>` : '';
    const body = rows.map(r => {
        const score = accuracyOutOf100(r.holdout_accuracy);
        const acc = score === null
            ? '<span class="text-muted">اندازه گرفته نشد</span>'
            : `${esc(fa(score))} از ${esc(fa(100))}`;
        return `<tr><td>${esc(fa(r.model_version))}</td><td>${esc(formatDate(r.trained_at))}</td><td>${acc}</td></tr>`;
    }).join('');
    return `<h4 class="mt-4 mb-2">نسخه‌های اخیر</h4>
      <div class="table-responsive"><table class="table table-sm card-table" id="own-model-history">
        <thead><tr><th>نسخه</th><th>تاریخ آموزش</th><th>دقت در آزمون</th></tr></thead>
        <tbody>${body}</tbody></table></div>${note}`;
}

function technicalDetails(m) {
    const rows = [
        ['شناسهٔ فایل وزن‌ها (sha256)', m.model_sha256],
        ['اثر انگشت دادهٔ آموزش', m.training_fingerprint],
        ['مدل embedding', m.embedding_model_name],
        ['نسخهٔ مدل embedding', m.embedding_model_revision],
        ['scikit-learn', m.scikit_learn_version],
        ['numpy', m.numpy_version],
    ].filter(([, value]) => value !== null && value !== undefined).map(([label, value]) => `<tr><th class="text-muted fw-normal">${esc(label)}</th>`
        + `<td><code dir="ltr" class="text-break">${esc(value)}</code></td></tr>`).join('');
    return `<details class="mt-4" id="own-model-tech">
      <summary class="text-muted small" style="cursor: pointer;">جزئیات فنی</summary>
      <div class="table-responsive mt-2"><table class="table table-sm"><tbody>${rows}</tbody></table></div>
    </details>`;
}

function fact(title, value) {
    return `<div class="datagrid-item"><div class="datagrid-title">${esc(title)}</div>`
        + `<div class="datagrid-content">${esc(value)}</div></div>`;
}

function renderOwnModel(data) {
    const card = document.getElementById('own-model-card');
    const stateLine = document.getElementById('own-model-state');
    const body = document.getElementById('own-model-body');
    const state = data && data.state;
    const history = (data && Array.isArray(data.history)) ? data.history : [];
    const historyStatus = data && data.history_status;
    card.dataset.state = state || 'error';
    stateLine.className = 'mb-0 fw-bold';

    // "not_saved": the operator set no model directory, so there is no
    // version, date or history to show. The facts come from memory and are
    // real; nothing failed, so there is no warning.
    if ((state === 'trained' || state === 'not_saved') && data.model) {
        const m = data.model;
        const saved = state === 'trained';
        stateLine.classList.add('text-success');
        stateLine.textContent = 'این نصب مدل آموزش‌دیدهٔ خودش را دارد.';
        body.innerHTML = `
          <p class="text-muted mt-1">این مدل روی پرسش‌های پایگاه دانش همین نصب آموزش دیده است.</p>
          ${saved ? '' : '<p class="text-muted mb-0" id="own-model-not-saved">این نصب نسخه‌ای از مدل را روی دیسک نگه نمی‌دارد، پس شمارهٔ نسخه و تاریخچه ندارد.</p>'}
          <div class="datagrid mt-3">
            ${saved ? fact('نسخه', fa(m.model_version)) : ''}
            ${saved ? fact('تاریخ آموزش', formatDate(m.trained_at)) : ''}
            ${fact('تعداد پرسش‌های آموزش', fa(m.sample_count))}
            ${fact('تعداد موضوع‌ها', fa(m.class_count))}
          </div>
          <p class="mt-3 mb-0" id="own-model-accuracy">${accuracySentence(m)}</p>
          ${saved ? historySection(history, historyStatus) : ''}
          ${technicalDetails(m)}`;
        return;
    }
    if (state === 'none') {
        const needs = data.needs || {};
        stateLine.textContent = 'این نصب فعلاً مدل آموزش‌دیده ندارد.';
        body.innerHTML = `
          <p class="text-muted mt-1 mb-0">برای ساخت مدل دست‌کم ${esc(fa(needs.questions))} پرسش
          در ${esc(fa(needs.topics))} موضوع لازم است.</p>
          ${historySection(history, historyStatus)}`;
        return;
    }
    if (state === 'unreadable') {
        stateLine.classList.add('text-warning');
        stateLine.textContent = 'پروندهٔ ذخیره‌شدهٔ مدل خوانده نشد یا با مدلی که الان کار می‌کند یکی نیست. چت‌بات همچنان پاسخ می‌دهد.';
        body.innerHTML = historySection(history, historyStatus);
        return;
    }
    stateLine.classList.add('text-danger');
    stateLine.textContent = 'اطلاعات مدل بارگذاری نشد.';
    body.innerHTML = '';
}

async function loadOwnModel() {
    let data = null;
    try {
        const res = await fetchAuth('/admin/api/ai/own-model');
        if (res.ok) data = await res.json();
    } catch { /* rendered below as "could not load" */ }
    renderOwnModel(data);
}
