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

const SOURCE_TEXT = {
    panel: ['text-success', 'مقصد از همین صفحه تنظیم شده است.'],
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
