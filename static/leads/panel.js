/* The visitor panel: find a company, register its contact, read back the code,
   hand over the invite. Four steps, one visible at a time.
   No framework and no build step — the rest of this project's public pages are
   vanilla JS, and a phone on exhibition wifi should download as little as
   possible. */
(function () {
  'use strict';

  var el = function (id) { return document.getElementById(id); };
  var state = { datasetId: '', leadId: '', masked: '' };

  /* Persian and Arabic-Indic digits to ASCII, mirroring
     app/services/otp.py normalize_digits(). A contact reading their number off
     a Persian keyboard must not be told it is invalid. */
  var DIGITS = { '۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9',
                 '٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9' };
  function ascii(s) { return (s || '').replace(/[۰-۹٠-٩]/g, function (d) { return DIGITS[d]; }); }

  function show(step) {
    ['step-company', 'step-contact', 'step-code', 'step-qr'].forEach(function (id, i) {
      el(id).hidden = (id !== step && i > 0);
    });
    var order = ['step-company', 'step-contact', 'step-code', 'step-qr'];
    var at = order.indexOf(step);
    Array.prototype.forEach.call(el('steps').children, function (bar, i) {
      bar.classList.toggle('done', i <= at);
    });
  }

  /* The rejection carries the status and the body, because a 409 on register
     is a question to the visitor (duplicate phone) and every other failure is
     just a message. */
  function api(url, body) {
    return fetch(url, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    } : {}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var err = new Error(data.detail || 'خطایی رخ داد. دوباره تلاش کنید.');
          err.status = r.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    });
  }

  /* --- Step 1: the company ------------------------------------------- */
  var searchTimer = null;
  function search() {
    var q = el('q').value.trim();
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () {
      api('/api/leads/companies?q=' + encodeURIComponent(q)).then(function (data) {
        var ul = el('results');
        ul.innerHTML = '';
        (data.companies || []).forEach(function (c) {
          var li = document.createElement('li');
          li.textContent = c.title;
          li.addEventListener('click', function () { choose(c); });
          ul.appendChild(li);
        });
      }).catch(function () { /* a failed search leaves the last list up */ });
    }, 200);
  }

  function choose(company) {
    state.datasetId = company.id;
    el('chosen-name').textContent = company.title;
    el('chosen').hidden = false;
    el('results').innerHTML = '';
    el('q').value = '';
    el('q').hidden = true;
    el('step-contact').hidden = false;
    el('first').focus();
    show('step-contact');
  }

  el('q').addEventListener('input', search);
  el('change').addEventListener('click', function () {
    state.datasetId = '';
    el('chosen').hidden = true;
    el('q').hidden = false;
    el('q').focus();
    show('step-company');
  });

  /* "My company isn't in this list" — a tiny inline form, not a second search.
     On success it behaves exactly like picking a search result: `choose()`
     moves straight to step 2 so the visitor can register the contact right
     away, without waiting on the admin approval this company still needs. */
  el('not-listed').addEventListener('click', function () {
    var open = el('new-company').hidden;
    el('new-company').hidden = !open;
    if (open) { el('new-title').focus(); }
  });

  el('new-company-save').addEventListener('click', function () {
    var btn = this;
    var title = el('new-title').value.trim();
    var text = el('new-text').value.trim();
    el('new-company-error').textContent = '';
    if (!title) {
      el('new-company-error').textContent = 'نام شرکت را وارد کنید.';
      el('new-title').focus();
      return;
    }
    if (!text) {
      el('new-company-error').textContent = 'متن پاسخ نمی‌تواند خالی باشد.';
      el('new-text').focus();
      return;
    }
    var label = btn.textContent;
    btn.disabled = true;
    btn.textContent = 'در حال ثبت…';
    api('/api/leads/companies', { title: title, text: text }).then(function (data) {
      el('new-title').value = '';
      el('new-text').value = '';
      el('new-company').hidden = true;
      choose({ id: data.id, title: data.title });
    }).catch(function (e) {
      el('new-company-error').textContent = e.message;
    }).then(function () {
      btn.disabled = false;
      btn.textContent = label;
    });
  });

  /* --- Step 2: the contact ------------------------------------------- */

  function hideDuplicateAsk() {
    el('dup').hidden = true;
    el('send-row').hidden = false;
  }

  /* The server answers a repeated number with 409 and sends no code. Asking
     again with the override flag is the visitor's deliberate answer. */
  function askDuplicate(message) {
    if (message) { el('dup-text').textContent = message; }
    el('reg-error').textContent = '';
    el('dup').hidden = false;
    el('send-row').hidden = true;
    el('dup').scrollIntoView({ block: 'center', behavior: 'smooth' });
  }

  function register(button, overrideDuplicate) {
    el('reg-error').textContent = '';
    if (!state.datasetId) { el('reg-error').textContent = 'اول شرکت را انتخاب کنید.'; return; }
    var label = button.textContent;
    button.disabled = true;
    button.textContent = 'در حال ارسال…';
    api('/api/leads/register', {
      dataset_id: state.datasetId,
      first_name: el('first').value.trim(),
      last_name: el('last').value.trim(),
      position: el('position').value.trim(),
      phone: ascii(el('phone').value.trim()),
      override_duplicate: !!overrideDuplicate
    }).then(function (data) {
      hideDuplicateAsk();
      state.leadId = data.lead_id;
      state.masked = data.destination_masked || '';
      el('masked').textContent = state.masked;
      el('step-code').hidden = false;
      show('step-code');
      el('code').focus();
    }).catch(function (e) {
      if (e.status === 409 && e.data && e.data.duplicate) {
        askDuplicate(e.data.detail);
      } else {
        hideDuplicateAsk();
        el('reg-error').textContent = e.message;
      }
    }).then(function () {
      button.disabled = false;
      button.textContent = label;
    });
  }

  el('send').addEventListener('click', function () { register(this, false); });
  el('dup-yes').addEventListener('click', function () { register(this, true); });
  el('dup-no').addEventListener('click', function () {
    hideDuplicateAsk();
    el('phone').value = '';
    el('phone').focus();
  });

  /* --- Step 3: the code --------------------------------------------- */
  el('code').addEventListener('input', function () {
    this.value = ascii(this.value).replace(/\D/g, '');
  });

  /* Step 4 has two faces. The raw invite link is never one of them: the
     visitor holds up a QR or the contact gets an SMS, and nobody who
     registered the lead ever sees the address (SPEC F1). */
  function deliver(data) {
    var smsOnly = data.channel === 'sms' && !data.qr;
    el('sms-failed').hidden = !(data.channel === 'sms' && data.qr);
    el('by-sms').hidden = !smsOnly;
    el('by-qr').hidden = smsOnly;
    if (smsOnly) {
      el('deliver-title').textContent = '۴. پیامک برای ایشان رفت';
      el('deliver-hint').textContent = 'به ایشان بگویید پیامک را باز کنند.';
      el('sms-to').textContent = data.destination_masked || state.masked;
    } else {
      el('deliver-title').textContent = '۴. این کد را اسکن کنند';
      el('deliver-hint').textContent = 'گوشی را جلوی ایشان بگیرید. با اسکن، صفحه اطلاعات شرکت باز می‌شود.';
      el('qr').innerHTML = data.qr || '';
    }
    el('step-qr').hidden = false;
    show('step-qr');
  }

  el('confirm').addEventListener('click', function () {
    var btn = this;
    el('code-error').textContent = '';
    btn.disabled = true;
    btn.textContent = 'در حال بررسی…';
    api('/api/leads/verify', { lead_id: state.leadId, code: el('code').value })
      .then(function (data) {
        deliver(data);
        loadMine();
      })
      .catch(function (e) { el('code-error').textContent = e.message; })
      .then(function () { btn.disabled = false; btn.textContent = 'تأیید'; });
  });

  /* --- Step 4: next company ----------------------------------------- */
  el('again').addEventListener('click', function () {
    state = { datasetId: '', leadId: '', masked: '' };
    ['first', 'last', 'position', 'phone', 'code'].forEach(function (id) { el(id).value = ''; });
    hideDuplicateAsk();
    el('chosen').hidden = true;
    el('q').hidden = false;
    el('q').value = '';
    el('qr').innerHTML = '';
    ['reg-error', 'code-error'].forEach(function (id) { el(id).textContent = ''; });
    /* Kiosk hygiene: the next visitor at this shared phone must not inherit a
       half-typed company proposal from whoever used it before them. */
    el('new-company').hidden = true;
    ['new-title', 'new-text'].forEach(function (id) { el(id).value = ''; });
    el('new-company-error').textContent = '';
    show('step-company');
    el('q').focus();
    search();
  });

  /* --- The visitor's own tally --------------------------------------- */
  /* Three states, in words. The badge says where it stands, the line under it
     says what is still missing, so nobody has to guess what `verified` means. */
  var LABEL = {
    unverified: ['منتظر کد', 'wait', 'کد تأیید هنوز وارد نشده.'],
    verified: ['شماره تأیید شد', 'info', 'مخاطب هنوز اطلاعات شرکت را تأیید نکرده.'],
    completed: ['تمام شد', 'ok', 'مخاطب اطلاعات شرکت را تأیید کرد.']
  };

  function row(lead) {
    var meta = LABEL[lead.status] || ['در جریان', '', ''];
    var li = document.createElement('li');
    var main = document.createElement('div');
    main.className = 'lead-main';
    var name = document.createElement('span');
    name.className = 'lead-name';
    name.textContent = lead.company_name;
    main.appendChild(name);
    if (meta[2]) {
      var note = document.createElement('span');
      note.className = 'lead-note';
      note.textContent = meta[2];
      main.appendChild(note);
    }
    var badge = document.createElement('span');
    badge.className = 'badge ' + meta[1];
    badge.textContent = meta[0];
    li.appendChild(main);
    li.appendChild(badge);
    return li;
  }

  function loadMine() {
    api('/api/leads/mine').then(function (data) {
      var ul = el('mine');
      ul.innerHTML = '';
      var leads = data.leads || [];
      if (!leads.length) {
        var empty = document.createElement('li');
        empty.textContent = 'هنوز شرکتی ثبت نکرده‌اید.';
        ul.appendChild(empty);
        return;
      }
      leads.forEach(function (lead) { ul.appendChild(row(lead)); });
    }).catch(function () {});
  }

  search();
  loadMine();
})();
