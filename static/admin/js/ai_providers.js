// AI → Providers page logic. Provider-originated text is ALWAYS passed
// through escapeHtml() before insertion — provider responses are untrusted
// external input and must never reach the DOM raw.
import { fetchAuth, showMsg, escapeHtml } from './utils.js';

let TYPES = [];

const HEALTH_BADGE = {
    healthy:  '<span class="badge bg-success-lt">سالم</span>',
    degraded: '<span class="badge bg-warning-lt">افت‌کرده</span>',
    down:     '<span class="badge bg-danger-lt">قطع</span>',
    unknown:  '<span class="badge bg-secondary-lt">نامعلوم</span>',
    disabled: '<span class="badge bg-secondary-lt">غیرفعال</span>',
};
const CIRCUIT_BADGE = {
    closed:    '<span class="badge bg-success-lt">بسته</span>',
    open:      '<span class="badge bg-danger-lt">باز</span>',
    half_open: '<span class="badge bg-warning-lt">نیم‌باز</span>',
};
const STATUS_FA = {
    connected: 'متصل', authentication_failed: 'اعتبارنامه رد شد',
    permission_denied: 'دسترسی ندارد', rate_limited: 'محدودیت نرخ',
    quota_exceeded: 'سهمیه تمام شده', model_not_found: 'مدل یافت نشد',
    timeout: 'مهلت تمام شد', unreachable: 'در دسترس نیست',
    invalid_config: 'پیکربندی نامعتبر', provider_error: 'خطای سرویس‌دهنده',
    requires_documentation: 'در انتظار مستندات',
};

export async function initAIProviders() {
    await loadTypes();
    await loadProviders();
    document.getElementById('btn-add-provider').onclick = openAdd;
    document.getElementById('btn-save-provider').onclick = saveProvider;
}

async function loadTypes() {
    const res = await fetchAuth('/admin/api/ai/provider-types');
    if (!res.ok) return;
    TYPES = (await res.json()).types || [];
    const sel = document.getElementById('inp-type');
    sel.innerHTML = TYPES.map(t =>
        `<option value="${escapeHtml(t.type)}">${escapeHtml(t.display_name)}</option>`).join('');
    sel.onchange = renderConfigFields;
}

function renderConfigFields() {
    const type = TYPES.find(t => t.type === document.getElementById('inp-type').value);
    if (!type) return;
    document.getElementById('type-note').textContent = type.note || '';
    const host = document.getElementById('config-fields');
    host.innerHTML = type.config_schema
        .filter(f => f.key !== 'api_key')
        .map(f => {
            if (f.type === 'enum' && f.options.length) {
                return `<div class="mb-3"><label class="form-label">${escapeHtml(f.label)}</label>
                  <select class="form-select" data-cfg="${escapeHtml(f.key)}">${
                    f.options.map(o => `<option value="${escapeHtml(o.value)}">${escapeHtml(o.label)}</option>`).join('')
                  }</select>
                  ${f.help ? `<div class="form-text">${escapeHtml(f.help)}</div>` : ''}</div>`;
            }
            return `<div class="mb-3"><label class="form-label">${escapeHtml(f.label)}</label>
              <input class="form-control" data-cfg="${escapeHtml(f.key)}" dir="ltr"
                value="${escapeHtml(f.default || '')}" ${f.required ? 'required' : ''}>
              ${f.help ? `<div class="form-text">${escapeHtml(f.help)}</div>` : ''}</div>`;
        }).join('');
}

let editingId = null;

function openAdd() {
    editingId = null;
    document.getElementById('provider-modal-title').textContent = 'افزودن سرویس‌دهنده';
    document.getElementById('fld-type').style.display = '';
    document.getElementById('inp-display-name').value = '';
    document.getElementById('inp-api-key').value = '';
    document.getElementById('key-note').textContent = 'برای نمونه‌های جدید خالی نگذارید.';
    document.getElementById('inp-notes').value = '';
    document.getElementById('inp-trust').value = 'public';
    renderConfigFields();
    new bootstrap.Modal('#provider-modal').show();
}

