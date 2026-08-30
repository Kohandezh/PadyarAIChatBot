import { API_BASE, getDatasetItems, setDatasetItems, getQuestions, setQuestions } from './state.js';
import { fetchAuth, showMsg, escapeHtml, exportResource, openImportModal, submitImportResource, initBulkSelection } from './utils.js';
import { createPager } from './pager.js';

let bulkSelection = null;
let questionsPager = null;

async function loadQuestionsTable() {
    const res = await fetchAuth(API_BASE + '/questions');
    if (!res.ok) return;
    const items = await res.json();
    setQuestions(items);
    populateQuestionsFilter();
    questionsPager.reset();
    renderQuestionsTable(items);
}

function populateQuestionsFilter() {
    const select = document.getElementById('questions-filter-id');
    const questions = getQuestions();
    const ids = [...new Set(questions.map(q => q.dataset_id))].sort();
    select.innerHTML = '<option value="">همه موضوعات</option>' +
        ids.map(id => `<option value="${id}">${id}</option>`).join('');
}

function filteredQuestions(questions) {
    const search = document.getElementById('questions-search').value.trim().toLowerCase();
    const filterId = document.getElementById('questions-filter-id').value;

    let filtered = questions;
    if (filterId) filtered = filtered.filter(q => q.dataset_id === filterId);
    if (search) filtered = filtered.filter(q => q.question.toLowerCase().includes(search));
    return filtered;
}

