/* ── Companion UI: control rail, drag, and the mini ChatBox ──
   Ported from the Pet-Inotex module (src/ui/AvatarView.ts and
   src/ui/ChatBox.ts) so the companion behaves the way that validated app
   does: eye / − / + controls beside the character, pointer drag, single tap
   to open the chat (and look toward the tap), double tap for one energetic
   reaction, and an expandable chat panel.

   ONE DELIBERATE DIFFERENCE from the standalone module: Pet-Inotex's ChatBox
   answers from its own bundled JSON. Here the panel is a compact VIEW of the
   page's single conversation — it mirrors the main transcript and sends
   through the same /chat pipeline. A second, competing knowledge source on
   the same screen is how a user gets two different answers to one question.

   Preferences (hidden, size, position) persist in localStorage, as in the
   original.
*/
(function () {
    'use strict';

    const pet = window.PetCompanion;
    const slot = document.getElementById('pet-slot');
    const rail = document.getElementById('pet-rail');
    const panel = document.getElementById('pet-panel');
    if (!pet || !slot || !panel) return;

    const KEY_HIDDEN = 'inotex-pet-hidden';
    const KEY_SCALE = 'inotex-pet-scale';
    const KEY_POS = 'inotex-pet-pos';
    const SCALE_MIN = 0.7, SCALE_MAX = 1.8, SCALE_STEP = 0.15;

    const eyeBtn = document.getElementById('pet-eye');
    const smallerBtn = document.getElementById('pet-smaller');
    const largerBtn = document.getElementById('pet-larger');
    const hitBtn = document.getElementById('pet-hit');
    const closeBtn = document.getElementById('pet-close');
    const log = document.getElementById('pet-log');
    const chips = document.getElementById('pet-chips');
    const form = document.getElementById('pet-form');
    const input = document.getElementById('pet-input');
    const status = document.getElementById('pet-status');

    const isFa = function () { return document.documentElement.lang !== 'en'; };
    const T = {
        fa: { show: 'نمایش همراه', hide: 'مخفی‌کردن همراه', open: 'گفت‌وگو با دستیار',
              ready: 'آمادهٔ دریافت پرسش شما', loading: 'در حال یافتن پاسخ…',
              empty: 'لطفاً پرسش خود را بنویسید.', done: 'پاسخ نمایش داده شد.' },
        en: { show: 'Show companion', hide: 'Hide companion', open: 'Chat with the assistant',
              ready: 'Ready for your question', loading: 'Finding an answer…',
              empty: 'Please enter your question.', done: 'Answer displayed.' }
    };
    const t = function () { return isFa() ? T.fa : T.en; };

    // ── Visibility ───────────────────────────────────────────────────
    // `animate` is false when restoring the saved preference on load: an
    // animation on first paint would be noise, not feedback.
    function applyHidden(hidden, animate) {
        localStorage.setItem(KEY_HIDDEN, hidden ? '1' : '0');
        if (eyeBtn) {
            eyeBtn.setAttribute('aria-pressed', hidden ? 'true' : 'false');
            eyeBtn.title = hidden ? t().show : t().hide;
            eyeBtn.setAttribute('aria-label', eyeBtn.title);
        }
        if (hidden) openPanel(false);

        if (!animate || !pet.playTransition) {
            slot.dataset.hidden = hidden ? 'true' : 'false';
            if (!hidden && pet.resume) pet.resume();
            return;
        }

        if (hidden) {
            // Play the fold-into-a-brick strip, THEN mark it hidden, so the
            // character is not yanked off screen before the animation runs.
            pet.playTransition(false, function () { slot.dataset.hidden = 'true'; });
        } else {
            slot.dataset.hidden = 'false';
            pet.playTransition(true, function () { pet.resume(); });
        }
    }

    // ── Size ─────────────────────────────────────────────────────────
    function applyScale(value) {
        const v = Math.min(SCALE_MAX, Math.max(SCALE_MIN, Math.round(value * 100) / 100));
        slot.style.setProperty('--pet-scale', String(v));
        localStorage.setItem(KEY_SCALE, String(v));
        if (largerBtn) largerBtn.disabled = v >= SCALE_MAX;
        if (smallerBtn) smallerBtn.disabled = v <= SCALE_MIN;
        positionPanel();
        return v;
    }
    function currentScale() {
        const v = parseFloat(getComputedStyle(slot).getPropertyValue('--pet-scale'));
        return isNaN(v) ? 1 : v;
    }

    // ── Position (drag) ──────────────────────────────────────────────
    let posX = 0, posY = 0;
    function applyPos() {
        slot.style.setProperty('--pet-dx', posX + 'px');
        slot.style.setProperty('--pet-dy', posY + 'px');
    }
    function clampPos() {
        // Keep the whole character on screen whatever the drag asked for.
        const r = slot.getBoundingClientRect();
        const left = r.left - posX, top = r.top - posY;
        const pad = 4;
        posX = Math.min(Math.max(posX, pad - left), window.innerWidth - pad - left - r.width);
        posY = Math.min(Math.max(posY, pad - top), window.innerHeight - pad - top - r.height);
    }
    function savePos() {
        try { localStorage.setItem(KEY_POS, JSON.stringify({ x: Math.round(posX), y: Math.round(posY) })); }
        catch (e) { /* storage disabled — position simply is not remembered */ }
    }

    // ── Chat panel ───────────────────────────────────────────────────
    function openPanel(open) {
        panel.hidden = !open;
        slot.dataset.open = open ? 'true' : 'false';
        if (hitBtn) hitBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (open) {
            renderChips();
            syncFromMainTranscript();
            positionPanel();
            if (status) status.textContent = t().ready;
            setTimeout(function () { input && input.focus({ preventScroll: true }); }, 60);
        }
    }

    function appendRow(text, role) {
        if (!log) return;
        const li = document.createElement('li');
        li.className = 'pet-msg pet-msg-' + (role === 'user' ? 'user' : 'bot');
        const p = document.createElement('p');
        p.dir = 'auto';
        // textContent, never innerHTML: visitor input can never execute markup.
        p.textContent = String(text).replace(/<[^>]*>/g, '');
        li.appendChild(p);
        log.appendChild(li);
        while (log.childElementCount > 100) log.firstElementChild.remove();
        requestAnimationFrame(function () { log.scrollTop = log.scrollHeight; });
    }

    /** Mirror whatever the main transcript already holds (one conversation). */
    function syncFromMainTranscript() {
        if (!log || log.dataset.synced === '1') return;
        document.querySelectorAll('#chat-view-content .message').forEach(function (m) {
            // The loading bubble is transient UI, not a message — mirroring it
            // would leave a permanent "typing…" row in the panel.
            if (m.id === 'loading-bubble') return;
            const text = (m.textContent || '').trim();
            if (text) appendRow(text, m.classList.contains('user') ? 'user' : 'bot');
        });
        log.dataset.synced = '1';
    }

    /** Park the panel directly above the character, and keep it there while
        the character is dragged — the two are one object to the user. */
    function positionPanel() {
        if (panel.hidden) return;
        const petRect = slot.getBoundingClientRect();
        const r = { left: petRect.left, top: petRect.top };
        const gap = 16;
        const wide = window.innerWidth >= 900;
        if (wide) {
            panel.style.left = Math.round(r.left) + 'px';
            panel.style.right = 'auto';
        }
        const bottom = window.innerHeight - r.top + gap;
        // Never let the panel run off the top of the viewport.
        const maxBottom = window.innerHeight - 12 - panel.offsetHeight;
        panel.style.bottom = Math.min(bottom, Math.max(12, maxBottom)) + 'px';
    }

    function renderChips() {
        if (!chips || typeof getDisplayQuestions !== 'function') return;
        const lang = isFa() ? 'fa' : 'en';
        if (chips.dataset.filled === lang) return;
        Promise.resolve(getDisplayQuestions()).then(function (list) {
            chips.textContent = '';
            (list || []).slice(0, 4).forEach(function (item) {
                const label = (item && (item.question || item.title)) || '';
                if (!label) return;
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'pet-chip';
                b.textContent = label;
                b.addEventListener('click', function () { send(label); });
                chips.appendChild(b);
            });
            chips.dataset.filled = lang;
        }).catch(function () { /* chips are optional */ });
    }

    function send(text) {
        const value = (text || '').trim();
        if (!value) {
            if (status) status.textContent = t().empty;
            input && input.focus();
            return;
        }
        if (status) status.textContent = t().loading;
        // Route through the page's single pipeline; the reply mirrors back
        // here via the transcript observer below.
        if (typeof sendPreset === 'function') sendPreset(value);
        if (input) { input.value = ''; input.style.removeProperty('height'); }
    }

    // Mirror new main-transcript messages into the panel as they arrive.
    const content = document.getElementById('chat-view-content');
    if (content && log) {
        new MutationObserver(function (records) {
            records.forEach(function (r) {
                r.addedNodes.forEach(function (n) {
                    if (n.nodeType !== 1 || !n.classList || !n.classList.contains('message')) return;
                    if (n.id === 'loading-bubble') return;
                    const role = n.classList.contains('user') ? 'user' : 'bot';
                    if (role === 'user') {
                        const text = (n.textContent || '').trim();
                        if (text) appendRow(text, 'user');
                        return;
                    }
                    // The bot bubble is typed out character by character, so
                    // a fixed delay captures a half-written sentence. Wait for
                    // the text to STOP growing instead — that is the only
                    // signal that does not depend on the answer's length.
                    let previous = '';
                    let stableFor = 0;
                    const started = Date.now();
                    const poll = setInterval(function () {
                        const text = (n.textContent || '').trim();
                        stableFor = text === previous ? stableFor + 1 : 0;
                        previous = text;
                        // ~500ms unchanged, or a 30s ceiling for a very long answer.
                        if ((text && stableFor >= 5) || Date.now() - started > 30000) {
                            clearInterval(poll);
                            if (text) {
                                appendRow(text, 'bot');
                                if (status) status.textContent = t().done;
                            }
                        }
                    }, 100);
                });
            });
        }).observe(content, { childList: true });
    }

    // ── Wiring ───────────────────────────────────────────────────────
    if (eyeBtn) eyeBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        applyHidden(slot.dataset.hidden !== 'true', true);
    });
    if (largerBtn) largerBtn.addEventListener('click', function (e) {
        e.stopPropagation(); applyScale(currentScale() + SCALE_STEP);
    });
    if (smallerBtn) smallerBtn.addEventListener('click', function (e) {
        e.stopPropagation(); applyScale(currentScale() - SCALE_STEP);
    });
    if (closeBtn) closeBtn.addEventListener('click', function () { openPanel(false); });
    if (form) form.addEventListener('submit', function (e) {
        e.preventDefault();
        send(input ? input.value : '');
    });
    if (input) {
        input.addEventListener('input', function () {
            pet.set('attentive');
            input.style.height = 'auto';
            input.style.height = Math.min(input.scrollHeight, 104) + 'px';
        });
        input.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' || e.shiftKey || e.isComposing) return;
            e.preventDefault();
            form.requestSubmit();
        });
    }
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !panel.hidden) openPanel(false);
    });

    // Pointer: drag past a 6px deadzone, otherwise it is a tap that opens
    // the chat — the same discrimination AvatarView makes.
    if (hitBtn) {
        let startX = 0, startY = 0, fromX = 0, fromY = 0, moved = false, suppress = 0;

        hitBtn.addEventListener('pointerdown', function (e) {
            if (e.button != null && e.button !== 0) return;
            moved = false;
            startX = e.clientX; startY = e.clientY;
            fromX = posX; fromY = posY;
            slot.dataset.dragging = 'true';
            try { hitBtn.setPointerCapture(e.pointerId); } catch (err) { }
        });
        hitBtn.addEventListener('pointermove', function (e) {
            if (!slot.dataset.dragging) { pet.lookAt(e.clientX); return; }
            const dx = e.clientX - startX, dy = e.clientY - startY;
            if (!moved && Math.hypot(dx, dy) > 6) moved = true;
            if (!moved) return;
            e.preventDefault();
            posX = fromX + dx; posY = fromY + dy;
            clampPos(); applyPos(); positionPanel();
        });
        function endDrag(e) {
            if (!slot.dataset.dragging) return;
            delete slot.dataset.dragging;
            try { hitBtn.releasePointerCapture(e.pointerId); } catch (err) { }
            if (moved) { suppress = performance.now() + 320; savePos(); }
        }
        hitBtn.addEventListener('pointerup', endDrag);
        hitBtn.addEventListener('pointercancel', endDrag);

        hitBtn.addEventListener('click', function (e) {
            if (performance.now() < suppress) { e.preventDefault(); return; }
            pet.lookAt(e.clientX);
            openPanel(panel.hidden);
        });
        hitBtn.addEventListener('dblclick', function () { pet.flap(); });
    }

    // Follow the pointer anywhere on the page, like the original's gaze.
    document.addEventListener('pointermove', function (e) {
        if (slot.dataset.hidden === 'true') return;
        pet.lookAt(e.clientX);
    }, { passive: true });

    window.addEventListener('resize', function () { clampPos(); applyPos(); positionPanel(); });

    // ── Restore preferences ──────────────────────────────────────────
    const savedScale = parseFloat(localStorage.getItem(KEY_SCALE));
    applyScale(isNaN(savedScale) ? 1 : savedScale);
    applyHidden(localStorage.getItem(KEY_HIDDEN) === '1', false);
    try {
        const saved = JSON.parse(localStorage.getItem(KEY_POS) || 'null');
        // A spot chosen on a desktop must not strand the companion off-screen
        // on a phone, so a saved position is only restored on wide viewports.
        if (saved && typeof saved.x === 'number' && window.innerWidth >= 900) {
            posX = saved.x; posY = saved.y || 0;
            clampPos(); applyPos();
        }
    } catch (e) { /* ignore malformed stored position */ }

    openPanel(false);
})();