function openEdit(p) {
    editingId = p.id;
    document.getElementById('provider-modal-title').textContent = `ویرایش «${p.display_name}»`;
    document.getElementById('fld-type').style.display = 'none';
    document.getElementById('inp-display-name').value = p.display_name;
    document.getElementById('inp-api-key').value = '';
    document.getElementById('key-note').textContent = p.has_secret
        ? 'کلید ذخیره شده است — خالی بگذارید تا بی‌ تغییر بماند.'
        : 'کلیدی ذخیره نشده است.';
    document.getElementById('inp-notes').value = p.notes || '';
    document.getElementById('inp-trust').value = p.trust_class;
    renderConfigFieldsFor(p.provider_type, p.config);
    new bootstrap.Modal('#provider-modal').show();
}

function renderConfigFieldsFor(typeKey, cfg) {
    const t = TYPES.find(x => x.type === typeKey);
    const host = document.getElementById('config-fields');
    if (!t) { host.innerHTML = ''; return; }
    host.innerHTML = t.config_schema.filter(f => f.key !== 'api_key').map(f => {
        const val = (cfg && cfg[f.key]) ?? f.default ?? '';
        if (f.type === 'enum' && f.options.length) {
            return `<div class="mb-3"><label class="form-label">${escapeHtml(f.label)}</label>
              <select class="form-select" data-cfg="${escapeHtml(f.key)}">${
                f.options.map(o => `<option value="${escapeHtml(o.value)}" ${o.value === val ? 'selected' : ''}>${escapeHtml(o.label)}</option>`).join('')
              }</select></div>`;
        }
        return `<div class="mb-3"><label class="form-label">${escapeHtml(f.label)}</label>
          <input class="form-control" data-cfg="${escapeHtml(f.key)}" dir="ltr" value="${escapeHtml(String(val))}"></div>`;
    }).join('');
}

function collectConfig() {
    const cfg = {};
    document.querySelectorAll('#config-fields [data-cfg]').forEach(el => {
        cfg[el.dataset.cfg] = el.value.trim();
    });
    return cfg;
}

