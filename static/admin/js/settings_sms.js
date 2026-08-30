/* Registration + SMS settings page.
 *
 * The gateway password and API key are WRITE-ONLY here on purpose: the server
 * never sends them back (only `has_password` / `has_api_key`), so this module
 * never has a secret to put in the DOM. The two password inputs always start
 * empty; an empty field means "keep whatever is stored".
 */
import { fetchAuth, showMsg } from './utils.js';
import { loadProfile } from './settings.js';

const el = (id) => document.getElementById(id);

/* Provider lives in a tab strip: the active tab IS the saved provider.
   Adding a gateway later means adding a tab button and a pane in the template —
   nothing here changes. Falls back to the safe 'dev' option if a stored value
   names a gateway this build does not ship, so the page can never end up with
   no tab selected. */
function providerTabs() {
    return Array.prototype.slice.call(
        document.querySelectorAll('#sms-provider-tabs [data-provider]'));
}

function getProvider() {
    const active = document.querySelector('#sms-provider-tabs .nav-link.active');
    return active ? active.dataset.provider : 'dev';
}

function setProvider(value) {
    const known = providerTabs().some(t => t.dataset.provider === value);
    const target = known ? value : 'dev';
    providerTabs().forEach(t => t.classList.toggle('active', t.dataset.provider === target));
    document.querySelectorAll('[data-provider-pane]').forEach(pane => {
        pane.classList.toggle('active', pane.dataset.providerPane === target);
    });
}

function wireProviderTabs() {
    providerTabs().forEach(tab => {
        tab.addEventListener('click', () => setProvider(tab.dataset.provider));
    });
}

function setSecretState(spanId, saved) {
    const span = el(spanId);
    span.textContent = saved ? '✅ ذخیره شده است' : 'هنوز ذخیره نشده است';
    span.className = saved ? 'text-success' : 'text-muted';
}

function setEnabledLabel(on) {
    el('sms-enabled-label').textContent = on ? 'روشن' : 'خاموش';
}

async function loadSettings() {
    try {
        const res = await fetchAuth('/admin/api/sms');
        if (!res.ok) return;
        const d = await res.json();

        el('sms-enabled').checked = !!d.enabled;
        setEnabledLabel(!!d.enabled);
        // Radio group, not a <select>: the dropdown was mistaken for a text field.
        setProvider(d.provider || 'dev');
        el('sms-username').value = d.username || '';
        el('sms-source').value = d.source || '';
        el('sms-template-id').value = d.template_id || '';
        el('sms-invite-text').value = d.invite_text || '';
        el('sms-reject-text').value = d.reject_text || '';
        el('sms-daily-budget').value = d.daily_budget || '0';
        // Today's count next to the cap, so the operator can see how close the
        // day is to going silent instead of finding out when it does.
        el('sms-budget-used').textContent = `امروز ${d.sent_today || 0} پیامک فرستاده شده است.`;
        el('sms-url').value = d.url || '';
        el('sms-url').placeholder = d.url_default || '';
        el('sms-status-url').value = d.status_url || '';
        el('sms-status-url').placeholder = d.status_url_default || '';
        el('sms-credit-url').value = d.credit_url || '';
        el('sms-credit-url').placeholder = d.credit_url_default || '';
        el('sms-trim').checked = d.trim !== false;
        el('sms-send-to-blacklist').checked = d.send_to_blacklist !== false;
        el('sms-host').value = d.sms_host || '';

        // Booleans only — the secrets themselves never arrive here.
        setSecretState('sms-password-state', d.has_password);
        setSecretState('sms-api-key-state', d.has_api_key);

        const status = el('sms-status');
        // Three states, not two. The middle one is the trap: every credential
        // can be filled and correct while the provider is still 'dev', because
        // the provider is whichever TAB was active when Save was pressed. That
        // combination used to read "✅ آماده" here and then fail the test-send
        // with a message about a setting the operator could not find.
        if (d.configured && (d.provider || 'dev') !== 'asanak') {
            status.className = 'small mt-3 text-warning';
            status.textContent = '⚠️ اطلاعات آسانک کامل است، ولی سرویس‌دهندهٔ فعال «'
                + (d.provider || 'dev') + '» است. بالای همین فرم تب «آسانک» را انتخاب کنید'
                + ' و دکمهٔ ذخیره را بزنید تا ارسال واقعی روشن شود.';
        } else {
            status.className = d.configured ? 'small mt-3 text-success' : 'small mt-3 text-danger';
            status.textContent = d.configured
                ? '✅ سامانه پیامک آماده‌ی ارسال است.'
                : '⚠️ هنوز آماده نیست — نام کاربری، رمز عبور و شماره فرستنده باید هر سه پر باشند.';
        }
    } catch {
        // page stays usable — the form just shows its defaults
    }
}

