// Taxonomy editor — the lists the registration form offers.
//
// One document, two views: the tables below edit `doc.jobs/positions/
// interests/flags`, the advanced textarea edits the whole of `doc`. Both save
// through the same endpoint, which validates before it writes, so neither view
// can put a broken taxonomy in front of visitors.

import { fetchAuth, escapeHtml } from './utils.js';

const LISTS = {
    jobs: {
        title: 'شغل‌ها',
        help: 'شغل‌هایی که بازدیدکننده می‌تواند از فرم انتخاب کند.',
        keywords: false,
    },
    positions: {
        title: 'سمت‌ها',
        help: 'سمت سازمانی بازدیدکننده. اگر این فهرست را خالی بگذارید، بازدیدکننده سمتش را خودش می‌نویسد.',
        keywords: false,
    },
    interests: {
        title: 'علاقه‌مندی‌ها',
        help: 'موضوع‌هایی که بازدیدکننده انتخاب می‌کند. «واژه‌های کمکی» اختیاری است و وقتی به کار می‌آید که عنوان با واژگان بخش‌های نمایشگاه فرق داشته باشد.',
        keywords: true,
    },
    flags: {
        title: 'گزینه‌های تیک‌دار',
        help: 'جمله‌هایی که بازدیدکننده می‌تواند تیک بزند.',
        keywords: false,
    },
};

let doc = null;          // the whole file, as the admin is editing it
let current = 'jobs';    // which list the table is showing

function note(id, text, type) {
    const el = document.getElementById(id);
    el.className = `fw-bold mt-2 text-${type}`;
    el.textContent = text;
}

function rows(list) {
    if (!doc) return [];
    if (!Array.isArray(doc[list])) doc[list] = [];
    return doc[list];
}

// ---- rendering ----

function renderHead() {
    const cols = ['عنوان فارسی', 'عنوان انگلیسی'];
    if (LISTS[current].keywords) cols.push('واژه‌های کمکی (اختیاری)');
    cols.push('شناسه');
    document.getElementById('tax-head').innerHTML =
        cols.map((c, i) => `<th${i === 0 ? ' class="ps-4"' : ''}>${c}</th>`).join('') +
        '<th style="width:150px">جابه‌جایی و حذف</th>';
}

function renderRows() {
    const list = rows(current);
    const tbody = document.getElementById('tax-rows');

    if (list.length === 0) {
        const cols = LISTS[current].keywords ? 5 : 4;
        tbody.innerHTML = `<tr><td colspan="${cols}" class="text-center text-muted py-4">
            هنوز چیزی اضافه نشده است. روی «افزودن ردیف تازه» بزنید.</td></tr>`;
        return;
    }

    tbody.innerHTML = list.map((item, i) => {
        const kw = (item.keywords || []).join('، ');
        const keywordCell = LISTS[current].keywords
            ? `<td><input class="form-control form-control-sm" dir="rtl" data-field="keywords"
                  data-index="${i}" value="${escapeHtml(kw) || ''}" placeholder="مثلاً: اینترنت اشیا، حسگر"></td>`
            : '';
        return `<tr>
            <td class="ps-4"><input class="form-control form-control-sm" dir="rtl" data-field="fa"
                data-index="${i}" value="${escapeHtml(item.fa) || ''}" placeholder="مثلاً: پژوهشگر"></td>
            <td><input class="form-control form-control-sm" dir="ltr" data-field="en"
                data-index="${i}" value="${escapeHtml(item.en) || ''}" placeholder="Researcher"></td>
            ${keywordCell}
            <td><input class="form-control form-control-sm text-muted" dir="ltr" data-field="id"
                data-index="${i}" value="${escapeHtml(item.id) || ''}" placeholder="خودکار"></td>
            <td class="text-nowrap">
                <button type="button" class="btn btn-sm btn-outline-secondary" data-act="up" data-index="${i}"
                    title="بالاتر" ${i === 0 ? 'disabled' : ''}><i class="fas fa-arrow-up"></i></button>
                <button type="button" class="btn btn-sm btn-outline-secondary" data-act="down" data-index="${i}"
                    title="پایین‌تر" ${i === list.length - 1 ? 'disabled' : ''}><i class="fas fa-arrow-down"></i></button>
                <button type="button" class="btn btn-sm btn-outline-danger" data-act="del" data-index="${i}"
                    title="حذف"><i class="fas fa-trash"></i></button>
            </td>
        </tr>`;
    }).join('');
}

