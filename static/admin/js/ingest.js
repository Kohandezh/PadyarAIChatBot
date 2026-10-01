// The review page of the ingest module (docs/features/knowledge-ingestion/SPEC.md,
// section F): send a file or a web address, watch it being read, look at each
// proposal beside the document's own text, approve.
//
// Every string that came from a document, a web page or a model is written with
// textContent, never as markup (SEC-020). Every request goes through fetchAuth,
// so each change carries the CSRF header (REQ-079). Nothing is kept in browser
// storage: on a shared booth browser the next person must not inherit the last
// person's file, so the open file lives only in the address (?job=<id>).
import { fetchAuth } from './utils.js';
import { createPager } from './pager.js';

const API = '/admin/api/ingest';
const PAGE_SIZE = 20;
const JOB_POLL_MS = 3000;
const SEEN_DWELL_MS = 1000;
const SEEN_FLUSH_MS = 2000;
const SEEN_BATCH = 50;
const INDEX_POLL_MS = 2000;
const INDEX_WAIT_MS = 150000;
const RUNNING = ['queued', 'extracting', 'extracted', 'proposing', 'cancelling'];
const REVIEWABLE = ['done', 'failed', 'local'];
const EDIT_FIELDS = ['title', 'text', 'questions', 'synonyms'];
const PERSIAN = /[\u0600-\u06FF]/;

const fa = (n) => String(n).replace(/\d/g, (d) => '۰۱۲۳۴۵۶۷۸۹'[d]);

// The sentences of SPEC sections 8 and 10. No code, no technical word (REQ-080).
const T = {
    read: 'بخوان',
    sending: 'در حال فرستادن…',
    reading: 'در حال خواندن فایل…',
    preparing: (done, total) => `در حال آماده کردن پیشنهادها: ${fa(done)} از ${fa(total)}`,
    cancelling: 'در حال لغو…',
    cancelQuestion: 'کار این فایل متوقف شود؟ پیشنهادهای بررسی‌نشده حذف می‌شوند.',
    cancelled: 'کار این فایل لغو شده است.',
    aiStopped: (n) => `هوش مصنوعی در بخش ${fa(n)} متوقف شد؛ بقیهٔ پیشنهادها بدون پرسش آماده شدند.`,
    ready: (n) => `${fa(n)} پیشنهاد آماده است. هرکدام را کنار متن اصلی ببینید و تأیید کنید.`,
    allReviewed: 'همهٔ پیشنهادهای این فایل بررسی شده‌اند.',
    approveSeen: (n) => `تأیید همهٔ موارد دیده‌شده (${fa(n)})`,
    bulkDone: (n) => `${fa(n)} پیشنهاد تأیید شد.`,
    bulkNone: 'هیچ پیشنهادی تأیید نشد.',
    bulkSkipped: (n) => `${fa(n)} مورد تأیید نشد و در فهرست ماند.`,
    updating: 'تأیید شد؛ در حال به‌روزرسانی پاسخ‌ها…',
    updatingBulk: 'در حال به‌روزرسانی پاسخ‌ها…',
    live: 'از همین حالا در پاسخ‌ها است',
    slow: 'پاسخ ذخیره شد و به‌زودی در چت دیده می‌شود.',
    rejected: 'رد شد.',
    waiting: 'در حال آماده شدن',
    similar: (title) => (title ? `شبیه یک پاسخ موجود است: ${title}` : 'شبیه یک پاسخ موجود است.'),
    company: (name) => `به نظر شناسنامهٔ شرکت ${name} است؛ جایش صفحهٔ شرکت‌ها است.`,
    duplicate: 'این متن تکراری است.',
    sameTitle: (title) => `عنوانی برابر با پاسخ «${title}» دارد؛ اگر لازم است عنوان را عوض کنید.`,
    modelTitle: 'عنوان پیشنهادی',
    source: 'متن اصلی سند',
    answer: 'پاسخ پیشنهادی',
    questions: 'پرسش‌ها',
    synonyms: 'مترادف‌ها',
    approve: 'تأیید',
    edit: 'ویرایش',
    reject: 'رد',
    approveFor: (title) => `تأیید پیشنهاد: ${title}`,
    editFor: (title) => `ویرایش پیشنهاد: ${title}`,
    rejectFor: (title) => `رد پیشنهاد: ${title}`,
    save: 'ذخیره و تأیید',
    saveOnly: 'ذخیره',
    saved: 'ذخیره شد.',
    cancelEdit: 'انصراف',
    fieldTitle: 'عنوان',
    fieldText: 'متن پاسخ',
    fieldQuestions: 'پرسش‌ها (هر پرسش در یک خط)',
    fieldSynonyms: 'مترادف‌ها (هر خط: کلمه = مترادف)',
    synonymLine: 'هر خط مترادف باید به شکل «کلمه = مترادف» باشد.',
    unreadable: 'این فایل خوانده نشد.',
    failedRequest: 'کار انجام نشد. اتصال را بررسی کنید و دوباره امتحان کنید.',
    jobPreparing: 'در حال آماده شدن',
    jobCancelling: 'در حال لغو',
    jobReady: (n) => `آمادهٔ بررسی · ${fa(n)} پیشنهاد`,
    jobDone: 'تمام شد',
    jobFailed: 'خوانده نشد',
    jobCancelled: 'لغو شد',
};

