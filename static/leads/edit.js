/* The company contact's page, after the gate. The link in the URL is dead;
   everything this page does rides on the HttpOnly session cookie minted by
   the button press. Refreshing keeps working until the session expires or
   the form is submitted, whichever comes first — which is why the buttons
   are removed rather than re-enabled on success.

   Two ways to finish: send the (possibly corrected) fields for review, or
   confirm everything as-is. The confirm button only ever answers a pristine
   form: the moment a box differs from what the server sent, confirming would
   be a lie about "without changes", so it turns itself off and the hint
   below it says why. */
(function () {
  'use strict';
  var el = function (id) { return document.getElementById(id); };
  var MAX = 4000;
  var FIELDS = ['title', 'text', 'activity_field', 'contact_name',
                'contact_position', 'contact_mobile', 'email', 'website',
                'company_phone', 'fax', 'address', 'province'];
  var sent = {};

  function api(url, body) {
    return fetch(url, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    } : {}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var err = new Error(data.detail || 'خطایی رخ داد. دوباره تلاش کنید.');
          err.status = r.status;
          throw err;
        }
        return data;
      });
    });
  }

  function count() {
    var left = MAX - el('text').value.length;
    el('counter').hidden = left > 400;
    el('counter').textContent = 'جای ' + left + ' حرف دیگر باقی است.';
    el('counter').classList.toggle('over', left <= 0);
  }

  /* How long this page still answers: the session's own clock, shown in
     whole minutes because a countdown in seconds reads as urgency, not
     information. */
  function countdown(expiresAt) {
    var end = new Date(expiresAt).getTime();
    if (!end) { el('minutes-left').textContent = 'مدت محدود'; return; }
    var tick = function () {
      var mins = Math.floor((end - Date.now()) / 60000);
      if (mins <= 0) {
        el('minutes-left').textContent = 'کمتر از یک دقیقه';
        return;
      }
      el('minutes-left').textContent = 'حدود ' + mins + ' دقیقه';
      setTimeout(tick, 30000);
    };
    tick();
  }

  function collect() {
    var out = {};
    FIELDS.forEach(function (f) { out[f] = el(f).value; });
    return out;
  }

  function pristine() {
    return FIELDS.every(function (f) { return el(f).value === sent[f]; });
  }

  function refreshConfirm() {
    var ok = pristine();
    el('confirm').disabled = !ok;
    el('confirm').title = ok ? '' : 'چیزی را تغییر داده‌اید؛ دکمهٔ ذخیره را بزنید.';
  }

  function done(kind) {
    el('form-card').hidden = true;
    el('final-card').hidden = false;
    if (kind === 'confirm') {
      el('final-title').textContent = 'ثبت شد. کار شما تمام است.';
      el('final-line').textContent = 'تأیید شما ثبت شد. اطلاعات شرکت شما همین‌گونه در نمایشگاه استفاده می‌شود.';
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  api('/api/leads/edit/state').then(function (data) {
    /* A missing name leaves the heading that shipped with the page. An empty
       heading, or the word undefined, would be worse than a generic one. */
    var company = typeof data.company === 'string' ? data.company.trim() : '';
    if (company) { el('company').textContent = company; }

    var fields = data.fields || {};
    FIELDS.forEach(function (f) { sent[f] = fields[f] || ''; el(f).value = sent[f]; });
    el('pending-note').hidden = !data.pending;

    var ctx = data.context || {};
    if (ctx.booth_number || ctx.hall) {
      el('boothline').hidden = false;
      el('booth-number').textContent = ctx.booth_number || '—';
      el('hall').textContent = ctx.hall ? (' — ' + ctx.hall) : '';
    }

    /* The booth script is an admin setting and can be reworded. The page
       ships with the approved text so the consent is never missing. */
    if (data.consent_script) { el('consent-text').textContent = data.consent_script; }
    countdown(data.expires_at);
    count();
    refreshConfirm();
  }).catch(function (e) {
    el('error').textContent = e.message;
    el('save').disabled = true;
    el('confirm').disabled = true;
  });

  FIELDS.forEach(function (f) {
    el(f).addEventListener('input', function () { refreshConfirm(); });
  });
  el('text').addEventListener('input', count);

  el('save').addEventListener('click', function () {
    var btn = this;
    el('error').textContent = '';
    if (!el('title').value.trim()) {
      el('error').textContent = 'نام شرکت خالی است.';
      el('title').focus();
      return;
    }
    if (!el('text').value.trim()) {
      el('error').textContent = 'کادر متن خالی است. متن معرفی شرکت را بنویسید.';
      el('text').focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'در حال ثبت…';
    api('/api/leads/edit/submit', { fields: collect() })
      .then(function (data) { done(data.kind); })
      .catch(function (e) {
        el('error').textContent = e.message;
        btn.disabled = false;
        btn.textContent = 'ذخیره و ارسال برای بررسی';
      });
  });

  el('confirm').addEventListener('click', function () {
    var btn = this;
    el('error').textContent = '';
    btn.disabled = true;
    btn.textContent = 'در حال ثبت…';
    api('/api/leads/edit/submit', { confirm: true })
      .then(function (data) { done(data.kind); })
      .catch(function (e) {
        el('error').textContent = e.message;
        btn.disabled = false;
        btn.textContent = 'تأیید می‌کنم — بدون تغییر';
      });
  });
})();
