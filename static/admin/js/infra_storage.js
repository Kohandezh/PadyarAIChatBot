/* Infrastructure → Storage.
 *
 * SECURITY: the category labels are ours, but the numbers and the state come
 * from the server reading a real filesystem. Everything is written with
 * textContent — no innerHTML in this file.
 *
 * The page shows what the server measured and nothing more. It does not
 * extrapolate, does not predict "days until full", and does not claim the
 * listed categories add up to the disk: they never do, because the operating
 * system and everything else on the server are not this application's files.
 */
import { fetchAuth, showMsg } from './utils.js';
import { loadProfile } from './settings.js';

const el = (id) => document.getElementById(id);
const faNum = (n) => Number(n || 0).toLocaleString('fa-IR');

function fmtBytes(bytes) {
    const n = Number(bytes || 0);
    if (n < 1024) return faNum(n) + ' بایت';
    const units = ['کیلوبایت', 'مگابایت', 'گیگابایت', 'ترابایت'];
    let value = n / 1024;
    let i = 0;
    while (value >= 1024 && i < units.length - 1) {
        value /= 1024;
        i += 1;
    }
    return value.toLocaleString('fa-IR', { maximumFractionDigits: 1 }) + ' ' + units[i];
}

function node(tag, className, text) {
    const n = document.createElement(tag);
    if (className) n.className = className;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
}

const STATE_CLASS = { ok: 'text-success', warning: 'text-warning', critical: 'text-danger' };
const STATE_ICON = { ok: '✅ ', warning: '⚠️ ', critical: '⛔ ' };

function renderDisk(disk) {
    const state = disk.state || 'unknown';
    const stateNode = el('st-state');
    stateNode.className = 'fw-bold ' + (STATE_CLASS[state] || 'text-muted');
    stateNode.textContent = (STATE_ICON[state] || '') + (disk.state_label_fa || '');

    const percent = Number(disk.percent_used || 0);
    el('st-percent').textContent = faNum(percent) + ' درصد پر شده';

    const fill = el('st-bar-fill');
    fill.className = 'st-bar-fill' + (state === 'ok' ? '' : ' ' + state);
    fill.style.width = Math.max(0, Math.min(100, percent)) + '%';

    el('st-total').textContent = fmtBytes(disk.total_bytes);
    el('st-used').textContent = fmtBytes(disk.used_bytes);
    el('st-free').textContent = fmtBytes(disk.free_bytes);

    el('st-thresholds').textContent =
        'هشدار از ' + faNum(disk.warn_percent) + ' درصد و وضعیت بحرانی از '
        + faNum(disk.critical_percent) + ' درصد اعلام می‌شود.';
}

function renderCategories(categories, tracked) {
    const body = el('st-categories');
    body.replaceChildren();

    const rows = (categories || []).slice().sort((a, b) => (b.bytes || 0) - (a.bytes || 0));
    if (!rows.length) {
        const tr = node('tr');
        const td = node('td', 'text-muted text-center', 'موردی برای نمایش نیست.');
        td.colSpan = 3;
        tr.appendChild(td);
        body.appendChild(tr);
        return;
    }

    rows.forEach((cat) => {
        const tr = node('tr');

        const name = node('td');
        name.appendChild(node('span', 'st-swatch'));
        name.appendChild(document.createTextNode(cat.label_fa || cat.key || ''));
        if (!cat.exists) {
            name.appendChild(node('span', 'text-muted small', ' (هنوز ساخته نشده)'));
        }
        tr.appendChild(name);

        tr.appendChild(node('td', 'st-ltr', fmtBytes(cat.bytes)));

        const share = tracked > 0 ? (Number(cat.bytes || 0) / tracked) * 100 : 0;
        tr.appendChild(node('td', 'text-muted',
            share.toLocaleString('fa-IR', { maximumFractionDigits: 1 }) + ' درصد'));

        body.appendChild(tr);
    });

    el('st-tracked').textContent = fmtBytes(tracked);
}

async function load() {
    try {
        const res = await fetchAuth('/admin/api/infra/storage');
        if (!res.ok) {
            showMsg('storage-msg', 'دریافت اطلاعات فضای ذخیره‌سازی ممکن نشد', 'danger');
            return;
        }
        const data = await res.json();
        renderDisk(data.disk || {});
        renderCategories(data.categories, Number(data.tracked_bytes || 0));
    } catch {
        showMsg('storage-msg', 'خطای ارتباط با سرور', 'danger');
    }
}

export function initInfraStorage() {
    loadProfile();
    el('st-refresh').addEventListener('click', load);
    load();
}