const $ = (id) => document.getElementById(id);

function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
}

async function call(url, options) {
    let res;
    try {
        res = await fetchAuth(url, options);
    } catch {
        return { ok: false, status: 0, body: null };
    }
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    return { ok: res.ok, status: res.status, body };
}

const postJson = (payload) => ({
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
});

// The server's refusal is a Persian sentence (SPEC section 6). Anything else,
// a framework default in English or a body that is not JSON, becomes one
// plain sentence: no status, no code, no English reaches the admin.
function refusal(result) {
    const detail = result.body && typeof result.body === 'object' ? result.body.detail : null;
    return typeof detail === 'string' && PERSIAN.test(detail) ? detail : T.failedRequest;
}

const enc = encodeURIComponent;
const lines = (value) => value.split('\n').map((line) => line.trim()).filter(Boolean);

const view = {
    jobId: null,
    job: null,
    counts: null,
    pollTimer: null,
    lastStatus: null,
    lastAiDone: null,
    busy: false,
    bulkBusy: false,
    cards: new Map(),
    handledOnPage: 0,
    lastOffset: 0,
};

// ── REQ-074: a card is seen after one second at least half in view ──────
// A card taller than two screens can never be half in view, so it also
// counts once its visible part covers half the screen height. The fine
// thresholds make the observer report as that part grows and shrinks.

const SEEN_STEPS = Array.from({ length: 101 }, (_, i) => i / 100);

function lookedAt(entry) {
    if (!entry.isIntersecting || document.hidden) return false;
    const screen = entry.rootBounds ? entry.rootBounds.height : window.innerHeight;
    return entry.intersectionRatio >= 0.5 || entry.intersectionRect.height >= screen / 2;
}

const seen = {
    observer: null,
    timers: new Map(),
    queue: new Set(),
    flushTimer: null,

    init() {
        this.observer = new IntersectionObserver((entries) => entries.forEach((e) => this.onEntry(e)),
            { threshold: SEEN_STEPS });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) [...this.timers.keys()].forEach((id) => this.stop(id));
        });
    },

    watch(card) {
        if (!card.data.seen) this.observer.observe(card.el);
    },

    forget(card) {
        this.observer.unobserve(card.el);
        this.stop(card.data.id);
    },

    stop(id) {
        clearTimeout(this.timers.get(id));
        this.timers.delete(id);
    },

    onEntry(entry) {
        const id = entry.target.dataset.id;
        if (lookedAt(entry)) {
            if (!this.timers.has(id)) {
                this.timers.set(id, setTimeout(() => this.mark(id, entry.target), SEEN_DWELL_MS));
            }
        } else {
            this.stop(id);
        }
    },

    mark(id, target) {
        this.timers.delete(id);
        this.observer.unobserve(target);
        this.queue.add(id);
        if (!this.flushTimer) this.flushTimer = setTimeout(() => this.flush(), SEEN_FLUSH_MS);
    },

    async flush() {
        const ids = [...this.queue].slice(0, SEEN_BATCH);
        ids.forEach((id) => this.queue.delete(id));
        const result = ids.length ? await call(`${API}/proposals/seen`, postJson({ ids })) : { ok: true };
        if (!result.ok) ids.forEach((id) => this.queue.add(id));
        this.flushTimer = this.queue.size ? setTimeout(() => this.flush(), SEEN_FLUSH_MS) : null;
        if (result.ok && ids.length) {
            ids.forEach((id) => {
                const card = view.cards.get(id);
                if (card) card.data.seen = true;
            });
            await refreshJob();
        }
    },
};

