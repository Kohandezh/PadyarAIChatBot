/* ── Visitor registration (Smart Visit) ──
   One way in: the visitor's FIRST message to the chatbot. It is held, not
   answered, and the sign-up card opens over the chat. The held question is
   sent for real the moment sign-up is finished, so nothing anyone typed is
   lost.

   There used to be a second entry point — a «بازدید هوشمند» CTA in the
   theme footer — which was removed as a redundant duplicate of this gate.
   Two consequences worth knowing before changing anything here:
     * the visit-plan card (renderPlanStep) and the profile editor
       (renderEditStep) it links to have no UI entry point any more. The code
       and /api/visit-plan are deliberately left intact; only the door is
       gone. Restoring a control that calls `openModal(renderPlanStep)` is
       all it takes to bring them back;
     * the whole module used to abort when the CTA was absent. It no longer
       depends on any markup, so it loads on every theme.

   Sign-up is deliberately three inputs: name, mobile, and the taxonomy's own
   checkbox. Everything else about the visitor is asked AFTERWARDS, in the
   chat, one question at a time, answered by TAPPING options that write
   themselves into the message box — a dropdown is a bad control on a phone,
   and this is used on phones at an exhibition.

   The modal keeps only what must be a form: the two identity fields and the
   SMS code.

   Mobile autofill, two independent paths:

     * WebOTP (Chrome on Android) reads the SMS itself, but only when the body
       ends with `@host #code` — see app/services/otp.py :: _message_for. On a
       gateway that sends an APPROVED TEMPLATE (Asanak service lines) that line
       cannot be added, so this path is simply inactive there.
     * The OS/keyboard code suggestion, which needs no format at all and is
       what actually fills the field for most visitors. It hands over the whole
       code at once — which is why the code entry is ONE input rather than a
       row of maxlength="1" boxes.

   The flow never depends on either; typing always works.

   WHO THE VISITOR IS IS NOT THIS FILE'S DECISION.

   A successful verification makes the SERVER set an HttpOnly session cookie
   (app/auth/visitor.py). This script cannot read it, cannot write it and
   cannot forge it, which is the entire point. "Am I signed in" is answered by
   GET /api/auth/session and by nothing else — the answer is cached in memory
   for the page and re-read at the two moments it changes, verification and
   logout.

   What this replaced, and why: the challenge id from the OTP step used to be
   written into localStorage and re-sent in the body of /api/auth/profile and
   /api/visit-plan as proof of identity. That is a bearer token that never
   expires, sitting where any injected script can read it. And isSignedIn()
   used to mean "localStorage has a name in it", so the gate below was a
   suggestion. Both are gone. localStorage now holds one thing, a display name,
   so the header does not flicker; see KEY_NAME.
*/
(function () {
    'use strict';

    /* A DISPLAY NAME AND NOTHING ELSE.
       This key is never proof of anything. Whether someone is signed in is
       answered by GET /api/auth/session, which reads the HttpOnly cookie the
       server issued — a cookie this script cannot read, write or forge. All
       this remembers is the name to print in the header, so it does not
       flicker in the moment between the page painting and that answer
       arriving. Editing it by hand changes a label, not an identity.

       The key it replaces, `inotex-visitor`, held the whole profile AND the
       challenge id, and the challenge id was treated as the login. That blob
       is deleted at boot from every browser that still has one. */
    const KEY_NAME = 'padyar-visitor-name';
    const KEY_LEGACY = 'inotex-visitor';
    const OTP_LENGTH = 6;

    /* Ask about interests in the chat? The owner may decide these belong on
       the sign-up card instead. Flip this to false and the third in-chat
       question disappears — nothing else changes. */
    const ASK_INTERESTS = true;

    // What /api/auth/profile accepts. Clamping here means a chatty answer is
    // shortened rather than rejected with a 422 the visitor cannot read.
    const MAX_JOB = 80, MAX_POSITION = 80, MAX_INTERESTS = 400;

    const isFa = function () { return document.documentElement.lang !== 'en'; };
    const T = {
        fa: {
            title: 'ثبت‌نام بازدید هوشمند',
            sub: 'برای شروع، مشخصات خود را وارد کنید.',
            first: 'نام', last: 'نام خانوادگی', phone: 'شماره موبایل',
            job: 'شغل / حوزهٔ فعالیت', position: 'سمت',
            interests: 'زمینه‌های مورد علاقه',
            targetedNote: 'با تکمیل این بخش، غرفه‌های مرتبط با کار و علاقهٔ شما پیشنهاد می‌شود.',
            submit: 'دریافت کد تأیید',
            codeTitle: 'تأیید شماره',
            codeSub: 'کد ۶ رقمی ارسال‌شده را وارد کنید',
            verify: 'تأیید و ورود',
            resend: 'ارسال مجدد کد', change: 'تغییر شماره',
            expires: 'زمان اعتبار کد',
            close: 'بستن',
            required: 'لطفاً همهٔ فیلدها را کامل کنید.',
            badPhone: 'شماره موبایل معتبر نیست.',
            network: 'خطای شبکه. دوباره تلاش کنید.',
            logout: 'خروج از سیستم',
            autofilled: 'کد از پیامک خوانده شد.',
            planTitle: 'بازدید هدفمند شما',
            planSub: 'بر اساس کار و علاقه‌مندی‌تان، اول سراغ این بخش‌ها بروید:',
            planEmpty: 'هنوز چیزی دربارهٔ کار و علاقه‌تان ثبت نشده است.',
            planGeneral: 'و اگر وقت داشتید:',
            planDone: 'باشه',
            choose: 'انتخاب کنید…',
            searchHint: 'جست‌وجو یا افزودن…',
            addItem: function (v) { return '«' + v + '» را اضافه کن'; },
            noMatch: 'موردی پیدا نشد — می‌توانید خودتان اضافه کنید.',
            remove: 'حذف',
            editProfile: 'ویرایش اطلاعات و علاقه‌مندی‌ها',
            editTitle: 'ویرایش اطلاعات شما',
            editSub: 'نام و شماره ثابت است؛ شغل، سمت و علاقه‌مندی‌ها را می‌توانید تغییر دهید.',
            saveEdit: 'ذخیره و به‌روزرسانی پیشنهادها',
            // Sign-up card (three inputs)
            signupSub: 'فقط دو کادر و یک تیک — بعد پاسخ شما را می‌فرستم.',
            fullName: 'نام و نام خانوادگی',
            needName: 'لطفاً نام و نام خانوادگی خود را بنویسید.',
            // Holding the first message
            held: 'سؤال شما را نگه داشتم. اول در چند ثانیه ثبت‌نام کنید تا پاسخ را بفرستم.',
            // The three in-chat questions
            hello2: function (name) {
                return 'خوش آمدید ' + name + '! سه سؤال کوتاه می‌پرسم تا بهتر راهنمایی‌تان کنم.';
            },
            askJob: 'شغل یا حوزهٔ فعالیت شما چیست؟',
            askPosition: 'سمت شما چیست؟',
            askInterests: 'به کدام زمینه‌ها علاقه دارید؟',
            tapOne: 'یکی را لمس کنید (یا خودتان بنویسید) و دکمهٔ ارسال را بزنید.',
            tapMany: 'هر چند مورد که خواستید لمس کنید و دکمهٔ ارسال را بزنید.',
            skip: 'رد کردن',
            profileSaved: 'ممنون! ثبت شد.'
        },
        en: {
            title: 'Smart Visit registration',
            sub: 'Enter your details to get started.',
            first: 'First name', last: 'Last name', phone: 'Mobile number',
            job: 'Field of work', position: 'Job title',
            interests: 'Topics you care about',
            targetedNote: 'Fill this in and the assistant suggests the booths that match your work and interests.',
            submit: 'Send verification code',
            codeTitle: 'Verify your number',
            codeSub: 'Enter the 6-digit code we sent',
            verify: 'Verify and continue',
            resend: 'Resend code', change: 'Change number',
            expires: 'Code expires in',
            close: 'Close',
            required: 'Please complete every field.',
            badPhone: 'That mobile number is not valid.',
            network: 'Network error. Please try again.',
            logout: 'Log out',
            autofilled: 'Code read from the SMS.',
            planTitle: 'Your targeted visit',
            planSub: 'Based on your work and interests, start with these:',
            planEmpty: 'Nothing about your work or interests has been saved yet.',
            planGeneral: 'And if you have time:',
            planDone: 'Got it',
            choose: 'Choose…',
            searchHint: 'Search or add…',
            addItem: function (v) { return 'Add "' + v + '"'; },
            noMatch: 'Nothing found — you can add your own.',
            remove: 'Remove',
            editProfile: 'Edit your details and interests',
            editTitle: 'Edit your details',
            editSub: 'Name and number stay fixed; your job, title and interests can change.',
            saveEdit: 'Save and update suggestions',
            signupSub: 'Two boxes and one tick — then I will answer you.',
            fullName: 'Full name',
            needName: 'Please enter your full name.',
            held: 'I am holding your question. Sign up — it takes seconds — and I will answer it.',
            hello2: function (name) {
                return 'Welcome ' + name + '! Three short questions so I can help you better.';
            },
            askJob: 'What is your field of work?',
            askPosition: 'What is your job title?',
            askInterests: 'Which topics do you care about?',
            tapOne: 'Tap one (or type your own), then press send.',
            tapMany: 'Tap as many as you like, then press send.',
            skip: 'Skip',
            profileSaved: 'Thank you — saved.'
        }
    };
    const t = function () { return isFa() ? T.fa : T.en; };

    // ── Who the server says you are ──────────────────────────────────
    /* The ONE answer to "am I signed in", and it is not this browser's to give.
       GET /api/auth/session reads the HttpOnly session cookie and reports back.

       Cached in memory for the life of the page: the send gate below runs on
       every message, and a request per keystroke at an exhibition, over a hall
       full of phones on one access point, is not a cost worth paying for an
       answer that changes exactly twice — at verification and at logout. Both
       of those call refreshServerSession() themselves.

       `known` matters as much as `signed_in`. Before the first answer arrives,
       and after a failed probe, this browser does not know; it must not guess.
       See gate(). */
    let server = { known: false, signed_in: false, profile: {} };

    function refreshServerSession() {
        return fetch('/api/auth/session', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return server;   // no such endpoint: module is off here
                server = {
                    known: true,
                    signed_in: !!d.signed_in,
                    profile: d.profile || {}
                };
                if (server.signed_in) rememberName(server.profile);
                else forgetName();
                paintSession();
                return server;
            })
            .catch(function () { return server; });
    }

    // ── The name in the header (a label, never a credential) ─────────
    function storedName() {
        try {
            const raw = JSON.parse(localStorage.getItem(KEY_NAME) || 'null');
            return raw && typeof raw === 'object' ? raw : null;
        } catch (e) { return null; }
    }
    function rememberName(profile) {
        try {
            localStorage.setItem(KEY_NAME, JSON.stringify({
                first_name: (profile || {}).first_name || '',
                last_name: (profile || {}).last_name || ''
            }));
        } catch (e) { /* private mode — the header just will not preload */ }
    }
    function forgetName() {
        try { localStorage.removeItem(KEY_NAME); } catch (e) { }
    }

    function displayName(p) {
        return [p.first_name, p.last_name].filter(Boolean).join(' ').trim();
    }

    // A door-with-an-arrow, same stroke style as every other drawer-row icon
    // (see themes/*/partials/menu.html). Built once, reused by every paint —
    // only the label text span is rewritten (language switches, etc.).
    var LOGOUT_ICON_SVG = '<svg viewBox="0 0 24 24" width="17" height="17" fill="none" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" ' +
        'aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/>' +
        '<polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>';

    /* Draw the logout button for anyone who is signed in.

       KEYED ON THE SESSION, NOT ON A NAME. It used to be keyed on the name,
       and the /verify page posts only { destination }: those visitors have
       first_name = '' and last_name = '', so displayName() was '' and this
       function REMOVED the button. They were signed in for weeks with no way
       to sign out anywhere in the UI. The button says «خروج از سیستم» /
       "Log out", which needs no name to make sense.

       displayName() stays in the condition as the second half: before the server
       answers, a remembered name is enough to paint the button, so a returning
       visitor does not watch their own header appear late. The moment the
       server answers, that answer wins: an anonymous reply erases the name and
       the button with it. */
    function paintSession() {
        // A mirror of the server's answer, for anything that has to look at
        // it from outside this closure: a theme styling a signed-in header, a
        // test waiting for the probe to land. It is a REFLECTION and never the
        // source — editing this attribute in a console changes a stylesheet,
        // not a session. "unknown" until the server has answered.
        document.documentElement.dataset.visitor = server.known
            ? (server.signed_in ? 'in' : 'out')
            : 'unknown';

        const p = server.known
            ? (server.signed_in ? server.profile : null)
            : storedName();
        // The account row at the bottom of the hamburger drawer — one fixed
        // anchor present on every theme, instead of the old `.header-tools`
        // lookup (a class only two of four themes ever had, so this control
        // silently never appeared on the other two).
        const section = document.getElementById('menu-account-section');
        let logoutBtn = document.getElementById('visitor-logout');
        // Admin-toggleable (Settings → برندینگ → «نمایش موارد منو»): the row
        // stays hidden even for a signed-in visitor when the admin turned it
        // off. Missing attribute (no server-rendered menu.html reached this
        // page, e.g. the legacy fallback index.html) defaults to allowed.
        const logoutAllowed = !section || section.dataset.showLogout !== 'false';

        if (logoutAllowed && p && (server.signed_in || displayName(p))) {
            if (!logoutBtn && section) {
                logoutBtn = document.createElement('button');
                logoutBtn.type = 'button';
                logoutBtn.id = 'visitor-logout';
                logoutBtn.className = 'visitor-logout menu-logout-btn';
                logoutBtn.innerHTML = LOGOUT_ICON_SVG + '<span class="menu-logout-btn-label"></span>';
                logoutBtn.addEventListener('click', logout);
                section.append(logoutBtn);
            }
            if (logoutBtn) {
                const label = logoutBtn.querySelector('.menu-logout-btn-label');
                if (label) label.textContent = t().logout;
                logoutBtn.title = t().logout;
                logoutBtn.setAttribute('aria-label', t().logout);
            }
        } else {
            if (logoutBtn) logoutBtn.remove();
        }
    }

    /* Signing out is the server's job. Deleting the local name only hid the
       button: the session row and its cookie survived, so /chat still answered
       and the next person on a shared booth phone inherited the last one's
       identity. The row has to die, and only the server can kill it. */
    function logout() {
        post('/api/auth/logout', {})
            .then(function () {
                /* The session is dead, so the words on the screen have to go
                   too. Sign-out used to revoke and reload, and nothing else:
                   the bubbles stayed, and static/chat/core.js loadHistory()
                   replayed them from localStorage on the next page load. On a
                   shared booth phone the next person read the last person's
                   conversation. The "New chat" button already forgot both;
                   the strongest leaving gesture in the product forgot less.

                   Only on success, for the same reason New chat only clears
                   on success: a wiped screen while the session is still live
                   looks exactly like signing out and is not.

                   forgetTranscript() lives in core.js, which the theme footer
                   loads BEFORE this file. The guard is for a page that loads
                   this script without the chat. */
                if (typeof forgetTranscript === 'function') forgetTranscript();
            })
            .catch(function () { /* offline: nothing was revoked, so say nothing */ })
            .then(function () { return refreshServerSession(); });
    }

    // ── Modal ────────────────────────────────────────────────────────
    let modal = null, state = {};

    function closeModal() {
        if (!modal) return;
        stopTimer();
        abortOtpListener();
        modal.remove();
        modal = null;
        // Focus goes back to the message box — the CTA that used to own it
        // is gone, and the chat is where the visitor was.
        const input = document.getElementById('user-input');
        if (input) input.focus();
    }

    function el(tag, cls, text) {
        const n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    function openModal(step) {
        if (modal) return;
        modal = el('div', 'reg-overlay');
        modal.setAttribute('role', 'dialog');
        modal.setAttribute('aria-modal', 'true');
        modal.setAttribute('aria-labelledby', 'reg-title');

        const card = el('div', 'reg-card');
        const close = el('button', 'reg-close', '×');
        close.type = 'button';
        close.title = t().close;
        close.setAttribute('aria-label', t().close);
        close.addEventListener('click', closeModal);

        const heading = el('h2', 'reg-title', t().title);
        heading.id = 'reg-title';
        const sub = el('p', 'reg-sub', t().sub);
        card.append(close, heading, sub);

        const body = el('div', 'reg-body');
        card.append(body);
        const status = el('p', 'reg-status');
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        card.append(status);

        modal.append(card);
        // Clicking the backdrop (never the card) dismisses.
        modal.addEventListener('mousedown', function (e) { if (e.target === modal) closeModal(); });
        document.addEventListener('keydown', onEsc);
        document.body.append(modal);

        state = { status: status, body: body, heading: heading, sub: sub };
        (step || renderSignupStep)();
    }

    function onEsc(e) { if (e.key === 'Escape') closeModal(); }

    function setHead(title, subtitle) {
        if (state.heading) state.heading.textContent = title;
        if (state.sub) state.sub.textContent = subtitle;
    }

    function say(text, kind) {
        if (!state.status) return;
        state.status.textContent = text || '';
        state.status.dataset.kind = kind || '';
    }

    // ── Options (jobs / interests / checkboxes) ──────────────────────
    /* Fetched from the server, which reads them from the taxonomy file. The
       form renders whatever arrives — replacing the file changes the choices
       with no change here. A failed fetch degrades to free-text inputs rather
       than blocking registration. */
    let options = null;

    function loadOptions() {
        if (options) return Promise.resolve(options);
        return fetch('/api/registration/options?lang=' + (isFa() ? 'fa' : 'en'))
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (o) { options = o || { jobs: [], interests: [], flags: [] }; return options; })
            .catch(function () { options = { jobs: [], interests: [], flags: [] }; return options; });
    }

    // ── Searchable multi-select ──────────────────────────────────────
    /* One text box that filters the list and also accepts anything the visitor
       types — a taxonomy can never be complete, and a visitor whose field is
       missing must not be forced into an approximate one. */
    function multiSelect(items, preselected) {
        const chosen = [];
        const wrap = el('div', 'reg-multi');

        const chips = el('div', 'reg-chips');
        const search = el('input', 'reg-search');
        search.type = 'text';
        search.autocomplete = 'off';
        search.placeholder = t().searchHint;
        const list = el('div', 'reg-options');
        const addBtn = el('button', 'reg-add');
        addBtn.type = 'button';
        addBtn.hidden = true;

        function has(label) {
            return chosen.some(function (c) { return c.toLowerCase() === label.toLowerCase(); });
        }

        function paintChips() {
            chips.textContent = '';
            chosen.forEach(function (label) {
                const chip = el('span', 'reg-chip', label);
                const x = el('button', 'reg-chip-x', '×');
                x.type = 'button';
                x.setAttribute('aria-label', t().remove + ' ' + label);
                x.addEventListener('click', function () {
                    chosen.splice(chosen.indexOf(label), 1);
                    paintChips(); paintList();
                });
                chip.append(x);
                chips.append(chip);
            });
            chips.hidden = chosen.length === 0;
        }

        function paintList() {
            const q = search.value.trim().toLowerCase();
            list.textContent = '';
            let shown = 0;
            items.forEach(function (item) {
                if (q && item.label.toLowerCase().indexOf(q) === -1) return;
                const opt = el('button', 'reg-option', item.label);
                opt.type = 'button';
                const on = has(item.label);
                opt.dataset.on = on ? 'true' : 'false';
                opt.setAttribute('aria-pressed', on ? 'true' : 'false');
                opt.addEventListener('click', function () {
                    if (has(item.label)) chosen.splice(chosen.findIndex(function (c) {
                        return c.toLowerCase() === item.label.toLowerCase();
                    }), 1);
                    else chosen.push(item.label);
                    paintChips(); paintList();
                });
                list.append(opt);
                shown++;
            });
            // Nothing in the list matches what they typed → offer to add it.
            const typed = search.value.trim();
            const canAdd = typed.length > 1 && !has(typed) &&
                !items.some(function (i) { return i.label.toLowerCase() === typed.toLowerCase(); });
            addBtn.hidden = !canAdd;
            addBtn.textContent = t().addItem(typed);
            if (!shown && !canAdd) list.append(el('p', 'reg-empty', t().noMatch));
        }

        addBtn.addEventListener('click', function () {
            const typed = search.value.trim();
            if (!typed || has(typed)) return;
            chosen.push(typed);
            search.value = '';
            paintChips(); paintList();
            search.focus();
        });

        search.addEventListener('input', paintList);
        search.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); if (!addBtn.hidden) addBtn.click(); }
        });

        (preselected || []).forEach(function (label) {
            if (label && !has(label)) chosen.push(label);
        });
        paintChips();
        paintList();

        wrap.append(chips, search, addBtn, list);
        return { node: wrap, value: function () { return chosen.join('، '); }, focus: function () { search.focus(); } };
    }

    function splitInterests(text) {
        return String(text || '').split(/[،,]/).map(function (s) { return s.trim(); }).filter(Boolean);
    }

    // ── Step 1: sign up — three inputs, nothing else ─────────────────
    /* Name, mobile, and whatever checkbox the taxonomy defines. Job, position
       and interests are deliberately NOT here: they are asked in the chat once
       the number is verified, where each one is a single tap to answer. */
    function renderSignupStep() {
        state.body.textContent = '';
        setHead(t().title, t().signupSub);

        const form = el('form', 'reg-form');
        state.body.append(form);

        function field(id, labelText, type, autocomplete) {
            const wrap = el('div', 'reg-field');
            const lab = el('label', null, labelText);
            lab.htmlFor = id;
            const input = el('input');
            input.id = id;
            input.type = type;
            input.autocomplete = autocomplete;
            input.required = true;
            wrap.append(lab, input);
            form.append(wrap);
            return input;
        }

        const nameInput = field('reg-name', t().fullName, 'text', 'name');
        const phoneInput = field('reg-phone', t().phone, 'tel', 'tel');
        phoneInput.dir = 'ltr';
        phoneInput.inputMode = 'tel';
        phoneInput.placeholder = '09xxxxxxxxx';

        // The checkbox and its wording come from the taxonomy file, so an
        // admin changes the question without anyone touching this file.
        const flagsWrap = el('div', 'reg-flags');
        form.append(flagsWrap);

        const submit = el('button', 'reg-submit', t().submit);
        submit.type = 'submit';
        form.append(submit);

        loadOptions().then(function (o) {
            (o.flags || []).forEach(function (f) {
                const row = el('label', 'reg-check');
                const box = el('input');
                box.type = 'checkbox';
                box.value = f.label;
                box.dataset.flag = f.id;
                row.append(box, el('span', null, f.label));
                flagsWrap.append(row);
            });
        });

        function checkedFlags() {
            return [].slice.call(flagsWrap.querySelectorAll('input:checked'))
                .map(function (b) { return b.value; });
        }

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            const full = nameInput.value.trim().replace(/\s+/g, ' ');
            const phone = phoneInput.value.trim();
            if (!full) { say(t().needName, 'error'); nameInput.focus(); return; }
            if (!phone) { say(t().badPhone, 'error'); phoneInput.focus(); return; }

            // One field, two database columns: the first word is the given
            // name, the rest the family name. A single-word name is accepted
            // rather than refused — plenty of people have one.
            // 60 characters each is what the endpoint stores; clamping here
            // turns an over-long name into a shorter one rather than a 422.
            const parts = full.split(' ');
            const first = parts.shift().slice(0, 60);
            const last = parts.join(' ').slice(0, 60);

            submit.disabled = true;
            say('…');
            const flags = checkedFlags();
            post('/api/auth/otp/request', {
                destination: phone, first_name: first, last_name: last,
                job: '', position: '', interests: flags.join('، ')
            })
                .then(function (data) {
                    // Remembered so the interests answered in the chat later
                    // are MERGED with the checkbox instead of replacing it —
                    // both live in the same stored field.
                    state.flags = flags;
                    state.profile = { first_name: first, last_name: last };
                    renderCodeStep(data);
                })
                .catch(function (err) {
                    submit.disabled = false;
                    say(err.detail || t().network, 'error');
                });
        });

        setTimeout(function () { nameInput.focus(); }, 60);
    }

    // ── Editing a profile that already exists ────────────────────────
    /* Reached from the visit plan, never from sign-up. The visitor has already
       proved this number, so identity is shown locked and only the three
       descriptive fields can move. */
    function renderEditStep(prefill) {
        state.body.textContent = '';
        setHead(t().editTitle, t().editSub);
        const form = el('form', 'reg-form');
        state.body.append(form);

        [
            { id: 'reg-first', label: t().first, value: prefill.first_name || '' },
            { id: 'reg-last', label: t().last, value: prefill.last_name || '' },
            { id: 'reg-phone', label: t().phone, value: prefill.destination_masked || '' }
        ].forEach(function (f) {
            const wrap = el('div', 'reg-field');
            const lab = el('label', null, f.label);
            lab.htmlFor = f.id;
            const input = el('input');
            input.id = f.id;
            input.type = 'text';
            input.value = f.value;
            input.readOnly = true;
            input.dataset.locked = 'true';
            if (f.id === 'reg-phone') input.dir = 'ltr';
            wrap.append(lab, input);
            form.append(wrap);
        });

        const jobWrap = el('div', 'reg-field');
        const jobLab = el('label', null, t().job);
        jobLab.htmlFor = 'reg-job';
        const jobSel = el('select', 'reg-select');
        jobSel.id = 'reg-job';
        form.append(jobWrap);

        // سمت — its own field, because "what you do" and "how senior you are"
        // are different questions and the client asked for both.
        const posWrap = el('div', 'reg-field');
        const posLab = el('label', null, t().position);
        posLab.htmlFor = 'reg-position';
        form.append(posWrap);

        const interestWrap = el('div', 'reg-field');
        interestWrap.append(el('label', null, t().interests));
        form.append(interestWrap);

        const flagsWrap = el('div', 'reg-flags');
        form.append(flagsWrap);

        form.append(el('p', 'reg-note', t().targetedNote));
        const submit = el('button', 'reg-submit', t().saveEdit);
        submit.type = 'submit';
        form.append(submit);

        let picker = null, posField = null;

        /* A saved answer that is no longer in the taxonomy is kept as an extra
           option. Replacing the taxonomy must never silently erase what someone
           already told us. */
        function fillSelect(sel, items, saved) {
            const blank = el('option', null, t().choose);
            blank.value = '';
            sel.append(blank);
            (items || []).forEach(function (i) {
                const opt = el('option', null, i.label);
                opt.value = i.label;
                sel.append(opt);
            });
            if (saved) {
                if (!Array.prototype.some.call(sel.options, function (op) { return op.value === saved; })) {
                    const kept = el('option', null, saved);
                    kept.value = saved;
                    sel.append(kept);
                }
                sel.value = saved;
            }
        }

        loadOptions().then(function (o) {
            fillSelect(jobSel, o.jobs, prefill.job || '');
            jobWrap.append(jobLab, jobSel);

            // A taxonomy that lists positions gets a dropdown; one that does not
            // gets a plain text box, so the field never degrades into an empty
            // select the visitor cannot answer.
            if (o.positions && o.positions.length) {
                posField = el('select', 'reg-select');
                posField.id = 'reg-position';
                fillSelect(posField, o.positions, prefill.position || '');
            } else {
                posField = el('input');
                posField.id = 'reg-position';
                posField.type = 'text';
                posField.autocomplete = 'organization-title';
                posField.value = prefill.position || '';
            }
            posWrap.append(posLab, posField);

            // Flags are stored in the same field as the interests, so on the way
            // back in they must go to the checkbox that owns them — not also
            // become a chip, which would save them twice.
            const flagLabels = (o.flags || []).map(function (f) { return f.label; });
            const saved = splitInterests(prefill.interests);
            picker = multiSelect(o.interests || [], saved.filter(function (s) {
                return flagLabels.indexOf(s) === -1;
            }));
            interestWrap.append(picker.node);

            (o.flags || []).forEach(function (f) {
                const row = el('label', 'reg-check');
                const box = el('input');
                box.type = 'checkbox';
                box.value = f.label;
                box.dataset.flag = f.id;
                if (saved.indexOf(f.label) !== -1) box.checked = true;
                row.append(box, el('span', null, f.label));
                flagsWrap.append(row);
            });
        });

        function collect() {
            // Checked flags ride along with the interests, so the planner and
            // the stored profile stay a single field — no schema change.
            const flagged = [].slice.call(flagsWrap.querySelectorAll('input:checked'))
                .map(function (b) { return b.value; });
            const all = splitInterests(picker ? picker.value() : '').concat(flagged);
            // Edit → save → edit again must not accumulate duplicates.
            return all.filter(function (v, i) { return all.indexOf(v) === i; }).join('، ');
        }

        form.addEventListener('submit', function (e) {
            e.preventDefault();
            submit.disabled = true;
            say('…');
            // No identity in this body. The endpoint knows who is asking from
            // the session cookie, so all that travels is what changed.
            post('/api/auth/profile', {
                job: jobSel.value.trim().slice(0, MAX_JOB),
                position: (posField ? posField.value.trim() : '').slice(0, MAX_POSITION),
                interests: collect().slice(0, MAX_INTERESTS)
            })
                .then(function (data) {
                    server.profile = Object.assign({}, server.profile, data.profile || {});
                    rememberName(server.profile);
                    paintSession();
                    renderPlanStep();
                })
                .catch(function (err) {
                    submit.disabled = false;
                    say(err.detail || t().network, 'error');
                });
        });

        setTimeout(function () {
            const target = form.querySelector('#reg-job');
            if (target) target.focus();
        }, 60);
    }

    // ── Step 3: the targeted-visit plan ──────────────────────────────
    /* Shown right after verification, and again whenever a signed-in visitor
       taps their own name on the brick. The list comes from the server so the
       browser never decides what INOTEX contains. */
    function renderPlanStep() {
        state.body.textContent = '';
        setHead(t().planTitle, t().planSub);
        say('…');

        // The three descriptive fields, and no identity. They are plan input,
        // the same input an unregistered visitor may type for themselves — the
        // plan is public, so nothing here unlocks anything. They come from the
        // server's own copy of the profile rather than from localStorage,
        // because localStorage is now a name cache and nothing more.
        const p = server.profile || {};
        post('/api/visit-plan', {
            job: p.job || '', position: p.position || '', interests: p.interests || '',
            lang: isFa() ? 'fa' : 'en'
        })
            .then(function (plan) {
                say('');
                const list = el('ul', 'reg-plan');
                let generalHeaded = false;
                (plan.sections || []).forEach(function (s) {
                    // General sections sit under their own heading, so nothing
                    // without a reason can be read as a personal match.
                    if (s.general && !generalHeaded && plan.matched) {
                        generalHeaded = true;
                        list.append(el('li', 'reg-plan-head', t().planGeneral));
                    }
                    const li = el('li', 'reg-plan-item');
                    if (s.general) li.dataset.general = 'true';
                    li.append(el('strong', 'reg-plan-title', s.title));
                    if (s.why) li.append(el('span', 'reg-plan-why', s.why));
                    list.append(li);
                });
                state.body.append(list);
                // The plan always says what it is not — these are official
                // sections, not an exhibitor directory.
                if (!plan.matched && plan.empty_hint) {
                    state.body.append(el('p', 'reg-note', plan.empty_hint));
                }
                if (plan.note) state.body.append(el('p', 'reg-note reg-plan-note', plan.note));

                const done = el('button', 'reg-submit', t().planDone);
                done.type = 'button';
                done.addEventListener('click', closeModal);
                state.body.append(done);

                // Closing the loop: the plan is only as good as the profile, so
                // changing the profile is one click away from reading the plan.
                const actions = el('div', 'reg-actions');
                const edit = el('button', 'reg-link', t().editProfile);
                edit.type = 'button';
                edit.addEventListener('click', function () { renderEditStep(server.profile || {}); });
                actions.append(edit);
                state.body.append(actions);

                setTimeout(function () { done.focus(); }, 50);
            })
            .catch(function (err) {
                say(err.detail || t().network, 'error');
                const done = el('button', 'reg-submit', t().planDone);
                done.type = 'button';
                done.addEventListener('click', closeModal);
                state.body.append(done);
            });
    }

    // ── Step 2: code ─────────────────────────────────────────────────
    let cells = [], timerId = 0, expiresAt = 0, resendAt = 0, otpAbort = null;

    function renderCodeStep(data) {
        state.challenge = data.challenge_id;
        state.body.textContent = '';
        cells = [];

        state.body.append(el('h3', 'reg-step-title', t().codeTitle));
        const sub = el('p', 'reg-sub');
        sub.append(document.createTextNode(t().codeSub + ' '));
        const masked = el('bdi', 'reg-masked', data.destination_masked);
        masked.dir = 'ltr';
        sub.append(masked);
        state.body.append(sub);

        // ONE real input, five decorative cells laid over it.
        //
        // It used to be five inputs of maxlength="1". They look the same, but
        // the browser truncates an autofilled code to the first box, so the
        // OS/keyboard code suggestion filled "1" and stopped — the distribute
        // logic below never even ran. Documented behaviour on both Android
        // Chrome and iOS; the fix everyone lands on is a single field styled
        // to look like several.
        const group = el('div', 'reg-digits');
        group.dir = 'ltr';

        const field = el('input', 'reg-code-input');
        field.type = 'text';
        field.inputMode = 'numeric';
        field.autocomplete = 'one-time-code';
        field.maxLength = OTP_LENGTH;
        field.setAttribute('aria-label', t().codeTitle);
        group.append(field);
        state.codeInput = field;

        cells = [];
        for (let i = 0; i < OTP_LENGTH; i++) {
            const cell = el('div', 'reg-digit');
            cell.setAttribute('aria-hidden', 'true');
            group.append(cell);
            cells.push(cell);
        }
        state.body.append(group);
        wireDigits();

        const timer = el('p', 'reg-timer');
        timer.id = 'reg-timer';
        state.body.append(timer);

        const verify = el('button', 'reg-submit', t().verify);
        verify.type = 'button';
        verify.disabled = true;
        verify.addEventListener('click', submitCode);
        state.verifyBtn = verify;
        state.body.append(verify);

        const actions = el('div', 'reg-actions');
        const resend = el('button', 'reg-link', t().resend);
        resend.type = 'button';
        resend.disabled = true;
        resend.addEventListener('click', doResend);
        state.resendBtn = resend;
        const change = el('button', 'reg-link', t().change);
        change.type = 'button';
        change.addEventListener('click', function () { stopTimer(); abortOtpListener(); renderSignupStep(); });
        actions.append(resend, change);
        state.body.append(actions);

        applyTiming(data);
        say('');
        setTimeout(focusCode, 50);
        startOtpListener();
    }

    const DIGITS = { '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9', '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9' };
    function onlyDigits(v) {
        return String(v || '').replace(/[۰-۹٠-٩]/g, function (d) { return DIGITS[d] || d; }).replace(/\D/g, '');
    }

    function code() { return state.codeInput ? state.codeInput.value : ''; }

    function paint() {
        const v = code();
        const focused = document.activeElement === state.codeInput;
        cells.forEach(function (cell, i) {
            cell.textContent = v[i] || '';
            // The caret lives in an invisible input, so the cell about to be
            // typed into carries the focus ring instead.
            cell.classList.toggle('is-active', focused && i === Math.min(v.length, OTP_LENGTH - 1));
            cell.classList.toggle('is-filled', !!v[i]);
        });
        if (state.verifyBtn) state.verifyBtn.disabled = v.length !== OTP_LENGTH;
    }

    // Kept as the name the rest of the module calls after clearing or filling.
    function sync() { paint(); }

    function fill(text) {
        if (!state.codeInput) return;
        state.codeInput.value = onlyDigits(text).slice(0, OTP_LENGTH);
        paint();
    }

    function focusCode() {
        if (state.codeInput) state.codeInput.focus();
    }

    function wireDigits() {
        const inp = state.codeInput;
        if (!inp) return;
        inp.addEventListener('input', function () {
            // Normalises Persian digits and strips anything else, so a pasted
            // "کد ورود: ۱۱۱۱۱" still lands as 11111.
            inp.value = onlyDigits(inp.value).slice(0, OTP_LENGTH);
            paint();
            if (inp.value.length === OTP_LENGTH) submitCode();
        });
        inp.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && code().length === OTP_LENGTH) submitCode();
        });
        inp.addEventListener('focus', paint);
        inp.addEventListener('blur', paint);
        // Tapping anywhere on the row puts the caret in the one real field.
        inp.parentNode.addEventListener('click', focusCode);
    }

    // ── WebOTP: read the code straight from the SMS on Android Chrome ──
    function startOtpListener() {
        if (!('OTPCredential' in window) || !navigator.credentials) return;
        abortOtpListener();
        otpAbort = new AbortController();
        navigator.credentials.get({ otp: { transport: ['sms'] }, signal: otpAbort.signal })
            .then(function (cred) {
                if (!cred || !cred.code) return;
                fill(cred.code);
                say(t().autofilled, 'ok');
                if (code().length === OTP_LENGTH) submitCode();
            })
            .catch(function () { /* declined, timed out, or unsupported — typing still works */ });
    }

    function abortOtpListener() {
        if (otpAbort) { try { otpAbort.abort(); } catch (e) { } otpAbort = null; }
    }

    // ── Timer ────────────────────────────────────────────────────────
    function applyTiming(data) {
        expiresAt = Date.now() + (data.expires_in || 0) * 1000;
        resendAt = Date.now() + (data.resend_in || 0) * 1000;
        stopTimer();
        timerId = setInterval(tick, 1000);
        tick();
    }
    function stopTimer() { if (timerId) { clearInterval(timerId); timerId = 0; } }
    function fmt(s) {
        return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(s % 60).padStart(2, '0');
    }
    function tick() {
        const timer = document.getElementById('reg-timer');
        const left = Math.max(0, Math.round((expiresAt - Date.now()) / 1000));
        if (timer) timer.textContent = t().expires + ' ' + fmt(left);
        const rLeft = Math.max(0, Math.round((resendAt - Date.now()) / 1000));
        if (state.resendBtn) {
            state.resendBtn.disabled = rLeft > 0;
            state.resendBtn.textContent = rLeft > 0 ? t().resend + ' (' + fmt(rLeft) + ')' : t().resend;
        }
        if (left === 0 && state.verifyBtn) state.verifyBtn.disabled = true;
    }

    // ── Submit / resend ──────────────────────────────────────────────
    function submitCode() {
        if (!state.challenge || code().length !== OTP_LENGTH) return;
        state.verifyBtn.disabled = true;
        say('…');
        post('/api/auth/otp/verify', { challenge_id: state.challenge, code: code() })
            .then(function (data) {
                stopTimer(); abortOtpListener();
                const profile = data.profile || state.profile || {};
                // The challenge id is deliberately NOT kept. It used to be
                // stored and re-sent as proof of who this was, which made a
                // never-expiring bearer token out of a value that lives in
                // localStorage, where any injected script can read it. The
                // real credential is the HttpOnly cookie this response just
                // set; nothing on this side of the wire touches it.
                rememberName(profile);
                say(data.message || '', 'ok');
                // Re-read from the server rather than assuming the mint
                // worked. If the cookie did not arrive, the gate must know it
                // now, not at the visitor's next message.
                refreshServerSession();
                // Verified. The rest of the conversation belongs in the chat,
                // not in a modal: close the card and let the assistant ask.
                setTimeout(function () {
                    closeModal();
                    startChatQuestions(profile);
                }, 900);
            })
            .catch(function (err) {
                state.verifyBtn.disabled = false;
                say(err.detail || t().network, 'error');
                focusCode(); if (state.codeInput) state.codeInput.select();
            });
    }

    function doResend() {
        if (!state.challenge) return;
        state.resendBtn.disabled = true;
        say('…');
        post('/api/auth/otp/resend', { challenge_id: state.challenge })
            .then(function (data) {
                if (state.codeInput) state.codeInput.value = '';
                paint(); applyTiming(data); say('');
                focusCode();
                startOtpListener();
            })
            .catch(function (err) { say(err.detail || t().network, 'error'); tick(); });
    }

    function post(url, body) {
        return fetch(url, {
            method: 'POST',
            // Spelled out because these endpoints now act on the session
            // cookie: the browser has to be told to send it.
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                if (!r.ok) throw { status: r.status, detail: readable(data.detail) };
                return data;
            });
        });
    }

    /* Every caller does `say(err.detail || t().network, 'error')`, so `detail`
       has to be a STRING. The 401 these endpoints now raise carries an object
       instead — {code, message} — because the code is what the browser acts
       on. Left alone, a visitor would read "[object Object]". The sentence is
       what belongs on screen; the code is for the gate, not for a person. */
    function readable(detail) {
        if (detail && typeof detail === 'object') return detail.message || '';
        return detail;
    }

    // ── In the chat: a welcome, then the questions ───────────────────
    /* Why the questions live here and not on the sign-up card: on a phone a
       <select> is a spinning wheel and eighteen interests are a scroll inside
       a scroll. A chip is one tap. And because a tap WRITES INTO THE MESSAGE
       BOX instead of sending, the visitor can still edit the answer — or type
       something the list never had — before pressing the send button they
       already know.

       The three questions are the same three fields /api/auth/profile has
       always accepted; only the way they are asked has changed. */

    function chatSteps() {
        const steps = [
            /* شغل and سمت take ONE answer each. They are single facts about a
               person, and the endpoint stores each in its own 80-character
               field — a joined list would be truncated mid-word and would make
               the visit planner match on two contradictory jobs. Interests is
               the opposite: a list is the honest answer, and the endpoint
               gives it 400 characters. */
            { key: 'job', list: 'jobs', prompt: t().askJob, multi: false, max: MAX_JOB },
            { key: 'position', list: 'positions', prompt: t().askPosition, multi: false, max: MAX_POSITION }
        ];
        if (ASK_INTERESTS) {
            steps.push({
                key: 'interests', list: 'interests', prompt: t().askInterests,
                multi: true, max: MAX_INTERESTS
            });
        }
        return steps;
    }

    const ask = { steps: [], index: -1, answers: {}, box: null, watcher: null };
    let heldMessage = '';
    // True when the held message is ALREADY a bubble in the transcript, which
    // happens when the server refused it rather than the gate holding it. See
    // serverGate() and deliverHeld().
    let heldEchoed = false;

    function chatInput() { return document.getElementById('user-input'); }

    function botSay(text) {
        if (typeof addMessage === 'function') addMessage(text, 'bot', true, true);
    }

    function visitorSaid(text) {
        if (typeof addMessage === 'function') addMessage(text, 'user');
    }

    function appendToChat(node) {
        const log = document.getElementById('chat-view-content');
        if (!log) return false;
        const loader = document.getElementById('loading-bubble');
        if (loader && loader.parentNode === log) log.insertBefore(node, loader);
        else log.append(node);
        log.scrollTop = log.scrollHeight;
        return true;
    }

    function setInput(value) {
        const input = chatInput();
        if (!input) return;
        input.value = value;
        // The chat engine enables its send button on this event, and a value
        // written by script does not fire one by itself.
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }

    /* Signed in means the SERVER said so on this page load. It used to mean
       "localStorage has a name in it", which anyone could type into a console
       in four seconds. Nothing here reads storage. */
    function isSignedIn() {
        return server.known && server.signed_in;
    }

    function startChatQuestions(profile) {
        if (typeof switchTab === 'function') { try { switchTab('text'); } catch (e) { } }
        const who = (profile && profile.first_name) || displayName(profile || {});
        botSay(t().hello2(who));
        ask.steps = chatSteps();
        ask.answers = {};
        ask.index = -1;
        // The options are already cached from the sign-up card; this only
        // matters when that fetch failed, in which case the question is still
        // asked and simply has no chips to tap.
        loadOptions().then(nextQuestion, nextQuestion);
    }

    function nextQuestion() {
        ask.index += 1;
        const step = ask.steps[ask.index];
        if (!step) { saveChatAnswers(); return; }
        botSay(step.prompt);
        renderChoices(step);
    }

    function sameLabel(a, b) {
        return String(a).trim().toLowerCase() === String(b).trim().toLowerCase();
    }

    function chosenNow() {
        const input = chatInput();
        return splitInterests(input ? input.value : '');
    }

    function renderChoices(step) {
        clearChoices();
        const items = (options && options[step.list]) || [];
        // Deliberately NOT a `.message`: the companion's mini chat mirrors
        // messages as plain text, and a mirrored wall of option labels helps
        // nobody. This block is transient UI, so it is not saved to history
        // either — a reloaded page shows the conversation, not stale buttons.
        const box = el('div', 'reg-ask');

        if (items.length) {
            const list = el('div', 'reg-ask-options');
            items.forEach(function (item) {
                const option = el('button', 'reg-ask-option', item.label);
                option.type = 'button';
                option.addEventListener('click', function () { toggleChoice(step, item.label); });
                list.append(option);
            });
            box.append(list);
        }

        box.append(el('p', 'reg-ask-hint', step.multi ? t().tapMany : t().tapOne));

        // A visitor whose answer is not on the list and who does not want to
        // type one must still be able to reach their own question.
        const skip = el('button', 'reg-ask-skip', t().skip);
        skip.type = 'button';
        skip.addEventListener('click', function () { acceptAnswer(''); });
        box.append(skip);

        if (!appendToChat(box)) return;
        ask.box = box;

        // Typing in the box moves the chips with it: the message box is the
        // answer, the chips are only a fast way to fill it.
        ask.watcher = function () { paintChoices(); };
        const input = chatInput();
        if (input) input.addEventListener('input', ask.watcher);
        paintChoices();
    }

    function toggleChoice(step, labelText) {
        const current = chosenNow();
        let at = -1;
        current.forEach(function (c, i) { if (at === -1 && sameLabel(c, labelText)) at = i; });

        let next;
        if (at !== -1) next = current.filter(function (_, i) { return i !== at; });
        else if (step.multi) next = current.concat([labelText]);
        else next = [labelText];   // one answer replaces the other
        setInput(next.join('، ').slice(0, step.max));
        paintChoices();
    }

    function paintChoices() {
        if (!ask.box) return;
        const current = chosenNow();
        [].slice.call(ask.box.querySelectorAll('.reg-ask-option')).forEach(function (option) {
            const on = current.some(function (c) { return sameLabel(c, option.textContent); });
            option.dataset.on = on ? 'true' : 'false';
            option.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    function clearChoices() {
        const input = chatInput();
        if (ask.watcher && input) input.removeEventListener('input', ask.watcher);
        ask.watcher = null;
        if (ask.box) ask.box.remove();
        ask.box = null;
    }

    function acceptAnswer(text) {
        const step = ask.steps[ask.index];
        if (!step) return;
        const value = String(text || '').trim().slice(0, step.max);
        clearChoices();
        if (value) visitorSaid(value);
        ask.answers[step.key] = value;
        setInput('');
        nextQuestion();
    }

    function saveChatAnswers() {
        const stored = server.profile || {};
        /* The sign-up checkbox is stored in the SAME field as the interests
           (that is the existing schema), so it is merged back in here —
           answering the interests question must never silently untick it. */
        const flags = (state.flags && state.flags.length)
            ? state.flags : splitInterests(stored.interests);
        const all = flags.concat(splitInterests(ask.answers.interests || ''));
        const interests = all.filter(function (v, i) {
            let first = -1;
            all.forEach(function (o, j) { if (first === -1 && sameLabel(o, v)) first = j; });
            return first === i;
        }).join('، ').slice(0, MAX_INTERESTS);

        // Again, no identity in the body — the cookie carries it.
        post('/api/auth/profile', {
            job: ask.answers.job || '',
            position: ask.answers.position || '',
            interests: interests
        })
            .then(function (data) {
                server.profile = Object.assign({}, stored, data.profile || {});
                rememberName(server.profile);
                paintSession();
                botSay(t().profileSaved);
            })
            .catch(function () {
                // The visitor's own question matters more than their profile:
                // a failed save must not swallow the answer they are waiting for.
            })
            .then(deliverHeld);
    }

    /** Send the message that was held back at the start, now that there is
        someone to answer. It goes through the normal path, so it appears in
        the chat and is answered exactly as if it had just been typed. */
    function deliverHeld() {
        if (heldMessage) {
            const text = heldMessage;
            const echoed = heldEchoed;
            heldMessage = '';
            heldEchoed = false;
            setInput(text);
            // A message the SERVER refused is already on screen. Re-sending it
            // must not print it a second time, or the transcript reads as if
            // the visitor asked twice.
            if (typeof sendMessage === 'function') sendMessage(true, { echo: !echoed });
            return;
        }
        // Nobody was waiting on an answer — this visitor came in through the
        // brick, so give them what the brick promises: their visit plan.
        openModal(renderPlanStep);
    }

    /** ChatConfig.sendGateFn — see static/chat/core.js.
        Returns true when this module has taken the message. */
    function gate(text) {
        // An open question owns the message box until it is answered.
        if (ask.steps[ask.index]) { acceptAnswer(text); return true; }
        if (isSignedIn()) return false;
        // Session unknown (the probe failed, or has not answered yet). Do not
        // guess in either direction: send it and let the server rule. A signed
        // in visitor gets their answer, and a stranger gets the 401 that
        // serverGate() below turns into this same card.
        if (!server.known) return false;
        // First message from a stranger: hold it, do not answer it, and ask
        // them to sign up. Nothing they typed is thrown away.
        holdAndAsk(text, false);
        return true;
    }

    /** ChatConfig.signInRequiredFn — see static/chat/core.js.
        The server refused the message: this visitor has no session. The gate
        above normally catches that first, so reaching here means the client
        was wrong — the page loaded before the module knew, the session expired
        mid-conversation, or someone reached /chat with the gate disabled.
        Either way the visitor sees the sign-up card, never a raw error. */
    function serverGate(info) {
        // Whatever this browser believed is stale by definition: the server
        // has just said no. Correcting it here means the NEXT message is held
        // by the gate instead of making the same round trip.
        server = { known: true, signed_in: false, profile: {} };
        forgetName();
        paintSession();
        holdAndAsk((info && info.text) || '', true);
        return true;
    }

    function holdAndAsk(text, alreadyEchoed) {
        heldMessage = text;
        heldEchoed = !!alreadyEchoed;
        setInput('');
        botSay(t().held);
        openModal(renderSignupStep);
    }

    // ── Boot ─────────────────────────────────────────────────────────
    // The old identity blob, wherever it survives. It held a challenge id that
    // used to work as a password, so it is removed on sight rather than left
    // to rot in a booth phone.
    try { localStorage.removeItem(KEY_LEGACY); } catch (e) { }

    // The sign-up gate is installed ONLY once the server confirms the
    // registration module is switched on. With the admin switch off, nothing
    // is installed and the chat behaves exactly as it does on an install that
    // never had the module: the first message is answered, not held.
    //
    // The 401 handler is installed either way. It costs nothing on an install
    // that can never send that status, and it is the safety net for the case
    // the gate cannot cover: registration switched on after this page loaded.
    if (typeof ChatConfig !== 'undefined') ChatConfig.signInRequiredFn = serverGate;

    fetch('/api/auth/registration-status')
        .then(function (r) { return r.ok ? r.json() : { enabled: false }; })
        .then(function (s) {
            if (!s.enabled || typeof ChatConfig === 'undefined') return;
            // Ask WHO before gating on it. Installing the gate first would
            // hold a signed-in visitor's message for the length of one round
            // trip and open a sign-up card they do not need.
            return refreshServerSession().then(function () {
                ChatConfig.sendGateFn = gate;
            });
        })
        .catch(function () { /* status unknown — leave the chat ungated */ });

    // Paint from the remembered name straight away, before any request. The
    // server's answer replaces it moments later.
    paintSession();
})();
