// AI → Routing page logic: per-task ordered targets with drag-free
// priority buttons (up/down — atomic reorder server-side), plus the kill
// switch. The kill switch reuses the legacy openai_enabled setting so there
// is exactly ONE switch, not two competing ones.
import { fetchAuth, showMsg, escapeHtml } from './utils.js';

// `null`-prototype map: a task named "constructor"/"toString" must not resolve
// to an inherited Object.prototype member and get printed into the page.
const TASK_FA = Object.assign(Object.create(null), {
    chat: 'گفتگو (CHAT)', classify: 'دسته‌بندی (CLASSIFICATION)',
});
// Circuit state per provider instance — same vocabulary as the Providers page.
const CIRCUIT_BADGE = Object.assign(Object.create(null), {
    closed: '<span class="badge bg-success-lt">بسته</span>',
    open: '<span class="badge bg-danger-lt">باز</span>',
    half_open: '<span class="badge bg-warning-lt">نیم‌باز</span>',
});
let routesData = null;
let circuitByInstance = {};   // instance id → circuit_state

export async function initAIRouting() {
    document.getElementById('btn-add-target').onclick = openAdd;
    document.getElementById('btn-save-target').onclick = saveTarget;
    document.getElementById('kill-switch').onchange = toggleKillSwitch;
    await loadAll();
}

async function loadAll() {
    const res = await fetchAuth('/admin/api/ai/routes');
    if (!res.ok) { showMsg('ai-routing-msg', 'خطا در دریافت مسیرها', 'danger'); return; }
    routesData = await res.json();
    await loadCircuits();
    renderRoutes();
    renderKillSwitch();
    fillInstanceSelect();
}

// Circuit state is not carried by /routes, so it is read from the providers
// endpoint (health.provider_rows_for_admin) and joined by instance id. Failing
// to read it must leave the column honestly blank, never fake "closed".
async function loadCircuits() {
    circuitByInstance = {};
    try {
        const res = await fetchAuth('/admin/api/ai/providers');
        if (!res.ok) return;
        for (const p of (await res.json()).providers || []) {
            circuitByInstance[p.id] = p.circuit_state || '';
        }
    } catch { /* leave empty → the column renders «نامعلوم» */ }
}

async function renderKillSwitch() {
    const res = await fetchAuth('/admin/api/settings');
    let enabled = true;
    if (res.ok) {
        const s = await res.json();
        enabled = (s.openai_enabled ?? 'true') === 'true';
    }
    document.getElementById('kill-switch').checked = enabled;
}

async function toggleKillSwitch(e) {
    const enabled = e.target.checked;
    const res = await fetchAuth('/admin/api/toggle_openai', {
        method: 'POST', body: JSON.stringify({ enabled }) });
    if (res.ok) showMsg('ai-routing-msg', enabled ? 'هوش مصنوعی بیرونی فعال شد' : 'هوش مصنوعی بیرونی قطع شد', 'success');
    else { showMsg('ai-routing-msg', 'تغییر ممکن نشد', 'danger'); e.target.checked = !enabled; }
}

function fillInstanceSelect() {
    const sel = document.getElementById('t-instance');
    sel.innerHTML = (routesData.instances || [])
        .map(i => `<option value="${escapeHtml(i.id)}">${escapeHtml(i.display_name)}${i.enabled ? '' : ' (غیرفعال)'}</option>`).join('');
    sel.onchange = fillModelList;
    fillModelList();
}

function fillModelList() {
    const inst = document.getElementById('t-instance').value;
    const list = document.getElementById('t-model-list');
    const models = (routesData.models || []).filter(m => m.provider_instance_id === inst);
    list.innerHTML = models.map(m => `<option value="${escapeHtml(m.model_id)}">`).join('');
}

// Reasoning-effort options, shared by the route header selector. Shown for
// the CHAT route only — classification reasoning is pinned OFF by the engine
// (it bills real money for zero visitor benefit).
const REASONING_FA = Object.assign(Object.create(null), {
    default: 'پیش‌فرض ارائه‌دهنده', off: 'خاموش',
    low: 'کم', medium: 'متوسط', high: 'زیاد',
});

function routeReasoningHeader(task, reasoning) {
    if (task !== 'chat') return '';
    const opts = Object.entries(REASONING_FA)
        .map(([v, fa]) => `<option value="${v}"${v === (reasoning || 'default') ? ' selected' : ''}>${fa}</option>`)
        .join('');
    return `
      <div class="d-flex align-items-center gap-2 ms-auto">
        <label class="form-label mb-0 me-1 small text-muted">سطح تفکر مدل</label>
        <select class="form-select form-select-sm" style="width:auto" data-reasoning="${task}">${opts}</select>
        <button class="btn btn-sm btn-outline-secondary" data-reasoning-test="${task}">آزمایش</button>
        <span class="small text-muted" data-reasoning-state="${task}"></span>
      </div>`;
}