// ── REQ-082: one poller for every approval waiting to go live ───────────
// Each approval waits for a published version above the one its answer
// named, or gives up after 150 seconds with an honest sentence: the row is
// saved either way, only the index is late.

const indexWatch = {
    waiters: new Set(),
    timer: null,

    add(before, onLive, onSlow) {
        const waiter = { before, onLive };
        waiter.deadline = setTimeout(() => {
            this.waiters.delete(waiter);
            onSlow();
        }, INDEX_WAIT_MS);
        this.waiters.add(waiter);
        if (!this.timer) this.timer = setTimeout(() => this.tick(), INDEX_POLL_MS);
    },

    async tick() {
        if (this.waiters.size) {
            const result = await call(`${API}/index-status`);
            const version = result.ok && result.body ? result.body.version : null;
            if (Number.isFinite(version)) {
                this.waiters.forEach((w) => {
                    if (version <= w.before) return;
                    clearTimeout(w.deadline);
                    this.waiters.delete(w);
                    w.onLive();
                });
            }
        }
        this.timer = this.waiters.size ? setTimeout(() => this.tick(), INDEX_POLL_MS) : null;
    },
};

// ── Sending a file or an address (REQ-071) ───────────────────────────────

function showAlert(text) {
    $('ingest-alert-text').textContent = text;
    $('ingest-alert').hidden = false;
}

function hideAlert() {
    $('ingest-alert').hidden = true;
}

function syncRead() {
    $('ingest-read').disabled = view.busy || !$('ingest-url').value.trim();
}

function setBusy(on) {
    view.busy = on;
    $('ingest-pick').disabled = on;
    $('ingest-url').disabled = on;
    $('ingest-read').textContent = on ? T.sending : T.read;
    syncRead();
}

async function send(path, options) {
    hideAlert();
    setBusy(true);
    const result = await call(path, options);
    setBusy(false);
    const job = result.ok && result.body ? result.body.job : null;
    if (!job) {
        showAlert(refusal(result));
        return false;
    }
    history.replaceState(null, '', `?job=${enc(job.id)}`);
    await openJob(job.id);
    jobsPager.reset();
    await jobsPager.load();
    return true;
}

function sendFile(file) {
    const form = new FormData();
    form.append('file', file);
    return send(`${API}/jobs/upload`, { method: 'POST', body: form });
}

async function sendUrl(url) {
    if (await send(`${API}/jobs/url`, postJson({ url }))) {
        $('ingest-url').value = '';
        syncRead();
    }
}

function startOver() {
    hideAlert();
    closeJob();
    history.replaceState(null, '', location.pathname);
    $('ingest-pick').focus();
}

function wireInput() {
    const file = $('ingest-file');
    const drop = $('ingest-drop');
    $('ingest-pick').addEventListener('click', () => file.click());
    file.addEventListener('change', () => {
        const chosen = file.files[0];
        file.value = '';
        if (chosen && !view.busy) sendFile(chosen);
    });
    drop.addEventListener('dragover', (e) => {
        e.preventDefault();
        if (!view.busy) drop.classList.add('dragover');
    });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
    drop.addEventListener('drop', (e) => {
        e.preventDefault();
        drop.classList.remove('dragover');
        const dropped = e.dataTransfer && e.dataTransfer.files[0];
        if (dropped && !view.busy) sendFile(dropped);
    });
    $('ingest-url').addEventListener('input', syncRead);
    $('ingest-url-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const url = $('ingest-url').value.trim();
        if (url && !view.busy) sendUrl(url);
    });
    $('ingest-alert-retry').addEventListener('click', startOver);
}

// ── One file: progress and counts (REQ-072, REQ-075) ─────────────────────

function closeJob() {
    clearTimeout(view.pollTimer);
    Object.assign(view, { jobId: null, job: null, counts: null, pollTimer: null,
        lastStatus: null, lastAiDone: null, handledOnPage: 0, lastOffset: 0 });
    cardsPager.reset();
    $('ingest-progress').hidden = true;
    $('ingest-review').hidden = true;
    clearCards();
}

async function openJob(id) {
    closeJob();
    view.jobId = id;
    await refreshJob();
}

function hasProposals(job, counts) {
    if (['queued', 'extracting', 'cancelled', 'cancelling'].includes(job.status)) return false;
    return counts.pending + counts.approved + counts.rejected > 0;
}

function stage(job, counts) {
    if (job.status === 'queued' || job.status === 'extracting') return T.reading;
    if (job.status === 'cancelling') return T.cancelling;
    return T.preparing(counts.ai_done, counts.chunk_count);
}

