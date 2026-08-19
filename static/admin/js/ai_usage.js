// AI → Usage & Costs page logic. Every number is aggregated by SQL on the
// server (`/admin/api/ai/usage` returns GROUP BY rows plus a P95, never a raw
// event dump); this page only renders and rolls the handful of group rows up
// into the header tiles. Unknown pricing renders as «ناموجود», never zero.
import { fetchAuth, showMsg, escapeHtml } from './utils.js';

export async function initAIUsage() {
    document.getElementById('sel-days').onchange = onRangeChange;
    document.getElementById('custom-from').onchange = load;
    document.getElementById('sel-group').onchange = load;
    await load();
}

function onRangeChange() {
    const custom = document.getElementById('sel-days').value === 'custom';
    const from = document.getElementById('custom-from');
    from.classList.toggle('d-none', !custom);
    if (custom && !from.value) {
        // Default the custom range to the last 90 days rather than querying an
        // empty window the moment the operator picks "custom".
        const d = new Date();
        d.setDate(d.getDate() - 90);
        from.value = d.toISOString().slice(0, 10);
    }
    load();
}

// The endpoint's window is "the last N days", so a custom START date maps to it
// exactly. Clamped to the same 1..365 the server enforces.
function selectedDays() {
    const sel = document.getElementById('sel-days').value;
    if (sel !== 'custom') return Number(sel);
    const raw = document.getElementById('custom-from').value;
    if (!raw) return 30;
    const days = Math.ceil((Date.now() - new Date(raw + 'T00:00:00').getTime()) / 86400000);
    return Math.min(365, Math.max(1, days || 1));
}

async function load() {
    const days = selectedDays();
    const group = document.getElementById('sel-group').value;
    const res = await fetchAuth(`/admin/api/ai/usage?days=${days}&group_by=${encodeURIComponent(group)}`);
    if (!res.ok) { showMsg('ai-usage-msg', 'خطا در دریافت داده‌ها', 'danger'); return; }
    const data = await res.json();
    const groups = data.groups || [];
    const num = (v) => (typeof v === 'number' ? v : Number(v) || 0);
    const sum = (k) => groups.reduce((a, g) => a + num(g[k]), 0);
    const set = (id, v) => { document.getElementById(id).textContent = v; };

    set('u-requests', sum('requests').toLocaleString());
    set('u-success', sum('successful').toLocaleString());
    set('u-failed', sum('failed').toLocaleString());
    set('u-failovers', sum('failovers').toLocaleString());
    set('u-tokens', sum('tokens_total').toLocaleString());

    // Cost: NULL means "this model has no price row". A group whose cost is
    // null contributes nothing; if EVERY group is null there is no cost to
    // state, so say so instead of printing $0.
    const costed = groups.filter(g => g.cost !== null && g.cost !== undefined);
    const cost = costed.reduce((a, g) => a + num(g.cost), 0);
    set('u-cost', costed.length ? `${money(cost, currencyOf(groups))}` : 'ناموجود');

    // Weighted by request count — the mean of per-group means is NOT the mean.
    const weighted = groups.reduce(
        (acc, g) => (g.avg_latency ? { s: acc.s + num(g.avg_latency) * num(g.requests),
                                       n: acc.n + num(g.requests) } : acc),
        { s: 0, n: 0 });
    set('u-avg', weighted.n ? Math.round(weighted.s / weighted.n).toLocaleString() : '—');
    set('u-p95', data.p95_latency_ms != null
        ? Math.round(num(data.p95_latency_ms)).toLocaleString() : '—');

    const labels = { provider_instance: 'نمونه', provider_type: 'نوع', model: 'مدل', task: 'وظیفه' };
    document.getElementById('g-col-name').textContent = labels[data.group_by] || 'گروه';
    const body = document.getElementById('usage-body');
    body.innerHTML = groups.map(g => `
      <tr>
        <td dir="auto">${escapeHtml(g.grp == null || g.grp === '' ? '—' : String(g.grp))}</td>
        <td dir="ltr">${num(g.requests).toLocaleString()}</td>
        <td dir="ltr" class="text-success">${num(g.successful).toLocaleString()}</td>
        <td dir="ltr" class="text-danger">${num(g.failed).toLocaleString()}</td>
        <td dir="ltr">${num(g.failovers).toLocaleString()}</td>
        <td dir="ltr">${num(g.tokens_in).toLocaleString()}</td>
        <td dir="ltr">${num(g.tokens_out).toLocaleString()}</td>
        <td dir="ltr">${num(g.tokens_cached).toLocaleString()}</td>
        <td dir="ltr">${g.avg_latency ? Math.round(num(g.avg_latency)).toLocaleString() : '—'}</td>
        <td dir="ltr">${g.cost == null ? 'ناموجود' : money(num(g.cost), g.currency)}</td>
      </tr>`).join('')
        || '<tr><td colspan="10" class="text-center text-muted py-4">داده‌ای در این بازه نیست.</td></tr>';
}

// The currency actually recorded on the usage rows — never assume dollars.
function currencyOf(groups) {
    return (groups.find(g => g.currency)?.currency) || '';
}

function money(value, currency) {
    const amount = value.toFixed(4);
    if (!currency || currency === 'USD') return `$${amount}`;
    return `${amount} ${escapeHtml(currency)}`;
}
