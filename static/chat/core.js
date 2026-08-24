/* ── INOTEX Chat Core ──
   Text-first chat functionality loaded by all themes.
   Themes override theme-specific behavior via ChatConfig before calling initChat().

   Design rules:
   - Video/avatar is OPTIONAL. The active (liquid-glass) theme is text-only, so
     every video reference is null-guarded. A future theme/module may render an
     #avatar-video element and the guards will simply activate.
   - All public-facing copy is bilingual (fa/en) via I18N / setLang().
   - No remote legacy media is referenced anywhere.
*/


// ── Theme Configuration ───────────────────────────────────────────────
// Themes set these before calling initChat() to override behavior.
const ChatConfig = {
    addMessageFn: null,              // Override: function(content, type, save, instant)
    switchTabFn: null,               // Override: function(tabName)
    playVideoTransitionFn: null,     // Override: function(videoUrl, muted)
    isTextOnly: false,               // Video and chat are both available in the public UI.
    // Optional module hook: function(text) -> true when the module has taken
    // this message and the chat engine must NOT send it to /chat. The
    // registration module uses it to hold the first message until the visitor
    // has signed up, and to read the answers to its in-chat questions. Left
    // null on an install without such a module, so nothing changes there.
    sendGateFn: null,
};


// ── Constants ─────────────────────────────────────────────────────────
const DATASET_URL = "/api/dataset";
const QUESTIONS_URL = "/api/questions";
const CHAT_HISTORY_KEY = 'inotex_chat_history';
const LANG_KEY = 'inotex_lang';
const QUESTIONS_PER_PAGE = 5;

// English suggested questions (map to the seeded INOTEX dataset IDs). The
// Persian suggestions come from the dataset titles via /api/dataset.
const EN_SUGGESTED = [
    { question: "What is INOTEX?", dataset_id: "inotex-overview" },
    { question: "When is INOTEX 2026?", dataset_id: "inotex-date" },
    { question: "Where is INOTEX held?", dataset_id: "inotex-venue" },
    { question: "Visiting hours", dataset_id: "inotex-hours" },
    { question: "Booth reservation", dataset_id: "inotex-booth" },
    { question: "Programs & events", dataset_id: "inotex-programs" },
    { question: "INOTEX Pitch", dataset_id: "inotex-pitch" },
    { question: "Contact the secretariat", dataset_id: "inotex-contact" },
    { question: "How big is INOTEX?", dataset_id: "inotex-stats" },
    { question: "Latest announcements", dataset_id: "inotex-news" },
];