function renderProgress(job, counts) {
    $('ingest-progress').hidden = false;
    $('ingest-job-name').textContent = job.source_name || '';
    const running = RUNNING.includes(job.status);
    $('ingest-running').hidden = !running;
    $('ingest-cancel').hidden = job.status === 'cancelling';
    $('ingest-stage').textContent = running ? stage(job, counts) : '';
    const bar = $('ingest-bar');
    const known = counts.chunk_count > 0 && ['extracted', 'proposing'].includes(job.status);
    bar.classList.toggle('progress-bar-striped', !known);
    bar.classList.toggle('progress-bar-animated', !known);
    if (known) {
        const percent = Math.round((100 * counts.ai_done) / counts.chunk_count);
        bar.style.width = `${percent}%`;
        bar.setAttribute('aria-valuenow', String(percent));
    } else {
        bar.style.width = '100%';
        bar.removeAttribute('aria-valuenow');
    }
    $('ingest-ended').hidden = job.status !== 'cancelled';
    $('ingest-ended').textContent = job.status === 'cancelled' ? T.cancelled : '';
    const stopped = job.ai_stopped_at !== null && job.ai_stopped_at !== undefined;
    $('ingest-ai-stopped').hidden = !stopped;
    $('ingest-ai-stopped').textContent = stopped ? T.aiStopped(job.ai_stopped_at) : '';
    $('ingest-progress-body').hidden = !running && job.status !== 'cancelled' && !stopped;
    if (job.status === 'failed') showAlert(job.error_message || T.unreadable);
}

function renderSummary(job, counts) {
    let text = '';
    if (counts.pending === 0 && !RUNNING.includes(job.status)) text = T.allReviewed;
    else if (job.status === 'ready' || job.status === 'failed') text = T.ready(counts.pending);
    $('ingest-summary').textContent = text;

    const n = counts.approvable_seen || 0;
    const button = $('ingest-approve-seen');
    $('ingest-approve-seen-label').textContent = T.approveSeen(n);
    button.disabled = n === 0 || view.bulkBusy;
    $('ingest-approve-seen-hint').hidden = n > 0;
    if (n === 0) button.setAttribute('aria-describedby', 'ingest-approve-seen-hint');
    else button.removeAttribute('aria-describedby');
}

// Reads the file's state again. The proposal list is read again only when it
// can have changed by itself: the status moved, or the model finished more
// parts. Returns whether it was read.
async function refreshJob() {
    const id = view.jobId;
    if (!id) return false;
    const result = await call(`${API}/jobs/${enc(id)}`);
    if (id !== view.jobId) return false;
    if (!result.ok || !result.body || !result.body.job) {
        // While the file is still being read, one lost answer is retried at
        // the next tick instead of stopping the progress for good.
        const transient = result.status === 0 || result.status >= 500;
        if (transient && view.job && RUNNING.includes(view.job.status)) {
            clearTimeout(view.pollTimer);
            view.pollTimer = setTimeout(refreshJob, JOB_POLL_MS);
        } else {
            showAlert(refusal(result));
        }
        return false;
    }
    const { job, counts } = result.body;
    // The recent-files list shows this file's status too.
    if (view.lastStatus !== null && job.status !== view.lastStatus) jobsPager.load();
    const changed = job.status !== view.lastStatus || counts.ai_done !== view.lastAiDone;
    Object.assign(view, { job, counts, lastStatus: job.status, lastAiDone: counts.ai_done });
    renderProgress(job, counts);
    const reviewable = hasProposals(job, counts);
    $('ingest-review').hidden = !reviewable;
    if (reviewable) renderSummary(job, counts);
    else clearCards();
    clearTimeout(view.pollTimer);
    view.pollTimer = RUNNING.includes(job.status) ? setTimeout(refreshJob, JOB_POLL_MS) : null;
    if (reviewable && changed) {
        await cardsPager.load();
        return true;
    }
    return false;
}

async function cancelJob() {
    const id = view.jobId;
    if (!id || !window.confirm(T.cancelQuestion)) return;
    const result = await call(`${API}/jobs/${enc(id)}/cancel`, { method: 'POST' });
    if (!result.ok) showAlert(refusal(result));
    await refreshJob();
}

