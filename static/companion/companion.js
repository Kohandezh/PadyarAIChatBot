/* ── Pet-INOTEX companion (shared renderer) ──
   Used by both the chat UI (theme footer) and the registration/verification
   page. Configuration comes from data-* attributes on the canvas, so the two
   surfaces can size and place it differently without forking this file.

   MOTION MODEL — ported from the Pet-Inotex engine
   (Pet-Inotex/src/avatar/CanvasAvatarRenderer.ts #calculateMotion and
   AvatarController.ts). The original is a state machine where most of the
   visible life comes from per-STATE gestures (a jump on success, a shake on
   error, a hover while attentive) layered over a quiet ambient breath — not
   from the breath alone.

   One deliberate deviation: the original's amplitudes are absolute pixels
   authored for a large, hero-sized avatar (e.g. translateY -18px on success).
   Here the companion renders at 96–190px in a page corner, so every amplitude
   is expressed as a FRACTION OF THE SPRITE SIZE. Copying the raw pixel values
   at this scale is what made the first version look frozen: a 2.4px bob on a
   132px sprite is below the threshold of noticing.

   Also ported: the controller's idle behaviour — periodic spontaneous
   gestures, and a slide into sleep after a long idle — which is what stops
   the character from reading as a static image between messages.
*/
(function () {
    'use strict';

    // Atlas comes from the canvas's data-atlas (or the page-level config a
    // host injects). A host with no character of its own ships no atlas and
    // gets no companion — neither surface depends on it.
    const canvasEl = document.getElementById('pet-canvas');
    const CFG = window.OTP_CONFIG || {};
    const ATLAS_URL = (canvasEl && canvasEl.dataset.atlas) || CFG.companionAtlas || '';
    const CELL = Number((canvasEl && canvasEl.dataset.cell) || CFG.companionCell || 512);
    const COLS = 4;

    const POSE = {
        'idle-neutral': 0, 'idle-smile': 1, 'welcome-wave': 2, 'attentive-hands': 3,
        'thinking': 4, 'not-found': 5, 'success': 6, 'sleep': 7,
        'tablet-work': 8, 'typing': 9, 'walk': 10, 'run': 11
    };

    // Public state → pose + how long the gesture that accompanies it runs.
    const STATE_POSE = {
        greet: 'welcome-wave',
        idle: 'idle-neutral',
        attentive: 'attentive-hands',
        typing: 'typing',
        ready: 'idle-smile',
        working: 'thinking',
        success: 'success',
        error: 'not-found',
        sleep: 'sleep',
        // AvatarController's guarded transient reaction — an energetic
        // walk/run burst, triggered by a double tap on the character.
        flap: 'run'
    };

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
    const canvas = canvasEl;
    // No canvas or no configured atlas → expose a COMPLETE no-op so every
    // caller stays unconditional and the page works identically without a
    // companion. This is the live path right now: the character's markup is
    // commented out in the theme footers and templates/otp/verify.html (search
    // COMPANION-OFF), so there is no #pet-canvas to find. Every method the
    // real object publishes is stubbed — a partial stub would turn a missing
    // character into a TypeError the first time anything called lookAt().
    const NOOP = function () { };
    if (!canvas || !ATLAS_URL) {
        window.PetCompanion = {
            set: NOOP, lookAt: NOOP, flap: NOOP, resume: NOOP,
            playTransition: function (reverse, done) { if (done) done(); },
            getState: function () { return 'off'; }
        };
        return;
    }
    const ctx = canvas.getContext('2d');

    let atlas = null;
    // Starts in 'boot' (like the original controller) rather than 'greet', so
    // the first set('greet') is a real transition and its settle-to-idle timer
    // actually arms — otherwise the character stays waving forever and the
    // idle gesture queue never starts.
    let state = 'boot';
    let pose = STATE_POSE.greet;
    let prevPose = null;
    let fadeStart = 0;
    let stateStart = 0;       // when the current state began (gesture clock)
    let raf = 0;
    let idleTimer = 0;
    let lastInteraction = performance.now();

    const FADE_MS = 260;
    const SLEEP_AFTER_MS = 45000;   // AvatarController: inactivityMs
    const GESTURE_MS = {            // AvatarController durations
        greet: 1650, success: 1100, error: 1300, attentive: 900, working: 1200,
        flap: 1350
    };

    // Pointer gaze, as in PointerGestureCoordinator/lookAt: -1..1 horizontal
    // offset of the pointer relative to the character, smoothed per frame.
    let gazeX = 0;
    let gazeTarget = 0;

    function easeOutCubic(v) { return 1 - Math.pow(1 - v, 3); }
    function easeInOutCubic(v) {
        return v < 0.5 ? 4 * v * v * v : 1 - Math.pow(-2 * v + 2, 3) / 2;
    }

    function size() {
        const dpr = Math.max(1, Math.min(3, window.devicePixelRatio || 1));
        const rect = canvas.getBoundingClientRect();
        const px = Math.max(64, Math.round(rect.width * dpr));
        if (canvas.width !== px) { canvas.width = px; canvas.height = px; }
    }

    /** Per-state motion, all amplitudes relative to sprite size `s`. */
    function motionFor(now, s) {
        const t = now / 1000;
        const elapsed = (now - stateStart) / 1000;
        const m = { dx: 0, dy: 0, rot: 0, sx: 1, sy: 1 };
        if (reduced.matches) return m;

        // Ambient breath + two slow waves on incommensurable periods, so the
        // idle never resolves into a loop the eye can catch.
        const breath = Math.sin(t * (Math.PI * 2) / (state === 'sleep' ? 5.2 : 3.4));
        m.sx = 1 - 0.010 * breath;
        m.sy = 1 + 0.026 * breath;
        // Amplitudes tuned by measuring the rendered sprite's centroid travel
        // in the browser, not by eye: below ~3% of sprite height the motion is
        // present in the pixels but not perceptible at corner size.
        m.dy = Math.sin(t * 1.35) * 0.035 * s;          // ambient bob
        m.rot = Math.sin(t * 0.58) * 0.022;
        m.dx = Math.sin(t * (Math.PI * 2) / 9.1) * 0.022 * s;

        if (state === 'greet') {
            const p = Math.min(1, elapsed / (GESTURE_MS.greet / 1000));
            const eased = easeOutCubic(p);
            const gesture = Math.sin(p * Math.PI);
            const entrance = 0.9 + eased * 0.1;
            m.sx *= entrance * (1 + gesture * 0.05);
            m.sy *= entrance;
            m.rot += (1 - eased) * -0.06 + gesture * 0.03;
            m.dy -= gesture * 0.05 * s;                  // lifts while waving
        } else if (state === 'attentive') {
            m.dy += -0.035 * s + Math.sin(t * 2.1) * 0.012 * s;
            m.rot += 0.02;
        } else if (state === 'working') {
            // Slow ponder: rises and settles, head tipped.
            const ponder = Math.sin(t * 1.6);
            m.dy += -0.02 * s + ponder * 0.022 * s;
            m.rot += ponder * 0.03;
        } else if (state === 'typing') {
            const tap = Math.sin(t * 9.5);
            m.dy += tap * 0.008 * s;
            m.rot += tap * 0.008;
        } else if (state === 'success') {
            // A real jump: the single most visible gesture in the original.
            const p = Math.min(1, elapsed / (GESTURE_MS.success / 1000));
            const gesture = Math.sin(p * Math.PI);
            m.dy -= gesture * 0.11 * s;
            m.sx *= 1 + gesture * 0.05;
            m.sy *= 1 + gesture * 0.035;
            m.rot += Math.sin(p * Math.PI * 2) * 0.04;
        } else if (state === 'error') {
            // Confused tilt, then a quick decaying shake.
            const p = Math.min(1, elapsed / (GESTURE_MS.error / 1000));
            const decay = 1 - p;
            m.rot += Math.sin(elapsed * 22) * 0.045 * decay + p * 0.03;
            m.dx += Math.sin(elapsed * 22) * 0.02 * s * decay;
        } else if (state === 'sleep') {
            m.dy += 0.02 * s + Math.sin(t * 1.1) * 0.01 * s;
            m.rot = -0.03 + Math.sin(t * 0.7) * 0.008;
        } else if (state === 'flap') {
            // Energetic burst: a hop with a wide swing, then it settles.
            const p = Math.min(1, elapsed / (GESTURE_MS.flap / 1000));
            const lift = Math.sin(p * Math.PI);
            m.dy -= lift * 0.16 * s;
            m.rot += Math.sin(p * Math.PI * 4) * 0.05;
            m.sx *= 1 + lift * 0.04;
        }

        // Gaze lean — the character turns toward the pointer (lookAt).
        if (state !== 'sleep') {
            m.rot += gazeX * 0.05;
            m.dx += gazeX * 0.03 * s;
        }
        return m;
    }

    function drawPose(name, alpha, m, extraScale) {
        const idx = POSE[name];
        if (idx === undefined || !atlas) return;
        const sx = (idx % COLS) * CELL;
        const sy = Math.floor(idx / COLS) * CELL;
        const s = canvas.width;
        const k = extraScale || 1;
        ctx.save();
        ctx.globalAlpha = alpha;
        // Transform origin at the feet: breathing, bobbing and sway pivot from
        // the ground like a standing figure, not from the sprite's middle.
        ctx.translate(s / 2 + m.dx, s + m.dy);
        ctx.rotate(m.rot);
        ctx.scale(m.sx * k, m.sy * k);
        ctx.drawImage(atlas, sx, sy, CELL, CELL, -s / 2, -s, s, s);
        ctx.restore();
    }

    function frame(now) {
        if (!atlas) return;
        const s = canvas.width;
        ctx.clearRect(0, 0, s, s);

        // Ease the gaze toward its target instead of snapping (the original
        // blends over ~95ms).
        gazeX += (gazeTarget - gazeX) * 0.12;

        // Long quiet → the character dozes off, exactly like the original
        // controller's inactivity timer. Any set() call wakes it.
        if (state === 'idle' && now - lastInteraction > SLEEP_AFTER_MS) {
            transition('sleep');
        }

        const m = motionFor(now, s);

        if (prevPose && fadeStart) {
            const p = Math.min(1, (now - fadeStart) / FADE_MS);
            const e = easeInOutCubic(p);
            drawPose(prevPose, 1 - e, m, 1);
            drawPose(pose, e, m, 0.985 + 0.015 * e);
            if (p >= 1) { prevPose = null; fadeStart = 0; }
        } else {
            drawPose(pose, 1, m, 1);
        }

        raf = requestAnimationFrame(frame);
    }

    function drawStatic() {
        if (!atlas) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        drawPose(pose, 1, { dx: 0, dy: 0, rot: 0, sx: 1, sy: 1 }, 1);
    }

    function transition(next) {
        const nextPose = STATE_POSE[next] || 'idle-neutral';
        state = next;
        stateStart = performance.now();
        if (nextPose === pose) return;
        prevPose = reduced.matches ? null : pose;
        pose = nextPose;
        fadeStart = performance.now();
        if (reduced.matches) drawStatic();
    }

    // Spontaneous idle life. The original controller fires "flap"/"curious"
    // gestures on its own; without them the character reads as a still image
    // between messages, which was the whole complaint.
    const IDLE_GESTURES = [
        { pose: 'idle-smile', hold: 2600, weight: 3 },
        { pose: 'welcome-wave', hold: 1500, weight: 2 },
        { pose: 'attentive-hands', hold: 2200, weight: 2 },
        { pose: 'tablet-work', hold: 3000, weight: 1 },
        { pose: 'thinking', hold: 2000, weight: 1 }
    ];

    function scheduleIdleGesture() {
        clearTimeout(idleTimer);
        if (reduced.matches) return;
        const wait = 6000 + Math.random() * 7000;
        idleTimer = setTimeout(function () {
            if (state === 'idle') {
                let total = 0;
                IDLE_GESTURES.forEach(function (g) { total += g.weight; });
                let r = Math.random() * total;
                let pick = IDLE_GESTURES[0];
                for (const g of IDLE_GESTURES) { r -= g.weight; if (r <= 0) { pick = g; break; } }
                prevPose = pose;
                pose = pick.pose;
                fadeStart = performance.now();
                setTimeout(function () {
                    if (state === 'idle') {
                        prevPose = pose;
                        pose = STATE_POSE.idle;
                        fadeStart = performance.now();
                    }
                }, pick.hold);
            }
            scheduleIdleGesture();
        }, wait);
    }

    function set(nextState) {
        if (!STATE_POSE[nextState]) nextState = 'idle';
        lastInteraction = performance.now();
        if (nextState === state) return;
        transition(nextState);
        // Transient reactions settle back to idle on their own.
        if (nextState === 'success' || nextState === 'greet' || nextState === 'error') {
            clearTimeout(idleTimer);
            const hold = nextState === 'greet' ? 1800 : 2400;
            idleTimer = setTimeout(function () {
                if (state === nextState) { transition('idle'); scheduleIdleGesture(); }
            }, hold);
        } else if (nextState === 'idle') {
            scheduleIdleGesture();
        }
    }

    function start() {
        size();
        stateStart = performance.now();
        if (reduced.matches) { drawStatic(); return; }
        if (!raf) raf = requestAnimationFrame(frame);
        set('greet');
    }

    function stop() {
        if (raf) { cancelAnimationFrame(raf); raf = 0; }
    }

    const img = new Image();
    img.decoding = 'async';
    img.onload = function () { atlas = img; canvas.classList.add('is-ready'); start(); };
    img.onerror = function () {
        // Atlas unavailable → fall back to the still image rather than an
        // empty box, mirroring AvatarView's fallback path. The companion is
        // decorative, so a still frame loses nothing functionally.
        console.error('Pet-INOTEX atlas failed to load; using still fallback');
        const fallback = canvas.dataset.fallback;
        if (!fallback) return;
        const still = new Image();
        still.className = 'pet-fallback';
        still.alt = '';
        still.setAttribute('aria-hidden', 'true');
        still.src = fallback;
        canvas.replaceWith(still);
    };
    img.src = ATLAS_URL;

    // Pause when hidden; honour runtime reduced-motion changes.
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'hidden') stop();
        else if (!reduced.matches && atlas && !raf) {
            lastInteraction = performance.now();
            raf = requestAnimationFrame(frame);
        }
    });
    reduced.addEventListener('change', function () {
        stop();
        if (reduced.matches) { clearTimeout(idleTimer); drawStatic(); }
        else if (atlas) { raf = requestAnimationFrame(frame); scheduleIdleGesture(); }
    });
    window.addEventListener('resize', function () { size(); if (reduced.matches) drawStatic(); });

    /** Look toward a viewport point (AvatarController.lookAt). */
    function lookAt(clientX) {
        const r = canvas.getBoundingClientRect();
        const cx = r.left + r.width / 2;
        gazeTarget = Math.max(-1, Math.min(1, (clientX - cx) / 320));
        lastInteraction = performance.now();
        if (state === 'sleep') set('idle');
    }

    /* ── Hide / unhide transition ──────────────────────────────────────
       A dedicated strip (3 frames, same cell size) shows the character
       folding down into a single brick. Played forward it hides; played in
       reverse it brings the character back, so one strip covers both
       directions. The strip is optional: with no strip configured — or under
       prefers-reduced-motion — the caller's callback fires immediately and
       the companion simply appears/disappears, which is what a reduced-motion
       visitor should get anyway.
    */
    const HIDE_URL = (canvasEl && canvasEl.dataset.hideStrip) || '';
    const HIDE_FRAMES = 3;
    const HIDE_FRAME_MS = 150;
    let hideStrip = null;
    if (HIDE_URL) {
        const hs = new Image();
        hs.decoding = 'async';
        hs.onload = function () { hideStrip = hs; };
        hs.src = HIDE_URL;
    }

    let transitionRaf = 0;

    function drawStripFrame(index, m) {
        const s = canvas.width;
        const sx = index * CELL;
        ctx.clearRect(0, 0, s, s);
        ctx.save();
        ctx.translate(s / 2 + m.dx, s + m.dy);
        ctx.scale(m.sx, m.sy);
        ctx.drawImage(hideStrip, sx, 0, CELL, CELL, -s / 2, -s, s, s);
        ctx.restore();
    }

    /** Play the strip; `reverse` brings the character back. */
    function playTransition(reverse, done) {
        if (!hideStrip || reduced.matches) { if (done) done(); return; }
        stop();                                   // the idle loop yields
        if (transitionRaf) cancelAnimationFrame(transitionRaf);
        const started = performance.now();
        const total = HIDE_FRAMES * HIDE_FRAME_MS;

        (function step(now) {
            const p = Math.min(1, (now - started) / total);
            const k = reverse ? 1 - p : p;
            const index = Math.min(HIDE_FRAMES - 1, Math.floor(k * HIDE_FRAMES));
            // A small squash on the way down / stretch on the way back reads
            // as weight rather than a slideshow of three stills.
            const squash = reverse ? 0.94 + 0.06 * p : 1 - 0.06 * p;
            drawStripFrame(index, { dx: 0, dy: 0, sx: 1 / squash, sy: squash });
            if (p < 1) {
                transitionRaf = requestAnimationFrame(step);
            } else {
                transitionRaf = 0;
                if (done) done();
            }
        })(performance.now());
    }

    /** Resume the normal idle loop after an unhide. */
    function resume() {
        lastInteraction = performance.now();
        if (reduced.matches) { drawStatic(); return; }
        if (!raf) raf = requestAnimationFrame(frame);
        set('greet');
    }

    /** One guarded energetic reaction (AvatarController.triggerFlap). */
    function flap() {
        if (state === 'flap') return;
        set('flap');
        clearTimeout(idleTimer);
        idleTimer = setTimeout(function () {
            if (state === 'flap') { transition('idle'); scheduleIdleGesture(); }
        }, GESTURE_MS.flap);
    }

    window.PetCompanion = {
        set: set,
        lookAt: lookAt,
        flap: flap,
        playTransition: playTransition,
        resume: resume,
        getState: function () { return state; }
    };
})();