async function saveProvider() {
    const apiKey = document.getElementById('inp-api-key').value;
    const body = {
        display_name: document.getElementById('inp-display-name').value.trim(),
        trust_class: document.getElementById('inp-trust').value,
        notes: document.getElementById('inp-notes').value,
        config: collectConfig(),
    };
    const url = editingId
        ? `/admin/api/ai/providers/${encodeURIComponent(editingId)}/update`
        : '/admin/api/ai/providers';
    if (!editingId) {
        body.provider_type = document.getElementById('inp-type').value;
        body.api_key = apiKey;
    } else if (apiKey) {
        body.api_key = apiKey;
    }
    const res = await fetchAuth(url, { method: 'POST', body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showMsg('provider-form-msg', data.detail || 'خطا در ذخیره', 'danger'); return; }
    bootstrap.Modal.getInstance(document.getElementById('provider-modal')).hide();
    showMsg('ai-msg', 'ذخیره شد', 'success');
    loadProviders();
}

async function loadProviders() {
    const res = await fetchAuth('/admin/api/ai/providers');
    if (!res.ok) { showMsg('ai-msg', 'خطا در دریافت فهرست', 'danger'); return; }
    const { providers } = await res.json();
    const body = document.getElementById('providers-body');
    if (!providers.length) {
        body.innerHTML = '<tr><td colspan="11" class="text-center text-muted py-4">هنوز سرویس‌دهنده‌ای تعریف نشده است.</td></tr>';
        return;
    }
    body.innerHTML = providers.map(p => `
      <tr>
        <td>${escapeHtml(p.display_name)}</td>
        <td dir="ltr" class="text-muted small">${escapeHtml(p.id)}</td>
        <td>${p.enabled ? '<span class="badge bg-success-lt">فعال</span>' : '<span class="badge bg-secondary-lt">غیرفعال</span>'}</td>
        <td>${HEALTH_BADGE[p.health] || ''}</td>
        <td dir="ltr" class="small">${(p.chat_models || []).map(escapeHtml).join('<br>') || '—'}</td>
        <td dir="ltr" class="small">${(p.classify_models || []).map(escapeHtml).join('<br>') || '—'}</td>
        <td dir="ltr">${p.requests_24h || 0}</td>
        <td dir="ltr">${p.error_rate_24h == null ? '—' : Math.round(p.error_rate_24h * 100) + '٪'}</td>
        <td>${CIRCUIT_BADGE[p.circuit_state] || ''}</td>
        <td dir="ltr" class="small text-muted">${escapeHtml(p.last_success_at || '—')}</td>
        <td class="text-end text-nowrap">
          <button class="btn btn-sm btn-outline-primary" data-act="test" data-id="${escapeHtml(p.id)}">آزمون</button>
          <button class="btn btn-sm btn-outline-secondary" data-act="edit" data-id="${escapeHtml(p.id)}">ویرایش</button>
          <button class="btn btn-sm ${p.enabled ? 'btn-outline-warning' : 'btn-outline-success'}" data-act="toggle" data-id="${escapeHtml(p.id)}" data-enabled="${p.enabled}">${p.enabled ? 'غیرفعال' : 'فعال'}</button>
          <button class="btn btn-sm btn-outline-info" data-act="circuit" data-id="${escapeHtml(p.id)}" title="بازنشانی مدار">مدار</button>
          <button class="btn btn-sm btn-outline-danger" data-act="delete" data-id="${escapeHtml(p.id)}">حذف</button>
        </td>
      </tr>`).join('');
    body.querySelectorAll('button').forEach(btn => {
        btn.onclick = () => handleAction(btn.dataset.act, btn.dataset.id, btn.dataset.enabled === 'true');
    });
}

async function handleAction(act, id, enabled) {
    const enc = encodeURIComponent(id);
    if (act === 'test') {
        const res = await fetchAuth(`/admin/api/ai/providers/${enc}/test`, { method: 'POST', body: '{}' });
        const data = await res.json();
        const ok = data.ok;
        document.getElementById('test-result').innerHTML = ok
            ? '<span class="badge bg-success-lt p-2">متصل</span>'
            : `<span class="badge bg-danger-lt p-2">${escapeHtml(STATUS_FA[data.status] || data.status || 'ناموفق')}</span>`;
        document.getElementById('test-detail').textContent = `${data.detail || ''} (${data.latency_ms || 0}ms)`;
        new bootstrap.Modal('#test-modal').show();
        loadProviders();
    } else if (act === 'edit') {
        const res = await fetchAuth(`/admin/api/ai/providers/${enc}`);
        if (res.ok) openEdit((await res.json()).provider);
    } else if (act === 'toggle') {
        const res = await fetchAuth(`/admin/api/ai/providers/${enc}/set-enabled`, {
            method: 'POST', body: JSON.stringify({ enabled: !enabled }) });
        if (res.ok) { showMsg('ai-msg', 'تغییر وضعیت ثبت شد', 'success'); loadProviders(); }
    } else if (act === 'circuit') {
        const res = await fetchAuth(`/admin/api/ai/providers/${enc}/reset-circuit`, { method: 'POST', body: '{}' });
        if (res.ok) { showMsg('ai-msg', 'مدار بازنشانی شد', 'success'); loadProviders(); }
    } else if (act === 'delete') {
        const confirmText = prompt(`برای حذف، شناسهٔ نمونه را دقیقاً تایپ کنید: ${id}`);
        if (confirmText !== id) return;
        const res = await fetchAuth(`/admin/api/ai/providers/${enc}/delete`, {
            method: 'POST', body: JSON.stringify({ confirm: confirmText }) });
        const data = await res.json().catch(() => ({}));
        if (res.ok) { showMsg('ai-msg', 'حذف شد', 'success'); loadProviders(); }
        else showMsg('ai-msg', data.detail || 'حذف ممکن نیست', 'danger');
    }
}
