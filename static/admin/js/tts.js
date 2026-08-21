// AI → Text to speech. Drives the Chatterbox engine through the admin proxy
// in app/routers/tts.py; port 8003 is never touched from the browser.
//
// The page has one job an operator does constantly (type text, press بشنو) and
// one they do rarely (add a voice). So the defaults are chosen to work with
// nothing configured, the three model parameters are folded away behind
// «تنظیمات پیشرفته», and every failure is reported as a Persian sentence in
// place rather than as a status code.
import { fetchAuth, showMsg, escapeHtml } from './utils.js';

// Chatterbox's own defaults. Named once so the reset button and the initial
// render cannot drift apart.
const DEFAULTS = { exaggeration: 0.5, cfg_weight: 0.5, temperature: 0.8 };

const SLIDERS = [
    { input: 'p-exaggeration', out: 'v-exaggeration', key: 'exaggeration' },
    { input: 'p-cfg', out: 'v-cfg', key: 'cfg_weight' },
    { input: 'p-temperature', out: 'v-temperature', key: 'temperature' },
];

// The clip waiting to be named and saved — from the recorder or the file
// picker, whichever the operator used last. One variable, because the save box
// is shared and two sources of truth would let a stale one be uploaded.
let pendingClip = null;      // { blob, filename }
let mediaRecorder = null;
let recTimer = null;
let recStart = 0;
// Each <audio> keeps ONE object URL, revoked when it is replaced. Tracked per
// player rather than in one list, because a shared list meant pressing «بشنو»
// revoked the recording the operator was still auditioning.
const liveUrl = { preview: '', rec: '', file: '' };

function el(id) { return document.getElementById(id); }

function swapUrl(slot, blob) {
    if (liveUrl[slot]) URL.revokeObjectURL(liveUrl[slot]);
    liveUrl[slot] = URL.createObjectURL(blob);
    return liveUrl[slot];
}

export async function initTTS() {
    el('btn-refresh-status').onclick = loadStatus;
    el('btn-speak').onclick = speak;
    el('btn-reset-params').onclick = resetParams;
    el('btn-record').onclick = startRecording;
    el('btn-stop').onclick = stopRecording;
    el('btn-save-voice').onclick = saveVoice;
    el('voice-file').onchange = pickFile;
    el('tts-text').oninput = updateCharCount;

    SLIDERS.forEach(s => {
        const input = el(s.input);
        input.oninput = () => { el(s.out).innerText = Number(input.value).toFixed(2); };
        input.oninput();
    });

    updateCharCount();
    await loadStatus();
    await loadVoices();
}

function updateCharCount() {
    // Persian digits: the rest of the panel counts in them, so this does too.
    const n = el('tts-text').value.length;
    el('tts-char-count').innerText = n.toLocaleString('fa-IR');
}

function params() {
    const out = {};
    SLIDERS.forEach(s => { out[s.key] = Number(el(s.input).value); });
    return out;
}

function resetParams() {
    SLIDERS.forEach(s => {
        el(s.input).value = DEFAULTS[s.key];
        el(s.input).oninput();
    });
    showMsg('tts-msg', 'تنظیمات به حالت پیش‌فرض بازگشت', 'success');
}

// ── Engine status ───────────────────────────────────────────────────────

// The proxy answers 200 with reachable:false when the service is down, so a
// dead engine renders as a red badge and a sentence — never a spinner that
// never stops, and never a stack trace.
async function loadStatus() {
    const badge = el('tts-status-badge');
    const detail = el('tts-status-detail');
    badge.className = 'badge bg-secondary-lt';
    badge.innerText = 'در حال بررسی…';
    detail.innerText = 'وضعیت موتور صدا خوانده می‌شود…';

    let data;
    try {
        const res = await fetchAuth('/admin/api/tts/health');
        data = await res.json();
    } catch {
        data = { reachable: false, message_fa: 'ارتباط با سرور برقرار نشد.' };
    }

    if (!data.reachable) {
        badge.className = 'badge bg-danger-lt';
        badge.innerText = 'خاموش';
        detail.innerText = data.message_fa || 'سرویس تبدیل متن به صدا در دسترس نیست.';
        el('btn-speak').disabled = true;
        return;
    }

    el('btn-speak').disabled = false;
    if (data.model_loaded) {
        badge.className = 'badge bg-success-lt';
        badge.innerText = 'آماده';
    } else {
        badge.className = 'badge bg-warning-lt';
        badge.innerText = 'در حال آماده‌سازی';
    }

    // The device line is the reason this panel shows a status bar at all: an
    // operator must be able to see whether the engine fell back to the CPU,
    // because that is the difference between one second and thirty.
    const bits = [];
    if (data.device === 'cuda') bits.push(`روی کارت گرافیک${data.gpu ? ' — ' + data.gpu : ''}`);
    else if (data.device) bits.push('روی پردازندهٔ مرکزی (کند)');
    if (!data.model_loaded) bits.push('مدل هنوز بارگذاری نشده است');
    if (data.error) bits.push(`خطا: ${data.error}`);
    detail.innerText = bits.join(' · ') || 'موتور صدا در حال کار است.';
}

