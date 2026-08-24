import { getSynonyms, setSynonyms } from './state.js';
import { fetchAuth, showMsg, escapeHtml } from './utils.js';

async function loadSynonyms() {
    const res = await fetchAuth('/api/synonyms');
    if (!res.ok) return;
    const data = await res.json();
    setSynonyms(data.synonyms);
    renderSynonymsTable(data.synonyms);
}

function renderSynonymsTable(synonyms) {
    const tbody = document.getElementById('synonyms-table');
    if (!synonyms.length) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center py-3 text-muted">موردی یافت نشد</td></tr>';
        return;
    }
    // One row per mapping. A word with three synonyms is three rows, each with
    // its own delete button, so the operator can remove exactly one of them.
    tbody.innerHTML = synonyms.map(s => `
        <tr>
            <td class="ps-4 fw-bold">${escapeHtml(s.source)}</td>
            <td class="text-muted">${escapeHtml(s.target)}</td>
            <td>
                <button class="btn btn-sm btn-outline-danger" data-source="${escapeHtml(s.source)}" data-target="${escapeHtml(s.target)}"><i class="fas fa-trash"></i></button>
            </td>
        </tr>
    `).join('');
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

export function initSynonyms() {
    loadSynonyms();

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
}
