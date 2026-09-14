import { API_BASE } from './state.js';
import { fetchAuth, showMsg } from './utils.js';

let msgChart = null;
let costChart = null;

async function loadStats() {
    const res = await fetchAuth(API_BASE + '/stats');
    if (!res.ok) return;
    const data = await res.json();

    document.getElementById('total-tokens').innerText = data.total_tokens.toLocaleString();
    document.getElementById('total-cost').innerText = '$' + data.total_cost.toFixed(4);
    document.getElementById('total-msgs').innerText = data.total_messages.toLocaleString();

    renderCharts(data.daily_stats);
    renderSourceBreakdown(data.by_source || []);
}

// Plain Persian for every tier name. An operator who cannot read the code must
// still be able to tell "answered from the list" from "asked the visitor to
// choose"; an unknown key falls back to its raw name rather than disappearing.
const SOURCE_FA = {
    local_pick: 'انتخاب بازدیدکننده از فهرست',
    local_company_search: 'فهرست شرکت‌ها',
    local_company_field: 'یک اطلاعات ثبت‌شده شرکت',
    local_entity: 'شرکت یا موضوعی که نامش برده شد',
    local_questions: 'سوال آماده',
    local: 'جستجوی محلی',
    local_intent: 'تشخیص موضوع محلی',
    ai_selected: 'انتخاب هوش مصنوعی از میان نتایج',
    ai_options: 'ارائه چند گزینه به بازدیدکننده',
    openai_classified: 'تشخیص موضوع با هوش مصنوعی',
    openai: 'پاسخ نوشته‌شده با هوش مصنوعی',
    refuse: 'رد سوال خارج از موضوع',
    system: 'بدون پاسخ مطمئن',
};

function renderSourceBreakdown(rows) {
    const body = document.getElementById('source-breakdown-body');
    if (!body) return;
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="2" class="text-center text-muted py-3">'
            + 'در ۲۴ ساعت گذشته پیامی نبوده است.</td></tr>';
        return;
    }
    body.innerHTML = rows.map(r => {
        const name = SOURCE_FA[r.source] || r.source || '—';
        const label = document.createElement('span');
        label.textContent = name;
        return `<tr><td>${label.innerHTML}</td><td>${Number(r.count).toLocaleString()}</td></tr>`;
    }).join('');
}

function renderCharts(dailyStats) {
    const labels = dailyStats.map(d => d.date);
    const msgData = dailyStats.map(d => d.count);
    const costData = dailyStats.map(d => d.cost);

    Chart.defaults.font.family = 'Vazirmatn';
    Chart.defaults.color = '#858796';

    if (msgChart) msgChart.destroy();
    if (costChart) costChart.destroy();

    const ctxMsg = document.getElementById('msgChart').getContext('2d');
    msgChart = new Chart(ctxMsg, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'تعداد پیام‌ها',
                data: msgData,
                fill: true,
                backgroundColor: 'rgba(78, 115, 223, 0.05)',
                borderColor: '#4e73df',
                tension: 0.3,
                pointRadius: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: { grid: { display: false } },
                y: { border: { display: false }, grid: { borderDash: [2] } }
            },
            plugins: { legend: { display: false } }
        }
    });

    const ctxCost = document.getElementById('costChart').getContext('2d');
    costChart = new Chart(ctxCost, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: costData,
                backgroundColor: ['#4e73df', '#1cc88a', '#36b9cc', '#f6c23e', '#e74a3b'],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            cutout: '70%',
            plugins: { legend: { position: 'bottom', labels: { boxWidth: 10, usePointStyle: true } } }
        }
    });
}

