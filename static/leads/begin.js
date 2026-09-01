/* The one-time gate. The link in the URL is worth exactly one button press;
   the press is a POST, so a messenger prefetching the page spends nothing.
   After a successful press the browser holds the edit-session cookie and is
   sent back to the same URL, which now serves the form directly. */
(function () {
  'use strict';
  var el = function (id) { return document.getElementById(id); };
  var token = (location.pathname.match(/\/edit\/([^/?#]+)/) || [])[1] || '';

  el('begin').addEventListener('click', function () {
    var btn = this;
    el('error').textContent = '';
    btn.disabled = true;
    btn.textContent = 'در حال باز کردن…';
    fetch('/api/leads/edit/' + encodeURIComponent(token) + '/begin', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}'
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) { throw new Error(data.detail || 'خطایی رخ داد. دوباره تلاش کنید.'); }
        return data;
      });
    }).then(function () {
      /* Same URL, new state: the cookie answers for the dead token now. */
      location.reload();
    }).catch(function (e) {
      el('error').textContent = e.message;
      btn.disabled = false;
      btn.textContent = 'نمایش اطلاعات شرکت من';
    });
  });
})();