function renderSections() {
    const tbody = document.getElementById('tax-sections');
    const list = (doc && Array.isArray(doc.sections)) ? doc.sections : [];
    if (list.length === 0) {
        tbody.innerHTML = '<tr><td colspan="3" class="text-center text-muted py-3">بخشی تعریف نشده است</td></tr>';
        return;
    }
    tbody.innerHTML = list.map(s => `<tr>
        <td class="ps-4">${escapeHtml(s.fa) || ''}</td>
        <td dir="ltr">${escapeHtml(s.en) || ''}</td>
        <td class="text-muted small">${escapeHtml((s.keywords || []).join('، ')) || ''}</td>
    </tr>`).join('');
}

function renderAll() {
    document.getElementById('tax-list-help').textContent = LISTS[current].help;
    renderHead();
    renderRows();
    renderSections();
    document.getElementById('tax-raw').value = doc ? JSON.stringify(doc, null, 2) : '';
}

// ---- loading ----

async function load() {
    const alertBox = document.getElementById('tax-alert');
    try {
        const res = await fetchAuth('/admin/api/taxonomy');
        if (!res.ok) { alertBox.textContent = 'دریافت اطلاعات ممکن نشد.'; alertBox.classList.remove('d-none'); return; }
        const data = await res.json();

        doc = data.data;
        const counts = data.live_counts || {};
        document.getElementById('tax-live-note').textContent =
            `نسخهٔ فعال: ${data.live_version} — ${counts.sections || 0} بخش، ${counts.jobs || 0} شغل`;

        if (data.parse_error) {
            // The file on disk is not readable JSON. The tables cannot show it,
            // so send the admin straight to the advanced box with the raw text.
            alertBox.innerHTML = 'فایل فعلی خوانده نمی‌شود، بنابراین جدول‌ها خالی هستند. ' +
                'بازدیدکنندگان همچنان نسخهٔ سالم قبلی را می‌بینند. ' +
                'متن فایل در «ویرایش پیشرفته» پایین صفحه است.<br><span dir="ltr" class="small">' +
                escapeHtml(data.parse_error) + '</span>';
            alertBox.classList.remove('d-none');
            document.getElementById('tax-save').disabled = true;
            renderAll();
            document.getElementById('tax-raw').value = data.text;
            showRaw(true);
            return;
        }

        document.getElementById('tax-save').disabled = false;
        if (data.using_fallback) {
            alertBox.textContent = 'هیچ فهرست سالمی بارگذاری نشده است — فرم ثبت‌نام فعلاً گزینه‌ای برای نمایش ندارد.';
            alertBox.classList.remove('d-none');
        } else {
            alertBox.classList.add('d-none');
        }
        renderAll();
    } catch {
        alertBox.textContent = 'خطای ارتباط با سرور.';
        alertBox.classList.remove('d-none');
    }
}

// ---- saving ----

// Ids are machinery, not something an admin should have to invent. Anything
// left blank gets one derived from the English title (or the row number).
function fillIds(list) {
    const taken = new Set(rows(list).map(r => (r.id || '').trim()).filter(Boolean));
    rows(list).forEach((row, i) => {
        if ((row.id || '').trim()) { row.id = row.id.trim(); return; }
        const base = (row.en || '').trim().toLowerCase()
            .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
            || `${list.replace(/s$/, '')}-${i + 1}`;
        let id = base, n = 2;
        while (taken.has(id)) id = `${base}-${n++}`;
        taken.add(id);
        row.id = id;
    });
}