function renderRoutes() {
    const host = document.getElementById('routes-container');
    const tasks = (routesData.routes || []).map(r => r.task);
    host.innerHTML = tasks.map(task => {
        const route = (routesData.routes || []).find(r => r.task === task) || {};
        const targets = (routesData.targets || []).filter(t => t.task === task)
            .sort((a, b) => a.priority - b.priority);
        const rows = targets.map((t, idx) => `
          <tr data-id="${t.id}">
            <td class="text-center">
              <div class="btn-group btn-group-sm" dir="ltr">
                <button class="btn btn-outline-secondary" data-move="up" data-id="${t.id}" ${idx === 0 ? 'disabled' : ''}>▲</button>
                <button class="btn btn-outline-secondary" data-move="down" data-id="${t.id}" ${idx === targets.length - 1 ? 'disabled' : ''}>▼</button>
              </div>
            </td>
            <td class="text-center"><span class="badge bg-primary-lt">${t.priority}</span></td>
            <td>${escapeHtml(t.provider_name || '')}<div dir="ltr" class="text-muted small">${escapeHtml(t.provider_instance_id)}</div></td>
            <td dir="ltr">${escapeHtml(t.model_id)}</td>
            <td>${t.enabled ? '<span class="badge bg-success-lt">فعال</span>' : '<span class="badge bg-secondary-lt">غیرفعال</span>'}</td>
            <td>${CIRCUIT_BADGE[circuitByInstance[t.provider_instance_id]]
                || '<span class="badge bg-secondary-lt">نامعلوم</span>'}</td>
            <td dir="ltr">${t.max_attempts || '—'}</td>
            <td dir="ltr">${t.timeout_s || '—'}</td>
            <td class="text-end text-nowrap">
              <button class="btn btn-sm btn-outline-info" data-circuit="${escapeHtml(t.provider_instance_id)}" title="بازنشانی مدار این سرویس‌دهنده">مدار</button>
              <button class="btn btn-sm btn-outline-warning" data-toggle="${t.id}" data-enabled="${t.enabled}">${t.enabled ? 'از کار انداختن' : 'به کار انداختن'}</button>
              <button class="btn btn-sm btn-outline-danger" data-remove="${t.id}">حذف</button>
            </td>
          </tr>`).join('') ||
            '<tr><td colspan="9" class="text-center text-muted py-3">هدفی تعریف نشده است.</td></tr>';
        return `<div class="col-lg-6">
          <div class="card">
            <div class="card-header d-flex align-items-center">
              <h3 class="card-title">${escapeHtml(TASK_FA[task] || task)}</h3>
              ${routeReasoningHeader(task, route.reasoning)}
            </div>
            <div class="table-responsive">
              <table class="table table-sm card-table">
                <thead><tr><th></th><th>اولویت</th><th>سرویس‌دهنده</th><th>مدل</th>
                  <th>وضعیت</th><th>مدار</th><th>تلاش</th><th>مهلت</th><th></th></tr></thead>
                <tbody>${rows}</tbody>
              </table>
            </div>
          </div></div>`;
    }).join('');
    host.querySelectorAll('[data-move]').forEach(b => b.onclick = () => move(b.dataset.move, Number(b.dataset.id)));
    host.querySelectorAll('[data-toggle]').forEach(b => b.onclick = () => toggleTarget(Number(b.dataset.toggle), b.dataset.enabled === 'true'));
    host.querySelectorAll('[data-remove]').forEach(b => b.onclick = () => removeTarget(Number(b.dataset.remove)));
    host.querySelectorAll('[data-circuit]').forEach(b => b.onclick = () => resetCircuit(b.dataset.circuit));
    host.querySelectorAll('[data-reasoning]').forEach(sel => sel.onchange = () => saveReasoning(sel.dataset.reasoning, sel.value));
    host.querySelectorAll('[data-reasoning-test]').forEach(b => b.onclick = () => testReasoning(b.dataset.reasoningTest));
}