async function loadSettings() {
    const res = await fetchAuth(API_BASE + '/settings');
    if (!res.ok) return;
    const data = await res.json();
    const toggle = document.getElementById('openai-toggle');
    toggle.checked = data.openai_enabled;
    document.getElementById('openai-status').innerText = data.openai_enabled ? 'فعال' : 'غیرفعال';

    toggle.onchange = async () => {
        const statusSpan = document.getElementById('openai-status');
        statusSpan.innerText = '...';
        await fetchAuth(API_BASE + '/toggle_openai', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: toggle.checked })
        });
        statusSpan.innerText = toggle.checked ? 'فعال' : 'غیرفعال';
    };

    const voiceToggle = document.getElementById('voice-toggle');
    voiceToggle.checked = data.voice_enabled;
    document.getElementById('voice-status').innerText = data.voice_enabled ? 'فعال' : 'غیرفعال';

    voiceToggle.onchange = async () => {
        const statusSpan = document.getElementById('voice-status');
        statusSpan.innerText = '...';
        await fetchAuth(API_BASE + '/toggle_voice', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: voiceToggle.checked })
        });
        statusSpan.innerText = voiceToggle.checked ? 'فعال' : 'غیرفعال';
    };

    const ttsToggle = document.getElementById('tts-toggle');
    if (ttsToggle) {
        ttsToggle.checked = data.tts_enabled;
        document.getElementById('tts-status').innerText = data.tts_enabled ? 'فعال' : 'غیرفعال';

        ttsToggle.onchange = async () => {
            const statusSpan = document.getElementById('tts-status');
            statusSpan.innerText = '...';
            await fetchAuth(API_BASE + '/toggle_tts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ enabled: ttsToggle.checked })
            });
            statusSpan.innerText = ttsToggle.checked ? 'فعال' : 'غیرفعال';
        };
    }
}

