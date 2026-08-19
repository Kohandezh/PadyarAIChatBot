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
