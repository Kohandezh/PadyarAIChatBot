/* Infrastructure → Backups → where copies are kept off the server (SPEC-H2).
 *
 * The password and the private key are WRITE-ONLY here. The server answers
 * only password_saved / private_key_saved, so this page never holds a stored
 * secret. Both secret inputs start empty and are emptied again after each
 * save; an empty field means "keep the stored one".
 *
 * "Test connection" tests what is SAVED, never the form (the browser does not
 * have the stored secret, and the saved settings are what tonight's copy
 * uses). So the button waits while the form has unsaved changes, and says why.
 *
 * Every value is set with .value / .textContent, never innerHTML.
 */
import { fetchAuth } from './utils.js';

const API = '/admin/api/infra/backups/offsite-settings';
const el = (id) => document.getElementById(id);

let dirty = false;
let source = 'none';
let onSaved = () => {};

// Neutral on purpose: whether copies can really leave the server also needs
// the encryption key on the server, and the status line above (fed by
// showOffsiteState) is the one place that knows both.
const SOURCE_TEXT = {
    panel: ['text-body', 'مقصد در همین صفحه ذخیره شده است.'],
    env: ['text-info', 'اکنون مقصد از تنظیمات خود سرور خوانده می‌شود. اگر اینجا مقصدی ذخیره کنید، جای آن را می‌گیرد.'],
    none: ['text-muted', 'هنوز مقصدی تنظیم نشده است.'],
};

function setMsg(text, tone) {
    const box = el('offsite-msg');
    box.className = `fw-bold mt-2 text-${tone}`;
    box.textContent = text;
}

function chosenAuth() {
    const checked = document.querySelector('input[name="offsite-auth"]:checked');
    return checked ? checked.value : 'password';
}

function showAuth(value) {
    el(`offsite-auth-${value}`).checked = true;
    document.querySelectorAll('[data-auth-pane]').forEach((pane) => {
        pane.hidden = pane.dataset.authPane !== value;
    });
}

function secretState(id, saved) {
    const span = el(id);
    span.textContent = saved ? '✅ ذخیره شده' : 'هنوز ذخیره نشده';
    span.className = saved ? 'small text-success' : 'small text-muted';
}

function setDirty(value) {
    dirty = value;
    el('offsite-test-btn').disabled = dirty || source !== 'panel';
    let hint = '';
    if (dirty) hint = 'اول «ذخیره» را بزنید، بعد اتصال را آزمایش کنید.';
    else if (source !== 'panel') hint = 'برای آزمایش اتصال، اول مقصد را در همین فرم ذخیره کنید.';
    el('offsite-test-hint').textContent = hint;
}

function render(d) {
    source = d.source || 'none';
    el('offsite-host').value = d.host || '';
    el('offsite-port').value = d.host ? String(d.port || 22) : '';
    el('offsite-user').value = d.user || '';
    el('offsite-path').value = d.path || '';
    el('offsite-fingerprint').value = d.fingerprint || '';
    showAuth(d.auth === 'key' ? 'key' : 'password');
    el('offsite-password').value = '';
    el('offsite-key').value = '';
    el('offsite-clear').checked = false;
    secretState('offsite-password-state', d.password_saved);
    secretState('offsite-key-state', d.private_key_saved);
    const [tone, text] = SOURCE_TEXT[source] || SOURCE_TEXT.none;
    el('offsite-source').className = `small mb-3 ${tone}`;
    el('offsite-source').textContent = text;
    setDirty(false);
}

function formBody() {
    const auth = chosenAuth();
    const body = {
        host: el('offsite-host').value.trim(),
        port: el('offsite-port').value.trim(),
        user: el('offsite-user').value.trim(),
        path: el('offsite-path').value.trim(),
        auth,
        fingerprint: el('offsite-fingerprint').value.trim(),
        clear_secret: el('offsite-clear').checked,
    };
    // Only the secret of the chosen method is sent.
    const password = el('offsite-password').value;
    const key = el('offsite-key').value;
    if (auth === 'password' && password) body.password = password;
    if (auth === 'key' && key.trim()) body.private_key = key;
    return body;
}

