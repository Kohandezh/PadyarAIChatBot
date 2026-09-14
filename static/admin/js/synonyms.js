import { getSynonyms, setSynonyms } from './state.js';
import { fetchAuth, showMsg, escapeHtml, initBulkSelection } from './utils.js';
import { createPager } from './pager.js';

let bulkSelection = null;
let synonymsPager = null;
let suggestModal = null;

async function loadSynonyms() {
    const res = await fetchAuth('/api/synonyms');
    if (!res.ok) return;
    const data = await res.json();
    setSynonyms(data.synonyms);
    synonymsPager.reset();
    renderSynonymsTable(data.synonyms);
}

// A synonym has no single id — its identity is the (source, target) pair — so
// the row checkbox carries both words JSON-encoded into its value. The browser
// decodes the escaped attribute back to a plain JSON string by the time
// getSelected() reads .value, so this round-trips Persian text safely.
function pairValue(s) {
    return escapeHtml(JSON.stringify({ source: s.source, target: s.target }));
}

function renderSynonymsTable(synonyms) {
    const tbody = document.getElementById('synonyms-table');
    const { offset, limit } = synonymsPager.state;
    const page = synonyms.slice(offset, offset + limit);
    if (!page.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-3 text-muted">موردی یافت نشد</td></tr>';
    } else {
        // One row per mapping. A word with three synonyms is three rows, each with
        // its own delete button, so the operator can remove exactly one of them.
        tbody.innerHTML = page.map(s => `
            <tr>
                <td><input type="checkbox" class="form-check-input row-check" value="${pairValue(s)}"></td>
                <td class="ps-4 fw-bold">${escapeHtml(s.source)}</td>
                <td class="text-muted">${escapeHtml(s.target)}</td>
                <td>
                    <button class="btn btn-sm btn-outline-danger" data-source="${escapeHtml(s.source)}" data-target="${escapeHtml(s.target)}"><i class="fas fa-trash"></i></button>
                </td>
            </tr>
        `).join('');
    }
    if (bulkSelection) bulkSelection.clear();
    synonymsPager.setResult({ shown: page.length, total: synonyms.length });
}

async function deleteSynonym(source, target) {
    if (!confirm(`آیا از حذف مترادف «${target}» برای «${source}» مطمئن هستید؟`)) return;
    const url = '/api/synonyms/' + encodeURIComponent(source)
        + '?target=' + encodeURIComponent(target);
    const res = await fetchAuth(url, { method: 'DELETE' });
    if (res.ok) {
        showMsg('synonym-msg', '✅ مترادف حذف شد', 'success');
        loadSynonyms();
    } else {
        showMsg('synonym-msg', '❌ خطا در حذف', 'danger');
    }
}

async function bulkDeleteSynonyms() {
    const pairs = bulkSelection.getSelected().map(v => JSON.parse(v));
    if (pairs.length === 0) return;
    if (!confirm(`آیا از حذف ${pairs.length} مترادف انتخاب‌شده مطمئن هستید؟ این عمل قابل بازگشت نیست.`)) return;

    const res = await fetchAuth('/api/synonyms/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pairs })
    });
    if (res.ok) {
        showMsg('synonym-msg', '✅ موارد انتخاب‌شده حذف شدند', 'success');
        loadSynonyms();
    } else {
        const data = await res.json().catch(() => ({}));
        showMsg('synonym-msg', '❌ خطا: ' + (data.detail || 'عملیات ناموفق'), 'danger');
    }
}

// ── Smart suggestions ────────────────────────────────────────────────────
// The model proposes (one paid call, cooldown on the server), the operator
// picks rows, and apply writes them through the same path as the manual add
// form. Suggest itself never stores anything.

function suggestMsg(text, type) {
    const el = document.getElementById('suggest-list-msg');
    el.className = `text-center fw-bold mb-2${type ? ' text-' + type : ''}`;
    el.innerText = text;
}

