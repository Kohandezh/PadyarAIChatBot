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
// The voice this installation saved as its default. loadVoices() cannot simply
// set select.value before the <option> elements exist, so it is remembered here
// and applied once the list is built.
let savedVoice = '';
// Which device the engine reported last. The wait is dominated by it: the same
// sentence is roughly ten times slower on CPU than on the P40s, so a single
// hard-coded estimate would be wrong for one of them.
let engineDevice = 'cuda';
let speakTimer = null;
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
    el('btn-save-params').onclick = saveParams;
    el('btn-fix-params').onclick = fixParamConflict;
    el('btn-record').onclick = startRecording;
    el('btn-stop').onclick = stopRecording;
    el('btn-save-voice').onclick = saveVoice;
    el('voice-file').onchange = pickFile;
    el('tts-text').oninput = updateCharCount;

    SLIDERS.forEach(s => {
        const input = el(s.input);
        input.oninput = () => {
            el(s.out).innerText = Number(input.value).toFixed(2);
            updateExaggerationHint();
            updateParamConflict();
        };
        input.oninput();
    });

    updateCharCount();
    await loadStatus();
    // Saved defaults BEFORE the voice list, so loadVoices() can preselect the
    // saved voice instead of whatever happens to sort first.
    await loadSavedParams();
    await loadVoices();
}


// Defaults this installation chose, from the settings table. Until these are
// saved the sliders are per-request only: an operator tunes a voice until it
// sounds right, reloads, and the tuning is gone.
async function loadSavedParams() {
    try {
        const res = await fetchAuth('/admin/api/tts/settings');
        if (!res.ok) return;
        const saved = await res.json();
        SLIDERS.forEach(s => {
            if (typeof saved[s.key] === 'number') {
                el(s.input).value = saved[s.key];
                el(s.out).innerText = Number(saved[s.key]).toFixed(2);
            }
        });
        if (saved.voice) savedVoice = saved.voice;
        updateExaggerationHint();
        updateParamConflict();
    } catch (e) {
        // A missing or unreadable setting must not stop the page loading;
        // the built-in defaults are already in the markup.
        console.warn('could not load saved TTS settings', e);
    }
}