async function readJson(res) {
    try {
        return await res.json();
    } catch {
        return {};
    }
}

async function load() {
    try {
        const res = await fetchAuth(API);
        if (!res.ok) {
            setMsg('خواندن تنظیمات مقصد ناموفق بود.', 'danger');
            return;
        }
        render(await res.json());
    } catch {
        setMsg('خطای ارتباط با سرور', 'danger');
    }
}

async function save(e) {
    e.preventDefault();
    const btn = el('offsite-save-btn');
    btn.disabled = true;
    setMsg('⏳ در حال ذخیره...', 'muted');
    try {
        const res = await fetchAuth(API, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(formBody()),
        });
        const data = await readJson(res);
        if (res.ok) {
            render(data);
            setMsg(data.source === 'panel'
                ? 'ذخیره شد. حالا «آزمایش اتصال» را بزنید.'
                : 'مقصد این صفحه حذف شد.', 'success');
            onSaved();
        } else {
            // The typed secret stays in its field, so a typo elsewhere does
            // not mean typing the password again.
            setMsg(data.detail || 'ذخیره نشد.', 'danger');
        }
    } catch {
        setMsg('خطای ارتباط با سرور', 'danger');
    } finally {
        btn.disabled = false;
    }
}

async function testConnection() {
    if (dirty) return;
    el('offsite-test-btn').disabled = true;
    setMsg('⏳ در حال آزمایش اتصال... (تا ۳۰ ثانیه)', 'muted');
    try {
        const res = await fetchAuth(`${API}/test`, { method: 'POST' });
        const data = await readJson(res);
        if (res.ok) {
            setMsg(data.message || (data.ok ? 'اتصال برقرار شد.' : 'اتصال برقرار نشد.'),
                data.ok ? 'success' : 'danger');
        } else {
            setMsg(data.detail || 'آزمایش انجام نشد.', 'danger');
        }
    } catch {
        setMsg('خطای ارتباط با سرور', 'danger');
    } finally {
        setDirty(dirty);
    }
}

/* The page's off-site state, from the backups list. 'not_ready' = a
 * destination is set but copies cannot be encrypted on this server. */
export function showOffsiteState(offsite) {
    const box = el('offsite-encryption');
    const notReady = (offsite || {}).state === 'not_ready';
    box.hidden = !notReady;
    box.textContent = notReady
        ? 'رمزگذاری نسخه‌ها روی خود سرور آماده نیست، پس با این مقصد هنوز هیچ نسخه‌ای بیرون فرستاده نمی‌شود. کلید عمومی رمزگذاری را در همین صفحه، در بخش «کلید عمومی رمزگذاری» پایین‌تر، بگذارید.'
        : '';
}

export function initOffsiteSettings(options = {}) {
    if (options.onSaved) onSaved = options.onSaved;
    el('offsite-form').addEventListener('submit', save);
    el('offsite-form').addEventListener('input', () => setDirty(true));
    document.querySelectorAll('input[name="offsite-auth"]').forEach((radio) => {
        radio.addEventListener('change', () => {
            showAuth(radio.value);
            setDirty(true);
        });
    });
    el('offsite-test-btn').addEventListener('click', testConnection);
    load();
}

/* ── Encryption public key (SPEC-H3) ─────────────────────────────────────
 *
 * Only the PUBLIC key is handled here. The server answers with its
 * fingerprint, user id and date, never the key text, and the textarea and the
 * file input are emptied after every save and clear so the key does not stay
 * on the page.
 *
 * A pasted key goes as JSON. A chosen file goes as multipart (FormData). For
 * FormData we set NO Content-Type header: the browser must add it, because
 * only the browser knows the boundary. fetchAuth never sets a content type
 * itself, it only adds the CSRF header, so both kinds of save stay protected.
 *
 * Server text (key id, date, refusal sentences) is set with .textContent only.
 */
const GPG_API = '/admin/api/infra/backups/offsite-gpg';

const GPG_SOURCE_TEXT = {
    panel: 'از پنل',
    env: 'از تنظیمات سرور',
    none: 'تنظیم نشده',
};

