/* The company contact's page. One field, one button, one ending.
   Which company this is comes from the invite in the URL and is resolved on
   the server, so there is nothing on this page to point at another company.
   The invite dies the moment the server accepts the text, which is why the
   confirm button is removed rather than re-enabled on success. */
(function () {
  'use strict';
  var el = function (id) { return document.getElementById(id); };
  var MAX = 4000;

  var token = (location.pathname.match(/\/edit\/([^/?#]+)/) || [])[1] || '';
  var endpoint = '/api/leads/edit/' + token;

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

  api(endpoint).then(function (data) {
    /* A missing name leaves the heading that shipped with the page. An empty
       heading, or the word undefined, would be worse than a generic one. */
    var company = typeof data.company === 'string' ? data.company.trim() : '';
    if (company) { el('company').textContent = company; }
    el('text').value = data.text || '';
    el('pending-note').hidden = !data.pending;
    /* The booth script is an admin setting and can be reworded. The page ships
       with the approved text so the consent is never missing. */
    if (data.consent_script) { el('consent-text').textContent = data.consent_script; }
    count();
  }).catch(function (e) {
    el('error').textContent = e.message;
    el('save').disabled = true;
  });

  el('text').addEventListener('input', count);

  el('save').addEventListener('click', function () {
    var btn = this;
    el('error').textContent = '';
    if (!el('text').value.trim()) {
      el('error').textContent = 'کادر متن خالی است. متن معرفی شرکت را بنویسید.';
      el('text').focus();
      return;
    }
    btn.disabled = true;
    btn.textContent = 'در حال ثبت…';
    api(endpoint, { text: el('text').value })
      .then(function () {
        el('form-card').hidden = true;
        el('final-card').hidden = false;
        window.scrollTo({ top: 0, behavior: 'smooth' });
      })
      .catch(function (e) {
        el('error').textContent = e.message;
        btn.disabled = false;
        btn.textContent = 'تأیید می‌کنم';
      });
  });
})();