async function loadLowConf() {
    const res = await fetchAuth(API_BASE + '/low_confidence');
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById('low-conf-table');
    tbody.innerHTML = '';

    if (data.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center py-3 text-muted">موردی یافت نشد</td></tr>';
        return;
    }

    const { escapeHtml } = await import('./utils.js');
    data.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="ps-4">${new Date(row.created_at).toLocaleDateString('fa-IR')} <small class="text-muted">${new Date(row.created_at).toLocaleTimeString('fa-IR')}</small></td>
            <td class="fw-bold text-dark">${escapeHtml(row.query)}</td>
            <td><span class="badge bg-danger bg-opacity-10 text-danger">${(row.confidence * 100).toFixed(1)}%</span></td>
            <td><span class="badge bg-light text-dark border">${escapeHtml(row.response_type)}</span></td>
        `;
        // Same fix flow as the conversations weak view: openFix() → POST
        // /admin/api/questions, which reindexes on the way out. The button is
        // built with createElement (never innerHTML) — row data is visitor
        // text rendered inside an authenticated admin session.
        const action = document.createElement('td');
        const fix = document.createElement('button');
        fix.type = 'button';
        fix.className = 'btn btn-sm btn-outline-primary';
        fix.textContent = 'اصلاح';
        fix.addEventListener('click', () => openFix(row.query, row.entry_id));
        action.appendChild(fix);
        tr.appendChild(action);
        tbody.appendChild(tr);
    });
}

// ── Fix a wrong answer (same flow as conversations.js openFix) ────────

let datasetEntries = null;
let fixTarget = { question: '', entryId: '' };

async function loadDatasetEntries() {
    if (datasetEntries) return datasetEntries;
    const res = await fetchAuth('/admin/api/dataset');
    datasetEntries = res.ok ? await res.json() : [];
    return datasetEntries;
}

function fillEntries(term) {
    const select = document.getElementById('fix-entry');
    select.replaceChildren();
    const needle = (term || '').trim().toLowerCase();
    (datasetEntries || [])
        .filter((d) => !needle || ((d.title || '') + ' ' + (d.id || '')).toLowerCase().includes(needle))
        .slice(0, 300)
        .forEach((d) => {
            const option = document.createElement('option');
            option.value = d.id;
            option.textContent = d.title || d.id;
            if (d.id === fixTarget.entryId) option.selected = true;
            select.append(option);
        });
    if (!select.options.length) {
        const option = document.createElement('option');
        option.disabled = true;
        option.textContent = 'پاسخی پیدا نشد';
        select.append(option);
    }
}

async function openFix(question, entryId) {
    fixTarget = { question: question || '', entryId: entryId || '' };
    document.getElementById('fix-question').value = fixTarget.question;
    document.getElementById('fix-search').value = '';
    const msg = document.getElementById('fix-msg');
    msg.textContent = '';
    msg.className = 'small';
    new bootstrap.Modal(document.getElementById('fix-modal')).show();
    await loadDatasetEntries();
    fillEntries('');
}

async function saveFix() {
    const question = document.getElementById('fix-question').value.trim();
    const datasetId = document.getElementById('fix-entry').value;
    const msg = document.getElementById('fix-msg');
    if (!question || !datasetId) {
        msg.className = 'small text-danger';
        msg.textContent = 'هم سوال و هم پاسخ باید انتخاب شوند.';
        return;
    }
    msg.className = 'small text-muted';
    msg.textContent = 'در حال ذخیره…';
    try {
        const res = await fetchAuth('/admin/api/questions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ question, dataset_id: datasetId }),
        });
        if (!res.ok) throw new Error('http ' + res.status);
        msg.className = 'small text-success';
        msg.textContent = 'ذخیره شد. از این به بعد ربات این سوال را درست جواب می‌دهد.';
        setTimeout(() => {
            const modal = bootstrap.Modal.getInstance(document.getElementById('fix-modal'));
            if (modal) modal.hide();
        }, 1200);
    } catch (e) {
        msg.className = 'small text-danger';
        msg.textContent = 'ذخیره نشد. دوباره تلاش کنید.';
    }
}

// AI control-plane summary cards — concise counts that drill down into the
// AI pages. A failing endpoint leaves the placeholders untouched.
async function loadAISummary() {
    try {
        const res = await fetchAuth('/admin/api/ai/summary');
        if (!res.ok) return;
        const s = await res.json();
        const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
        set('ai-providers-active', `${s.providers.active} فعال`);
        set('ai-providers-health',
            `${s.providers.healthy} سالم · ${s.providers.degraded} افت‌کرده · ${s.providers.down} قطع`);
        set('ai-requests-today', (s.requests_today || 0).toLocaleString('fa-IR'));
        set('ai-requests-detail',
            s.error_rate_today == null ? '—'
            : `نرخ خطا ${Math.round(s.error_rate_today * 100)}٪`);
        set('ai-tokens-today', (s.tokens_today || 0).toLocaleString('fa-IR'));
        set('ai-failovers-today', `${s.failovers_today || 0} جابه‌جایی`);
        set('ai-cost-today', s.cost_today ? `$${s.cost_today.toFixed(4)}` : '—');
        set('ai-latency-today', s.avg_latency_ms ? `میانگین ${s.avg_latency_ms}ms` : '—');
    } catch { /* cards stay placeholders */ }
}

export function initDashboard() {
    loadStats();
    loadSettings();
    loadLowConf();
    loadAISummary();

    document.getElementById('fix-search').addEventListener('input', (e) => fillEntries(e.target.value));
    document.getElementById('fix-save').addEventListener('click', saveFix);

    // Expose for inline onclick in template
    window.loadLowConf = loadLowConf;

    // Clear buttons
    const clearButtons = {
        'clear-tokens': { endpoint: '/clear_tokens', msg: 'آیا از صفر کردن توکن‌ها مطمئن هستید؟' },
        'clear-cost': { endpoint: '/clear_cost', msg: 'آیا از صفر کردن هزینه مطمئن هستید؟' },
        'clear-history': { endpoint: '/clear_history', msg: 'آیا از پاک‌سازی کل پیام‌ها مطمئن هستید؟ این عمل قابل بازگشت نیست.' }
    };

    Object.entries(clearButtons).forEach(([id, config]) => {
        const btn = document.getElementById(id);
        if (!btn) return;
        btn.addEventListener('click', async () => {
            if (!confirm(config.msg)) return;
            btn.disabled = true;
            btn.innerText = '...';
            try {
                const res = await fetchAuth(API_BASE + config.endpoint, { method: 'POST' });
                if (res.ok) {
                    loadStats();
                    loadLowConf();
                } else {
                    alert('خطا در عملیات');
                }
            } catch {
                alert('خطا در ارتباط با سرور');
            }
            btn.disabled = false;
            btn.innerText = 'پاک‌سازی';
        });
    });
}
