// Live CPU / memory / GPU gauges for the dashboard.
//
// Doughnuts rather than bars: the question here is "how much headroom is
// left", which is a proportion, and three of them side by side are comparable
// at a glance. The number in the middle carries what the ring cannot — a ring
// can show 8%, only text can say "3.4 of 40 cores".
import { fetchAuth, escapeHtml } from '/static/admin/js/utils.js';

const POLL_MS = 5000;

// Green until it matters, amber when it starts to, red when headroom is nearly
// gone. Thresholds are about attention, not correctness: a server at 70% is
// working, not failing.
const OK = '#2fb344';
const WARN = '#f76707';
const BAD = '#d63939';
const TRACK = 'rgba(130,130,130,.15)';

const charts = {};
let timer = null;

function colourFor(pct) {
    if (pct >= 90) return BAD;
    if (pct >= 70) return WARN;
    return OK;
}

function bytes(n) {
    if (!n && n !== 0) return '—';
    const gb = n / (1024 ** 3);
    if (gb >= 1) return gb.toFixed(1) + ' گیگ';
    return Math.round(n / (1024 ** 2)) + ' مگ';
}

function gauge(canvasId, pct) {
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;
    const value = Math.max(0, Math.min(100, pct || 0));

    if (charts[canvasId]) {
        charts[canvasId].data.datasets[0].data = [value, 100 - value];
        charts[canvasId].data.datasets[0].backgroundColor = [colourFor(value), TRACK];
        charts[canvasId].update('none');   // no animation on a 5s poll
        return;
    }
    charts[canvasId] = new Chart(el, {
        type: 'doughnut',
        data: {
            datasets: [{
                data: [value, 100 - value],
                backgroundColor: [colourFor(value), TRACK],
                borderWidth: 0,
            }],
        },
        options: {
            cutout: '72%',
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false }, tooltip: { enabled: false } },
            animation: { duration: 400 },
        },
    });
}

function centre(id, value, sub) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = `<div class="gauge-value">${value}</div><div class="gauge-sub">${sub}</div>`;
}

function text(id, value) {
    const el = document.getElementById(id);
    if (el) el.innerText = value;
}

function renderCpu(cpu) {
    if (!cpu || !cpu.available) {
        gauge('cpuGauge', 0);
        centre('cpuCenter', '—', 'در دسترس نیست');
        text('cpuDetail', `${cpu?.cores ?? '?'} هسته`);
        return;
    }
    gauge('cpuGauge', cpu.percent);
    centre('cpuCenter', cpu.percent + '٪', `${cpu.busy_cores} از ${cpu.cores} هسته`);
    text('cpuDetail', `بار سیستم: ${cpu.load1} / ${cpu.load5} / ${cpu.load15}`);
}

function renderMemory(mem) {
    if (!mem || !mem.available) {
        gauge('memGauge', 0);
        centre('memCenter', '—', 'در دسترس نیست');
        text('memDetail', '');
        return;
    }
    gauge('memGauge', mem.percent);
    centre('memCenter', mem.percent + '٪', `${bytes(mem.used)} از ${bytes(mem.total)}`);
    text('memDetail', mem.swap_total
        ? `سواپ: ${bytes(mem.swap_used)} از ${bytes(mem.swap_total)}`
        : 'بدون سواپ');
}

// "No card", "no driver" and "driver installed but not talking to the card"
// are three different problems and the operator has to tell them apart — but
// not by reading `nvidia-smi` in English on a Persian admin panel.
function gpuReasonFa(reason) {
    const r = reason || '';
    if (r.includes('not installed')) return 'این سرور کارت گرافیک انویدیا ندارد.';
    if (r.includes('timed out')) return 'کارت گرافیک به موقع پاسخ نداد.';
    if (r.includes('no GPU reported')) return 'کارت گرافیکی شناسایی نشد.';
    if (r) return 'کارت گرافیک پاسخ می‌دهد اما درست کار نمی‌کند.';
    return 'کارت گرافیکی شناسایی نشد.';
}