// ── Preview ─────────────────────────────────────────────────────────────

async function speak() {
    const text = el('tts-text').value.trim();
    if (!text) { showMsg('tts-msg', 'ابتدا متنی بنویسید', 'danger'); return; }

    const btn = el('btn-speak');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm ms-2"></span>در حال ساخت صدا…';

    try {
        const res = await fetchAuth('/admin/api/tts/preview', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text, voice: el('tts-voice').value, ...params() }),
        });
        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            showMsg('tts-msg', body.detail || 'ساخت صدا ممکن نشد', 'danger');
            return;
        }
        const cached = res.headers.get('X-TTS-Cache');
        const blob = await res.blob();
        const url = swapUrl('preview', blob);
        el('tts-audio').src = url;
        el('tts-download').href = url;
        el('tts-download').download = 'padyar-voice.wav';
        const badge = el('tts-cache-badge');
        // 'hit' means the engine had this exact text+settings on disk already.
        badge.className = cached === 'hit' ? 'badge bg-azure-lt' : 'badge bg-secondary-lt';
        badge.innerText = cached === 'hit' ? 'از حافظه — بدون پردازش دوباره' : 'تازه ساخته شد';
        el('tts-player-box').classList.remove('d-none');
        el('tts-audio').play().catch(() => { /* autoplay blocked: the controls are right there */ });
    } catch {
        showMsg('tts-msg', 'ارتباط با سرور برقرار نشد', 'danger');
    } finally {
        btn.disabled = false;
        btn.innerHTML = original;
    }
}

// ── Voice library ───────────────────────────────────────────────────────

async function loadVoices() {
    const host = el('tts-voice-list');
    const select = el('tts-voice');
    const chosen = select.value;

    let data = { voices: [], reachable: false };
    try {
        const res = await fetchAuth('/admin/api/tts/voices');
        data = await res.json();
    } catch { /* fall through to the unreachable rendering */ }

    const voices = data.voices || [];
    select.innerHTML = '<option value="">صدای پیش‌فرض مدل</option>' +
        voices.map(v => `<option value="${escapeHtml(v)}">${escapeHtml(v)}</option>`).join('');
    if (voices.includes(chosen)) select.value = chosen;

    if (!data.reachable) {
        host.innerHTML = '<div class="text-muted">تا وقتی موتور صدا خاموش است، فهرست صداها در دسترس نیست.</div>';
        return;
    }
    if (!voices.length) {
        host.innerHTML = '<div class="text-muted">هنوز صدایی ذخیره نشده است. از دستیار با صدای پیش‌فرض مدل استفاده می‌شود.</div>';
        return;
    }
    host.innerHTML = voices.map(v => `
      <span class="badge bg-blue-lt p-2 ms-2 mb-2">
        <span dir="ltr">${escapeHtml(v)}</span>
        <button class="btn btn-sm btn-ghost-danger py-0 px-1 ms-1" data-del="${escapeHtml(v)}"
                title="حذف این صدا"><i class="fas fa-xmark"></i></button>
      </span>`).join('');
    host.querySelectorAll('[data-del]').forEach(b => b.onclick = () => deleteVoice(b.dataset.del));
}

async function deleteVoice(name) {
    if (!confirm(`صدای «${name}» حذف شود؟`)) return;
    const res = await fetchAuth(`/admin/api/tts/voices/${encodeURIComponent(name)}`, { method: 'DELETE' });
    if (res.ok) { showMsg('tts-msg', 'صدا حذف شد', 'success'); loadVoices(); }
    else {
        const body = await res.json().catch(() => ({}));
        showMsg('tts-msg', body.detail || 'حذف صدا ممکن نشد', 'danger');
    }
}

// ── Recorder ────────────────────────────────────────────────────────────