async function approveSeen() {
    const id = view.jobId;
    if (!id) return;
    view.bulkBusy = true;
    $('ingest-approve-seen').disabled = true;
    $('ingest-bulk-result').textContent = '';
    $('ingest-bulk-index').textContent = '';
    const result = await call(`${API}/jobs/${enc(id)}/approve-seen`, { method: 'POST' });
    view.bulkBusy = false;
    if (!result.ok || !result.body) {
        $('ingest-bulk-result').textContent = refusal(result);
    } else {
        const { approved = 0, skipped = [], index_version_before: before } = result.body;
        let text = approved ? T.bulkDone(approved) : T.bulkNone;
        if (skipped.length) text += ` ${T.bulkSkipped(skipped.length)}`;
        $('ingest-bulk-result').textContent = text;
        if (approved) {
            const line = $('ingest-bulk-index');
            line.textContent = T.updatingBulk;
            indexWatch.add(before, () => { line.textContent = T.live; }, () => { line.textContent = T.slow; });
        }
    }
    cardsPager.reset();
    view.handledOnPage = 0;
    if (!(await refreshJob()) && !$('ingest-review').hidden) await cardsPager.load();
}

// ── The cards (REQ-073, REQ-076, REQ-078) ────────────────────────────────

function clearCards() {
    view.cards.forEach((card) => seen.forget(card));
    view.cards.clear();
    $('ingest-cards').replaceChildren();
}

const signature = (p) => JSON.stringify([p.title, p.text, p.title_source, p.questions, p.synonyms,
    p.ai_state, p.similar_kind, p.similar_title, p.same_title_as, p.source_text]);

let fieldCounter = 0;

function buildCard(p) {
    const card = { data: p, sig: '', state: 'pending', editing: false, parts: {} };
    const root = el('article', 'card ingest-card');
    root.dataset.id = p.id;
    root.dataset.state = 'pending';
    const body = el('div', 'card-body');
    const notes = el('div');
    const fold = el('div', 'ingest-body');
    const row = el('div', 'row g-3');
    const left = el('div', 'col-lg-6');
    const right = el('div', 'col-lg-6');
    const source = el('div', 'ingest-source');
    source.tabIndex = 0;
    source.setAttribute('role', 'region');
    source.setAttribute('aria-label', T.source);
    left.append(el('h4', '', T.source), source);
    const answer = el('div', 'ingest-answer');
    right.append(el('h4', '', T.answer), answer);
    row.append(left, right);

    const actions = el('div', 'ingest-actions');
    const approve = el('button', 'btn btn-success ingest-approve', T.approve);
    const edit = el('button', 'btn btn-outline-primary ingest-edit', T.edit);
    const reject = el('button', 'btn btn-outline-danger ingest-reject', T.reject);
    const waitNote = el('span', 'small text-muted ingest-wait-note', T.waiting);
    waitNote.id = `ingest-wait-${++fieldCounter}`;
    [approve, edit, reject].forEach((b) => { b.type = 'button'; });
    actions.append(approve, edit, reject, waitNote);
    fold.append(row, actions);

    const message = el('p', 'ingest-card-msg small');
    message.setAttribute('role', 'status');
    body.append(notes, fold, message);
    root.append(body);

    card.el = root;
    Object.assign(card.parts, { notes, fold, source, answer, right, actions, approve, edit, reject,
        waitNote, message });
    approve.addEventListener('click', () => approveCard(card));
    edit.addEventListener('click', () => openEditor(card));
    reject.addEventListener('click', () => rejectCard(card));
    fill(card);
    return card;
}

function chips(items, className) {
    const box = el('div', 'ingest-chips');
    items.forEach((text) => box.append(el('span', className, text)));
    return box;
}

