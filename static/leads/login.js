/* Signing in. A phone number, a code, and nothing else. The same two steps on
   the first visit and the tenth, so there is nothing to remember, forget or
   reset.
   No framework and no build step, like the rest of this project's public
   pages: a phone on exhibition wifi should download as little as possible. */
(function () {
  'use strict';

  var el = function (id) { return document.getElementById(id); };

  /* Persian and Arabic-Indic digits to ASCII, mirroring
     app/services/otp.py normalize_digits(). Someone typing their number on a
     Persian keyboard must not be told it is invalid. */
  var DIGITS = { '۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9',
                 '٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9' };
  function ascii(s) { return (s || '').replace(/[۰-۹٠-٩]/g, function (d) { return DIGITS[d]; }); }
  function digitsOnly(s) { return ascii(s).replace(/\D/g, ''); }

  var state = { challengeId: '', phone: '', length: 6, expiresAt: 0, resendAt: 0, expired: false };
  var ticker = null;
  var otpAbort = null;

  /* Every refusal on this page is a limit a real person will hit: the code
     times out, the resend has a cooldown, five wrong tries close the door, and
     one number gets a handful of codes an hour. Each one says what happened
     and what to do next. The server's own Persian is the fallback, not the
     first choice, because «تعداد تلاش‌های مجاز به پایان رسیده است» leaves the
     person holding the phone with nowhere to go. */
  var MSG = {
    bad_phone: 'این شماره درست نیست. شمارهٔ موبایل خودتان را کامل بنویسید، مثل 09121234567.',
    hourly: 'برای این شماره چند بار کد فرستادیم. چند دقیقه صبر کنید و دوباره بزنید. اگر باز هم نشد، حدود یک ساعت دیگر سر بزنید.',
    cooldown: 'هنوز زود است. چند لحظه صبر کنید و بعد دوباره کد بخواهید.',
    attempts: 'کد چند بار اشتباه وارد شد. یک کد تازه بگیرید و همان را وارد کنید.',
    expired: 'مهلت این کد تمام شد. دکمهٔ «ارسال دوبارهٔ کد» را بزنید تا کد تازه برایتان بیاید.',
    bad_code: 'کد درست نبود. یک بار دیگر پیامک را نگاه کنید و همان عددی را که فرستادیم وارد کنید.',
    sms_down: 'کد فرستاده نشد. چند لحظه دیگر دوباره بزنید. اگر باز هم نشد، به همکار ما در غرفه بگویید.',
    offline: 'اینترنت گوشی وصل نشد. یک بار دیگر بزنید.',
    generic: 'کاری از پیش نرفت. یک بار دیگر بزنید.'
  };

  function api(url, body) {
    return fetch(url, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    } : {}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var err = new Error(data.detail || '');
          err.status = r.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error(MSG.offline);
      err.offline = true;
      throw err;
    });
  }

  /* `phase` is 'request' or 'verify': the same 429 means «too many codes for
     this number» on one and «too many wrong tries» on the other. */
  function explain(err, phase) {
    if (err.offline) { return MSG.offline; }
    var code = (err.data && err.data.code) || '';
    if (MSG[code]) { return MSG[code]; }
    if (err.status === 503) { return MSG.sms_down; }
    if (err.status === 404) { return MSG.expired; }
    if (err.status === 429) {
      return phase === 'verify' ? MSG.attempts : MSG.hourly;
    }
    if (err.status === 400) {
      if (phase === 'request') { return MSG.bad_phone; }
      return state.expired ? MSG.expired : MSG.bad_code;
    }
    return err.message || MSG.generic;
  }

  function fmt(sec) {
    var m = String(Math.floor(sec / 60));
    var s = String(sec % 60);
    return (m.length < 2 ? '0' + m : m) + ':' + (s.length < 2 ? '0' + s : s);
  }

  /* One second of arithmetic drives three things: the validity line, whether
     the code box still accepts a code, and whether a new code can be asked
     for. The numbers come from the server, never from a local guess. */
  function tick() {
    var now = Date.now();
    var left = Math.max(0, Math.round((state.expiresAt - now) / 1000));
    var resendLeft = Math.max(0, Math.round((state.resendAt - now) / 1000));

    if (left > 0) {
      el('timer').textContent = 'این کد تا ' + fmt(left) + ' دیگر معتبر است.';
    } else if (!state.expired) {
      state.expired = true;
      el('timer').textContent = '';
      el('code').disabled = true;
      el('confirm').disabled = true;
      el('code-error').textContent = MSG.expired;
    }

    if (resendLeft > 0) {
      el('resend').disabled = true;
      el('resend').textContent = 'ارسال دوبارهٔ کد (' + fmt(resendLeft) + ')';
    } else {
      el('resend').disabled = false;
      el('resend').textContent = 'ارسال دوبارهٔ کد';
    }
  }

  function startTicker() {
    clearInterval(ticker);
    tick();
    ticker = setInterval(tick, 1000);
  }

  /* Android Chrome hands the code over directly when the SMS ends with the
     `@host #code` line app/services/otp.py already writes. Everywhere else the
     `one-time-code` attribute on the field does the same job through the
     keyboard suggestion. */
  function listenForSms() {
    if (!('OTPCredential' in window) || !window.AbortController) { return; }
    otpAbort = new AbortController();
    navigator.credentials.get({ otp: { transport: ['sms'] }, signal: otpAbort.signal })
      .then(function (otp) {
        if (otp && otp.code && !state.expired) {
          el('code').value = digitsOnly(otp.code).slice(0, state.length);
          submitCode();
        }
      })
      .catch(function () { /* dismissed, unsupported, or superseded */ });
  }

  function openCodeStep(data) {
    state.challengeId = data.challenge_id || state.challengeId;
    state.length = data.otp_length || state.length;
    state.expired = false;
    state.expiresAt = Date.now() + (data.expires_in || 120) * 1000;
    state.resendAt = Date.now() + (data.resend_in || 45) * 1000;

    el('masked').textContent = data.destination_masked || '';
    el('code').maxLength = state.length;
    el('code').value = '';
    el('code').disabled = false;
    el('confirm').disabled = false;
    el('code-error').textContent = '';
    el('step-phone').hidden = true;
    el('step-code').hidden = false;
    el('code').focus();
    startTicker();
    listenForSms();
  }

  /* --- Step 1: the number ------------------------------------------- */

  function requestCode(button, isResend) {
    var target = isResend ? el('code-error') : el('phone-error');
    target.textContent = '';
    var phone = digitsOnly(el('phone').value);
    if (!phone) {
      el('phone-error').textContent = MSG.bad_phone;
      el('phone').focus();
      return;
    }
    state.phone = phone;
    var label = button.textContent;
    button.disabled = true;
    button.textContent = 'در حال ارسال…';
    api('/api/auth/login/request', { phone: phone })
      .then(function (data) {
        openCodeStep(data);
      })
      .catch(function (e) {
        target.textContent = explain(e, 'request');
      })
      .then(function () {
        button.disabled = false;
        button.textContent = label;
        tick();
      });
  }

  el('send').addEventListener('click', function () { requestCode(this, false); });
  el('phone').addEventListener('input', function () {
    el('phone-error').textContent = '';
  });
  el('phone').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { el('send').click(); }
  });

  /* --- Step 2: the code --------------------------------------------- */

  function submitCode() {
    var btn = el('confirm');
    var code = digitsOnly(el('code').value);
    el('code-error').textContent = '';
    if (state.expired) { el('code-error').textContent = MSG.expired; return; }
    if (code.length !== state.length) {
      el('code-error').textContent = 'کد کامل وارد نشده است.';
      el('code').focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'در حال بررسی…';
    api('/api/auth/login/verify', { challenge_id: state.challengeId, code: code })
      .then(function (data) {
        if (otpAbort) { otpAbort.abort(); }
        clearInterval(ticker);
        location.replace(data.redirect || '/my');
      })
      .catch(function (e) {
        el('code-error').textContent = explain(e, 'verify');
        btn.disabled = false;
        btn.textContent = 'ورود';
        el('code').focus();
        el('code').select();
      });
  }

  el('code').addEventListener('input', function () {
    /* Normalises a pasted «کد ورود: ۱۲۳۴۵۶» down to the digits in it. */
    this.value = digitsOnly(this.value).slice(0, state.length);
    el('code-error').textContent = '';
    if (this.value.length === state.length) { submitCode(); }
  });
  el('code').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { submitCode(); }
  });
  el('confirm').addEventListener('click', submitCode);

  /* A fresh code, not a resend of the old one: the login path has one way in,
     and asking again is the same request the button above made. */
  el('resend').addEventListener('click', function () { requestCode(this, true); });

  el('back').addEventListener('click', function () {
    if (otpAbort) { otpAbort.abort(); }
    clearInterval(ticker);
    state.challengeId = '';
    el('step-code').hidden = true;
    el('step-phone').hidden = false;
    el('phone-error').textContent = '';
    el('phone').focus();
    el('phone').select();
  });
})();