async function suggestSynonyms() {
    const btn = document.getElementById('suggest-synonyms-btn');
    const list = document.getElementById('suggest-list');
    btn.disabled = true;
    list.innerHTML = '';
    suggestMsg('⏳ در حال گرفتن پیشنهاد…');
    suggestModal.show();
    try {
        const res = await fetchAuth('/admin/api/synonyms/suggest', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
            suggestMsg('❌ ' + (data.detail || 'خطا در گرفتن پیشنهاد'), 'danger');
            return;
        }
        const items = data.suggestions || [];
        if (!items.length) {
            suggestMsg('پیشنهاد تازه‌ای پیدا نشد.', 'muted');
            return;
        }
        suggestMsg('موارد موردنظر را انتخاب کنید.', 'muted');
        // The checkbox value carries the pair JSON-encoded, same round-trip
        // as the bulk-delete table rows — Persian text survives the escape.
        list.innerHTML = items.map(s => `
            <label class="list-group-item d-flex gap-2 align-items-start">
                <input type="checkbox" class="form-check-input mt-1 suggest-check" checked
                       value="${pairValue({ source: s.word, target: s.suggestion })}">
                <span>
                    <span class="fw-bold">${escapeHtml(s.word)}</span>
                    <i class="fas fa-arrow-left mx-1 text-muted"></i>${escapeHtml(s.suggestion)}
                    <small class="d-block text-muted">${escapeHtml(s.reason)}</small>
                </span>
            </label>
        `).join('');
    } catch {
        suggestMsg('❌ خطای ارتباط با سرور', 'danger');
    } finally {
        btn.disabled = false;
    }
}

async function applySuggestedSynonyms() {
    const pairs = Array.from(document.querySelectorAll('.suggest-check:checked'))
        .map(cb => JSON.parse(cb.value))
        .map(p => ({ word: p.source, suggestion: p.target }));
    if (!pairs.length) {
        suggestMsg('حداقل یک مورد را انتخاب کنید.', 'danger');
        return;
    }
    const btn = document.getElementById('suggest-apply-btn');
    btn.disabled = true;
    try {
        const res = await fetchAuth('/admin/api/synonyms/suggest/apply', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pairs })
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            suggestModal.hide();
            const msg = data.added
                ? `✅ ${data.added} مترادف اضافه شد`
                : 'انتخاب‌شده‌ها از قبل موجود بودند';
            showMsg('synonym-msg', msg, 'success');
            loadSynonyms();
        } else {
            suggestMsg('❌ ' + (data.detail || 'خطا در افزودن'), 'danger');
        }
    } catch {
        suggestMsg('❌ خطای ارتباط با سرور', 'danger');
    } finally {
        btn.disabled = false;
    }
}

export function initSynonyms() {
    bulkSelection = initBulkSelection({
        selectAllEl: document.getElementById('synonyms-select-all'),
        toolbarEl: document.getElementById('synonyms-bulk-toolbar'),
        countEl: document.getElementById('synonyms-bulk-count'),
    });
    bulkSelection.attach(document.getElementById('synonyms-table'));

    synonymsPager = createPager({
        pageSizeEl: document.getElementById('synonyms-page-size'),
        prevBtnEl: document.getElementById('synonyms-btn-prev'),
        nextBtnEl: document.getElementById('synonyms-btn-next'),
        rangeEl: document.getElementById('synonyms-range'),
        defaultLimit: 25,
        onPage: () => renderSynonymsTable(getSynonyms()),
    });
    loadSynonyms();

    suggestModal = new bootstrap.Modal(document.getElementById('suggestModal'));
    document.getElementById('suggest-synonyms-btn')
        .addEventListener('click', suggestSynonyms);
    document.getElementById('suggest-apply-btn')
        .addEventListener('click', applySuggestedSynonyms);

    // Delegated so the buttons carry both words as data attributes. An inline
    // onclick would have to embed two pieces of Persian text in a JS string.
    document.getElementById('synonyms-table').addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-source]');
        if (btn) deleteSynonym(btn.dataset.source, btn.dataset.target);
    });

    // Synonym form submit
    document.getElementById('synonym-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const source = document.getElementById('synonym-source').value.trim();
        const target = document.getElementById('synonym-target').value.trim();
        if (!source || !target) return;

        try {
            const res = await fetchAuth('/api/synonyms', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ source, target })
            });
            if (res.ok) {
                showMsg('synonym-msg', '✅ مترادف اضافه شد', 'success');
                document.getElementById('synonym-source').value = '';
                document.getElementById('synonym-target').value = '';
                loadSynonyms();
            } else {
                const data = await res.json();
                showMsg('synonym-msg', '❌ ' + (data.detail || 'خطا'), 'danger');
            }
        } catch {
            showMsg('synonym-msg', '❌ خطای ارتباط با سرور', 'danger');
        }
    });

    // Expose for inline onclick in templates
    window.loadSynonyms = loadSynonyms;
    window.bulkDeleteSynonyms = bulkDeleteSynonyms;
}