function fill(card) {
    const p = card.data;
    const { notes, source, answer, approve, edit, reject, waitNote } = card.parts;
    card.sig = signature(p);

    notes.replaceChildren();
    if (p.similar_kind) {
        const band = el('div', 'alert alert-warning py-2 mb-3 ingest-similar');
        band.append(el('i', 'fas fa-triangle-exclamation me-1'));
        band.lastChild.setAttribute('aria-hidden', 'true');
        const text = p.similar_kind === 'company' ? T.company(p.similar_title)
            : p.similar_kind === 'duplicate' ? T.duplicate : T.similar(p.similar_title);
        band.append(el('span', 'ingest-break', text));
        notes.append(band);
    }
    if (p.same_title_as) notes.append(el('p', 'small text-muted mb-3 ingest-same-title', T.sameTitle(p.title)));

    source.textContent = p.source_text || '';

    const titleRow = el('div', 'ingest-title-row');
    titleRow.append(el('h3', 'ingest-break ingest-title', p.title));
    if (p.title_source === 'model') titleRow.append(el('span', 'badge bg-info-lt', T.modelTitle));
    const parts = [titleRow, el('div', 'ingest-text', p.text)];
    const questions = Array.isArray(p.questions) ? p.questions : [];
    if (questions.length) {
        parts.push(el('h4', 'mt-3', T.questions), chips(questions, 'badge bg-secondary-lt'));
    }
    const synonyms = Array.isArray(p.synonyms) ? p.synonyms : [];
    if (synonyms.length) {
        parts.push(el('h4', 'mt-3', T.synonyms),
            chips(synonyms.map((s) => `${s.word} ← ${s.suggestion}`), 'badge bg-azure-lt'));
    }
    answer.replaceChildren(...parts);

    const ready = REVIEWABLE.includes(p.ai_state);
    approve.disabled = !ready;
    edit.disabled = false;
    waitNote.hidden = ready;
    if (ready) approve.removeAttribute('aria-describedby');
    else approve.setAttribute('aria-describedby', waitNote.id);
    approve.setAttribute('aria-label', T.approveFor(p.title));
    edit.setAttribute('aria-label', T.editFor(p.title));
    reject.setAttribute('aria-label', T.rejectFor(p.title));
}

function setCardState(card, state, text, tone) {
    card.state = state;
    card.el.dataset.state = state;
    const { message } = card.parts;
    message.textContent = text || '';
    message.className = `ingest-card-msg small${tone ? ` text-${tone}` : ''}`;
}

function setCardButtons(card, enabled) {
    const ready = REVIEWABLE.includes(card.data.ai_state);
    card.parts.approve.disabled = !enabled || !ready;
    card.parts.edit.disabled = !enabled;
    card.parts.reject.disabled = !enabled;
}

function settle(card, state, text) {
    setCardState(card, state, text, state === 'rejected' ? 'muted' : 'success');
    card.parts.fold.hidden = true;
}

async function approveCard(card) {
    setCardButtons(card, false);
    setCardState(card, 'busy', '');
    const result = await call(`${API}/proposals/${enc(card.data.id)}/approve`, { method: 'POST' });
    if (!result.ok || !result.body) {
        setCardState(card, 'pending', refusal(result), 'danger');
        setCardButtons(card, true);
        await refreshJob();
        return;
    }
    seen.forget(card);
    view.handledOnPage += 1;
    card.parts.actions.hidden = true;
    setCardState(card, 'approved', T.updating, 'success');
    indexWatch.add(result.body.index_version_before,
        () => settle(card, 'live', T.live), () => settle(card, 'slow', T.slow));
    await refreshJob();
}

async function rejectCard(card) {
    setCardButtons(card, false);
    const result = await call(`${API}/proposals/${enc(card.data.id)}/reject`, postJson({}));
    if (!result.ok) {
        setCardState(card, 'pending', refusal(result), 'danger');
        setCardButtons(card, true);
        await refreshJob();
        return;
    }
    seen.forget(card);
    view.handledOnPage += 1;
    settle(card, 'rejected', T.rejected);
    await refreshJob();
}

// ── Editing in the card (REQ-076) ────────────────────────────────────────

function field(form, name, label, control) {
    const id = `ingest-field-${++fieldCounter}`;
    const wrap = el('div', 'ingest-field');
    const caption = el('label', 'form-label', label);
    caption.htmlFor = id;
    control.id = id;
    control.className = `form-control ingest-f-${name}`;
    const error = el('div', 'small text-danger mt-1');
    error.dataset.errorFor = name;
    error.id = `${id}-error`;
    error.hidden = true;
    wrap.append(caption, control, error);
    form.append(wrap);
    return control;
}

function buildEditor(card) {
    const form = el('form', 'ingest-editor');
    form.noValidate = true;
    const title = field(form, 'title', T.fieldTitle, el('input'));
    title.type = 'text';
    const text = field(form, 'text', T.fieldText, el('textarea'));
    text.rows = 6;
    const questions = field(form, 'questions', T.fieldQuestions, el('textarea'));
    questions.rows = 3;
    const synonyms = field(form, 'synonyms', T.fieldSynonyms, el('textarea'));
    synonyms.rows = 3;
    const buttons = el('div', 'ingest-actions');
    const save = el('button', 'btn btn-success ingest-save', T.save);
    save.type = 'submit';
    const cancel = el('button', 'btn btn-outline-secondary ingest-cancel-edit', T.cancelEdit);
    cancel.type = 'button';
    buttons.append(save, cancel);
    form.append(buttons);
    form.addEventListener('submit', (e) => {
        e.preventDefault();
        saveAndApprove(card);
    });
    cancel.addEventListener('click', () => closeEditor(card, true));
    form.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            e.preventDefault();
            closeEditor(card, true);
        }
    });
    card.parts.right.append(form);
    Object.assign(card.parts, { form, title, text, questions, synonyms, save, cancel });
}