// ── Bilingual UI strings ──────────────────────────────────────────────
const I18N = {
    fa: {
        html_lang: 'fa', html_dir: 'rtl',
        app_title: "دستیار پادیار",
        welcome: "سلام! من دستیار پادیار هستم. درباره نمایشگاه اینوتکس هر سوالی دارید بپرسید.",
        videoTab: "ویدیو",
        textTab: "چت",
        videoReady: "ویدیوهای راهنمای اینوتکس در این بخش نمایش داده می‌شوند",
        startVideo: "شروع",
        videoReadyHint: "برای گفتگو یا دریافت راهنمایی، تب چت را انتخاب کنید.",
        placeholder: "سوال خود را بنویسید...",
        sendTitle: "ارسال پیام",
        stopTitle: "توقف",
        micTitle: "تایپ صوتی",
        rateLimit: "لطفاً کمی صبر کنید و دوباره تلاش کنید.",
        refresh: "لطفاً صفحه را رفرش کنید و دوباره تلاش کنید.",
        aiUnavailable: "متأسفانه در حال حاضر سرویس هوش مصنوعی پاسخگو نیست. لطفاً سؤال خود را دوباره مطرح کنید یا از سؤالات پیشنهادی استفاده کنید.",
        genericError: "پاسخگویی هوشمند فعلاً در دسترس نیست. می‌توانید از سوالات پیشنهادی انتخاب کنید:",
        generating: "در حال نوشتن پاسخ",
        askAi: "از دستیار بپرس",
        noQuestions: "سوالی یافت نشد.",
        questionsTitle: "سوالات پرتکرار:",
        showMore: function (n) { return 'نمایش بیشتر (' + n + ' سوال دیگر)'; },
        langLabel: 'EN',       // button shows the language you can switch TO
        langTitle: 'Switch to English',
        a11yTitle: "تنظیمات دسترسی‌پذیری",
        fontInc: "افزایش سایز متن",
        fontDec: "کاهش سایز متن",
    },
    en: {
        html_lang: 'en', html_dir: 'ltr',
        app_title: "Padyar Assistant",
        welcome: "Hello! I'm the Padyar assistant. Ask me anything about the INOTEX exhibition.",
        videoTab: "Video",
        textTab: "Chat",
        videoReady: "INOTEX guide videos will appear here",
        startVideo: "Start",
        videoReadyHint: "Choose the Chat tab to start a conversation or get help.",
        placeholder: "Type your question...",
        sendTitle: "Send message",
        stopTitle: "Stop",
        micTitle: "Voice input",
        rateLimit: "Please wait a moment and try again.",
        refresh: "Please refresh the page and try again.",
        aiUnavailable: "The AI service is currently unavailable. Please try again or pick a suggested question.",
        genericError: "The assistant is temporarily unavailable. You can pick a suggested question:",
        generating: "Generating",
        askAi: "Ask AI",
        noQuestions: "No questions found.",
        questionsTitle: "Frequently asked questions:",
        showMore: function (n) { return 'Show more (' + n + ' more)'; },
        langLabel: 'فا',
        langTitle: 'تغییر به فارسی',
        a11yTitle: "Accessibility settings",
        fontInc: "Increase font size",
        fontDec: "Decrease font size",
    },
};

let currentLang = 'fa';
function t() { return I18N[currentLang] || I18N.fa; }


// ── DOM References (resolved in initChat) ──────────────────────────────
let chatContent, userInput, sendBtn, micBtn, loadingBubble, avatarVideo, welcomeEl, langBtn;


// ── State ──────────────────────────────────────────────────────────────
let isResponsePlaying = false;
let isRecording = false;
let mediaRecorder = null;
let audioChunks = [];

let questionsData = [];
let questionsLoaded = false;
let displayQuestions = [];
let currentFontSize = 100;


// ── Language ───────────────────────────────────────────────────────────

function setLang(lang) {
    if (!I18N[lang]) lang = 'fa';
    currentLang = lang;
    localStorage.setItem(LANG_KEY, lang);
    const s = t();
    const html = document.documentElement;
    html.setAttribute('lang', s.html_lang);
    // The layout never mirrors: switching language changes the words, not the
    // room. Bubbles and inputs carry dir="auto" so English text still reads
    // left-to-right inside the fixed frame.
    html.setAttribute('dir', 'rtl');

    if (userInput) userInput.setAttribute('placeholder', s.placeholder);
    if (sendBtn) sendBtn.title = s.sendTitle;
    if (micBtn) micBtn.title = s.micTitle;
    if (langBtn) {
        langBtn.textContent = s.langLabel;
        langBtn.title = s.langTitle;
        langBtn.setAttribute('aria-label', s.langTitle);
    }
    // Welcome message
    if (welcomeEl) welcomeEl.textContent = s.welcome;
    // Rebuild suggested questions in the active language (if previously shown)
    rebuildQuestionsIfVisible();
    // Re-localize any visible static error affordances (data-i18n)
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (s[key]) el.textContent = s[key];
    });
    // Tooltips / aria-labels on static controls
    document.querySelectorAll('[data-i18n-title]').forEach(el => {
        const key = el.getAttribute('data-i18n-title');
        if (s[key]) { el.setAttribute('title', s[key]); el.setAttribute('aria-label', s[key]); }
    });
}