export function initSms() {
    loadProfile();
    wireProviderTabs();   // before loadSettings, so the saved provider can select its tab
    loadSettings();

    el('sms-enabled').addEventListener('change', (e) => setEnabledLabel(e.target.checked));

    el('sms-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const body = {
            enabled: el('sms-enabled').checked,
            provider: getProvider(),
            username: el('sms-username').value.trim(),
            password: el('sms-password').value.trim(),
            api_key: el('sms-api-key').value.trim(),
            source: el('sms-source').value.trim(),
            template_id: el('sms-template-id').value.trim(),
            invite_text: el('sms-invite-text').value.trim(),
            reject_text: el('sms-reject-text').value.trim(),
            daily_budget: el('sms-daily-budget').value.trim(),
            url: el('sms-url').value.trim(),
            status_url: el('sms-status-url').value.trim(),
            credit_url: el('sms-credit-url').value.trim(),
            trim: el('sms-trim').checked,
            send_to_blacklist: el('sms-send-to-blacklist').checked,
            sms_host: el('sms-host').value.trim(),
        };
        try {
            const res = await fetchAuth('/admin/api/sms', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(body),
            });
            if (res.ok) {
                // Clear the secret inputs so nothing typed lingers on screen.
                el('sms-password').value = '';
                el('sms-api-key').value = '';
                const saved = await res.json().catch(() => ({}));
                // Honest about a half-save: the settings are live either way,
                // but the operator should know if the server could not write
                // its own environment file.
                if (saved.env_file === false) {
                    showMsg('sms-msg', 'تنظیمات ذخیره شد، ولی فایل تنظیمات سرور (.env) قابل نوشتن نبود.', 'warning');
                } else {
                    showMsg('sms-msg', 'تنظیمات ذخیره شد', 'success');
                }
                loadSettings();
            } else {
                let detail = '';
                try { detail = (await res.json()).detail || ''; } catch { /* no body */ }
                showMsg('sms-msg', detail || 'خطا در ذخیره تنظیمات', 'danger');
            }
        } catch {
            showMsg('sms-msg', 'خطای ارتباط با سرور', 'danger');
        }
    });

    el('sms-credit-btn').addEventListener('click', async () => {
        const msg = el('sms-credit-msg');
        const btn = el('sms-credit-btn');
        btn.disabled = true;
        msg.className = 'fw-bold mt-3 text-muted';
        msg.textContent = '⏳ در حال بررسی...';
        try {
            const res = await fetchAuth('/admin/api/sms/credit');
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                msg.className = 'fw-bold mt-3 text-success';
                msg.textContent = `✅ اتصال درست است. اعتبار باقی‌مانده: ${data.credit} پیامک`;
            } else {
                // The gateway's own reason — expired password, no credit, ...
                msg.className = 'fw-bold mt-3 text-danger';
                msg.textContent = '❌ ' + (data.detail || 'بررسی اعتبار ناموفق بود.');
            }
        } catch {
            msg.className = 'fw-bold mt-3 text-danger';
            msg.textContent = '❌ خطای ارتباط با سرور';
        } finally {
            btn.disabled = false;
        }
    });

    el('sms-test-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        const msg = el('sms-test-msg');
        const destination = el('sms-test-number').value.trim();
        if (!destination) {
            msg.className = 'text-center fw-bold mt-3 text-danger';
            msg.textContent = 'ابتدا یک شماره موبایل بنویسید.';
            return;
        }
        const btn = el('sms-test-btn');
        btn.disabled = true;
        msg.className = 'text-center fw-bold mt-3 text-muted';
        msg.textContent = '⏳ در حال ارسال...';
        try {
            const res = await fetchAuth('/admin/api/sms/test', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ destination }),
            });
            const data = await res.json().catch(() => ({}));
            if (res.ok) {
                // "پذیرفته شد", not "فرستاده شد". Asanak returns success the
                // moment it queues a message; delivery to the handset can
                // still fail silently. Claiming "sent" here sent an operator
                // hunting for a bug in the panel when the message was sitting
                // undelivered at the gateway. The id is the trace to quote.
                msg.className = 'text-center fw-bold mt-3 text-success';
                msg.textContent = `✅ سامانه پیامک را برای ${data.destination || destination} پذیرفت`
                    + (data.msgid ? ` (شناسه ${data.msgid})` : '')
                    + '. رسیدن پیام به گوشی را خودتان بررسی کنید.';
            } else {
                // Show the server's own reason — no invented message.
                msg.className = 'text-center fw-bold mt-3 text-danger';
                msg.textContent = '❌ ' + (data.detail || 'ارسال پیامک آزمایشی ناموفق بود.');
            }
        } catch {
            msg.className = 'text-center fw-bold mt-3 text-danger';
            msg.textContent = '❌ خطای ارتباط با سرور';
        } finally {
            btn.disabled = false;
        }
    });
}
