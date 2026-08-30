import { API_BASE, getDatasetItems, setDatasetItems } from './state.js';
import { fetchAuth, showMsg, escapeHtml, exportResource, openImportModal, submitImportResource, initBulkSelection } from './utils.js';

let mediaBrowserModal = null;
let bulkSelection = null;

async function loadDatasetTable() {
    const res = await fetchAuth(API_BASE + '/dataset');
    if (!res.ok) return;
    const items = await res.json();
    setDatasetItems(items);
    renderDatasetTable(items);
}

function renderDatasetTable(items) {
    const tbody = document.getElementById('dataset-table');
    const search = document.getElementById('dataset-search').value.trim().toLowerCase();
    const filtered = search
        ? items.filter(d => (d.id + d.title + d.text).toLowerCase().includes(search))
        : items;

    tbody.innerHTML = filtered.length === 0
        ? '<tr><td colspan="6" class="text-center py-3 text-muted">موردی یافت نشد</td></tr>'
        : '';

    filtered.forEach((item, i) => {
        const tr = document.createElement('tr');
        const textPreview = item.text.length > 80 ? item.text.substring(0, 80) + '...' : item.text;
        const hasVideo = item.video_url ? '<i class="fas fa-check text-success"></i>' : '<i class="fas fa-times text-muted"></i>';
        tr.innerHTML = `
            <td><input type="checkbox" class="form-check-input row-check" value="${escapeHtml(item.id)}"></td>
            <td>${i + 1}</td>
            <td><code>${escapeHtml(item.id)}</code></td>
            <td class="fw-bold">${escapeHtml(item.title)}</td>
            <td class="text-muted small">${escapeHtml(textPreview)}</td>
            <td>${hasVideo}</td>
            <td>
                <button class="btn btn-sm btn-outline-primary me-1" onclick="window.openDatasetModal('${item.id}')"><i class="fas fa-edit"></i></button>
                <button class="btn btn-sm btn-outline-danger" onclick="window.deleteDatasetItem('${item.id}')"><i class="fas fa-trash"></i></button>
            </td>`;
        tbody.appendChild(tr);
    });

    if (bulkSelection) bulkSelection.clear();
}

function openDatasetModal(item_id = null) {
    document.getElementById('datasetModalTitle').innerText = item_id ? 'ویرایش مورد' : 'افزودن مورد جدید';
    document.getElementById('ds-edit-id').value = item_id || '';
    document.getElementById('ds-edit-key').value = '';
    document.getElementById('ds-edit-title').value = '';
    document.getElementById('ds-edit-text').value = '';
    document.getElementById('ds-edit-title-en').value = '';
    document.getElementById('ds-edit-text-en').value = '';
    document.getElementById('ds-edit-video').value = '';
    document.getElementById('ds-edit-key').disabled = false;
    _updateVideoPreview();

    if (item_id) {
        const items = getDatasetItems();
        const item = items.find(d => d.id === item_id);
        if (item) {
            document.getElementById('ds-edit-key').value = item.id;
            document.getElementById('ds-edit-key').disabled = true;
            document.getElementById('ds-edit-title').value = item.title;
            document.getElementById('ds-edit-text').value = item.text;
            document.getElementById('ds-edit-title-en').value = item.title_en || '';
            document.getElementById('ds-edit-text-en').value = item.text_en || '';
            document.getElementById('ds-edit-video').value = item.video_url || '';
            _updateVideoPreview();
        }
    }
    new bootstrap.Modal(document.getElementById('datasetModal')).show();
}

function _updateVideoPreview() {
    const url = document.getElementById('ds-edit-video').value;
    const previewDiv = document.getElementById('ds-video-preview');
    const emptyDiv = document.getElementById('ds-video-empty');
    if (url) {
        document.getElementById('ds-preview-player').src = url;
        previewDiv.style.display = '';
        emptyDiv.style.display = 'none';
    } else {
        document.getElementById('ds-preview-player').src = '';
        previewDiv.style.display = 'none';
        emptyDiv.style.display = '';
    }
}

function removeVideo() {
    document.getElementById('ds-edit-video').value = '';
    _updateVideoPreview();
}

// --- Media Browser ---

