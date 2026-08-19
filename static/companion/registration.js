/* ── Visitor registration (Smart Visit) ──
   The yellow LEGO CTA opens a centred modal: profile fields first, then the
   SMS code. On success the visitor's name replaces the CTA label and a
   Logout control appears in the header.

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
*/
(function () {
    'use strict';

    const KEY_SESSION = 'inotex-visitor';
    const OTP_LENGTH = 6;

    const btn = document.getElementById('visit-btn');
    const label = document.getElementById('visit-btn-label');
    if (!btn || !label) return;

    const isFa = function () { return document.documentElement.lang !== 'en'; };
    const T = {
        fa: {
            cta: 'بازدید هوشمند',
            title: 'ثبت‌نام بازدید هوشمند',
            sub: 'برای شروع، مشخصات خود را وارد کنید.',
            first: 'نام', last: 'نام خانوادگی', phone: 'شماره موبایل',
            job: 'شغل / حوزهٔ فعالیت', position: 'سمت',
            interests: 'زمینه‌های مورد علاقه',
            interestsHint: 'مثلاً: هوش مصنوعی، فین‌تک، سلامت دیجیتال',
            targetedNote: 'با تکمیل این بخش، غرفه‌های مرتبط با کار و علاقهٔ شما پیشنهاد می‌شود.',
            optional: '(اختیاری)',
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
            welcome: 'خوش آمدید',
            hello: function (name) { return 'سلام ' + name; },
            logout: 'خروج',
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
            saveEdit: 'ذخیره و به‌روزرسانی پیشنهادها'
        },
        en: {
            cta: 'Smart Visit',
            title: 'Smart Visit registration',
            sub: 'Enter your details to get started.',
            first: 'First name', last: 'Last name', phone: 'Mobile number',
            job: 'Field of work', position: 'Job title',
            interests: 'Topics you care about',
            interestsHint: 'e.g. AI, fintech, digital health',
            targetedNote: 'Fill this in and the assistant suggests the booths that match your work and interests.',
            optional: '(optional)',
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
            welcome: 'Welcome',
            hello: function (name) { return 'Hi ' + name; },
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
            saveEdit: 'Save and update suggestions'
        }
    };
    const t = function () { return isFa() ? T.fa : T.en; };

    // ── Session ──────────────────────────────────────────────────────
    function session() {
        try { return JSON.parse(localStorage.getItem(KEY_SESSION) || 'null'); }
        catch (e) { return null; }
    }
    function saveSession(profile) {
        localStorage.setItem(KEY_SESSION, JSON.stringify(profile));
        paintSession();
    }
    function clearSession() {
        localStorage.removeItem(KEY_SESSION);
        paintSession();
    }

    function displayName(p) {
        return [p.first_name, p.last_name].filter(Boolean).join(' ').trim();
    }

    /* The brick is narrow, so the greeting uses the FIRST name only — a full
       name would be ellipsised into meaninglessness on most registrations. */
    function greeting(p) {
        const first = (p.first_name || '').trim() || displayName(p);
        return t().hello(first);
    }

    function paintSession() {
        const p = session();
        const header = document.querySelector('.header-tools');
        let logout = document.getElementById('visitor-logout');

        if (p && displayName(p)) {
            btn.dataset.state = 'member';
            label.textContent = greeting(p);
            btn.setAttribute('aria-label', t().welcome + ' ' + displayName(p));
            if (!logout && header) {
                logout = document.createElement('button');
                logout.type = 'button';
                logout.id = 'visitor-logout';
                logout.className = 'visitor-logout';
                logout.addEventListener('click', clearSession);
                // Beside the hamburger: leaving is an account action, and the
                // account controls live at that end of the header.
                const a11y = header.querySelector('.accessibility-controls');
                if (a11y) a11y.insertAdjacentElement('afterend', logout);
                else header.append(logout);
            }
            if (logout) {
                logout.textContent = t().logout;
                logout.title = t().logout;
                logout.setAttribute('aria-label', t().logout);
            }
        } else {
            btn.dataset.state = 'guest';
            label.textContent = t().cta;
            btn.setAttribute('aria-label', t().cta);
            if (logout) logout.remove();
        }
    }

    // ── Modal ────────────────────────────────────────────────────────
    let modal = null, state = {};

    function closeModal() {
        if (!modal) return;
        stopTimer();
        abortOtpListener();
        modal.remove();
        modal = null;
        btn.focus();
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
        (step || renderProfileStep)();
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

    // ── Step 1: profile ──────────────────────────────────────────────
    /* `prefill` carries the saved profile when a signed-in visitor edits it;
       the same renderer serves first-time registration and editing so the two
       can never drift apart. */
    function renderProfileStep(prefill) {
        const editing = !!prefill;
        state.body.textContent = '';
        setHead(editing ? t().editTitle : t().title, editing ? t().editSub : t().sub);
        const form = el('form', 'reg-form');
        state.body.append(form);

        const identity = [
            { id: 'reg-first', label: t().first, type: 'text', autocomplete: 'given-name', required: true },
            { id: 'reg-last', label: t().last, type: 'text', autocomplete: 'family-name', required: true },
            { id: 'reg-phone', label: t().phone, type: 'tel', autocomplete: 'tel', dir: 'ltr', required: true }
        ];
        identity.forEach(function (f) {
            const wrap = el('div', 'reg-field');
            const lab = el('label', null, f.label);
            lab.htmlFor = f.id;
            const input = el('input');
            input.id = f.id;
            input.type = f.type;
            input.autocomplete = f.autocomplete;
            input.required = true;
            if (f.dir) { input.dir = f.dir; input.inputMode = 'tel'; input.placeholder = '09xxxxxxxxx'; }
            // Editing keeps identity visible but read-only: the code verified
            // this name and number, so changing them here would be a lie.
            if (editing) {
                input.value = f.id === 'reg-first' ? (prefill.first_name || '')
                    : f.id === 'reg-last' ? (prefill.last_name || '')
                        : (prefill.destination_masked || '');
                input.readOnly = true;
                input.dataset.locked = 'true';
            }
            wrap.append(lab, input);
            form.append(wrap);
        });

        // Job — a fixed list, because a dropdown is faster and keeps the data
        // comparable. "سایر" is always there for anyone it does not fit.
        const jobWrap = el('div', 'reg-field');
        const jobLab = el('label', null, t().job + ' ' + t().optional);
        jobLab.htmlFor = 'reg-job';
        const jobSel = el('select', 'reg-select');
        jobSel.id = 'reg-job';
        form.append(jobWrap);

        // سمت — its own field, because "what you do" and "how senior you are"
        // are different questions and the client asked for both.
        const posWrap = el('div', 'reg-field');
        const posLab = el('label', null, t().position + ' ' + t().optional);
        posLab.htmlFor = 'reg-position';
        form.append(posWrap);

        const interestWrap = el('div', 'reg-field');
        const interestLab = el('label', null, t().interests + ' ' + t().optional);
        interestWrap.append(interestLab);
        form.append(interestWrap);

        const flagsWrap = el('div', 'reg-flags');
        form.append(flagsWrap);

        form.append(el('p', 'reg-note', t().targetedNote));
        const submit = el('button', 'reg-submit', editing ? t().saveEdit : t().submit);
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

        function selectField(id, items, saved) {
            const sel = el('select', 'reg-select');
            sel.id = id;
            fillSelect(sel, items, saved);
            return sel;
        }

        function textField(id, saved) {
            const input = el('input');
            input.id = id;
            input.type = 'text';
            input.autocomplete = 'organization-title';
            input.value = saved || '';
            return input;
        }

        loadOptions().then(function (o) {
            fillSelect(jobSel, o.jobs, editing ? prefill.job : '');
            jobWrap.append(jobLab, jobSel);

            // A taxonomy that lists positions gets a dropdown; one that does not
            // gets a plain text box, so the field never degrades into an empty
            // select the visitor cannot answer.
            posField = (o.positions && o.positions.length)
                ? selectField('reg-position', o.positions, editing ? prefill.position : '')
                : textField('reg-position', editing ? prefill.position : '');
            posWrap.append(posLab, posField);

            // Flags are stored in the same field as the interests, so on the way
            // back in they must go to the checkbox that owns them — not also
            // become a chip, which would save them twice.
            const flagLabels = (o.flags || []).map(function (f) { return f.label; });
            const saved = editing ? splitInterests(prefill.interests) : [];
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

        function positionValue() {
            return posField ? posField.value.trim() : '';
        }

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

            if (editing) {
                submit.disabled = true;
                say('…');
                post('/api/auth/profile', {
                    challenge_id: state.challenge || (session() || {}).challenge_id || '',
                    job: jobSel.value.trim(), position: positionValue(), interests: collect()
                })
                    .then(function (data) {
                        const merged = Object.assign({}, session(), data.profile || {});
                        saveSession(merged);
                        renderPlanStep();
                    })
                    .catch(function (err) {
                        submit.disabled = false;
                        say(err.detail || t().network, 'error');
                    });
                return;
            }

            const first = form.querySelector('#reg-first').value.trim();
            const last = form.querySelector('#reg-last').value.trim();
            const phone = form.querySelector('#reg-phone').value.trim();
            if (!first || !last || !phone) { say(t().required, 'error'); return; }

            submit.disabled = true;
            say('…');
            const job = jobSel.value.trim();
            const position = positionValue();
            const interests = collect();

            post('/api/auth/otp/request', {
                destination: phone, first_name: first, last_name: last,
                job: job, position: position, interests: interests
            })
                .then(function (data) {
                    state.profile = { first_name: first, last_name: last };
                    renderCodeStep(data);
                })
                .catch(function (err) {
                    submit.disabled = false;
                    say(err.detail || t().network, 'error');
                });
        });

        setTimeout(function () {
            const target = editing ? form.querySelector('#reg-job') : form.querySelector('#reg-first');
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

        const p = session() || {};
        post('/api/visit-plan', {
            challenge_id: state.challenge || p.challenge_id || '',
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
                edit.addEventListener('click', function () { renderProfileStep(session() || {}); });
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
        change.addEventListener('click', function () { stopTimer(); abortOtpListener(); renderProfileStep(); });
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
                // Kept so the visitor can edit their profile later without a new
                // code. It is an unguessable id that only unlocks their own
                // descriptive fields — when this app grows a real session layer,
                // that becomes the right home for it.
                profile.challenge_id = state.challenge;
                saveSession(profile);
                say(data.message || '', 'ok');
                // A visitor who described their work has earned a plan; one who
                // skipped those fields just gets their name on the brick.
                const targeted = profile.job || profile.position || profile.interests;
                setTimeout(targeted ? renderPlanStep : closeModal, 900);
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
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (data) {
                if (!r.ok) throw { status: r.status, detail: data.detail };
                return data;
            });
        });
    }

    // ── Boot ─────────────────────────────────────────────────────────
    // The CTA starts hidden and only appears once the server confirms the
    // registration module is switched on — an operator turning it off in the
    // panel takes the button away rather than leaving a control that fails.
    btn.hidden = true;
    fetch('/api/auth/registration-status')
        .then(function (r) { return r.ok ? r.json() : { enabled: false }; })
        .then(function (s) { btn.hidden = !s.enabled; })
        .catch(function () { btn.hidden = true; });

    btn.addEventListener('click', function () {
        // Signed in already: the brick shows the plan instead of restarting
        // registration — Logout in the header is the way out.
        if (session() && displayName(session())) { openModal(renderPlanStep); return; }
        openModal();
    });
    paintSession();
})();