// Returns a Persian complaint, or '' when the document is ready to send.
function prepare() {
    if (!doc) return 'فایل فعلی خوانده نمی‌شود؛ از «ویرایش پیشرفته» استفاده کنید.';
    for (const list of Object.keys(LISTS)) {
        rows(list).forEach(row => {
            row.fa = (row.fa || '').trim();
            row.en = (row.en || '').trim();
        });
        fillIds(list);
        const seen = new Set();
        for (let i = 0; i < rows(list).length; i++) {
            const row = rows(list)[i];
            if (!row.fa) return `«${LISTS[list].title}» ردیف ${i + 1}: عنوان فارسی را بنویسید (یا ردیف را حذف کنید).`;
            if (seen.has(row.id)) return `«${LISTS[list].title}» ردیف ${i + 1}: شناسهٔ «${row.id}» تکراری است.`;
            seen.add(row.id);
        }
    }
    return '';
}

async function post(text, msgId) {
    note(msgId, 'در حال ذخیره…', 'muted');
    try {
        const res = await fetchAuth('/admin/api/taxonomy', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text }),
        });
        let data = {};
        try { data = await res.json(); } catch { /* no body */ }
        if (!res.ok) {
            note(msgId, data.detail || 'ذخیره نشد.', 'danger');
            return;
        }
        note(msgId, 'ذخیره شد و همین حالا برای بازدیدکنندگان فعال است.' +
            (data.backup ? ` نسخهٔ پشتیبان قبلی: ${data.backup}` : ''), 'success');
        await load();
    } catch {
        note(msgId, 'خطای ارتباط با سرور.', 'danger');
    }
}

function showRaw(open) {
    document.getElementById('tax-raw-body').classList.toggle('d-none', !open);
    document.getElementById('tax-raw-toggle').textContent = open ? 'بستن' : 'نمایش';
}

// ---- wiring ----

export function initTaxonomy() {
    load();

    document.getElementById('tax-tabs').addEventListener('click', e => {
        const btn = e.target.closest('button[data-list]');
        if (!btn) return;
        document.querySelectorAll('#tax-tabs .nav-link').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        current = btn.dataset.list;
        renderAll();
    });

    // Typing writes straight into the document — no separate "edit" mode.
    document.getElementById('tax-rows').addEventListener('input', e => {
        const input = e.target.closest('input[data-field]');
        if (!input) return;
        const row = rows(current)[Number(input.dataset.index)];
        if (!row) return;
        if (input.dataset.field === 'keywords') {
            const words = input.value.split(/[,،]/).map(w => w.trim()).filter(Boolean);
            if (words.length) row.keywords = words; else delete row.keywords;
        } else {
            row[input.dataset.field] = input.value;
        }
    });

    document.getElementById('tax-rows').addEventListener('click', e => {
        const btn = e.target.closest('button[data-act]');
        if (!btn) return;
        const list = rows(current);
        const i = Number(btn.dataset.index);
        if (btn.dataset.act === 'del') {
            if (!confirm(`«${list[i].fa || 'این ردیف'}» حذف شود؟`)) return;
            list.splice(i, 1);
        } else {
            const j = btn.dataset.act === 'up' ? i - 1 : i + 1;
            if (j < 0 || j >= list.length) return;
            [list[i], list[j]] = [list[j], list[i]];
        }
        renderRows();
    });

    document.getElementById('tax-add').addEventListener('click', () => {
        rows(current).push({ id: '', fa: '', en: '' });
        renderRows();
        const inputs = document.querySelectorAll('#tax-rows input[data-field="fa"]');
        if (inputs.length) inputs[inputs.length - 1].focus();
    });

    document.getElementById('tax-save').addEventListener('click', () => {
        const problem = prepare();
        if (problem) { note('tax-msg', problem, 'danger'); renderRows(); return; }
        renderRows();  // show the ids that were filled in
        post(JSON.stringify(doc, null, 2), 'tax-msg');
    });

    document.getElementById('tax-raw-toggle').addEventListener('click', () => {
        const open = document.getElementById('tax-raw-body').classList.contains('d-none');
        // Opening shows the document as it stands now, unsaved table edits included.
        if (open && doc) document.getElementById('tax-raw').value = JSON.stringify(doc, null, 2);
        showRaw(open);
    });

    document.getElementById('tax-raw-save').addEventListener('click', () => {
        if (!confirm('کل فایل با متن این کادر جایگزین می‌شود. مطمئن هستید؟')) return;
        post(document.getElementById('tax-raw').value, 'tax-raw-msg');
    });
}