function showFieldError(card, name, text) {
    const control = card.parts[name];
    const error = card.parts.form.querySelector(`[data-error-for="${name}"]`);
    error.textContent = text;
    error.hidden = false;
    control.setAttribute('aria-invalid', 'true');
    control.setAttribute('aria-describedby', error.id);
    control.focus();
}

function clearFieldErrors(card) {
    EDIT_FIELDS.forEach((name) => {
        const error = card.parts.form.querySelector(`[data-error-for="${name}"]`);
        error.hidden = true;
        error.textContent = '';
        card.parts[name].removeAttribute('aria-invalid');
        card.parts[name].removeAttribute('aria-describedby');
    });
}

function openEditor(card) {
    if (!card.parts.form) buildEditor(card);
    const p = card.data;
    card.parts.title.value = p.title || '';
    card.parts.text.value = p.text || '';
    card.parts.questions.value = (p.questions || []).join('\n');
    card.parts.synonyms.value = (p.synonyms || []).map((s) => `${s.word} = ${s.suggestion}`).join('\n');
    clearFieldErrors(card);
    card.parts.save.textContent = REVIEWABLE.includes(p.ai_state) ? T.save : T.saveOnly;
    card.editing = true;
    card.parts.answer.hidden = true;
    card.parts.actions.hidden = true;
    card.parts.form.hidden = false;
    setCardState(card, 'pending', '');
    card.parts.title.focus();
}

function closeEditor(card, giveFocusBack) {
    card.editing = false;
    card.parts.form.hidden = true;
    card.parts.answer.hidden = false;
    card.parts.actions.hidden = false;
    if (signature(card.data) !== card.sig) fill(card);
    if (giveFocusBack) card.parts.edit.focus();
}

function sameList(a, b) {
    return JSON.stringify(a) === JSON.stringify(b || []);
}

async function saveAndApprove(card) {
    clearFieldErrors(card);
    const p = card.data;
    const synonyms = [];
    for (const line of lines(card.parts.synonyms.value)) {
        const at = line.indexOf('=');
        if (at < 0) {
            showFieldError(card, 'synonyms', T.synonymLine);
            return;
        }
        synonyms.push({ word: line.slice(0, at).trim(), suggestion: line.slice(at + 1).trim() });
    }
    const values = {
        title: card.parts.title.value.trim(),
        text: card.parts.text.value.trim(),
        questions: lines(card.parts.questions.value),
        synonyms,
    };
    const changes = {};
    if (values.title !== p.title) changes.title = values.title;
    if (values.text !== p.text) changes.text = values.text;
    if (!sameList(values.questions, p.questions)) changes.questions = values.questions;
    if (!sameList(values.synonyms, (p.synonyms || []).map((s) => ({ word: s.word, suggestion: s.suggestion })))) {
        changes.synonyms = values.synonyms;
    }
    if (Object.keys(changes).length) {
        card.parts.save.disabled = true;
        const result = await call(`${API}/proposals/${enc(p.id)}`, {
            method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
        });
        card.parts.save.disabled = false;
        if (!result.ok || !result.body || !result.body.proposal) {
            const name = result.body && result.body.field;
            if (result.status === 422 && EDIT_FIELDS.includes(name)) showFieldError(card, name, refusal(result));
            else setCardState(card, 'pending', refusal(result), 'danger');
            return;
        }
        card.data = result.body.proposal;
        fill(card);
    }
    // A card still being prepared cannot be approved yet (section 10):
    // the edit is kept and «تأیید» opens once the card is ready.
    if (!REVIEWABLE.includes(card.data.ai_state)) {
        closeEditor(card, true);
        if (Object.keys(changes).length) setCardState(card, 'pending', T.saved, 'success');
        return;
    }
    closeEditor(card, false);
    await approveCard(card);
}

// ── The list of cards, 20 to a page (REQ-077) ────────────────────────────