// The reasoning effort saves immediately on change — a dropdown is not a
// form; the change IS the intent. The test button probes what the dropdown
// shows right now (test-before-you-trust), before or after saving.
async function saveReasoning(task, level) {
    const res = await fetchAuth(`/admin/api/ai/routes/${encodeURIComponent(task)}/reasoning`, {
        method: 'POST', body: JSON.stringify({ reasoning: level }) });
    showMsg('ai-routing-msg', res.ok ? 'سطح تفکر ذخیره شد' : 'ذخیرهٔ سطح تفکر ممکن نشد',
            res.ok ? 'success' : 'danger');
}

async function testReasoning(task) {
    const state = document.querySelector(`[data-reasoning-state="${task}"]`);
    const sel = document.querySelector(`[data-reasoning="${task}"]`);
    if (state) state.textContent = 'در حال آزمایش… چند ثانیه طول می‌کشد.';
    try {
        const res = await fetchAuth('/admin/api/ai/reasoning-test', {
            method: 'POST', body: JSON.stringify({ task, level: sel ? sel.value : '' }) });
        const d = await res.json();
        if (state) state.textContent = res.ok ? (d.verdict_fa || 'نتیجه نامشخص') : 'خطا در آزمایش';
    } catch {
        if (state) state.textContent = 'خطای ارتباط با سرور';
    }
}

// Reset the breaker for the provider behind this target. The endpoint is
// CSRF-protected (fetchAuth attaches the token) and audited server-side as
// admin.ai_circuit.reset.
async function resetCircuit(instanceId) {
    if (!confirm('مدار این سرویس‌دهنده بازنشانی شود؟')) return;
    const res = await fetchAuth(
        `/admin/api/ai/providers/${encodeURIComponent(instanceId)}/reset-circuit`,
        { method: 'POST', body: '{}' });
    if (res.ok) { showMsg('ai-routing-msg', 'مدار بازنشانی شد', 'success'); loadAll(); }
    else showMsg('ai-routing-msg', 'بازنشانی مدار ممکن نشد', 'danger');
}

async function move(dir, targetId) {
    const tasks = (routesData.routes || []).map(r => r.task);
    for (const task of tasks) {
        let order = (routesData.targets || []).filter(t => t.task === task)
            .sort((a, b) => a.priority - b.priority).map(t => t.id);
        const idx = order.indexOf(targetId);
        if (idx === -1) continue;
        const swapWith = dir === 'up' ? idx - 1 : idx + 1;
        if (swapWith < 0 || swapWith >= order.length) return;
        [order[idx], order[swapWith]] = [order[swapWith], order[idx]];
        const res = await fetchAuth('/admin/api/ai/routes/reorder', {
            method: 'POST', body: JSON.stringify({ task, ordered_ids: order }) });
        // The server reorders in ONE transaction; on failure nothing moved, so
        // reload to show the real order rather than leaving a stale optimistic view.
        await loadAll();
        showMsg('ai-routing-msg', res.ok ? 'ترتیب ذخیره شد' : 'ذخیرهٔ ترتیب ممکن نشد',
                res.ok ? 'success' : 'danger');
        return;
    }
}

async function toggleTarget(id, enabled) {
    const res = await fetchAuth(`/admin/api/ai/routes/target/${id}/set-enabled`, {
        method: 'POST', body: JSON.stringify({ enabled: !enabled }) });
    if (res.ok) loadAll();
    else showMsg('ai-routing-msg', 'تغییر وضعیت هدف ممکن نشد', 'danger');
}

async function removeTarget(id) {
    if (!confirm('این هدف از مسیر حذف شود؟')) return;
    const res = await fetchAuth(`/admin/api/ai/routes/target/${id}/remove`, { method: 'POST', body: '{}' });
    if (res.ok) { showMsg('ai-routing-msg', 'حذف شد', 'success'); loadAll(); }
    else showMsg('ai-routing-msg', 'حذف ممکن نشد', 'danger');
}

function openAdd() {
    ['t-model', 't-attempts', 't-timeout'].forEach(id => document.getElementById(id).value = '');
    fillModelList();
    new bootstrap.Modal('#target-modal').show();
}

async function saveTarget() {
    const res = await fetchAuth('/admin/api/ai/routes/target', {
        method: 'POST',
        body: JSON.stringify({
            task: document.getElementById('t-task').value,
            instance_id: document.getElementById('t-instance').value,
            model_id: document.getElementById('t-model').value.trim(),
            max_attempts: document.getElementById('t-attempts').value || null,
            timeout_s: document.getElementById('t-timeout').value || null,
        }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { showMsg('target-form-msg', data.detail || 'خطا', 'danger'); return; }
    bootstrap.Modal.getInstance(document.getElementById('target-modal')).hide();
    showMsg('ai-routing-msg', 'هدف افزوده شد', 'success');
    loadAll();
}