function setGpgMsg(text, tone) {
    const box = el('gpg-msg');
    box.className = `fw-bold mt-2 text-${tone}`;
    box.textContent = text;
}

function renderGpg(d) {
    const source = d.source || 'none';
    const has = Boolean(d.fingerprint);
    el('gpg-none').hidden = has;
    el('gpg-details').hidden = !has;
    el('gpg-none').textContent = has ? ''
        : 'هنوز کلید عمومی تنظیم نشده است؛ تا وقتی نباشد هیچ نسخه‌ای بیرون از سرور فرستاده نمی‌شود.';
    if (has) {
        el('gpg-fpr').textContent = d.fingerprint;
        el('gpg-uid').textContent = d.uid || '';
        el('gpg-uid-row').hidden = !d.uid;
        el('gpg-created').textContent = d.created || '';
        el('gpg-created-row').hidden = !d.created;
        el('gpg-source').textContent = GPG_SOURCE_TEXT[source] || GPG_SOURCE_TEXT.none;
        const ready = el('gpg-ready');
        ready.textContent = d.ready ? 'رمزگذاری با این کلید آماده است.'
            : 'این کلید قابل استفاده نیست، پس نسخه‌ای بیرون فرستاده نمی‌شود.';
        ready.className = `fw-bold ${d.ready ? 'text-success' : 'text-danger'}`;
    }
    // Only a key saved in the panel can be removed here. A key from the
    // server settings is changed on the server, so the button stays away.
    el('gpg-clear-btn').hidden = source !== 'panel';
    // The key text must not stay on the page after a save or a clear.
    el('gpg-text').value = '';
    el('gpg-file').value = '';
}

async function loadGpg() {
    try {
        const res = await fetchAuth(GPG_API);
        if (!res.ok) {
            setGpgMsg('خواندن کلید عمومی ناموفق بود.', 'danger');
            return;
        }
        renderGpg(await res.json());
    } catch {
        setGpgMsg('خطای ارتباط با سرور', 'danger');
    }
}

// One place for both buttons: send, then show the answer or the reason.
async function sendGpg(options, doneText, onSaved) {
    const saveBtn = el('gpg-save-btn');
    const clearBtn = el('gpg-clear-btn');
    saveBtn.disabled = true;
    clearBtn.disabled = true;
    setGpgMsg('⏳ در حال ذخیره...', 'muted');
    try {
        const res = await fetchAuth(GPG_API, { method: 'POST', ...options });
        const data = await readJson(res);
        if (res.ok) {
            renderGpg(data);
            setGpgMsg(doneText, 'success');
            onSaved();
        } else {
            // Kept as text. The paste box and the file input are emptied: a
            // refused key may be a PRIVATE key pasted by mistake, and this page
            // can be open on a shared screen. A public key is easy to paste again.
            setGpgMsg(data.detail || 'ذخیره نشد.', 'danger');
            el('gpg-text').value = '';
            el('gpg-file').value = '';
        }
    } catch {
        setGpgMsg('خطای ارتباط با سرور', 'danger');
    } finally {
        saveBtn.disabled = false;
        clearBtn.disabled = false;
    }
}

export function initGpgKey(options = {}) {
    const onSaved = options.onSaved || (() => {});
    el('gpg-form').addEventListener('submit', (e) => {
        e.preventDefault();
        const text = el('gpg-text').value.trim();
        const file = el('gpg-file').files[0];
        if (text) {
            sendGpg({
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ public_key: text }),
            }, 'کلید ذخیره شد.', onSaved);
        } else if (file) {
            const form = new FormData();
            form.append('file', file);
            sendGpg({ body: form }, 'کلید ذخیره شد.', onSaved);
        } else {
            setGpgMsg('اول متن کلید عمومی را بچسبانید یا یک فایل انتخاب کنید.', 'danger');
        }
    });
    el('gpg-clear-btn').addEventListener('click', () => {
        sendGpg({
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ clear: true }),
        }, 'کلید پاک شد.', onSaved);
    });
    loadGpg();
}
