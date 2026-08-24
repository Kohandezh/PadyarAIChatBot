/* «شرکت من». The companies this person is responsible for, the one field they
   may change, and an honest answer about what happened to the last thing they
   sent. Nothing about their own profile, because there is no way to change it
   and offering one would be a lie. */
(function () {
  'use strict';

  var el = function (id) { return document.getElementById(id); };
  var MAX = 4000;
  var state = { companies: [], current: null };

  function api(url, body) {
    return fetch(url, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    } : {}).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          var err = new Error(data.detail || 'کاری از پیش نرفت. یک بار دیگر بزنید.');
          err.status = r.status;
          err.data = data;
          throw err;
        }
        return data;
      });
    }, function () {
      var err = new Error('اینترنت گوشی وصل نشد. یک بار دیگر بزنید.');
      err.offline = true;
      throw err;
    });
  }

  /* An expired or revoked session is not an error to explain, it is a login. */
  function guard(err) {
    if (err.status === 401 || err.status === 403) {
      location.replace('/login');
      return true;
    }
    return false;
  }

  /* The person reading this is a company manager on a phone who may not
     remember what this system is, so the banner carries the whole explanation.

     Two of these must never be mistaken for each other. `rejected` means a
     person read the text and refused it: there is a reason, and the manager
     has to change something. `none` means they have simply not sent anything
     yet: nothing was refused and nobody did anything wrong. Telling somebody
     their text was turned down when they never sent one is worse than saying
     nothing, so `none` gets its own tone, its own words and no reason line. */
  var STATE = {
    none: ['info', 'یک قدم مانده',
      'متن پایین را بخوانید. اگر درست است دکمهٔ «ثبت متن» را بزنید. اگر جایی‌اش باید عوض شود، همین‌جا اصلاحش کنید و بعد بفرستید.'],
    pending: ['wait', 'در انتظار بررسی',
      'متن شما ثبت شده و در نوبت بررسی است. تا وقتی تأیید نشده، همان متن قبلی روی چت‌بات می‌ماند.'],
    approved: ['ok', 'تأیید شد',
      'متن شما تأیید شد و روی چت‌بات نمایشگاه نشست. اگر باز هم تغییری لازم بود، همین‌جا بنویسید و بفرستید.'],
    rejected: ['bad', 'متن شما نیاز به اصلاح دارد',
      'همکار ما متن را خواند و فعلاً روی چت‌بات نگذاشت. متن را اصلاح کنید و دوباره بفرستید.']
  };

  /* The same sentence when there IS a reason to point at. A rejection made
     before the reviewer had a box to write in has none, and a page that says
     «دلیلش را پایین نوشته» above an empty line is a page that lies. */
  var REJECTED_WITH_REASON =
    'همکار ما متن را خواند و فعلاً روی چت‌بات نگذاشت. دلیلش را پایین نوشته است. متن را اصلاح کنید و دوباره بفرستید.';

  /* The server may carry the review result inline on the company row or under
     a `submission` object. Both are read here so the page has one shape. */
  function submission(company) {
    var s = company.submission || {};
    var status = s.status || company.edit_status || (company.pending ? 'pending' : 'none');
    var reason = s.reason || s.note || company.review_note || '';
    return { status: status, reason: typeof reason === 'string' ? reason.trim() : '' };
  }

  function paintState(company) {
    var info = submission(company);
    var meta = STATE[info.status];
    if (!meta) {
      el('state').hidden = true;
      return;
    }
    var rejectedWithReason = info.status === 'rejected' && info.reason;
    el('state').hidden = false;
    el('state').className = 'state ' + meta[0];
    el('state-title').textContent = meta[1];
    el('state-text').textContent = rejectedWithReason ? REJECTED_WITH_REASON : meta[2];
    /* textContent, never innerHTML. An administrator typed this and a stranger
       is reading it on their own phone. */
    el('state-reason').hidden = !rejectedWithReason;
    el('state-reason').textContent = info.reason ? 'دلیل: ' + info.reason : '';
  }

  function count() {
    var left = MAX - el('text').value.length;
    el('counter').hidden = left > 400;
    el('counter').textContent = 'جای ' + left + ' حرف دیگر باقی است.';
    el('counter').classList.toggle('over', left <= 0);
  }

  function openCompany(company) {
    state.current = company;
    el('pick').hidden = true;
    el('editor').hidden = false;
    el('back').hidden = state.companies.length < 2;
    el('company').textContent = company.title || 'شرکت شما';
    el('text').value = typeof company.text === 'string' ? company.text : '';
    el('error').textContent = '';
    el('saved').textContent = '';
    paintState(company);
    count();
    /* The one case where the person has to act. Put them in the box. */
    if (submission(company).status === 'rejected') { el('text').focus(); }
  }

  /* The list carries the text when the server sends it. When it does not, the
     text for the chosen company is fetched on its own. */
  function open(company) {
    if (typeof company.text === 'string') { openCompany(company); return; }
    api('/api/my/edit/' + encodeURIComponent(company.id)).then(function (data) {
      company.text = data.text || '';
      if (data.company && !company.title) { company.title = data.company; }
      if (data.submission && !company.submission) { company.submission = data.submission; }
      if (data.pending && !company.submission && !company.edit_status) { company.pending = true; }
      openCompany(company);
    }).catch(function (e) {
      if (guard(e)) { return; }
      openCompany(company);
      el('error').textContent = e.message;
    });
  }

  function renderPick() {
    var ul = el('companies');
    ul.innerHTML = '';
    state.companies.forEach(function (company) {
      var info = submission(company);
      var meta = STATE[info.status];
      var li = document.createElement('li');
      var row = document.createElement('div');
      row.className = 'pick-row';
      var name = document.createElement('span');
      name.className = 'lead-name';
      name.textContent = company.title || 'شرکت شما';
      row.appendChild(name);
      if (meta) {
        var badge = document.createElement('span');
        badge.className = 'badge ' + (meta[0] === 'bad' ? 'err' : meta[0]);
        badge.textContent = meta[1];
        row.appendChild(badge);
      }
      li.appendChild(row);
      li.addEventListener('click', function () { open(company); });
      ul.appendChild(li);
    });
    el('pick').hidden = false;
  }

  function load() {
    api('/api/my/companies').then(function (data) {
      el('loading').hidden = true;
      state.companies = data.companies || [];
      if (!state.companies.length) { el('empty').hidden = false; return; }
      /* One company is the normal case, so it opens straight into the text
         box. A list of one is a step that buys nobody anything. */
      if (state.companies.length === 1) { open(state.companies[0]); return; }
      renderPick();
    }).catch(function (e) {
      if (guard(e)) { return; }
      el('loading').hidden = true;
      el('empty').hidden = false;
      el('empty').querySelector('h2').textContent = 'الان باز نشد';
      el('empty').querySelector('p').textContent = e.message;
    });
  }

  el('text').addEventListener('input', function () {
    el('error').textContent = '';
    el('saved').textContent = '';
    count();
  });

  el('save').addEventListener('click', function () {
    var btn = this;
    var company = state.current;
    if (!company) { return; }
    el('error').textContent = '';
    el('saved').textContent = '';
    if (!el('text').value.trim()) {
      el('error').textContent = 'کادر متن خالی است. متن معرفی شرکت را بنویسید.';
      el('text').focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'در حال ثبت…';
    /* Only `text` goes up. Which company this is comes from the ownership
       record on the server, so the body has nothing to point elsewhere. */
    api('/api/my/edit/' + encodeURIComponent(company.id), { text: el('text').value })
      .then(function () {
        company.text = el('text').value;
        company.submission = { status: 'pending' };
        company.edit_status = 'pending';
        company.pending = true;
        paintState(company);
        el('saved').textContent = 'متن شما ثبت شد. بعد از بررسی روی چت‌بات نمایشگاه قرار می‌گیرد.';
        window.scrollTo({ top: 0, behavior: 'smooth' });
      })
      .catch(function (e) {
        if (guard(e)) { return; }
        el('error').textContent = e.message;
      })
      .then(function () {
        btn.disabled = false;
        btn.textContent = 'ثبت متن';
      });
  });

  el('back').addEventListener('click', function () {
    state.current = null;
    el('editor').hidden = true;
    renderPick();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  el('signout').addEventListener('click', function () {
    this.disabled = true;
    api('/api/auth/logout', {}).catch(function () {}).then(function () {
      location.replace('/login');
    });
  });

  load();
})();
