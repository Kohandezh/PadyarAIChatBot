/* ── INOTEX OTP verification flow ──
   Server-authoritative: the timer renders server state; verification,
   expiry, attempts and resend limits are enforced by the backend.
*/
(function () {
    'use strict';

    // ── i18n ─────────────────────────────────────────────────────────
    const I18N = {
        fa: {
            html_lang: 'fa', dir: 'rtl',
            back: 'بازگشت',
            destTitle: 'شمارهٔ خود را وارد کنید',
            destSubtitle: 'کد تأیید ۶ رقمی برای شما ارسال می‌شود.',
            sendCode: 'ارسال کد',
            title: 'تأیید کد',
            subtitle: 'کد ۶ رقمی ارسال‌شده را وارد کنید',
            expires: 'زمان اعتبار کد',
            verify: 'تأیید',
            resend: 'ارسال مجدد کد',
            or: 'یا',
            change: 'تغییر شماره',
            secureTitle: 'تأیید امن',
            secureBody: 'کد شما نزد ما محفوظ است.',
            codeSent: 'کد با موفقیت ارسال شد.',
            resent: 'کد جدید ارسال شد.',
            expiresSoon: 'کد به‌زودی منقضی می‌شود.',
            expired: 'کد منقضی شده است. دوباره درخواست دهید.',
            incorrect: 'کد واردشده صحیح نیست.',
            verified: 'کد با موفقیت تأیید شد.',
            network: 'خطای شبکه. دوباره تلاش کنید.',
            resendReady: 'ارسال مجدد کد اکنون امکان‌پذیر است.',
            codeLabel: 'کد تأیید ۶ رقمی',
        },
        en: {
            html_lang: 'en', dir: 'rtl', // layout stays RTL; words change
            back: 'Back',
            destTitle: 'Enter your number',
            destSubtitle: 'We will send you a 6-digit verification code.',
            sendCode: 'Send code',
            title: 'Verify code',
            subtitle: 'Enter the 6-digit code we sent',
            expires: 'Code expires in',
            verify: 'Verify',
            resend: 'Resend code',
            or: 'or',
            change: 'Change number',
            secureTitle: 'Secure verification',
            secureBody: 'Your code is safe with us.',
            codeSent: 'Code sent successfully.',
            resent: 'A new code has been sent.',
            expiresSoon: 'Code expires soon.',
            expired: 'The code has expired. Request a new one.',
            incorrect: 'The code is incorrect.',
            verified: 'Code verified successfully.',
            network: 'Network error. Please try again.',
            resendReady: 'You can resend the code now.',
            codeLabel: '6-digit verification code',
        },
    };
    let lang = localStorage.getItem('inotex_lang') === 'en' ? 'en' : 'fa';
    function t() { return I18N[lang]; }

    // ── DOM ──────────────────────────────────────────────────────────
    const stage = document.getElementById('otp-stage');
    const stepDest = document.querySelector('.step-destination');
    const stepCode = document.querySelector('.step-code');
    const destInput = document.getElementById('dest-input');
    const sendBtn = document.getElementById('send-code-btn');
    const digitsWrap = document.getElementById('otp-digits');
    const destMasked = document.getElementById('dest-masked');
    const timerEl = document.getElementById('otp-timer');
    const timerValue = document.getElementById('timer-value');
    const verifyBtn = document.getElementById('verify-btn');
    const resendBtn = document.getElementById('resend-btn');
    const resendCount = document.getElementById('resend-count');
    const changeBtn = document.getElementById('change-btn');
    const backBtn = document.getElementById('back-btn');
    const langBtn = document.getElementById('lang-btn');
    const feedback = document.getElementById('feedback');
    const feedbackText = document.getElementById('feedback-text');
    const feedbackClose = document.getElementById('feedback-close');
    const live = document.getElementById('otp-live');
    const pet = window.PetCompanion || { set: function () { } };

    // ── State ────────────────────────────────────────────────────────
    let OTP_LENGTH = 6;
    let challengeId = sessionStorage.getItem('otp_challenge') || '';
    let expiresAtMs = 0;     // reconciled: server expires_in + local clock
    let resendAtMs = 0;
    let timerId = 0;
    let cells = [], codeInput = null;
    let announcedResend = false;

    // ── Helpers ──────────────────────────────────────────────────────
    const DIGIT_MAP = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    };
    function normalizeDigits(s) {
        return String(s || '').replace(/[۰-۹٠-٩]/g, function (d) { return DIGIT_MAP[d] || d; });
    }
    function onlyDigits(s) { return normalizeDigits(s).replace(/\D/g, ''); }

    function setState(s) { stage.setAttribute('data-state', s); }

    function announce(msg) { live.textContent = msg; }

    function chip(kind, msg, sticky) {
        feedback.hidden = false;
        feedback.setAttribute('data-kind', kind);
        feedbackText.textContent = msg;
        if (!sticky && kind !== 'error') {
            clearTimeout(chip._t);
            chip._t = setTimeout(function () { feedback.hidden = true; }, 5000);
        }
        announce(msg);
    }

    feedbackClose.addEventListener('click', function () { feedback.hidden = true; });

    function applyLang() {
        const s = t();
        document.documentElement.lang = s.html_lang;
        langBtn.textContent = lang === 'fa' ? 'EN' : 'فا';
        document.querySelectorAll('[data-i18n]').forEach(function (el) {
            const k = el.getAttribute('data-i18n');
            if (s[k]) el.textContent = s[k];
        });
        document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
            const k = el.getAttribute('data-i18n-title');
            if (s[k]) { el.title = s[k]; el.setAttribute('aria-label', s[k]); }
        });
        if (codeInput) codeInput.setAttribute('aria-label', s.codeLabel);
    }

    langBtn.addEventListener('click', function () {
        lang = lang === 'fa' ? 'en' : 'fa';
        localStorage.setItem('inotex_lang', lang);
        applyLang();
    });

    // ── Digit group ──────────────────────────────────────────────────
    /* ONE real input under a row of decorative cells.
       A row of maxlength="1" inputs looks identical but breaks SMS autofill:
       the browser truncates the delivered code to the first box, so the OS
       code suggestion filled a single digit and the distribute() logic never
       ran. Documented on Android Chrome and iOS alike. */
    function buildDigits(n) {
        OTP_LENGTH = n;
        digitsWrap.textContent = '';
        cells = [];

        codeInput = document.createElement('input');
        codeInput.type = 'text';
        codeInput.className = 'otp-code-input';
        codeInput.inputMode = 'numeric';
        codeInput.autocomplete = 'one-time-code';
        codeInput.maxLength = n;
        codeInput.setAttribute('aria-label', t().codeLabel);
        digitsWrap.appendChild(codeInput);

        for (let i = 0; i < n; i++) {
            const cell = document.createElement('div');
            cell.className = 'otp-digit';
            cell.setAttribute('aria-hidden', 'true');
            digitsWrap.appendChild(cell);
            cells.push(cell);
        }
        wireDigits();
    }

    function code() { return codeInput ? codeInput.value : ''; }

    function paintFilled() {
        const v = code();
        const focused = document.activeElement === codeInput;
        cells.forEach(function (cell, i) {
            cell.textContent = v[i] || '';
            cell.classList.toggle('filled', !!v[i]);
            // The caret is invisible, so the next cell wears the focus ring.
            cell.classList.toggle('is-active', focused && i === Math.min(v.length, OTP_LENGTH - 1));
        });
        verifyBtn.disabled = v.length !== OTP_LENGTH || stage.dataset.state === 'verifying';
        if (v.length === OTP_LENGTH) pet.set('ready');
    }

    function clearMarks() {
        cells.forEach(function (c) { c.classList.remove('error', 'success'); });
    }

    function distribute(text) {
        if (!codeInput) return;
        codeInput.value = onlyDigits(text).slice(0, OTP_LENGTH);
        codeInput.focus();
        paintFilled();
    }

    function wireDigits() {
        const inp = codeInput;
        if (!inp) return;
        inp.addEventListener('focus', function () {
            if (stage.dataset.state !== 'verifying') pet.set('attentive');
            paintFilled();
        });
        inp.addEventListener('blur', paintFilled);
        inp.addEventListener('input', function () {
            clearMarks();
            // Normalises Persian digits, so a pasted "کد ورود: ۱۲۳۴۵۶" works.
            inp.value = onlyDigits(inp.value).slice(0, OTP_LENGTH);
            if (inp.value) pet.set('typing');
            paintFilled();
        });
        inp.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !verifyBtn.disabled) submit();
        });
        inp.addEventListener('paste', function (e) {
            e.preventDefault();
            distribute((e.clipboardData || window.clipboardData).getData('text'));
        });
        // Tapping any cell puts the caret in the one real field.
        digitsWrap.addEventListener('click', function () { inp.focus(); });
    }

    function setDigitsDisabled(disabled) {
        if (codeInput) codeInput.disabled = disabled;
    }

    // ── Timer (presentation of server state) ─────────────────────────
    function fmt(totalSec) {
        const m = String(Math.floor(totalSec / 60)).padStart(2, '0');
        const s = String(totalSec % 60).padStart(2, '0');
        return m + ':' + s;
    }

    function tick() {
        const now = Date.now();
        const left = Math.max(0, Math.round((expiresAtMs - now) / 1000));
        timerValue.textContent = fmt(left);
        timerEl.classList.toggle('expiring', left > 0 && left <= 15);
        if (left === 15) chip('warning', t().expiresSoon);
        if (left === 0 && stage.dataset.state !== 'success') {
            chip('error', t().expired, true);
            setDigitsDisabled(true);
            verifyBtn.disabled = true;
            pet.set('error');
        }
        const resendLeft = Math.max(0, Math.round((resendAtMs - now) / 1000));
        if (resendLeft > 0) {
            resendBtn.disabled = true;
            resendCount.hidden = false;
            resendCount.textContent = '(' + fmt(resendLeft) + ')';
            announcedResend = false;
        } else {
            resendBtn.disabled = stage.dataset.state === 'verifying';
            resendCount.hidden = true;
            if (!announcedResend && stage.dataset.state !== 'success') {
                announcedResend = true;
                announce(t().resendReady);
            }
        }
    }

    function applyChallenge(data) {
        challengeId = data.challenge_id;
        sessionStorage.setItem('otp_challenge', challengeId);
        destMasked.textContent = data.destination_masked;
        expiresAtMs = Date.now() + data.expires_in * 1000;
        resendAtMs = Date.now() + data.resend_in * 1000;
        if (data.otp_length && data.otp_length !== OTP_LENGTH) buildDigits(data.otp_length);
        clearInterval(timerId);
        timerId = setInterval(tick, 1000);
        tick();
    }

    // Pause rendering when hidden; re-sync on return (server-anchored).
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible' && challengeId) reconcile();
    });

    function reconcile() {
        fetch('/api/auth/otp/status/' + encodeURIComponent(challengeId))
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r); })
            .then(function (data) {
                showStep('code');
                applyChallenge(data);
            })
            .catch(function () {
                sessionStorage.removeItem('otp_challenge');
                challengeId = '';
            });
    }

    // ── Steps ────────────────────────────────────────────────────────
    function showStep(step) {
        stage.setAttribute('data-step', step);
        stepDest.hidden = step !== 'destination';
        stepCode.hidden = step !== 'code';
        feedback.hidden = true;
        if (step === 'code') {
            setDigitsDisabled(false);
            clearMarks();
            if (codeInput) codeInput.value = '';
            paintFilled();
            setTimeout(function () { codeInput && codeInput.focus(); }, 60);
        } else {
            clearInterval(timerId);
            setTimeout(function () { destInput && destInput.focus(); }, 60);
        }
    }

    // ── API calls ────────────────────────────────────────────────────
    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                if (!r.ok) throw { status: r.status, detail: data.detail || t().network };
                return data;
            });
        });
    }

    function requestCode() {
        const dest = destInput.value.trim();
        if (!dest) { destInput.focus(); return; }
        setState('sending');
        sendBtn.disabled = true;
        pet.set('working');
        post('/api/auth/otp/request', { destination: dest })
            .then(function (data) {
                setState('initial');
                sendBtn.disabled = false;
                showStep('code');
                applyChallenge(data);
                chip('info', t().codeSent);
                pet.set('greet');
            })
            .catch(function (err) {
                setState('initial');
                sendBtn.disabled = false;
                chip('error', err.detail || t().network, true);
                pet.set('error');
                destInput.focus();
            });
    }

    function submit() {
        if (verifyBtn.disabled) return;
        setState('verifying');
        verifyBtn.disabled = true;
        setDigitsDisabled(true);
        resendBtn.disabled = true;
        verifyBtn.setAttribute('aria-busy', 'true');
        pet.set('working');
        post('/api/auth/otp/verify', { challenge_id: challengeId, code: code() })
            .then(function () {
                setState('success');
                verifyBtn.removeAttribute('aria-busy');
                clearInterval(timerId);
                cells.forEach(function (c) { c.classList.add('success'); });
                chip('success', t().verified);
                pet.set('success');
                sessionStorage.removeItem('otp_challenge');
            })
            .catch(function (err) {
                setState('error');
                verifyBtn.removeAttribute('aria-busy');
                setDigitsDisabled(false);
                // Decision (documented in docs/features/otp-verification/RESEARCH.md):
                // preserve the entered digits and select the first one — retyping
                // six digits after a one-character slip punishes the user.
                cells.forEach(function (c) { c.classList.add('error'); });
                chip('error', err.detail || t().incorrect, true);
                pet.set('error');
                tick();
                codeInput.focus();
                codeInput.select();
            });
    }

    function resendCode() {
        if (resendBtn.disabled) return;
        setState('resending');
        resendBtn.disabled = true;
        pet.set('working');
        post('/api/auth/otp/resend', { challenge_id: challengeId })
            .then(function (data) {
                setState('initial');
                applyChallenge(data);
                clearMarks();
                setDigitsDisabled(false);
                if (codeInput) codeInput.value = '';
                paintFilled();
                chip('info', t().resent);
                pet.set('success');
                codeInput.focus();
            })
            .catch(function (err) {
                setState('initial');
                chip('error', err.detail || t().network, true);
                pet.set('error');
                tick();
            });
    }

    // ── Wire up ──────────────────────────────────────────────────────
    sendBtn.addEventListener('click', requestCode);
    destInput.addEventListener('keydown', function (e) { if (e.key === 'Enter') requestCode(); });
    verifyBtn.addEventListener('click', submit);
    resendBtn.addEventListener('click', resendCode);
    changeBtn.addEventListener('click', function () {
        sessionStorage.removeItem('otp_challenge');
        challengeId = '';
        showStep('destination');
        pet.set('idle');
    });
    backBtn.addEventListener('click', function () {
        if (stage.getAttribute('data-step') === 'code') changeBtn.click();
        else if (history.length > 1) history.back();
    });

    // ── Boot ─────────────────────────────────────────────────────────
    buildDigits(OTP_LENGTH);
    applyLang();
    pet.set('greet');
    if (challengeId) reconcile();
    else showStep('destination');
})();