async function openMediaBrowser() {
    if (!mediaBrowserModal) {
        mediaBrowserModal = new bootstrap.Modal(document.getElementById('mediaBrowserModal'));
    }
    document.getElementById('media-search').value = '';
    document.getElementById('media-upload-status').innerText = '';
    mediaBrowserModal.show();
    await loadMediaGrid();
}

async function loadMediaGrid() {
    const grid = document.getElementById('media-grid');
    grid.innerHTML = '<div class="text-center py-4 text-muted w-100"><div class="spinner-border spinner-border-sm me-1"></div> در حال بارگذاری...</div>';

    try {
        const res = await fetchAuth(API_BASE + '/videos');
        if (!res.ok) throw new Error('خطا در دریافت لیست ویدیوها');
        const videos = await res.json();
        renderMediaGrid(videos);
    } catch (err) {
        grid.innerHTML = `<div class="text-center py-4 text-danger w-100">${err.message}</div>`;
    }
}

function renderMediaGrid(videos) {
    const grid = document.getElementById('media-grid');
    const search = document.getElementById('media-search').value.trim().toLowerCase();
    const filtered = search ? videos.filter(v => v.filename.toLowerCase().includes(search)) : videos;

    if (filtered.length === 0) {
        grid.innerHTML = '<div class="text-center py-4 text-muted w-100">ویدیویی یافت نشد</div>';
        return;
    }

    grid.innerHTML = filtered.map(v => `
        <div class="col-6 col-md-4 col-lg-3">
            <div class="card h-100" style="cursor:pointer;border:2px solid transparent;transition:border-color .15s"
                 onmouseenter="this.style.borderColor='#4e73df'" onmouseleave="this.style.borderColor='transparent'"
                 onclick="window.selectVideo('${v.url}')">
                <div class="position-relative" style="padding-top:56%;background:#000;border-radius:6px 6px 0 0;overflow:hidden">
                    <video src="${v.url}" style="position:absolute;top:0;left:0;width:100%;height:100%;object-fit:cover" muted preload="metadata"></video>
                    <span class="position-absolute bottom-0 end-0 badge bg-dark bg-opacity-75 m-1" style="font-size:10px">${_formatSize(v.size)}</span>
                    <button class="btn btn-sm btn-danger position-absolute top-0 start-0 m-1 py-0 px-1" style="font-size:11px;line-height:1.2"
                            onclick="event.stopPropagation();window.deleteMediaVideo('${escapeHtml(v.filename)}')">
                        <i class="fas fa-trash-alt"></i>
                    </button>
                </div>
                <div class="card-body p-2">
                    <small class="text-truncate d-block" title="${escapeHtml(v.filename)}">${escapeHtml(v.filename)}</small>
                </div>
            </div>
        </div>
    `).join('');
}

function _formatSize(bytes) {
    if (!bytes) return '';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function selectVideo(url) {
    document.getElementById('ds-edit-video').value = url;
    _updateVideoPreview();
    mediaBrowserModal.hide();
}

async function deleteMediaVideo(filename) {
    if (!confirm(`آیا از حذف "${filename}" مطمئن هستید؟ این عمل قابل بازگشت نیست.`)) return;
    try {
        const res = await fetchAuth(API_BASE + '/videos/' + encodeURIComponent(filename), { method: 'DELETE' });
        if (res.ok) {
            await loadMediaGrid();
        } else {
            const data = await res.json();
            alert('خطا: ' + (data.detail || 'عملیات ناموفق'));
        }
    } catch {
        alert('خطای ارتباط با سرور');
    }
}

async function uploadFromMediaBrowser(input) {
    const file = input.files[0];
    if (!file) return;

    const status = document.getElementById('media-upload-status');
    status.className = 'text-muted small';
    status.innerText = '⏳ در حال آپلود ' + file.name + '...';

    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetchAuth(API_BASE + '/upload_video', { method: 'POST', body: formData });
        if (res.ok) {
            const data = await res.json();
            status.className = 'text-success small';
            status.innerText = '✅ ' + data.filename + ' آپلود شد';
            await loadMediaGrid();
        } else {
            const data = await res.json();
            status.className = 'text-danger small';
            status.innerText = '❌ ' + (data.detail || 'خطا در آپلود');
        }
    } catch {
        status.className = 'text-danger small';
        status.innerText = '❌ خطای ارتباط با سرور';
    }
    input.value = '';
}