function renderQuestionsTable(questions) {
    const tbody = document.getElementById('questions-table');
    const filtered = filteredQuestions(questions);
    const { offset, limit } = questionsPager.state;
    const page = filtered.slice(offset, offset + limit);

    tbody.innerHTML = page.length === 0
        ? '<tr><td colspan="4" class="text-center py-3 text-muted">موردی یافت نشد</td></tr>'
        : '';

    page.forEach((q, i) => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><input type="checkbox" class="form-check-input row-check" value="${q.id}"></td>
            <td>${offset + i + 1}</td>
            <td>${escapeHtml(q.question)}</td>
            <td><code>${escapeHtml(q.dataset_id)}</code></td>
            <td>
                <button class="btn btn-sm btn-outline-primary me-1" onclick="window.openQuestionModal(${q.id})"><i class="fas fa-edit"></i></button>
                <button class="btn btn-sm btn-outline-danger" onclick="window.deleteQuestion(${q.id})"><i class="fas fa-trash"></i></button>
            </td>`;
        tbody.appendChild(tr);
    });

    if (bulkSelection) bulkSelection.clear();
    questionsPager.setResult({ shown: page.length, total: filtered.length });
}

async function openQuestionModal(questionId = null) {
    document.getElementById('questionModalTitle').innerText = questionId !== null ? 'ویرایش سوال' : 'افزودن سوال';
    document.getElementById('q-edit-id').value = questionId !== null ? questionId : '';
    document.getElementById('q-edit-text').value = '';
    document.getElementById('q-edit-dataset-id').value = '';

    // Always fetch fresh dataset items for the dropdown
    const res = await fetchAuth(API_BASE + '/dataset');
    if (res.ok) setDatasetItems(await res.json());

    // Populate dataset_id dropdown
    const select = document.getElementById('q-edit-dataset-id');
    const items = getDatasetItems();
    select.innerHTML = items.map(d => `<option value="${d.id}">${d.id} - ${escapeHtml(d.title.substring(0, 40))}</option>`).join('');

    if (questionId !== null) {
        const questions = getQuestions();
        const q = questions.find(q => q.id === questionId);
        if (q) {
            document.getElementById('q-edit-text').value = q.question;
            select.value = q.dataset_id;
        }
    }
    new bootstrap.Modal(document.getElementById('questionModal')).show();
}

async function saveQuestion() {
    const editId = document.getElementById('q-edit-id').value;
    const isEdit = editId !== '';
    const payload = {
        question: document.getElementById('q-edit-text').value.trim(),
        dataset_id: document.getElementById('q-edit-dataset-id').value,
        video_url: ''
    };

    // Auto-fill video_url from dataset
    const items = getDatasetItems();
    const dsItem = items.find(d => d.id === payload.dataset_id);
    if (dsItem) payload.video_url = dsItem.video_url;

    if (!payload.question || !payload.dataset_id) {
        alert('متن سوال و شناسه دیتاست الزامی هستند');
        return;
    }

    const url = isEdit
        ? API_BASE + '/questions/' + editId
        : API_BASE + '/questions';
    const method = isEdit ? 'PUT' : 'POST';

    const saveBtn = document.querySelector('#questionModal .btn-primary');
    const origText = saveBtn.innerHTML;
    saveBtn.disabled = true;
    saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span> در حال ذخیره...';

    try {
        const res = await fetchAuth(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (res.ok) {
            bootstrap.Modal.getInstance(document.getElementById('questionModal')).hide();
            showMsg('questions-msg', isEdit ? '✅ سوال ویرایش شد' : '✅ سوال جدید اضافه شد', 'success');
            loadQuestionsTable();
        } else {
            const data = await res.json();
            showMsg('questions-msg', '❌ خطا: ' + (data.detail || 'عملیات ناموفق'), 'danger');
        }
    } catch (err) {
        showMsg('questions-msg', '❌ خطای ارتباط با سرور', 'danger');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = origText;
    }
}

async function deleteQuestion(id) {
    if (!confirm('آیا از حذف این سوال مطمئن هستید؟')) return;
    const res = await fetchAuth(API_BASE + '/questions/' + id, { method: 'DELETE' });
    if (res.ok) {
        showMsg('questions-msg', '✅ سوال حذف شد', 'success');
        loadQuestionsTable();
    } else {
        showMsg('questions-msg', '❌ خطا در حذف', 'danger');
    }
}

async function bulkDeleteQuestions() {
    const ids = bulkSelection.getSelected().map(id => parseInt(id, 10));
    if (ids.length === 0) return;
    if (!confirm(`آیا از حذف ${ids.length} مورد انتخاب‌شده مطمئن هستید؟ این عمل قابل بازگشت نیست.`)) return;

    const res = await fetchAuth(API_BASE + '/questions/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
    });
    if (res.ok) {
        showMsg('questions-msg', '✅ موارد انتخاب‌شده حذف شدند', 'success');
        loadQuestionsTable();
    } else {
        const data = await res.json().catch(() => ({}));
        showMsg('questions-msg', '❌ خطا: ' + (data.detail || 'عملیات ناموفق'), 'danger');
    }
}

export function initQuestions() {
    bulkSelection = initBulkSelection({
        selectAllEl: document.getElementById('questions-select-all'),
        toolbarEl: document.getElementById('questions-bulk-toolbar'),
        countEl: document.getElementById('questions-bulk-count'),
    });
    bulkSelection.attach(document.getElementById('questions-table'));

    questionsPager = createPager({
        pageSizeEl: document.getElementById('questions-page-size'),
        prevBtnEl: document.getElementById('questions-btn-prev'),
        nextBtnEl: document.getElementById('questions-btn-next'),
        rangeEl: document.getElementById('questions-range'),
        defaultLimit: 25,
        onPage: () => renderQuestionsTable(getQuestions()),
    });
    loadQuestionsTable();

    // Search and filter handlers
    document.getElementById('questions-search').addEventListener('input', () => {
        questionsPager.reset();
        renderQuestionsTable(getQuestions());
    });
    document.getElementById('questions-filter-id').addEventListener('change', () => {
        questionsPager.reset();
        renderQuestionsTable(getQuestions());
    });

    // Expose for inline onclick in templates
    window.openQuestionModal = openQuestionModal;
    window.saveQuestion = saveQuestion;
    window.deleteQuestion = deleteQuestion;
    window.bulkDeleteQuestions = bulkDeleteQuestions;

    // Import / Export
    window.exportQuestions = (format) => exportResource('questions', format);
    window.openImportModal = openImportModal;
    window.submitImport = () => submitImportResource('questions', loadQuestionsTable);
}