function renderGpu(gpu) {
    const list = document.getElementById('gpuList');
    if (!gpu || !gpu.available || !gpu.gpus || !gpu.gpus.length) {
        gauge('gpuGauge', 0);
        centre('gpuCenter', '—', 'در دسترس نیست');
        text('gpuDetail', gpuReasonFa(gpu && gpu.reason));
        // The English reason from nvidia-smi stays available on hover: the
        // sentence above is for whoever is looking at the screen, the raw
        // driver message is for whoever has to fix it.
        const detail = document.getElementById('gpuDetail');
        if (detail) detail.title = (gpu && gpu.reason) || '';
        if (list) list.innerHTML = '';
        return;
    }

    // The ring shows the busiest card; the table below shows each one, because
    // one saturated GPU beside one idle GPU must not average into "50%".
    const busiest = gpu.gpus.reduce((a, b) => (b.percent > a.percent ? b : a));
    gauge('gpuGauge', busiest.percent);
    centre('gpuCenter', Math.round(busiest.percent) + '٪',
           gpu.gpus.length > 1 ? `پرکارترین از ${gpu.gpus.length} کارت`
                               : escapeHtml(busiest.name || ""));
    text('gpuDetail', `${bytes(busiest.memory_used)} از ${bytes(busiest.memory_total)} حافظه`);

    if (!list) return;
    list.innerHTML = `
      <div class="table-responsive"><table class="table table-sm mb-0">
        <thead><tr>
          <th>کارت</th><th>پردازش</th><th>حافظه</th><th>دما</th>
        </tr></thead>
        <tbody>${gpu.gpus.map(g => `
          <tr>
            <td>${g.index} — ${escapeHtml(g.name || "")}</td>
            <td>${Math.round(g.percent)}٪</td>
            <td>${bytes(g.memory_used)} از ${bytes(g.memory_total)} (${g.memory_percent}٪)</td>
            <td>${g.temperature != null ? Math.round(g.temperature) + '°' : '—'}</td>
          </tr>`).join('')}
        </tbody>
      </table></div>`;
}

// Never a spinner that spins forever and never an empty card: either the
// gauges are showing numbers, or a plain Persian sentence says why they are
// not. `everRendered` is what decides — once real numbers are on screen a
// later failed poll keeps them (stale is better than blank) and only adds the
// sentence; before that there is nothing worth keeping, so the empty rings are
// taken away and the sentence stands alone.
let everRendered = false;

function showFailure(message) {
    const box = document.getElementById('resources-error');
    if (box) {
        box.innerText = message;
        box.classList.remove('d-none');
    }
    if (!everRendered) {
        document.getElementById('resources-gauges')?.classList.add('d-none');
        const list = document.getElementById('gpuList');
        if (list) list.innerHTML = '';
    }
}

async function tick() {
    let data;
    try {
        const res = await fetchAuth('/admin/api/ops/resources');
        if (!res.ok) throw new Error('http ' + res.status);
        data = await res.json();
    } catch (e) {
        // A dead metrics endpoint must not blank the dashboard or spam the
        // console every five seconds.
        showFailure('خواندن منابع سرور ممکن نشد.');
        return;
    }

    document.getElementById('resources-error')?.classList.add('d-none');
    document.getElementById('resources-gauges')?.classList.remove('d-none');
    renderCpu(data.cpu);
    renderMemory(data.memory);
    renderGpu(data.gpu);
    everRendered = true;
    text('resources-updated',
         new Date((data.ts || Date.now() / 1000) * 1000).toLocaleTimeString('fa-IR'));
}

export function initResources() {
    if (!document.getElementById('resources-row')) return;
    tick();
    if (timer) clearInterval(timer);
    timer = setInterval(tick, POLL_MS);
    // Polling a hidden tab wakes a subprocess on the server for nobody.
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            clearInterval(timer);
            timer = null;
        } else if (!timer) {
            tick();
            timer = setInterval(tick, POLL_MS);
        }
    });
}