// MediaRecorder produces whatever the browser prefers (webm/opus in Chrome,
// mp4 in Safari). No attempt is made to force a format: the engine converts
// with ffmpeg on arrival, so accepting the browser's native container is both
// simpler and more reliable than negotiating one here.
async function startRecording() {
    let stream;
    try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
        // The two cases a person actually hits, told apart, because the fix is
        // different: grant permission, versus plug in a microphone.
        const denied = err && (err.name === 'NotAllowedError' || err.name === 'SecurityError');
        el('rec-hint').className = 'text-danger small mt-2';
        el('rec-hint').innerText = denied
            ? 'اجازهٔ دسترسی به میکروفون داده نشد. از نوار آدرس مرورگر، دسترسی میکروفون را برای این صفحه فعال کنید و دوباره تلاش کنید.'
            : 'میکروفونی پیدا نشد. یک میکروفون وصل کنید و دوباره تلاش کنید.';
        return;
    }

    const chunks = [];
    mediaRecorder = new MediaRecorder(stream);
    mediaRecorder.ondataavailable = e => { if (e.data.size) chunks.push(e.data); };
    mediaRecorder.onstop = () => {
        // Releasing the tracks turns the browser's recording indicator off. Not
        // doing it leaves the tab looking like it is still listening, which is
        // both alarming and true.
        stream.getTracks().forEach(t => t.stop());
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
        setPendingClip(blob, 'recording');
        el('rec-preview').src = swapUrl('rec', blob);
        el('rec-preview-box').classList.remove('d-none');
    };
    mediaRecorder.start();

    recStart = Date.now();
    el('btn-record').classList.add('d-none');
    el('btn-stop').classList.remove('d-none');
    el('rec-indicator').classList.remove('d-none');
    el('rec-indicator').classList.add('d-inline-flex');
    el('rec-hint').className = 'text-muted small mt-2';
    el('rec-hint').innerText = 'در حال ضبط… حدود ۵ تا ۲۰ ثانیه صحبت کنید.';
    recTimer = setInterval(() => {
        const seconds = Math.floor((Date.now() - recStart) / 1000);
        el('rec-seconds').innerText = seconds;
        // 30s is the engine's hard ceiling; stopping here saves the operator a
        // rejected upload after they have already finished speaking.
        if (seconds >= 30) stopRecording();
    }, 200);
}

function stopRecording() {
    if (recTimer) { clearInterval(recTimer); recTimer = null; }
    if (mediaRecorder && mediaRecorder.state !== 'inactive') mediaRecorder.stop();
    el('btn-record').classList.remove('d-none');
    el('btn-stop').classList.add('d-none');
    el('rec-indicator').classList.add('d-none');
    el('rec-indicator').classList.remove('d-inline-flex');
    el('rec-hint').className = 'text-muted small mt-2';
    el('rec-hint').innerText = 'ضبط تمام شد. گوش کنید و اگر خوب بود، نامی بگذارید و ذخیره کنید.';
}

function pickFile(e) {
    const file = e.target.files && e.target.files[0];
    if (!file) return;
    setPendingClip(file, file.name);
    el('file-preview').src = swapUrl('file', file);
    el('file-preview-box').classList.remove('d-none');
    // Offer a name straight from the filename so the common case needs no typing.
    if (!el('voice-name').value) {
        el('voice-name').value = file.name.replace(/\.[^.]+$/, '').replace(/[^A-Za-z0-9_-]/g, '').slice(0, 48);
    }
}

function setPendingClip(blob, filename) {
    pendingClip = { blob, filename };
    el('btn-save-voice').disabled = false;
}

async function saveVoice() {
    const msg = el('voice-save-msg');
    const name = el('voice-name').value.trim();
    if (!pendingClip) { msg.className = 'text-danger small'; msg.innerText = 'ابتدا صدایی ضبط کنید یا فایلی انتخاب کنید.'; return; }
    if (!/^[A-Za-z0-9_-]+$/.test(name)) {
        msg.className = 'text-danger small';
        msg.innerText = 'نام را با حروف انگلیسی، عدد، خط تیره یا زیرخط بنویسید. مثلاً masoud';
        return;
    }

    const btn = el('btn-save-voice');
    btn.disabled = true;
    msg.className = 'text-muted small';
    msg.innerText = '⏳ در حال ذخیره…';

    const fd = new FormData();
    fd.append('name', name);
    fd.append('file', pendingClip.blob, pendingClip.filename);

    try {
        const res = await fetchAuth('/admin/api/tts/voices', { method: 'POST', body: fd });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            msg.className = 'text-danger small';
            msg.innerText = '❌ ' + (body.detail || 'ذخیرهٔ صدا ممکن نشد');
            btn.disabled = false;
            return;
        }
        msg.className = 'text-success small';
        msg.innerText = `✅ صدای «${body.name}» ذخیره شد (${body.seconds} ثانیه).`;
        pendingClip = null;
        el('voice-file').value = '';
        await loadVoices();
        el('tts-voice').value = body.name;   // so the next «بشنو» uses it
    } catch {
        msg.className = 'text-danger small';
        msg.innerText = '❌ ارتباط با سرور برقرار نشد';
        btn.disabled = false;
    }
}