async function loadCards(offset, limit) {
    const id = view.jobId;
    if (!id) return;
    // Approved and rejected cards left the pending list, so the next page
    // starts that many places earlier than the pager thinks.
    if (offset > view.lastOffset && view.handledOnPage) {
        offset = Math.max(0, offset - view.handledOnPage);
        cardsPager.state.offset = offset;
    }
    const samePage = offset === view.lastOffset;
    view.lastOffset = offset;
    const result = await call(`${API}/jobs/${enc(id)}/proposals?status=pending&limit=${limit}&offset=${offset}`);
    if (id !== view.jobId) return;
    if (!result.ok || !result.body) {
        showAlert(refusal(result));
        return;
    }
    const items = result.body.items || [];
    const keep = new Map();
    items.forEach((p) => {
        let card = view.cards.get(p.id);
        if (!card) {
            card = buildCard(p);
            seen.watch(card);
        } else if (card.editing) {
            card.data = p;
        } else if (card.state === 'pending' && signature(p) !== card.sig) {
            card.data = p;
            fill(card);
        }
        keep.set(p.id, card);
    });
    // On the same page a card the admin just handled stays where it was,
    // with its message, until the page changes.
    view.cards.forEach((card, cardId) => {
        if (keep.has(cardId)) return;
        if (samePage && card.state !== 'pending') keep.set(cardId, card);
        else seen.forget(card);
    });
    if (!samePage) view.handledOnPage = 0;
    view.cards = keep;
    const ordered = [...keep.values()].sort((a, b) => a.data.seq - b.data.seq);
    $('ingest-cards').replaceChildren(...ordered.map((card) => card.el));
    cardsPager.setResult({ shown: items.length, total: result.body.total });
    $('ingest-cards-pager').hidden = offset === 0 && result.body.total <= limit;
}

// ── Recent files (section 10, Default) ───────────────────────────────────

function jobStatus(job) {
    switch (job.status) {
        // The list API counts every proposal of the file, not the ones
        // still waiting, so the number is the file's total, set apart from
        // the state word instead of read as "N left to review".
        case 'ready': return T.jobReady(job.chunk_count);
        case 'done': return T.jobDone;
        case 'failed': return T.jobFailed;
        case 'cancelled': return T.jobCancelled;
        case 'cancelling': return T.jobCancelling;
        default: return T.jobPreparing;
    }
}

function jobRow(job, openId) {
    const row = el('a', 'list-group-item list-group-item-action ingest-job-row');
    row.href = `?job=${enc(job.id)}`;
    if (job.id === openId) {
        row.classList.add('active');
        row.setAttribute('aria-current', 'page');
    }
    const name = el('div', 'ingest-break fw-medium', job.source_name);
    row.append(name, el('div', 'small ingest-job-status', jobStatus(job)));
    return row;
}

async function loadJobs(offset, limit) {
    const result = await call(`${API}/jobs?limit=${limit}&offset=${offset}`);
    const list = $('ingest-jobs');
    if (!result.ok || !result.body) {
        list.replaceChildren(el('p', 'text-danger small mb-0', refusal(result)));
        return;
    }
    const items = result.body.items || [];
    const openId = new URLSearchParams(location.search).get('job');
    list.replaceChildren(...items.map((job) => jobRow(job, openId)));
    $('ingest-jobs-empty').hidden = result.body.total > 0;
    jobsPager.setResult({ shown: items.length, total: result.body.total });
    $('ingest-jobs-pager').hidden = offset === 0 && result.body.total <= limit;
}

const cardsPager = createPager({
    prevBtnEl: $('ingest-cards-prev'), nextBtnEl: $('ingest-cards-next'), rangeEl: $('ingest-cards-range'),
    defaultLimit: PAGE_SIZE, onPage: loadCards,
});

const jobsPager = createPager({
    prevBtnEl: $('ingest-jobs-prev'), nextBtnEl: $('ingest-jobs-next'), rangeEl: $('ingest-jobs-range'),
    defaultLimit: PAGE_SIZE, onPage: loadJobs,
});

export async function initIngest() {
    seen.init();
    wireInput();
    $('ingest-cancel').addEventListener('click', cancelJob);
    $('ingest-approve-seen').addEventListener('click', approveSeen);
    const jobId = new URLSearchParams(location.search).get('job');
    try {
        await Promise.all([jobsPager.load(), jobId ? openJob(jobId) : null]);
    } finally {
        $('ingest-root').dataset.ready = '1';
    }
}