async function saveParams() {
    const btn = el('btn-save-params');
    const note = el('params-saved-note');
    btn.disabled = true;
    note.innerText = '';
    try {
        const body = { ...params(), voice: el('tts-voice').value || '' };
        const res = await fetchAuth('/admin/api/tts/settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            note.className = 'text-danger small';
            note.innerText = err.detail || 'ذخیره نشد';
            return;
        }
        note.className = 'text-success small';
        note.innerText = 'ذخیره شد ✓';
        setTimeout(() => { note.innerText = ''; }, 4000);
    } catch (e) {
        note.className = 'text-danger small';
        note.innerText = 'ارتباط با سرور برقرار نشد';
    } finally {
        btn.disabled = false;
    }
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

// The two sliders interact: exaggeration above ~0.7 already speeds delivery up,
// so a cfg_weight above the neutral 0.5 on top of it makes speech race. Shown
// only while it is true, because a warning that is always on stops being read.
const EXAGGERATION_RUSHES_ABOVE = 0.7;
const CFG_SAFE_WHEN_EXCITED = 0.4;


// The description under the emotion slider describes what the CURRENT value
// does, instead of a paragraph covering every value at once. Removing the text
// entirely was the wrong fix — the operator still needs to know what the number
// means; it just needs to say the right thing for where the slider is.
function updateExaggerationHint() {
    const hint = el('hint-exaggeration');
    if (!hint) return;
    const v = Number(el('p-exaggeration').value);
    if (v <= 0.4) {
        hint.innerText = 'بیان آرام و یکنواخت — مناسب متن‌های رسمی و اطلاع‌رسانی.';
    } else if (v <= EXAGGERATION_RUSHES_ABOVE) {
        hint.innerText = 'حالت متعادل — برای بیشتر متن‌ها همین خوب است.';
    } else {
        hint.innerText = 'پرهیجان — صدا زنده‌تر ولی کم‌ثبات‌تر می‌شود و گفتار تندتر می‌رود.';
    }
}


function updateParamConflict() {
    const box = el('params-conflict');
    if (!box) return;
    const clash = Number(el('p-exaggeration').value) > EXAGGERATION_RUSHES_ABOVE
               && Number(el('p-cfg').value) > 0.5;
    box.classList.toggle('d-none', !clash);
}


function fixParamConflict() {
    const cfg = el('p-cfg');
    cfg.value = CFG_SAFE_WHEN_EXCITED;
    cfg.dispatchEvent(new Event('input'));
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
        if (data.device) engineDevice = data.device;
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

// Measured on this installation, not guessed:
//   * the 16 seeded answers are 3968 characters and render to 304s of audio,
//     so Persian speech runs at roughly 0.077 seconds per character;
//   * a full pre-render pass took 7.5 minutes for 299s of audio on the P40s
//     (RTF ~1.5), and the service measured RTF ~1.7 warm; on CPU the same work
//     ran at RTF ~16.
// The estimate is deliberately a little pessimistic: finishing early reads as
// fast, finishing late reads as broken.
const SECONDS_PER_CHAR = 0.077;
const RTF_BY_DEVICE = { cuda: 2.0, cpu: 17.0 };


function estimateSeconds(text) {
    const audio = Math.max(1, text.length * SECONDS_PER_CHAR);
    const rtf = RTF_BY_DEVICE[engineDevice] || RTF_BY_DEVICE.cpu;
    return Math.max(2, Math.round(audio * rtf));
}


function startSpeakProgress(estimate) {
    const box = el('tts-progress-box');
    const bar = el('tts-progress-bar');
    const label = el('tts-progress-label');
    box.classList.remove('d-none');
    bar.style.width = '0%';

    const started = Date.now();
    speakTimer = setInterval(() => {
        const elapsed = Math.round((Date.now() - started) / 1000);
        // Creep toward 95% and stop. A bar that hits 100% and then keeps
        // waiting is worse than one that visibly has a little left to go.
        const pct = Math.min(95, Math.round((elapsed / estimate) * 95));
        bar.style.width = pct + '%';
        label.innerText = elapsed <= estimate
            ? `حدود ${estimate - elapsed} ثانیه دیگر…`
            : 'کمی بیشتر از حد انتظار طول کشید — هنوز در حال کار است…';
    }, 250);
    label.innerText = `حدود ${estimate} ثانیه دیگر…`;
}


function stopSpeakProgress() {
    if (speakTimer) { clearInterval(speakTimer); speakTimer = null; }
    el('tts-progress-box').classList.add('d-none');
    el('tts-progress-bar').style.width = '0%';
}


async function speak() {
    const text = el('tts-text').value.trim();
    if (!text) { showMsg('tts-msg', 'ابتدا متنی بنویسید', 'danger'); return; }

    const btn = el('btn-speak');
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm ms-2"></span>در حال ساخت صدا…';
    const started = Date.now();
    startSpeakProgress(estimateSeconds(text));

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
        const took = Math.max(1, Math.round((Date.now() - started) / 1000));
        const badge = el('tts-cache-badge');
        // 'hit' means the engine had this exact text+settings on disk already.
        badge.className = cached === 'hit' ? 'badge bg-azure-lt' : 'badge bg-secondary-lt';
        badge.innerText = cached === 'hit'
            ? 'از حافظه — بدون پردازش دوباره'
            : `تازه ساخته شد — ${took} ثانیه`;
        el('tts-player-box').classList.remove('d-none');
        el('tts-audio').play().catch(() => { /* autoplay blocked: the controls are right there */ });
    } catch {
        showMsg('tts-msg', 'ارتباط با سرور برقرار نشد', 'danger');
    } finally {
        stopSpeakProgress();
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
    // Whatever the operator has on screen wins; otherwise fall back to the
    // saved default, so a reload opens on the voice this install chose.
    const want = chosen || savedVoice;
    if (voices.includes(want)) select.value = want;

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
