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
        tbody.innerHTML = '<tr><td colspan="4" class="text-center py-3 text-muted">موردی یافت نشد</td></tr>';
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
        tbody.appendChild(tr);
    });
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