async function saveDatasetItem() {
    const isEdit = !!document.getElementById('ds-edit-id').value;
    const payload = {
        id: document.getElementById('ds-edit-key').value.trim(),
        title: document.getElementById('ds-edit-title').value.trim(),
        text: document.getElementById('ds-edit-text').value.trim(),
        title_en: document.getElementById('ds-edit-title-en').value.trim(),
        text_en: document.getElementById('ds-edit-text-en').value.trim(),
        video_url: document.getElementById('ds-edit-video').value.trim()
    };

    if (!payload.id || !payload.title || !payload.text) {
        alert('شناسه، عنوان و متن پاسخ الزامی هستند');
        return;
    }

    const url = isEdit
        ? API_BASE + '/dataset/' + encodeURIComponent(document.getElementById('ds-edit-id').value)
        : API_BASE + '/dataset';
    const method = isEdit ? 'PUT' : 'POST';

    const saveBtn = document.querySelector('#datasetModal .btn-primary');
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
            bootstrap.Modal.getInstance(document.getElementById('datasetModal')).hide();
            showMsg('dataset-msg', isEdit ? '✅ مورد با موفقیت ویرایش شد' : '✅ مورد جدید اضافه شد', 'success');
            loadDatasetTable();
        } else {
            const data = await res.json();
            showMsg('dataset-msg', '❌ خطا: ' + (data.detail || 'عملیات ناموفق'), 'danger');
        }
    } catch (err) {
        showMsg('dataset-msg', '❌ خطای ارتباط با سرور', 'danger');
    } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = origText;
    }
}

async function deleteDatasetItem(item_id) {
    if (!confirm(`آیا از حذف "${item_id}" مطمئن هستید؟`)) return;
    const res = await fetchAuth(API_BASE + '/dataset/' + encodeURIComponent(item_id), { method: 'DELETE' });
    if (res.ok) {
        showMsg('dataset-msg', '✅ مورد حذف شد', 'success');
        loadDatasetTable();
    } else {
        showMsg('dataset-msg', '❌ خطا در حذف', 'danger');
    }
}

async function bulkDeleteDatasetItems() {
    const ids = bulkSelection.getSelected();
    if (ids.length === 0) return;
    if (!confirm(`آیا از حذف ${ids.length} مورد انتخاب‌شده مطمئن هستید؟ این عمل قابل بازگشت نیست.`)) return;

    const res = await fetchAuth(API_BASE + '/dataset/bulk-delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ids })
    });
    if (res.ok) {
        showMsg('dataset-msg', '✅ موارد انتخاب‌شده حذف شدند', 'success');
        loadDatasetTable();
    } else {
        const data = await res.json().catch(() => ({}));
        showMsg('dataset-msg', '❌ خطا: ' + (data.detail || 'عملیات ناموفق'), 'danger');
    }
}

export function initDataset() {
    bulkSelection = initBulkSelection({
        selectAllEl: document.getElementById('dataset-select-all'),
        toolbarEl: document.getElementById('dataset-bulk-toolbar'),
        countEl: document.getElementById('dataset-bulk-count'),
    });
    bulkSelection.attach(document.getElementById('dataset-table'));

    loadDatasetTable();

    // Search handler
    document.getElementById('dataset-search').addEventListener('input', () => {
        renderDatasetTable(getDatasetItems());
    });

    // Media search handler
    document.getElementById('media-search').addEventListener('input', async () => {
        const res = await fetchAuth(API_BASE + '/videos');
        if (res.ok) {
            const videos = await res.json();
            renderMediaGrid(videos);
        }
    });

    // Expose for inline onclick in templates
    window.openDatasetModal = openDatasetModal;
    window.saveDatasetItem = saveDatasetItem;
    window.deleteDatasetItem = deleteDatasetItem;
    window.bulkDeleteDatasetItems = bulkDeleteDatasetItems;
    window.removeVideo = removeVideo;
    window.openMediaBrowser = openMediaBrowser;
    window.selectVideo = selectVideo;
    window.deleteMediaVideo = deleteMediaVideo;
    window.uploadFromMediaBrowser = uploadFromMediaBrowser;

    // Import / Export
    window.exportDataset = (format) => exportResource('dataset', format);
    window.openImportModal = openImportModal;
    window.submitImport = () => submitImportResource('dataset', loadDatasetTable);
}