// ── Data Loading ───────────────────────────────────────────────────────
const questionsPromise = fetch(QUESTIONS_URL)
    .then(res => res.json())
    .then(data => { questionsData = data; questionsLoaded = true; return data; })
    .catch(err => console.error("Failed to load questions:", err));

const displayQuestionsPromise = fetch(DATASET_URL)
    .then(res => res.json())
    .then(data => {
        displayQuestions = data.map(item => ({
            question: item.title,
            question_en: item.title_en || item.title,
            video_url: item.video_url,
            dataset_id: item.id
        }));
    })
    .catch(err => console.error("Failed to load display questions:", err));

const datasetPromise = fetch(DATASET_URL)
    .then(response => {
        if (!response.ok) throw new Error("Failed to load dataset");
        return response.json();
    })
    .catch(error => { console.error("Failed to fetch dataset:", error); return []; });


// ── Utility Functions ──────────────────────────────────────────────────

function escapeHtml(text) {
    if (!text) return text;
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

function saveToHistory(text, type) {
    const history = JSON.parse(localStorage.getItem(CHAT_HISTORY_KEY) || '[]');
    history.push({ text, type, timestamp: Date.now() });
    localStorage.setItem(CHAT_HISTORY_KEY, JSON.stringify(history));
}

function loadHistory() {
    const history = JSON.parse(localStorage.getItem(CHAT_HISTORY_KEY) || '[]');
    history.forEach(msg => {
        addMessage(msg.text, msg.type, false, true);
    });
}


// ── Tab Logic ──────────────────────────────────────────────────────────

function switchTab(tabName) {
    if (ChatConfig.switchTabFn) return ChatConfig.switchTabFn(tabName);
    switchTabInternal(tabName);
}

function switchTabInternal(tabName) {
    const view = document.getElementById(`${tabName}-view`);
    if (!view) {
        // No such view (e.g. no video-view in a text-only theme) — fall back to text.
        const textView = document.getElementById('text-view');
        if (textView) textView.classList.add('active');
        document.body.classList.remove('video-mode');
        return;
    }
    document.querySelectorAll('.tab-view').forEach(v => v.classList.remove('active'));
    view.classList.add('active');

    document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
    const tabBtn = document.getElementById(`tab-${tabName}-btn`);
    if (tabBtn) tabBtn.classList.add('active');

    if (tabName === 'video' && ChatConfig.isTextOnly === false) {
        document.body.classList.add('video-mode');
    } else {
        document.body.classList.remove('video-mode');
    }

    if (tabName === 'text' && chatContent) {
        chatContent.scrollTop = chatContent.scrollHeight;
    }
}


// ── Message Logic ──────────────────────────────────────────────────────

/* The one place an answer becomes HTML.
   An answer's text is no longer written only by the admin. Since the
   lead-capture flow an exhibitor contact can propose it, and the reviewer
   approves it from a screen that escapes it correctly — so `<img src=x
   onerror=...>` looks like ordinary prose during review and then runs in every
   visitor's session, on the same origin as /verify and the admin panel.
   marked builds the HTML and strips nothing, so DOMPurify has to run on the
   result. It keeps headings, lists, bold, code and links, which is everything
   the answers actually use.
   Every theme routes its bubble through here rather than calling marked
   itself, because one theme that forgets the sanitiser reopens the hole for
   everyone on that install. */
function renderMarkdown(element, text) {
    // Fail closed. No parser or no sanitiser means plain text, never raw HTML.
    if (typeof marked === 'undefined' || typeof DOMPurify === 'undefined') {
        return renderPlainText(element, text);
    }
    element.innerHTML = DOMPurify.sanitize(marked.parse(text), {
        /* An answer is prose. It has never carried an image or a form, and a
           video has its own field on the entry. Stripping the handler off a
           remote <img> is not enough: the tag still fetches an
           attacker-chosen URL from every visitor's browser, which reports the
           whole exhibition floor to whoever proposed the text. <form>/<input>
           would let an approved answer draw a login box inside the chat, on
           the same origin as the real one. svg and math are dropped because
           they are where the parser-confusion bypasses live and no answer
           uses them. */
        FORBID_TAGS: ['img', 'svg', 'math', 'form', 'input', 'button', 'style'],
    });
    element.querySelectorAll('a').forEach(a => a.target = '_blank');
}

/* Plain text with its line breaks, built from text nodes and <br> elements.
   Nothing here goes through innerHTML, so the string cannot execute whatever
   it contains. */
function renderPlainText(element, text) {
    element.textContent = '';
    String(text).split('\n').forEach((line, i) => {
        if (i) element.appendChild(document.createElement('br'));
        element.appendChild(document.createTextNode(line));
    });
}

function typeWriter(element, text, speed = 20) {
    let i = 0;
    element.innerHTML = '';
    element.setAttribute('dir', 'auto');
    let currentTextNode = document.createTextNode('');
    element.appendChild(currentTextNode);

    function type() {
        if (i < text.length) {
            const char = text.charAt(i);
            if (char === "\n") {
                element.appendChild(document.createElement('br'));
                currentTextNode = document.createTextNode('');
                element.appendChild(currentTextNode);
            } else {
                currentTextNode.textContent += char;
            }
            i++;
            if (chatContent) chatContent.scrollTop = chatContent.scrollHeight;
            setTimeout(type, speed);
        } else {
            renderMarkdown(element, text);
        }
    }
    type();
}

// Default addMessage — plain bubble (minimal-style)
// Themes override via ChatConfig.addMessageFn
function addMessage(content, type, save = true, instant = false) {
    if (ChatConfig.addMessageFn) return ChatConfig.addMessageFn(content, type, save, instant);

    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${type}`;
    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.setAttribute('dir', 'auto');
    msgDiv.appendChild(bubble);
    if (loadingBubble) chatContent.insertBefore(msgDiv, loadingBubble);
    else chatContent.appendChild(msgDiv);

    if (type === 'bot' && !instant) {
        typeWriter(bubble, content);
    } else {
        renderMarkdown(bubble, content);
    }
    chatContent.scrollTop = chatContent.scrollHeight;
    if (save) saveToHistory(content, type);
}


// ── Chat Logic ─────────────────────────────────────────────────────────

/* What a registered visitor said about their work, if anything, so the
   targeted-visit answer can name the sections that fit them. Only the three
   descriptive fields travel — the name and phone number stay in this browser.
   Registration is an optional module, so its absence is the normal case. */
function visitorProfile() {
    try {
        const p = JSON.parse(localStorage.getItem('inotex-visitor') || 'null');
        if (!p) return {};
        const visitor = {
            job: p.job || '',
            position: p.position || '',
            interests: p.interests || ''
        };
        if (!visitor.job && !visitor.position && !visitor.interests) return {};
        return { visitor: visitor };
    } catch (e) {
        return {};
    }
}

async function sendMessage(fromPreset = false) {
    // In a text-only theme there is never background video to stop.
    if (!ChatConfig.isTextOnly && isResponsePlaying && avatarVideo) {
        stopVideoPlayback();
        return;
    }

    if (isRecording) stopRecording();

    const text = userInput.value.trim();
    if (!text) return;

    // A module may claim this message before the assistant answers it (see
    // ChatConfig.sendGateFn). Whoever claims it owns what appears in the chat
    // and what happens to the text — the engine simply stands down.
    if (typeof ChatConfig.sendGateFn === 'function') {
        let claimed = false;
        try { claimed = ChatConfig.sendGateFn(text) === true; }
        catch (e) { console.error('send gate failed:', e); }
        if (claimed) return;
    }

    addMessage(text, 'user');

    userInput.value = '';
    userInput.disabled = true;
    sendBtn.disabled = true;

    loadingBubble.style.opacity = '1';
    chatContent.scrollTop = chatContent.scrollHeight;

    try {
        const chatToken = document.querySelector('meta[name="chat-token"]')?.content || '';
        const response = await fetch('/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Chat-Token': chatToken
            },
            body: JSON.stringify(Object.assign(
                { message: text, lang: currentLang },
                visitorProfile()
            ))
        });

        if (!response.ok) {
            if (response.status === 429) {
                loadingBubble.style.opacity = '0';
                addMessage(t().rateLimit, 'bot', true, true);
                userInput.disabled = false;
                sendBtn.disabled = false;
                return;
            }
            if (response.status === 403) {
                loadingBubble.style.opacity = '0';
                addMessage(t().refresh, 'bot', true, true);
                return;
            }
            if (response.status === 503) {
                loadingBubble.style.opacity = '0';
                addMessage(t().aiUnavailable, 'bot', true, true);
                showQuestions();
                userInput.disabled = false;
                sendBtn.disabled = false;
                return;
            }
            throw new Error('Network response was not ok');
        }
        const data = await response.json();

        loadingBubble.style.opacity = '0';

        // Text-first: only attempt video when a video element actually exists.
        const hasVideoElement = !!avatarVideo;
        const hasVideo = hasVideoElement && data.type === 'video' && data.video_url && data.video_url.trim().length > 0;
        const videoOk = hasVideo && await checkVideoUrl(data.video_url);

        if (videoOk) {
            switchTab('video');
            playVideoWithTransition(data.video_url);
        } else {
            switchTab('text');
        }

        addMessage(data.text, 'bot');

    } catch (error) {
        console.error('Error:', error);
        loadingBubble.style.opacity = '0';
        addMessage(t().genericError, 'bot', true, true);
        showQuestions();
        switchTab('text');
    } finally {
        userInput.disabled = false;
        sendBtn.disabled = false;
        userInput.blur();
        updateSendButtonState();
    }
}


// ── Preset / Questions Logic ───────────────────────────────────────────

async function sendPreset(text) {
    userInput.value = text;
    sendMessage(true);
}

async function showQuestions() {
    const list = await getDisplayQuestions();
    if (!list.length) {
        addMessage(t().noQuestions, 'bot');
        return;
    }

    switchTab('text');

    const msgDiv = document.createElement('div');
    msgDiv.className = 'message bot questions-msg';
    const bubble = document.createElement('div');
    bubble.className = 'bubble';

    const title = document.createElement('p');
    title.textContent = t().questionsTitle;
    title.style.fontWeight = '600';
    title.style.marginBottom = '6px';
    bubble.appendChild(title);

    const ul = document.createElement('ul');
    ul.className = 'questions-list';
    bubble.appendChild(ul);

    let shown = 0;
    const showBatch = () => {
        const end = Math.min(shown + QUESTIONS_PER_PAGE, list.length);
        for (let i = shown; i < end; i++) {
            const li = document.createElement('li');
            li.textContent = list[i].question;
            li.setAttribute('data-index', i);
            li.tabIndex = 0;
            li.setAttribute('role', 'button');
            li.onclick = function () { playQuestionVideo(parseInt(this.getAttribute('data-index'))); };
            li.addEventListener('keydown', function (e) {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.click(); }
            });
            ul.appendChild(li);
        }
        shown = end;

        const oldBtn = bubble.querySelector('.show-more-btn');
        if (oldBtn) oldBtn.remove();

        if (shown < list.length) {
            const moreBtn = document.createElement('button');
            moreBtn.className = 'show-more-btn';
            moreBtn.textContent = t().showMore(list.length - shown);
            moreBtn.onclick = function (e) {
                e.stopPropagation();
                showBatch();
                chatContent.scrollTop = chatContent.scrollHeight;
            };
            bubble.appendChild(moreBtn);
        }
    };

    showBatch();
    msgDiv.appendChild(bubble);
    if (loadingBubble) chatContent.insertBefore(msgDiv, loadingBubble);
    else chatContent.appendChild(msgDiv);
    chatContent.scrollTop = chatContent.scrollHeight;
}

// Returns the suggested-questions list in the active language. Both titles come
// from the same dataset rows, so the menu keeps its curated order and its
// dataset_id links in either language — no hand-maintained English list.
async function getDisplayQuestions() {
    await displayQuestionsPromise;
    if (currentLang === 'en') {
        return displayQuestions.map(item => ({
            ...item,
            question: item.question_en || item.question,
        }));
    }
    return displayQuestions;
}

// Rebuild the questions list inside the last .questions-msg bubble, if present.
async function rebuildQuestionsIfVisible() {
    const msgBubble = document.querySelector('.questions-msg > .bubble');
    if (!msgBubble) return;
    // Easiest correct approach: clear and re-render via showQuestions by
    // removing the old node and re-showing.
    const oldNode = msgBubble.closest('.questions-msg');
    if (oldNode) oldNode.remove();
    showQuestions();
}

async function playQuestionVideo(index) {
    const list = await getDisplayQuestions();
    const q = list[index];
    if (!q) return;

    // English suggested questions route through the full chat pipeline so the
    // AI answers in English (the seeded dataset is Persian). Persian questions
    // resolve directly to the dataset entry for a fast, free, offline answer.
    if (currentLang === 'en') {
        userInput.value = q.question;
        sendMessage(true);
        return;
    }

    addMessage(q.question, 'user');
    loadingBubble.style.opacity = '1';
    chatContent.scrollTop = chatContent.scrollHeight;

    try {
        const dataset = await datasetPromise;
        const entry = dataset.find(item => item.id === q.dataset_id);
        const textResponse = entry ? entry.text : q.question;

        loadingBubble.style.opacity = '0';

        const hasVideoElement = !!avatarVideo;
        if (hasVideoElement && q.video_url && await checkVideoUrl(q.video_url)) {
            switchTab('video');
            playVideoWithTransition(q.video_url);
        } else {
            switchTab('text');
        }
        addMessage(textResponse, 'bot');
    } catch (error) {
        console.error("Question answer error:", error);
        loadingBubble.style.opacity = '0';
        addMessage(t().genericError, 'bot');
        switchTab('text');
    }
}


// ── Video Logic (all guarded — a no-op when no avatar element exists) ──

function initVideoState() {
    // Keep the video panel available even before local media is configured.
    if (!avatarVideo) return;
    ChatConfig.isTextOnly = false;

    // Only local, administrator-configured media may be loaded here.
    const waitingSrc = avatarVideo.getAttribute('data-waiting-src') || '';
    if (waitingSrc) {
        try {
            avatarVideo.src = waitingSrc;
            avatarVideo.loop = true;
            avatarVideo.muted = true;
            avatarVideo.play().catch(() => { /* autoplay prevented — fine */ });
        } catch (e) { /* ignore */ }
    }
}

async function checkVideoUrl(url) {
    try {
        if (url.startsWith('http://') && location.protocol === 'https:') return false;
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        const res = await fetch(url, { method: 'HEAD', mode: 'no-cors', signal: controller.signal });
        clearTimeout(timeout);
        return res.type === 'opaque' || res.ok;
    } catch {
        return false;
    }
}

// Default video transition — simple opacity fade (no-op theme guard)
function playVideoWithTransition(videoUrl, muted = false) {
    if (ChatConfig.playVideoTransitionFn) return ChatConfig.playVideoTransitionFn(videoUrl, muted);
    if (!avatarVideo) return;   // text-only: nothing to play

    const unmuteBtn = document.getElementById('unmute-btn');
    if (unmuteBtn) unmuteBtn.style.display = 'none';

    isResponsePlaying = true;
    updateSendButtonState();

    const container = document.querySelector('.avatar-container');
    if (container) container.style.opacity = '0.3';
    setTimeout(() => {
        avatarVideo.src = videoUrl;
        avatarVideo.loop = false;
        avatarVideo.muted = muted;
        const playPromise = avatarVideo.play();
        if (playPromise !== undefined) {
            playPromise.catch(() => {
                if (!muted) {
                    avatarVideo.muted = true;
                    avatarVideo.play().catch(() => {});
                }
            });
        }
        if (container) container.style.opacity = '1';
    }, 300);
}

function stopVideoPlayback() {
    if (!avatarVideo) { isResponsePlaying = false; updateSendButtonState(); return; }
    const waitingSrc = avatarVideo.getAttribute('data-waiting-src') || '';
    if (waitingSrc) {
        avatarVideo.src = waitingSrc;
        avatarVideo.loop = true;
        avatarVideo.muted = true;
        avatarVideo.play().catch(() => {});
    } else {
        try { avatarVideo.pause(); } catch (e) { /* ignore */ }
    }
    isResponsePlaying = false;
    updateSendButtonState();
}

function updateSendButtonState() {
    if (!sendBtn) return;
    if (!ChatConfig.isTextOnly && isResponsePlaying) {
        sendBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect></svg>';
        sendBtn.classList.add('stop-mode');
        sendBtn.title = t().stopTitle;
    } else {
        sendBtn.innerHTML = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M2.01 21L23 12L2.01 3L2 10L17 12L2 14L2.01 21Z" fill="white"/></svg>';
        sendBtn.classList.remove('stop-mode');
        sendBtn.title = t().sendTitle;
    }
}


// ── Voice Recording ────────────────────────────────────────────────────

async function toggleRecording() {
    if (isRecording) stopRecording();
    else await startRecording();
}

async function startRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioChunks = [];

        const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
            ? 'audio/webm;codecs=opus'
            : 'audio/webm';

        mediaRecorder = new MediaRecorder(stream, { mimeType });

        mediaRecorder.ondataavailable = (event) => {
            if (event.data.size > 0) audioChunks.push(event.data);
        };

        mediaRecorder.onstop = async () => {
            stream.getTracks().forEach(track => track.stop());
            const audioBlob = new Blob(audioChunks, { type: mimeType });
            await transcribeAudio(audioBlob);
        };

        mediaRecorder.start();
        if (micBtn) micBtn.classList.add('recording');
        isRecording = true;

    } catch (err) {
        console.error("Failed to start recording:", err);
        isRecording = false;
        if (micBtn) micBtn.classList.remove('recording');
    }
}

function stopRecording() {
    if (mediaRecorder && mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop();
    }
    isRecording = false;
    if (micBtn) micBtn.classList.remove('recording');
}

async function transcribeAudio(audioBlob) {
    try {
        const formData = new FormData();
        formData.append('audio', audioBlob, 'recording.webm');

        const response = await fetch('/api/transcribe', {
            method: 'POST',
            body: formData,
        });

        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();
        if (data.text) {
            const currentVal = userInput.value.trim();
            userInput.value = currentVal ? (currentVal + " " + data.text) : data.text;
        }
    } catch (err) {
        console.error("Transcription failed:", err);
    }
}


// ── Accessibility ──────────────────────────────────────────────────────

function adjustFontSize(change) {
    currentFontSize += change * 10;
    if (currentFontSize < 80) currentFontSize = 80;
    if (currentFontSize > 150) currentFontSize = 150;
    const content = document.getElementById("chat-view-content");
    if (content) content.style.fontSize = `${currentFontSize}%`;
}


// ── Initialization ─────────────────────────────────────────────────────

function initChat() {
    // Resolve DOM references
    chatContent = document.getElementById('chat-view-content');
    userInput = document.getElementById('user-input');
    sendBtn = document.getElementById('send-btn');
    micBtn = document.getElementById('mic-btn');
    loadingBubble = document.getElementById('loading-bubble');
    avatarVideo = document.getElementById('avatar-video');
    welcomeEl = document.getElementById('welcome-text');
    langBtn = document.getElementById('lang-btn');

    // Configure marked
    if (typeof marked !== 'undefined') {
        marked.use({ breaks: true, gfm: true });
    }

    // Language first — so all copy is localized before any message renders.
    setLang(localStorage.getItem(LANG_KEY) || 'fa');

    // Input state
    const input = document.getElementById("user-input");
    const button = document.getElementById("send-btn");
    if (input && button) {
        if (button) button.disabled = true;
        input.addEventListener("input", () => {
            const hasValue = input.value.length > 0;
            button.disabled = !hasValue;
        });
    }

    // Event listeners
    if (sendBtn) sendBtn.addEventListener('click', () => sendMessage(false));
    if (userInput) {
        userInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                sendMessage(false);
            }
        });
    }
    if (micBtn) micBtn.addEventListener('click', toggleRecording);
    if (langBtn) langBtn.addEventListener('click', () => {
        setLang(currentLang === 'fa' ? 'en' : 'fa');
    });

    // Feature switches (admin-controlled). STT off disables the mic; TTS off
    // removes the speaker entirely — a control that can never work should not
    // sit in front of an exhibition visitor.
    fetch('/api/voice-status')
        .then(r => r.ok ? r.json() : { voice_enabled: false, tts_enabled: true })
        .then(data => {
            if (micBtn) {
                micBtn.disabled = !data.voice_enabled;
                micBtn.style.display = data.voice_enabled ? '' : 'none';
            }
            const petVoice = document.getElementById('pet-voice');
            if (petVoice && !data.voice_enabled) petVoice.style.display = 'none';
            if (data.tts_enabled === false) {
                const tts = document.getElementById('tts-btn') || document.querySelector('.tts-btn');
                if (tts) tts.style.display = 'none';
                if ('speechSynthesis' in window) speechSynthesis.cancel();
            }
            // Admin-chosen first-visit language; a visitor's own choice
            // (stored on toggle) always wins over the default.
            if (!localStorage.getItem(LANG_KEY) && data.default_lang === 'en' && currentLang !== 'en') {
                setLang('en');
            }
        })
        .catch(() => { if (micBtn) micBtn.disabled = true; });

    // Hamburger menu
    const a11yHamburger = document.getElementById('a11y-hamburger');
    const a11yDropdown = document.getElementById('a11y-dropdown');
    if (a11yHamburger && a11yDropdown) {
        a11yHamburger.addEventListener('click', (e) => {
            e.stopPropagation();
            const isOpen = a11yDropdown.classList.toggle('open');
            a11yHamburger.setAttribute('aria-expanded', isOpen);
        });
        document.addEventListener('click', (e) => {
            if (!a11yDropdown.contains(e.target) && !a11yHamburger.contains(e.target)) {
                a11yDropdown.classList.remove('open');
                a11yHamburger.setAttribute('aria-expanded', 'false');
            }
        });
    }

    // Load chat history
    loadHistory();

    // Video (no-op in text-only themes)
    initVideoState();

    // Video ended — return to waiting (only if a video element exists)
    if (avatarVideo) {
        avatarVideo.addEventListener('ended', () => {
            const unmuteBtn = document.getElementById('unmute-btn');
            if (unmuteBtn) unmuteBtn.style.display = 'none';
            stopVideoPlayback();
        });
    }

    // Open on whichever view the theme's markup marked active, instead of
    // hardcoding one here. A theme that ships no INOTEX media should land the
    // visitor in the chat; one with a real avatar can mark the video view
    // active and land there. Falls back to video for older themes.
    const initialView = document.getElementById('text-view')?.classList.contains('active')
        ? 'text'
        : 'video';
    switchTab(initialView);
}
