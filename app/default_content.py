"""Default, publicly verifiable knowledge for a new INOTEX installation.

Source: https://inotex.com/ — every fact below was read directly from the
official site (home page, /fa/Faq, /fa/events) and verified on 1405/05/23
(2026-08-14). The machine-readable source manifest with URLs, hashes and
retrieval timestamps lives in content/sources.json.

Nothing here is invented. Where the site is silent (visitor tickets, exact
per-program schedule) the answer says so plainly and points the user at
۰۲۱-۸۸۵۰۳۰۳۰ / secretary@inotex.com / https://inotex.com/ instead of guessing.
Where two official pages disagree (visiting hours), the answer follows the FAQ
page and carries the caveat inline; the conflict is logged in
content/review-queue.md for human resolution.

The admin panel remains the source for richer, event-specific content after
installation.

BILINGUAL: INOTEX is an international exhibition, so every entry also carries
`title_en` / `text_en`. Those were translated from the verified Persian above.
Every caveat in the Persian is carried into the English.

Design placeholders (e.g. "INOTEX 2025" text visible inside UI reference
images under image/) are NOT facts and are never used here.
"""

# The first eight entries are what the chat menu shows, in this order:
# what is INOTEX, date, venue, visiting hours, booth reservation, programs,
# INOTEX Pitch, contact.
INOTEX_DATASET = [
    {
        "id": "inotex-overview",
        "title": "اینوتکس چیست؟",
        "text": "اینوتکس (INOTEX) نمایشگاه بین‌المللی نوآوری و فناوری و بزرگ‌ترین گردهمایی زیست‌بوم نوآوری ایران است. پانزدهمین دورهٔ این رویداد با نام «اینوتکس ۲۰۲۶» برگزار می‌شود و محل اتصال استارتاپ‌ها، شرکت‌های دانش‌بنیان، سرمایه‌گذاران و فعالان فناوری است. اطلاعات رسمی: https://inotex.com/",
        "title_en": "What is INOTEX?",
        "text_en": "INOTEX is the International Innovation and Technology Exhibition and the largest gathering of Iran's innovation ecosystem. The 15th edition, branded \"INOTEX 2026\", connects startups, knowledge-based companies, investors and technology professionals. Official information: https://inotex.com/",
        "video_url": "",
    },
    {
        "id": "inotex-date",
        "title": "اینوتکس کی برگزار می‌شود؟",
        "text": "پانزدهمین رویداد اینوتکس از ۱۱ تا ۱۴ شهریور ۱۴۰۵ (۲ تا ۵ سپتامبر ۲۰۲۶) به مدت چهار روز برگزار می‌شود. این تاریخ در صفحهٔ پرسش‌های متداول سایت رسمی اعلام شده است: https://inotex.com/fa/Faq",
        "title_en": "When is INOTEX held?",
        "text_en": "The 15th INOTEX runs from 11 to 14 Shahrivar 1405 (2 to 5 September 2026), over four days. The date is announced on the official FAQ page: https://inotex.com/fa/Faq",
        "video_url": "",
    },
    {
        "id": "inotex-venue",
        "title": "اینوتکس کجا برگزار می‌شود؟",
        "text": "اینوتکس ۲۰۲۶ در پارک فناوری پردیس برگزار می‌شود. نشانی دبیرخانهٔ رویداد: پارک فناوری پردیس، ساختمان سراج، واحد ۱۲۵. منبع: https://inotex.com/",
        "title_en": "Where is INOTEX held?",
        "text_en": "INOTEX 2026 is held at Pardis Technology Park. The event secretariat address is: Pardis Technology Park, Seraj Building, Unit 125. Source: https://inotex.com/",
        "video_url": "",
    },
    {
        "id": "inotex-hours",
        "title": "ساعت بازدید اینوتکس چگونه است؟",
        "text": "طبق صفحهٔ پرسش‌های متداول سایت رسمی، تمامی برنامه‌ها، رویدادها و غرفه‌ها در چهار روز برگزاری نمایشگاه از ساعت ۱۴ تا ۲۰ میزبان بازدیدکنندگان هستند. برای اطمینان از ساعت دقیق در روز مراجعه، سایت رسمی https://inotex.com/ را بررسی کنید.",
        "title_en": "What are the INOTEX visiting hours?",
        "text_en": "According to the official FAQ page, all programs, events and booths welcome visitors from 14:00 to 20:00 on each of the four exhibition days. Please confirm the exact hours on https://inotex.com/ before your visit.",
        "video_url": "",
    },
    {
        "id": "inotex-booth",
        "title": "چطور غرفه رزرو کنم؟",
        "text": "رزرو غرفهٔ اینوتکس از طریق پنل کاربری سایت رسمی انجام می‌شود: https://panel.inotex.com/booth/prices — غرفه‌ها در قالب فیزیکی، مجازی و سه‌بعدی ارائه می‌شوند. برای راهنمایی دربارهٔ انتخاب غرفه با شمارهٔ ۰۲۱-۷۶۲۵۰۲۵۰ داخلی ۲۳۳۸ تماس بگیرید.",
        "title_en": "How do I reserve a booth?",
        "text_en": "INOTEX booth reservation is done through the official user panel: https://panel.inotex.com/booth/prices — booths are offered in physical, virtual and 3D formats. For guidance on choosing a booth, call 021-76250250, extension 2338.",
        "video_url": "",
    },
    {
        "id": "inotex-programs",
        "title": "برنامه‌های اینوتکس چیست؟",
        "text": "اینوتکس ۲۰۲۶ مجموعه‌ای از برنامه‌ها را در چهار روز برگزاری میزبانی می‌کند؛ از جمله: استیج اینوتکس (سخنرانی‌ها و گفتگوهای نوآورانه)، رقابت استارتاپی اینوتکس پیچ و بتل، فروم حکمرانی و قانون‌گذاری، کافه سرمایه (تأمین مالی)، پاویون مشاوران، سکوی تأمین مالی جمعی، ریورس پیچ (نیازهای فناورانه صنایع)، جشنوارهٔ پیشگامان، مدیا هاب، همایش ملی هوش مصنوعی و اینترنت اشیا و پاویون سرمایه‌گذاران. ورود به بیشتر برنامه‌ها رایگان است. جزئیات: https://inotex.com/fa/events",
        "title_en": "What are the INOTEX programs?",
        "text_en": "INOTEX 2026 hosts a range of programs across its four days, including: INOTEX Stage (keynotes and innovation talks), the INOTEX Pitch & Battle startup competition, the Governance and Legislation Forum, Capital Café (financing), the Consultants Pavilion, a crowdfunding platform, Reverse Pitch (industry technology needs), the Pioneers Festival, Media Hub, the National AI & IoT Conference, and the Investors Pavilion. Most programs are free to attend. Details: https://inotex.com/fa/events",
        "video_url": "",
    },
    {
        "id": "inotex-pitch",
        "title": "اینوتکس پیچ چیست؟",
        "text": "اینوتکس پیچ و بتل رقابت استارتاپی اینوتکس است که در آن استارتاپ‌ها در حضور سرمایه‌گذاران ارائه می‌دهند و داوری می‌شوند. طبق سایت رسمی، مراحل مقدماتی این رقابت از دی‌ماه ۱۴۰۴ در استان‌ها برگزار می‌شود و مرحلهٔ نهایی در ایام نمایشگاه است. شرکت در این برنامه رایگان اعلام شده است. جزئیات: https://inotex.com/fa/events",
        "title_en": "What is INOTEX Pitch?",
        "text_en": "INOTEX Pitch & Battle is the INOTEX startup competition where startups present to investors and are scored. According to the official site, preliminary rounds run in the provinces from Dey 1404 (December 2025/January 2026), with the finals during the exhibition. Participation is announced as free. Details: https://inotex.com/fa/events",
        "video_url": "",
    },
    {
        "id": "inotex-contact",
        "title": "راه‌های تماس با دبیرخانه اینوتکس",
        "text": "تلفن دبیرخانه: ۰۲۱-۸۸۵۰۳۰۳۰ — ایمیل: secretary@inotex.com — نشانی: پارک فناوری پردیس، ساختمان سراج، واحد ۱۲۵. سایت رسمی: https://inotex.com/",
        "title_en": "How to contact the INOTEX secretariat",
        "text_en": "Secretariat phone: 021-88503030 — Email: secretary@inotex.com — Address: Pardis Technology Park, Seraj Building, Unit 125. Official site: https://inotex.com/",
        "video_url": "",
    },
    {
        "id": "inotex-exhibitors",
        "title": "چه کسانی می‌توانند غرفه‌دار شوند؟",
        "text": "طبق سایت رسمی، شرکت‌های دانش‌بنیان، استارتاپ‌ها، سرمایه‌گذاران، شتابدهنده‌ها، مخترعان، مشاوران و رسانه‌های حوزهٔ استارتاپی می‌توانند در اینوتکس غرفه داشته باشند. ثبت‌نام غرفه از مسیر https://panel.inotex.com/booth/prices انجام می‌شود.",
        "title_en": "Who can be an exhibitor?",
        "text_en": "According to the official site, knowledge-based companies, startups, investors, accelerators, inventors, consultants and startup-focused media can exhibit at INOTEX. Booth registration is at https://panel.inotex.com/booth/prices",
        "video_url": "",
    },
    {
        "id": "inotex-visitors",
        "title": "بازدید از اینوتکس چگونه است؟",
        "text": "بازدید از برنامه‌ها و غرفه‌های اینوتکس در چهار روز برگزاری برای علاقه‌مندان زیست‌بوم نوآوری امکان‌پذیر است و ورود به بیشتر برنامه‌ها رایگان اعلام شده است. جزئیات نحوهٔ حضور و ثبت‌نام بازدید از طریق سایت رسمی https://inotex.com/ و پنل کاربری https://panel.inotex.com/ اطلاع‌رسانی می‌شود؛ در صورت ابهام با دبیرخانه (۰۲۱-۸۸۵۰۳۰۳۰) تماس بگیرید.",
        "title_en": "How can I visit INOTEX?",
        "text_en": "Visiting INOTEX programs and booths is open to the innovation community during the four event days, and entry to most programs is announced as free. Attendance and visitor-registration details are published on the official site https://inotex.com/ and the user panel https://panel.inotex.com/ — for questions, call the secretariat at 021-88503030.",
        "video_url": "",
    },
    {
        "id": "inotex-stats",
        "title": "ابعاد رویداد اینوتکس",
        "text": "طبق صفحهٔ اصلی سایت رسمی، اینوتکس ۲۰۲۶ میزبان بیش از ۴۵۰ شرکت فناور و نوآور، بیش از ۳۰,۰۰۰ بازدیدکننده از زیست‌بوم نوآوری، بیش از ۱۵۰ سرمایه‌گذار، بیش از ۴۰۰ جلسهٔ B2B و بیش از ۳۰ رویداد جانبی است. منبع: https://inotex.com/",
        "title_en": "How big is INOTEX?",
        "text_en": "According to the official home page, INOTEX 2026 hosts 450+ technology and innovation companies, 30,000+ visitors from the innovation ecosystem, 150+ investors, 400+ B2B meetings and 30+ side events. Source: https://inotex.com/",
        "video_url": "",
    },
    {
        "id": "inotex-app",
        "title": "برنامه زمانی و اپلیکیشن اینوتکس",
        "text": "برنامهٔ زمانی رویدادهای اینوتکس از طریق صفحهٔ رویدادها (https://inotex.com/fa/events) و اپلیکیشن رسمی رویداد (https://inotex.com/dlapp و نسخهٔ وب https://on-time.app/) در دسترس است.",
        "title_en": "INOTEX schedule and app",
        "text_en": "The INOTEX event schedule is available on the events page (https://inotex.com/fa/events) and through the official event app (https://inotex.com/dlapp, web version at https://on-time.app/).",
        "video_url": "",
    },
    {
        "id": "inotex-volunteer",
        "title": "ثبت‌نام داوطلبان اینوتکس",
        "text": "علاقه‌مندان به همکاری داوطلبانه در برگزاری اینوتکس می‌توانند از طریق فرم رسمی سایت ثبت‌نام کنند: https://panel.inotex.com/f/id/19",
        "title_en": "INOTEX volunteer registration",
        "text_en": "Those interested in volunteering at INOTEX can register through the official form: https://panel.inotex.com/f/id/19",
        "video_url": "",
    },
    {
        "id": "inotex-organizers",
        "title": "برگزارکنندگان اینوتکس",
        "text": "اینوتکس با مشارکت نهادهای زیست‌بوم نوآوری کشور و با میزبانی پارک فناوری پردیس برگزار می‌شود. فهرست دقیق برگزارکنندگان و حامیان هر دوره در سایت رسمی https://inotex.com/ اعلام می‌شود.",
        "title_en": "Who organizes INOTEX?",
        "text_en": "INOTEX is organized with the participation of Iran's innovation-ecosystem institutions and hosted by Pardis Technology Park. The exact list of organizers and sponsors of each edition is announced on the official site https://inotex.com/",
        "video_url": "",
    },
    {
        "id": "inotex-targeted-visit",
        "title": "بازدید هدفمند چیست؟",
        "text": "بازدید هدفمند یعنی به‌جای گشتن تصادفی در نمایشگاه، ابتدا غرفه‌ها و برنامه‌های مرتبط با کار و علاقهٔ خودتان را بشناسید. کافی است در همین گفتگو ثبت‌نام کنید و شغل، سمت و زمینه‌های مورد علاقه‌تان را بنویسید تا دستیار غرفه‌ها و رویدادهای مرتبط را به شما معرفی کند تا در زمان محدودِ بازدید، اول سراغ چیزهایی بروید که به کارتان می‌آید. تکمیل این اطلاعات اختیاری است و بدون آن هم می‌توانید هر سؤالی درباره اینوتکس بپرسید. پیشنهادها بر اساس بخش‌ها و برنامه‌های رسمی اعلام‌شده در https://inotex.com/ ساخته می‌شود؛ فهرست غرفه‌داران هنوز روی سایت رسمی منتشر نشده است.",
        "title_en": "What is a targeted visit?",
        "text_en": "A targeted visit means finding the booths and sessions that match your work and interests first, instead of wandering the halls at random. Use the Smart Visit button and add your field of work, job title and interests, and the assistant can point you to the relevant booths and events so your limited time at the exhibition goes to what is actually useful to you. Providing this is optional — you can ask anything about INOTEX without it. Suggestions are built from the official sections and programmes announced on https://inotex.com/; the exhibitor directory is not published on the official site yet.",
        "video_url": "",
    },
    {
        "id": "inotex-news",
        "title": "اخبار و اطلاعیه‌های اینوتکس",
        "text": "آخرین اخبار و اطلاعیه‌های رسمی اینوتکس در بخش اخبار سایت (https://inotex.com/fa/allnews) و بلاگ رسمی (https://inotex.com/fa/blogs) منتشر می‌شود.",
        "title_en": "INOTEX news and announcements",
        "text_en": "The latest official INOTEX news and announcements are published in the news section (https://inotex.com/fa/allnews) and the official blog (https://inotex.com/fa/blogs).",
        "video_url": "",
    },
    # --- INOTEX 2026 program content -------------------------------------
    # Crawled 2026-08-27 from the official timetable (https://inotex.com/programs)
    # and the current-edition program news (https://inotex.com/fa/allnews, ids
    # 578-605). The ids below are also listed in INOTEX_2026_PROGRAM_IDS so
    # scripts/import-inotex-programs.py can upsert exactly this block into an
    # already-installed database without touching anything else.
    {
        "id": "inotex-schedule-2026",
        "title": "جدول زمان‌بندی برنامه‌های اینوتکس ۲۰۲۶",
        "text": "برنامهٔ زمانی رسمی همهٔ رویدادهای اینوتکس ۲۰۲۶ در صفحهٔ «برنامه زمانی» سایت منتشر شده است: https://inotex.com/programs — در این جدول می‌توانید برنامه را بر اساس روزهای برگزاری و مکان‌ها (استیج اصلی، استیج جانبی، سالن فن‌بازار، سالن سراج، اینونایت، میت‌آپ و هاب رسانه) دنبال کنید. چند ساعت کلیدی طبق این جدول: افتتاحیه ۱۵:۳۰ تا ۱۷:۳۰، برنامهٔ استیج اینوتکس از ساعت ۱۴، فینال اینوتکس‌پیچ و بتل ۱۴ تا ۱۸ و اختتامیه ۱۸ تا ۲۰ در روز پایانی، پخش زندهٔ بازی استقلال و پرسپولیس ۱۹:۳۰ تا ۲۱:۳۰ و هاب رسانه ۱۰ تا ۱۸. برای دیدن برنامهٔ دقیق هر روز و هر سالن به همان صفحه مراجعه کنید.",
        "title_en": "INOTEX 2026 event schedule",
        "text_en": "The official timetable for all INOTEX 2026 programs is published on the schedule page: https://inotex.com/programs — browse it by event day and by stage (main stage, side stage, FanBazar hall, Seraj hall, InoNight, Meetup and Media Hub). Key times from the table: opening ceremony 15:30–17:30, INOTEX Stage program from 14:00, the INOTEX Pitch & Battle final 14:00–18:00 and the closing ceremony 18:00–20:00 on the final day, a live screening of the Esteghlal–Persepolis match 19:30–21:30, and the Media Hub 10:00–18:00. Check that page for the exact per-day, per-hall program.",
        "video_url": "",
    },
    {
        "id": "inotex-express-2026",
        "title": "اینوتکس اکسپرس چیست؟",
        "text": "«اینوتکس اکسپرس» (INOTEX Express) نام و رویکرد دورهٔ پانزدهم است. نمایشگاه که در ابتدا برای اردیبهشت برنامه‌ریزی شده بود، به دلیل شرایط جنگی به ۱۱ تا ۱۴ شهریور ۱۴۰۵ منتقل شد و با طراحی چابک‌تر و تمرکز بر شبکه‌سازی، سرمایه‌گذاری، بازار و افزایش تاب‌آوری کسب‌وکارهای نوآور، در ساعات عصر (۱۴ تا ۲۰) برگزار می‌شود. طبق خبر رسمی، دست‌کم ۲۰۰ مجموعه حضور دارند: حدود ۱۰۰ شرکت نوپا و استارتاپ، ۶۵ شرکت فناور و دانش‌بنیان، ۲۰ مجموعهٔ سرمایه‌گذاری و ۱۵ شتاب‌دهنده و مرکز نوآوری. پیش از نمایشگاه نیز رقابت‌های اینوتکس‌پیچ در بوشهر، تهران و تبریز با حضور ۶۷، ۱۳۶ و ۱۷۵ تیم برگزار شد و در بخش منتورشیپ ۱۳ منتور در ۶ حوزهٔ تخصصی ۱۱۸ جلسهٔ مشاوره برگزار کردند. منابع: https://inotex.com/fa/news?id=593 و https://inotex.com/fa/news?id=594",
        "title_en": "What is INOTEX Express?",
        "text_en": "INOTEX Express is the name and concept of the 15th edition. Originally planned for Ordibehesht, the exhibition was moved to 11–14 Shahrivar 1405 (2–5 September 2026) because of war conditions, redesigned to be more agile and focused on networking, investment, market access and the resilience of innovative businesses, and runs in the evening hours (14:00–20:00). According to the official news, at least 200 organizations attend: about 100 startups, 65 technology and knowledge-based companies, 20 investment groups and 15 accelerators and innovation centers. Before the exhibition, INOTEX Pitch city rounds were held in Bushehr, Tehran and Tabriz with 67, 136 and 175 teams, and in the mentorship program 13 mentors held 118 consulting sessions across 6 specialty areas. Sources: https://inotex.com/fa/news?id=593 and https://inotex.com/fa/news?id=594",
        "video_url": "",
    },
    {
        "id": "inotex-pitch-2026-final",
        "title": "فینال اینوتکس‌پیچ ۲۰۲۶ چه برنامه‌ای دارد؟",
        "text": "طبق خبر رسمی دبیرخانه، اینوتکس‌پیچ ۲۰۲۶ پس از رقابت‌های استانی در هفت استان (ایلام، سیستان و بلوچستان، کرمان، گلستان، بوشهر، آذربایجان شرقی و تهران) و ثبت‌نام بیش از ۴۵۰ تیم، با ۱۴ تیم برگزیده به مرحلهٔ نهایی رسیده است. فینال ۱۴ شهریورماه (روز پایانی نمایشگاه) برگزار می‌شود و هر تیم ۶ دقیقه ارائه و ۴ دقیقه پاسخ به پرسش داوران دارد. تیم اول ۱۰۰ میلیون تومان و تیم دوم ۵۰ میلیون تومان جایزه نقدی می‌گیرد و تیم‌های برتر کانتر رایگان نمایشگاه و جلسه با سرمایه‌گذاران نیز دارند. پس از اعلام تیم‌های برتر، بخش حذفی «بتل» برگزار می‌شود: هر تیم حدود ۳ دقیقه نقاط ضعف رقیب را به چالش می‌کشد و تیم مقابل یک دقیقه فرصت دفاع دارد؛ برندهٔ بتل ۵۰ میلیون تومان و کمربند قهرمانی دریافت می‌کند. طبق جدول زمان‌بندی، اینوتکس‌پیچ و بتل از ساعت ۱۴ تا ۱۸ روی صحنه است. منبع: https://inotex.com/fa/news?id=605",
        "title_en": "What is planned for the INOTEX Pitch 2026 final?",
        "text_en": "According to the official news, INOTEX Pitch 2026 held provincial rounds in seven provinces (Ilam, Sistan and Baluchestan, Kerman, Golestan, Bushehr, East Azerbaijan and Tehran) with more than 450 registered teams, and 14 teams reached the final. The final takes place on 14 Shahrivar (the last exhibition day); each team pitches for 6 minutes followed by 4 minutes of jury questions. The first-place team wins a 100-million-toman cash prize and the runner-up 50 million tomans, and top teams also get a free booth counter and investor meetings. After the winners are announced, the knockout 'Battle' round follows: each team has about 3 minutes to challenge its rival's weaknesses and 1 minute to defend; the Battle winner takes 50 million tomans plus a championship belt. Per the timetable, INOTEX Pitch & Battle runs 14:00–18:00. Source: https://inotex.com/fa/news?id=605",
        "video_url": "",
    },
    {
        "id": "inotex-stage-2026",
        "title": "استیج اینوتکس امسال چه برنامه‌ای دارد؟",
        "text": "استیج اینوتکس امسال در قالب «اینوتکس اکسپرس» یک برنامهٔ یک‌روزهٔ فشرده است که طبق جدول زمان‌بندی از ساعت ۱۴ تا حدود ۲۰:۲۵ برگزار می‌شود و بیش از ۳۰ چهره از بخش خصوصی، دولت، حاکمیت و ایرانیان خارج از کشور روی صحنه می‌رود. محورهای اصلی استیج: «آتش و ابتکار» (تجربهٔ کسب‌وکارها در یک سال جنگ: شکست، ورشکستگی و بقا)، «جریان‌های پنهان» (روندها، رویه‌ها، بازار سرمایه و حکمرانی)، «منطق دوام» (رازهای بقا با تحلیل‌های اقتصادی و اجتماعی) و «وطن‌های متصل» (اکوسیستم و دیاسپورای ایرانی خارج از کشور). در کنار سخنرانی‌ها و پنل‌ها، گزیده‌ای از فیلم‌های جذاب با رویکرد دنیای آینده و نقش فناوری نیز پخش می‌شود. منبع: https://inotex.com/fa/news?id=604",
        "title_en": "What is on this year's INOTEX Stage?",
        "text_en": "This year's INOTEX Stage, under the INOTEX Express format, is a compressed one-day program running 14:00 to about 20:25 per the official timetable, putting more than 30 figures from the private sector, government, the state and the Iranian diaspora on stage. The main themes: 'Fire and Innovation' (businesses in a year of war: failure, bankruptcy and survival), 'Hidden Currents' (trends, practices, the capital market and governance), 'The Logic of Endurance' (survival secrets with economic and social analysis) and 'Connected Homelands' (the ecosystem and the Iranian diaspora). Alongside the talks and panels, highlights of future-focused technology films are screened. Source: https://inotex.com/fa/news?id=604",
        "video_url": "",
    },
    {
        "id": "inotex-capital-cafe-2026",
        "title": "کافه سرمایه در اینوتکس ۲۰۲۶ چگونه کار می‌کند؟",
        "text": "کافه سرمایه بخش تخصصی اتصال استارتاپ‌ها به سرمایه‌گذاران (صندوق‌های سرمایه‌گذاری، سرمایه‌گذاران خطرپذیر و سرمایه‌گذاران فرشته) است. استارتاپ‌ها در سامانهٔ کافه سرمایه ثبت‌نام می‌کنند و اطلاعات طرح و نیاز سرمایه‌گذاری را ثبت می‌کنند؛ طرح‌ها ارزیابی و برای سرمایه‌گذاران مرتبط ارسال می‌شود و نخستین جلسات مذاکره در جریان نمایشگاه برگزار می‌شود. امسال این رویداد با همکاری انجمن سرمایه‌گذاران خطرپذیر و با شعار «هر استارتاپ، ۵ سرمایه‌گذار» برگزار می‌شود تا تیم‌های منتخب فرصت ارائهٔ مستقیم به پنج سرمایه‌گذار را داشته باشند. کارگاه‌های آموزشی کافه سرمایه نیز طبق جدول زمان‌بندی برگزار می‌شود؛ از جمله «کالبدشکافی حقوقی فروپاشی استارتاپ‌ها در ایران»، «تجربهٔ ساخت و بازاریابی استارتاپ در ایران و اروپا» و «نقشهٔ راه تأمین مالی استارتاپ‌ها». منابع: https://inotex.com/fa/news?id=596 و https://inotex.com/fa/news?id=593",
        "title_en": "How does Capital Café work at INOTEX 2026?",
        "text_en": "Capital Café is the dedicated program connecting startups with investors (funds, VCs and angel investors). Startups register in the Capital Café system and submit their plan and funding needs; plans are evaluated and sent to matching investors, with the first negotiation meetings held during the exhibition. This year it runs with the Venture Capital Association under the slogan 'every startup, 5 investors', so selected teams pitch directly to five investors. Capital Café training workshops also appear in the timetable, including 'A legal autopsy of startup failures in Iran', 'Building and marketing a startup in Iran and Europe' and 'The startup financing roadmap'. Sources: https://inotex.com/fa/news?id=596 and https://inotex.com/fa/news?id=593",
        "video_url": "",
    },
    {
        "id": "inotex-investors-pavilion-2026",
        "title": "پاویون سرمایه‌گذاران اینوتکس چه امکاناتی دارد؟",
        "text": "پاویون سرمایه‌گذاران میزبان صندوق‌های سرمایه‌گذاری خطرپذیر، صندوق‌های پژوهش و فناوری، سرمایه‌گذاران فرشته، بازوهای سرمایه‌گذاری شرکتی (CVC) و هلدینگ‌های سرمایه‌گذاری است و بستری برای جلسات B2B و مذاکرات تخصصی فراهم می‌کند. خدمات سرمایه‌گذاران شامل فضای اختصاصی مذاکره، برنامه‌ریزی جلسات هدفمند با شرکت‌های فناور، دسترسی به بانک اطلاعاتی استارتاپ‌های منتخب، معرفی در سایت و رسانه‌های رسمی اینوتکس، حضور در کافه سرمایه، داوری فینال اینوتکس‌پیچ و حضور در Demo Day است. ثبت‌نام و اطلاعات بیشتر: https://inotex.com/fa/event/?id=2117 — منبع: https://inotex.com/fa/news?id=597",
        "title_en": "What does the INOTEX Investors Pavilion offer?",
        "text_en": "The Investors Pavilion hosts venture capital funds, research and technology funds, angel investors, corporate venture arms (CVC) and investment holdings, providing a space for B2B sessions and dedicated negotiations. Services include a private negotiation space, targeted meeting scheduling with tech companies, access to a database of selected startups, promotion on INOTEX's official site and media, participation in Capital Café, jury seats at the INOTEX Pitch final, and Demo Day presence. Registration: https://inotex.com/fa/event/?id=2117 — Source: https://inotex.com/fa/news?id=597",
        "video_url": "",
    },
    {
        "id": "inotex-reverse-pitch-2026",
        "title": "ریورس‌پیچ اینوتکس ۲۰۲۶ چه نیازهایی ارائه می‌کند؟",
        "text": "در ریورس‌پیچ، صنایع و سازمان‌های بزرگ نیازهای فناورانهٔ خود ارائه می‌کنند و از استارتاپ‌ها و شرکت‌های دانش‌بنیان برای همکاری و سرمایه‌گذاری دعوت می‌شود. طبق جدول زمان‌بندی ۲۰۲۶، جلسات نیازهای فناورانهٔ این حوزه‌ها برگزار می‌شود: بهداشت، درمان و تجهیزات پزشکی؛ معدن و فرآوری قیر طبیعی؛ صنعت آب و برق (وزارت نیرو)؛ بهره‌وری، نوآوری و تحول دیجیتال در معدن؛ نفت، گاز و پتروشیمی؛ و شرکت همراه اول. منبع: https://inotex.com/programs",
        "title_en": "What technology needs does INOTEX 2026 Reverse Pitch present?",
        "text_en": "In Reverse Pitch, major industries and organizations present their technology needs and invite startups and knowledge-based companies to collaborate and invest. Per the 2026 timetable, needs sessions cover: health, treatment and medical equipment; mining and natural bitumen processing; the water and power industry (Ministry of Energy); productivity, innovation and digital transformation in mining; oil, gas and petrochemicals; and Hamrah-e Aval (telecom). Source: https://inotex.com/programs",
        "video_url": "",
    },
    {
        "id": "inotex-fanbazar-2026",
        "title": "برنامه‌های فن‌بازار ملی در اینوتکس چیست؟",
        "text": "شبکهٔ فن‌بازار ملی ایران در چهار روز اینوتکس چهار برنامهٔ اصلی دارد: بیست‌وششمین نشست سراسری فن‌بازارهای کشور با حضور مدیران و کارگزاران تجارت فناوری (طبق جدول زمان‌بندی از ساعت ۸ تا ۱۶)، دورهٔ توانمندسازی کارگزاران با آموزش‌های تخصصی حوزهٔ تجارت فناوری، برگزاری دست‌کم ۱۰ تور فناوری از استان‌های مختلف به نمایشگاه، و رویدادهای تجاری با محوریت ارائهٔ نیازهای فناورانه در حوزه‌های اولویت‌دار مانند فناوری اطلاعات و ارتباطات، نفت و گاز و پتروشیمی، صنایع معدنی و حوزهٔ پزشکی. هدف این برنامه‌ها توسعهٔ بازار فناوری و شبکه‌سازی میان فعالان زیست‌بوم است. منبع: https://inotex.com/fa/news?id=599",
        "title_en": "What are the National FanBazar programs at INOTEX?",
        "text_en": "The Iran National FanBazar network runs four main programs across the four INOTEX days: the 26th national gathering of the country's technology marketplaces with managers and technology-trade brokers (08:00–16:00 per the timetable), a broker empowerment course with specialized technology-trade training, at least 10 technology tours from various provinces to the exhibition, and trade events centered on presented technology needs in priority areas such as ICT, oil, gas and petrochemicals, mining and medical fields. The goal is developing the technology market and networking across the ecosystem. Source: https://inotex.com/fa/news?id=599",
        "video_url": "",
    },
    {
        "id": "inotex-ai-iot-conf-2026",
        "title": "کنفرانس هوش مصنوعی و اینترنت اشیا اینوتکس",
        "text": "کنفرانس هوش مصنوعی و اینترنت اشیا در سه روز نخست برگزاری اینوتکس ادامه می‌دارد و هر روز بین دو تا سه پنل تخصصی در ساعت‌های ۱۴ تا ۲۰ برگزار می‌کند. موضوعات پنل‌ها به تفکیک روز: روز اول — پنل مالی و فین‌تک هوشمند، پنل سلامت و پزشکی هوشمند؛ روز دوم — پنل امنیت در اینترنت اشیا، پنل صنعت و تولید هوشمند، پنل معدن و اکتشاف هوشمند؛ روز سوم — پنل نقش هوش مصنوعی در نوآوری، پنل هوش مصنوعی و اینترنت اشیا، پنل علوم شناختی و آموزش هوشمند. منبع: https://inotex.com/programs",
        "title_en": "The INOTEX AI & IoT Conference",
        "text_en": "The AI & IoT Conference runs across the first three INOTEX days, with two to three specialized panels per day between 14:00 and 20:00. Panels by day: day one — smart finance and fintech, smart health and medicine; day two — IoT security, smart industry and manufacturing, smart mining and exploration; day three — the role of AI in innovation, AI and IoT, cognitive science and smart education. Source: https://inotex.com/programs",
        "video_url": "",
    },
    {
        "id": "inotex-work-station-2026",
        "title": "ایستگاه کار اینوتکس برای کارجویان",
        "text": "«ایستگاه کار» برنامهٔ مرکز توسعهٔ سرمایه انسانی پارک فناوری پردیس در اینوتکس است: شرکت‌های فناور فرصت‌های شغلی و کارآموزی خود را معرفی می‌کنند و کارجویان می‌توانند رزومه بدهند، با شرکت‌ها ارتباط مستقیم بگیرند و وارد فرایند مصاحبهٔ شغلی شوند. کافه کار، مشاوره و کوچینگ شغلی، پنل توسعهٔ سرمایه انسانی و کتابچهٔ فرصت‌های شغلی نیز بخش‌های دیگر این ایستگاه است. در کنار آن، نشست تخصصی مدیران منابع انسانی شرکت‌های دانش‌بنیان برگزار می‌شود و تورهای فناوری برای دانشجویان و دانش‌آموزان شامل بازدید از شرکت‌های دانش‌بنیان، فازهای پارک فناوری پردیس و موزهٔ هوانوردی برنامه‌ریزی شده است. منبع: https://inotex.com/fa/news?id=602",
        "title_en": "The INOTEX Work Station for job seekers",
        "text_en": "The Work Station is the Pardis Technology Park Human Capital Development Center's program at INOTEX: tech companies introduce their job and internship openings, and job seekers can submit resumes, connect directly with companies and enter interview processes. The Work Café, career coaching, a human-capital panel and a job-opportunities booklet complete the station. Alongside it, a specialized HR-managers meeting for knowledge-based companies is held, and technology tours for students cover visits to knowledge-based companies, the park's phases and the Aviation Museum. Source: https://inotex.com/fa/news?id=602",
        "video_url": "",
    },
    {
        "id": "inotex-mentors-2026",
        "title": "پاویون مشاوران و منتورهای اینوتکس",
        "text": "پاویون مشاوران و منتورها بستری برای دریافت مشاورهٔ تخصصی از منتورها و مشاوران باتجربهٔ زیست‌بوم نوآوری است؛ با تمرکز بر افزایش تاب‌آوری و مدیریت بحران: استراتژی بحران، مدیریت منابع و نقدینگی، بازطراحی مدل کسب‌وکار، تحول دیجیتال و مدیریت تیم در شرایط بحرانی. برای ثبت‌نام و اطلاعات بیشتر به mentorship.inotex.com مراجعه کنید یا با ۰۹۹۳۱۸۷۷۳۱۷ تماس بگیرید. منبع: https://inotex.com/fa/news?id=580",
        "title_en": "The INOTEX Consultants and Mentors Pavilion",
        "text_en": "The Consultants and Mentors Pavilion offers specialized advice from experienced ecosystem mentors, focused on resilience and crisis management: crisis strategy, resource and liquidity management, business-model redesign, digital transformation and team management in critical conditions. For registration and more information visit mentorship.inotex.com or call 09931877317. Source: https://inotex.com/fa/news?id=580",
        "video_url": "",
    },
    {
        "id": "inotex-inonight-meetups-2026",
        "title": "اینونایت و میت‌آپ‌های اینوتکس",
        "text": "اینونایت برنامهٔ شبانهٔ شبکه‌سازی اینوتکس است و طبق جدول زمان‌بندی از ساعت ۲۰ تا ۲۴ برگزار می‌شود. چهار میت‌آپ تخصصی نیز در روزهای نمایشگاه برگزار می‌شود با موضوعاتی مانند: حقوق استارتاپ برای غیرحقوقی‌ها (اشتباهاتی که بعداً گران تمام می‌شوند)، استارتاپ واقعاً چه زمانی به مشاوره نیاز دارد، از ایده تا اولین مشتری، تیم مؤسس و انتخاب شریک (شراکت‌ها چرا خراب می‌شوند)، هوش مصنوعی در تجارت الکترونیک، جذب سرمایه در مراحل اولیه، دیده‌شدن کافی نیست؛ چطور انتخاب شویم، و اینکه آیا یک نفر می‌تواند تنها استارتاپ بسازد. منبع: https://inotex.com/programs",
        "title_en": "INOTEX InoNight and Meetups",
        "text_en": "InoNight is INOTEX's evening networking program, running 20:00 to 24:00 per the official timetable. Four specialized meetups also run across the event days, on topics such as: startup law for non-lawyers (mistakes that cost you later), when a startup really needs a consultant, from idea to first customer, choosing a co-founder (and why partnerships fail), AI in e-commerce, early-stage fundraising, being seen is not enough — how to get picked, and whether one person can build a startup alone. Source: https://inotex.com/programs",
        "video_url": "",
    },
    {
        "id": "inotex-governance-forum-2026",
        "title": "فروم حکمرانی اینوتکس چه برنامه‌ای دارد؟",
        "text": "فروم حکمرانی فضای گفت‌وگوی سازنده میان فعالان بخش خصوصی و سیاست‌گذاران است. برنامه‌های آن طبق جدول زمان‌بندی: پنل سیاستی «گونه‌ها و الگوهای قانون‌گذاری و تنظیم‌گری فناوری‌های نوظهور»، ارائه‌های کوتاه «ایده‌هایی که باید تکثیر شوند» و پنل «بازخوانی قانون جهش تولید دانش‌بنیان از نگاه اکوسیستم نوآوری». منبع: https://inotex.com/programs",
        "title_en": "What is on the INOTEX Governance Forum?",
        "text_en": "The Governance Forum is a constructive dialogue space between private-sector actors and policymakers. Per the timetable its program includes the policy panel 'Models of legislation and regulation for emerging technologies', short 'ideas that must be replicated' presentations, and the panel 'Revisiting the Knowledge-Based Production Leap Law through the innovation ecosystem's lens'. Source: https://inotex.com/programs",
        "video_url": "",
    },
    {
        "id": "inotex-pardis-summit-2026",
        "title": "پردیس سامیت و شتاب‌دهنده‌ها در اینوتکس",
        "text": "دهمین دورهٔ رویداد «پردیس سامیت» هم‌زمان با روز دوم اینوتکس و به‌صورت ترکیبی (حضوری و آنلاین) برگزار می‌شود؛ گردهمایی سالانهٔ اعضای مرکز شتابدهی نوآوری شامل شتاب‌دهنده‌ها، فضاهای کار اشتراکی و برگزارکنندگان رویدادهای کارآفرینی. همچنین نزدیک به ۲۰ شتاب‌دهندهٔ عضو مرکز شتابدهی نوآوری با غرفه در اینوتکس حضور می‌یابند تا دستاوردها و استارتاپ‌های تحت حمایت خود را معرفی کنند و با سرمایه‌گذاران حاضر در نمایشگاه ارتباط بگیرند. منبع: https://inotex.com/fa/news?id=601",
        "title_en": "Pardis Summit and accelerators at INOTEX",
        "text_en": "The 10th Pardis Summit runs hybrid (in-person and online) alongside INOTEX's second day — the annual gathering of the Innovation Acceleration Center's members: accelerators, coworking spaces and entrepreneurship-event organizers. In addition, nearly 20 member accelerators exhibit at INOTEX to showcase their startups and achievements and connect with investors at the show. Source: https://inotex.com/fa/news?id=601",
        "video_url": "",
    },
    {
        "id": "inotex-selection-day-2026",
        "title": "روز انتخاب و رونمایی محصول در اینوتکس",
        "text": "طبق جدول زمان‌بندی، «روز انتخاب» از ساعت ۱۴:۱۰ تا ۲۰:۱۰ به معرفی و رونمایی محصولات و پلتفرم‌های هوشمند اختصاص دارد؛ از جمله: ساخت تولید محتوای دیجیتال با هوش مصنوعی، بازی آنلاین موبایلی چندنفره، اپلیکیشن‌ساز و سایت‌ساز ماجوریس، پلتفرم هوشمند انطباق با استانداردها و الزامات، پلتفرم هوش مصنوعی معاملات املاک ایران، معماری چندلایهٔ پردازش هوشمند SYNAPSE، پلتفرم هوشمند رشد ریزدونه، پلتفرم سفارش ربات‌های بازار مالی، سیستم رباتیک تفکیک زباله و ساخت فیلم با هوش مصنوعی. فضای «لانچ محصول» نیز طبق جدول از ساعت ۱۴ تا ۱۹ فعال است. منبع: https://inotex.com/programs",
        "title_en": "Selection Day and product launches at INOTEX",
        "text_en": "Per the timetable, 'Selection Day' (14:10–20:10) is dedicated to introducing and launching smart products and platforms, including: AI digital-content creation, a multiplayer mobile game, the Majooris app/site builder, a smart standards-compliance platform, an Iranian real-estate AI trading platform, the SYNAPSE multi-layer intelligent processing architecture, the Rizdooneh smart growth platform, a marketplace-finance robot ordering platform, a robotic waste-sorting system, and AI filmmaking. The 'Product Launch' space also runs 14:00–19:00 per the timetable. Source: https://inotex.com/programs",
        "video_url": "",
    },
]

# Ids of the 2026 program block above (everything after inotex-news).
# scripts/import-inotex-programs.py upserts exactly these ids — and nothing
# else — into an already-installed database.
INOTEX_2026_PROGRAM_IDS = frozenset({
    "inotex-schedule-2026",
    "inotex-express-2026",
    "inotex-pitch-2026-final",
    "inotex-stage-2026",
    "inotex-capital-cafe-2026",
    "inotex-investors-pavilion-2026",
    "inotex-reverse-pitch-2026",
    "inotex-fanbazar-2026",
    "inotex-ai-iot-conf-2026",
    "inotex-work-station-2026",
    "inotex-mentors-2026",
    "inotex-inonight-meetups-2026",
    "inotex-governance-forum-2026",
    "inotex-pardis-summit-2026",
    "inotex-selection-day-2026",
})

# Question → dataset-id index. Persian first, English variants at the end of
# each block. These feed the questions table, the retrieval tiers, and the
# intent-classifier training corpus.
INOTEX_QUESTIONS = [
    # --- overview ---
    ("اینوتکس چیست", "inotex-overview"),
    ("اینوتکس چیه", "inotex-overview"),
    ("نمایشگاه اینوتکس چیست", "inotex-overview"),
    ("درباره اینوتکس توضیح بده", "inotex-overview"),
    ("اینوتکس ۲۰۲۶ چیست", "inotex-overview"),
    ("نمایشگاه نوآوری و فناوری چیست", "inotex-overview"),
    ("اینوتکس چه نمایشگاهی است", "inotex-overview"),
    ("معرفی اینوتکس", "inotex-overview"),
    ("چندمین دوره اینوتکس است", "inotex-overview"),
    ("what is inotex", "inotex-overview"),
    ("tell me about inotex", "inotex-overview"),
    # --- date ---
    ("اینوتکس کی برگزار می‌شود", "inotex-date"),
    ("تاریخ برگزاری اینوتکس", "inotex-date"),
    ("اینوتکس کی شروع می‌شود", "inotex-date"),
    ("زمان برگزاری نمایشگاه اینوتکس", "inotex-date"),
    ("اینوتکس چه تاریخی است", "inotex-date"),
    ("اینوتکس امسال کی است", "inotex-date"),
    ("چه روزهایی اینوتکس برگزار می‌شود", "inotex-date"),
    ("اینوتکس تا کی ادامه دارد", "inotex-date"),
    ("اینوتکس چند روز است", "inotex-date"),
    ("when is inotex", "inotex-date"),
    ("inotex dates", "inotex-date"),
    # --- venue ---
    ("اینوتکس کجا برگزار می‌شود", "inotex-venue"),
    ("محل برگزاری اینوتکس", "inotex-venue"),
    ("آدرس اینوتکس", "inotex-venue"),
    ("نشانی نمایشگاه اینوتکس", "inotex-venue"),
    ("اینوتکس کجاست", "inotex-venue"),
    ("مکان نمایشگاه کجاست", "inotex-venue"),
    ("پارک فناوری پردیس کجاست", "inotex-venue"),
    ("چطور به اینوتکس برسم", "inotex-venue"),
    ("where is inotex held", "inotex-venue"),
    ("inotex location", "inotex-venue"),
    # --- hours ---
    ("ساعت بازدید اینوتکس", "inotex-hours"),
    ("ساعات کاری نمایشگاه", "inotex-hours"),
    ("نمایشگاه از چه ساعتی باز است", "inotex-hours"),
    ("چه ساعتی می‌توانم بازدید کنم", "inotex-hours"),
    ("ساعت شروع و پایان نمایشگاه", "inotex-hours"),
    ("تا چه ساعتی باز است", "inotex-hours"),
    ("visiting hours of inotex", "inotex-hours"),
    # --- booth ---
    ("چطور غرفه رزرو کنم", "inotex-booth"),
    ("رزرو غرفه اینوتکس", "inotex-booth"),
    ("ثبت‌نام غرفه", "inotex-booth"),
    ("هزینه غرفه اینوتکس چقدر است", "inotex-booth"),
    ("قیمت غرفه", "inotex-booth"),
    ("تعرفه غرفه‌های نمایشگاه", "inotex-booth"),
    ("غرفه مجازی چیست", "inotex-booth"),
    ("انواع غرفه‌های اینوتکس", "inotex-booth"),
    ("می‌خواهم در اینوتکس غرفه بگیرم", "inotex-booth"),
    ("how to reserve a booth", "inotex-booth"),
    ("booth prices", "inotex-booth"),
    # --- programs ---
    ("برنامه‌های اینوتکس چیست", "inotex-programs"),
    ("چه رویدادهایی برگزار می‌شود", "inotex-programs"),
    ("برنامه رویداد", "inotex-programs"),
    ("رویدادهای جانبی اینوتکس", "inotex-programs"),
    ("استیج اینوتکس چیست", "inotex-programs"),
    ("کافه سرمایه چیست", "inotex-programs"),
    ("فروم حکمرانی چیست", "inotex-programs"),
    ("پاویون مشاوران چیست", "inotex-programs"),
    ("همایش هوش مصنوعی اینوتکس", "inotex-programs"),
    ("ریورس پیچ چیست", "inotex-programs"),
    ("ورود به برنامه‌ها رایگان است", "inotex-programs"),
    ("inotex programs", "inotex-programs"),
    ("inotex side events", "inotex-programs"),
    # --- pitch ---
    ("اینوتکس پیچ چیست", "inotex-pitch"),
    ("رقابت استارتاپی اینوتکس", "inotex-pitch"),
    ("پیچ و بتل چیست", "inotex-pitch"),
    ("مسابقه استارتاپ‌ها کی برگزار می‌شود", "inotex-pitch"),
    ("چطور در اینوتکس پیچ شرکت کنم", "inotex-pitch"),
    ("مراحل مقدماتی اینوتکس پیچ", "inotex-pitch"),
    ("ارائه به سرمایه‌گذاران", "inotex-pitch"),
    ("what is inotex pitch", "inotex-pitch"),
    # --- contact ---
    ("شماره تماس دبیرخانه", "inotex-contact"),
    ("تلفن اینوتکس", "inotex-contact"),
    ("ایمیل اینوتکس", "inotex-contact"),
    ("راه ارتباط با اینوتکس", "inotex-contact"),
    ("آدرس دبیرخانه اینوتکس", "inotex-contact"),
    ("با کی تماس بگیرم", "inotex-contact"),
    ("contact inotex", "inotex-contact"),
    ("inotex email", "inotex-contact"),
    # --- exhibitors ---
    ("چه کسانی می‌توانند غرفه‌دار شوند", "inotex-exhibitors"),
    ("شرایط غرفه‌دار شدن", "inotex-exhibitors"),
    ("استارتاپ‌ها می‌توانند شرکت کنند", "inotex-exhibitors"),
    ("شرکت دانش‌بنیان می‌تواند غرفه بگیرد", "inotex-exhibitors"),
    ("مشارکت‌کنندگان اینوتکس چه کسانی هستند", "inotex-exhibitors"),
    ("who can exhibit at inotex", "inotex-exhibitors"),
    # --- visitors ---
    ("چطور برای بازدید ثبت‌نام کنم", "inotex-visitors"),
    ("ثبت‌نام بازدیدکنندگان", "inotex-visitors"),
    ("بلیط اینوتکس چقدر است", "inotex-visitors"),
    ("ورودیه نمایشگاه", "inotex-visitors"),
    ("بازدید رایگان است", "inotex-visitors"),
    ("چطور بازدید کنم", "inotex-visitors"),
    ("how to visit inotex", "inotex-visitors"),
    ("inotex tickets", "inotex-visitors"),
    # --- stats ---
    ("چند شرکت در اینوتکس حضور دارند", "inotex-stats"),
    ("چند بازدیدکننده دارد", "inotex-stats"),
    ("آمار اینوتکس", "inotex-stats"),
    ("چند سرمایه‌گذار می‌آیند", "inotex-stats"),
    ("جلسات b2b اینوتکس", "inotex-stats"),
    ("how many companies attend inotex", "inotex-stats"),
    # --- app ---
    ("برنامه زمانی رویدادها", "inotex-app"),
    ("اپلیکیشن اینوتکس", "inotex-app"),
    ("از کجا برنامه رویدادها را ببینم", "inotex-app"),
    ("دانلود اپ اینوتکس", "inotex-app"),
    ("inotex app", "inotex-app"),
    ("inotex schedule", "inotex-app"),
    # --- volunteer ---
    ("ثبت‌نام داوطلبان", "inotex-volunteer"),
    ("چطور داوطلب شوم", "inotex-volunteer"),
    ("همکاری داوطلبانه در اینوتکس", "inotex-volunteer"),
    ("volunteer at inotex", "inotex-volunteer"),
    # --- organizers ---
    ("برگزارکننده اینوتکس کیست", "inotex-organizers"),
    ("چه نهادی اینوتکس را برگزار می‌کند", "inotex-organizers"),
    ("حامیان اینوتکس", "inotex-organizers"),
    ("who organizes inotex", "inotex-organizers"),
    # --- targeted visit ---
    ("بازدید هدفمند چیست", "inotex-targeted-visit"),
    ("بازدید هوشمند چیست", "inotex-targeted-visit"),
    ("چطور غرفه‌های مرتبط با کارم را پیدا کنم", "inotex-targeted-visit"),
    ("کدام غرفه‌ها به من مربوط است", "inotex-targeted-visit"),
    ("پیشنهاد غرفه بر اساس شغل", "inotex-targeted-visit"),
    ("چرا باید اطلاعاتم را وارد کنم", "inotex-targeted-visit"),
    ("زمان کمی دارم چه چیزی را ببینم", "inotex-targeted-visit"),
    ("what is a targeted visit", "inotex-targeted-visit"),
    ("recommend booths for my job", "inotex-targeted-visit"),
    # --- news ---
    ("اخبار اینوتکس", "inotex-news"),
    ("آخرین اطلاعیه‌ها", "inotex-news"),
    ("بلاگ اینوتکس", "inotex-news"),
    ("inotex news", "inotex-news"),
    # --- 2026 schedule (crawled 2026-08-27) ---
    ("جدول زمان‌بندی اینوتکس", "inotex-schedule-2026"),
    ("برنامه هر روز نمایشگاه چیست", "inotex-schedule-2026"),
    ("برنامه زمانی کامل رویدادها کجاست", "inotex-schedule-2026"),
    ("ساعت افتتاحیه چند است", "inotex-schedule-2026"),
    ("اختتامیه چه ساعتی است", "inotex-schedule-2026"),
    ("بازی استقلال و پرسپولیس در اینوتکس", "inotex-schedule-2026"),
    ("inotex timetable", "inotex-schedule-2026"),
    # --- inotex express ---
    ("اینوتکس اکسپرس چیست", "inotex-express-2026"),
    ("چرا اینوتکس عصرها برگزار می‌شود", "inotex-express-2026"),
    ("چند مجموعه در اینوتکس غرفه دارد", "inotex-express-2026"),
    ("inotex express", "inotex-express-2026"),
    # --- pitch 2026 final ---
    ("فینال اینوتکس‌پیچ کی برگزار می‌شود", "inotex-pitch-2026-final"),
    ("جوایز اینوتکس‌پیچ چقدر است", "inotex-pitch-2026-final"),
    ("بتل اینوتکس چیست", "inotex-pitch-2026-final"),
    ("چهارده تیم فینالیست اینوتکس‌پیچ", "inotex-pitch-2026-final"),
    ("مراحل استانی اینوتکس‌پیچ کجا برگزار شد", "inotex-pitch-2026-final"),
    ("inotex pitch final", "inotex-pitch-2026-final"),
    # --- stage 2026 ---
    ("برنامه استیج اینوتکس امسال چیست", "inotex-stage-2026"),
    ("استیج اینوتکس چند سخنران دارد", "inotex-stage-2026"),
    ("وطن‌های متصل چیست", "inotex-stage-2026"),
    ("inotex stage program", "inotex-stage-2026"),
    # --- capital cafe 2026 ---
    ("کافه سرمایه چگونه کار می‌کند", "inotex-capital-cafe-2026"),
    ("چطور در کافه سرمایه ثبت‌نام کنم", "inotex-capital-cafe-2026"),
    ("هر استارتاپ پنج سرمایه‌گذار یعنی چه", "inotex-capital-cafe-2026"),
    ("کارگاه‌های کافه سرمایه چیست", "inotex-capital-cafe-2026"),
    ("capital cafe inotex", "inotex-capital-cafe-2026"),
    # --- investors pavilion ---
    ("پاویون سرمایه‌گذاران چه امکاناتی دارد", "inotex-investors-pavilion-2026"),
    ("سرمایه‌گذاران در اینوتکس چه می‌کنند", "inotex-investors-pavilion-2026"),
    ("ثبت‌نام سرمایه‌گذاران اینوتکس", "inotex-investors-pavilion-2026"),
    ("investors pavilion inotex", "inotex-investors-pavilion-2026"),
    # --- reverse pitch 2026 ---
    ("ریورس‌پیچ امسال چه نیازهایی دارد", "inotex-reverse-pitch-2026"),
    ("نیازهای فناورانه صنایع کجا اعلام می‌شود", "inotex-reverse-pitch-2026"),
    ("ریورس‌پیچ نفت و گاز", "inotex-reverse-pitch-2026"),
    ("reverse pitch inotex", "inotex-reverse-pitch-2026"),
    # --- fanbazar ---
    ("فن‌بازار در اینوتکس چه برنامه‌ای دارد", "inotex-fanbazar-2026"),
    ("نشست فن‌بازارهای کشور کی است", "inotex-fanbazar-2026"),
    ("تورهای فناوری چیست", "inotex-fanbazar-2026"),
    ("fanbazar inotex", "inotex-fanbazar-2026"),
    # --- ai iot conference ---
    ("پنل‌های کنفرانس هوش مصنوعی چیست", "inotex-ai-iot-conf-2026"),
    ("کنفرانس اینترنت اشیا اینوتکس", "inotex-ai-iot-conf-2026"),
    ("پنل فین‌تک هوشمند", "inotex-ai-iot-conf-2026"),
    ("ai iot conference inotex", "inotex-ai-iot-conf-2026"),
    # --- work station ---
    ("ایستگاه کار چیست", "inotex-work-station-2026"),
    ("فرصت‌های شغلی در اینوتکس", "inotex-work-station-2026"),
    ("کارجویان در اینوتکس چه برنامه‌ای دارند", "inotex-work-station-2026"),
    ("کافه کار چیست", "inotex-work-station-2026"),
    ("jobs at inotex", "inotex-work-station-2026"),
    # --- mentors pavilion ---
    ("پاویون مشاوران چگونه کار می‌کند", "inotex-mentors-2026"),
    ("منتورشیپ اینوتکس", "inotex-mentors-2026"),
    ("مشاوره کسب‌وکار در اینوتکس", "inotex-mentors-2026"),
    ("mentoring at inotex", "inotex-mentors-2026"),
    # --- inonight / meetups ---
    ("اینونایت چیست", "inotex-inonight-meetups-2026"),
    ("میت‌آپ‌های اینوتکس چیست", "inotex-inonight-meetups-2026"),
    ("برنامه شبانه اینوتکس", "inotex-inonight-meetups-2026"),
    ("میت‌آپ استارتاپی", "inotex-inonight-meetups-2026"),
    ("inonight", "inotex-inonight-meetups-2026"),
    # --- governance forum ---
    ("فروم حکمرانی چه برنامه‌ای دارد", "inotex-governance-forum-2026"),
    ("پنل قانون جهش تولید دانش‌بنیان", "inotex-governance-forum-2026"),
    ("قانون‌گذاری فناوری‌های نوظهور در اینوتکس", "inotex-governance-forum-2026"),
    ("governance forum inotex", "inotex-governance-forum-2026"),
    # --- pardis summit ---
    ("پردیس سامیت چیست", "inotex-pardis-summit-2026"),
    ("شتاب‌دهنده‌ها در اینوتکس چه نقشی دارند", "inotex-pardis-summit-2026"),
    ("pardis summit", "inotex-pardis-summit-2026"),
    # --- selection day / launches ---
    ("روز انتخاب چیست", "inotex-selection-day-2026"),
    ("رونمایی محصول در اینوتکس", "inotex-selection-day-2026"),
    ("لانچ محصول اینوتکس", "inotex-selection-day-2026"),
    ("product launch inotex", "inotex-selection-day-2026"),
]

# Persian normalization helpers. Sourced here so both init_db() and the
# operator reset script share a single source of truth. Brand-specific rows
# come first; the generic rows are shared Persian-retrieval vocabulary.
INOTEX_SYNONYMS = [
    # Identity tokens expand MINIMALLY: "اینوتکس" appears in nearly every
    # title, so a wide expansion floods the token-overlap tier and makes
    # unrelated entries look like near-exact matches (measured on the golden
    # set: recall@1 drops when this expansion is wide).
    ("اینوتکس", "اینوتکس inotex"),
    ("اینو تکس", "اینوتکس inotex"),
    ("inotex", "inotex اینوتکس"),
    ("نمایششگاه", "نمایشگاه"),
    ("پیچ", "پیچ اینوتکس‌پیچ رقابت استارتاپی pitch"),
    ("اینوتکس پیچ", "اینوتکس‌پیچ پیچ رقابت استارتاپی بتل"),
    ("بتل", "پیچ رقابت مسابقه battle"),
    ("پردیس", "پارک فناوری پردیس پردیس"),
    ("استیج", "استیج صحنه سخنرانی stage"),
    ("ثبت نام", "ثبت‌نام ثبتنام نام‌نویسی رجیستر"),
    ("ثبت‌نام", "ثبت نام ثبتنام نام‌نویسی"),
    ("ثبتنام", "ثبت نام ثبت‌نام"),
    ("بلیت", "بلیط ورودی ورودیه هزینه ورود"),
    ("بلیط", "بلیت ورودی ورودیه هزینه ورود"),
    ("ورودیه", "بلیط بلیت هزینه ورود"),
    ("آدرس", "نشانی محل مکان کجا ادرس"),
    ("ادرس", "آدرس نشانی محل مکان"),
    ("نشانی", "آدرس محل مکان کجا"),
    ("مکان", "محل آدرس نشانی کجا"),
    ("کجاست", "کجا محل آدرس مکان"),
    ("تاریخ", "زمان کی چه زمانی موعد"),
    ("زمان", "تاریخ کی چه زمانی"),
    ("ساعت", "ساعات وقت زمان بازدید"),
    ("ساعات", "ساعت وقت زمان"),
    ("تلفن", "شماره تماس شماره تلفن تماس"),
    ("شماره", "تلفن شماره تماس تماس"),
    ("تماس", "تلفن شماره ارتباط"),
    ("ایمیل", "پست الکترونیک میل email"),
    ("غرفه", "غرفه استند booth فضای نمایشگاهی"),
    ("غرفه دار", "غرفه‌دار مشارکت کننده مشارکت‌کننده نمایشگر"),
    ("غرفه‌دار", "غرفه دار مشارکت‌کننده شرکت‌کننده"),
    ("مشارکت کنندگان", "مشارکت‌کنندگان شرکت‌کنندگان غرفه‌داران شرکت کنندگان"),
    ("شرکت کنندگان", "شرکت‌کنندگان مشارکت‌کنندگان غرفه‌داران"),
    ("بازدید کننده", "بازدیدکننده بازدیدکنندگان visitor مهمان"),
    ("بازدیدکنندگان", "بازدید کنندگان بازدیدکننده visitor"),
    ("هزینه", "قیمت تعرفه مبلغ نرخ"),
    ("قیمت", "هزینه تعرفه مبلغ نرخ"),
    ("تعرفه", "هزینه قیمت نرخ مبلغ"),
    ("استارتاپ", "استارت‌آپ استارت آپ نوپا کسب و کار نوپا"),
    ("استارت آپ", "استارتاپ استارت‌آپ نوپا"),
    ("دانش بنیان", "دانش‌بنیان دانشبنیان فناور"),
    ("دانش‌بنیان", "دانش بنیان فناور شرکت فناور"),
    ("سرمایه گذار", "سرمایه‌گذار سرمایه گذاران اینوستور investor"),
    ("سرمایه‌گذار", "سرمایه گذار سرمایه‌گذاران investor"),
    ("شتابدهنده", "شتاب‌دهنده اکسلراتور accelerator"),
    ("سمینار", "همایش کنفرانس نشست پنل سخنرانی"),
    ("همایش", "سمینار کنفرانس نشست"),
    ("هوش مصنوعی", "هوش‌مصنوعی ai هوش"),
    ("اینترنت اشیا", "اینترنت‌اشیا iot اشیا"),
    ("خبر", "اخبار اطلاعیه اطلاعیه‌ها"),
    ("اطلاعیه", "اخبار خبر اطلاعیه‌ها"),
    ("اپلیکیشن", "اپ برنامه اپلیکیشن application app"),
    ("هدفمند", "هدفمند هوشمند مرتبط متناسب"),
    ("علاقه", "علاقه‌مندی علاقمندی زمینه حوزه"),
    ("شغل", "حرفه کار تخصص سمت"),
    ("داوطلب", "داوطلبان داوطلبانه همکاری volunteer"),
    ("سایت", "وبسایت وب سایت وب‌سایت website"),
    ("انگلیسی", "english انگلیسی en"),
    ("برگزار کننده", "برگزارکننده مجری ستاد سازمان"),
    ("خبرنگار", "رسانه مطبوعات روابط عمومی"),
    ("تامین مالی", "تأمین مالی سرمایه فاندینگ جذب سرمایه"),
    ("جمع سپاری", "جمع‌سپاری تامین مالی جمعی کرادفاندینگ crowdfunding"),
]

# 2026 program vocabulary added with the crawled program/news block above.
# Kept as its own list so scripts/import-inotex-programs.py can add exactly
# these rows to an already-installed database.
INOTEX_2026_PROGRAM_SYNONYMS = [
    ("ریورس", "ریورس پیچ ریورس‌پیچ reverse"),
    ("ریورس پیچ", "ریورس‌پیچ نیازهای فناورانه reverse pitch"),
    ("میت آپ", "میت‌آپ میتاپ جلسه تخصصی meetup"),
    ("میت‌آپ", "میت آپ میتاپ meetup"),
    ("اینونایت", "اینو نایت inonight شب شبکه‌سازی"),
    ("فن بازار", "فن‌بازار فنبازار"),
    ("فن‌بازار", "فن بازار فنبازار"),
    ("زمان بندی", "زمان‌بندی زمانبندی برنامه زمانی جدول"),
    ("زمان‌بندی", "زمان بندی زمانبندی برنامه زمانی جدول"),
    ("کارجو", "کارجویان جویای کار استخدام job"),
    ("منتور", "منتورها منتورشیپ مشاور mentor"),
    ("شتاب دهنده", "شتاب‌دهنده شتابدهنده اکسلراتور accelerator"),
]

INOTEX_SYNONYMS.extend(INOTEX_2026_PROGRAM_SYNONYMS)


def seed_default_content(cursor) -> None:
    """Seed once, and only when a fresh installation has no content."""
    if cursor.execute("SELECT COUNT(*) FROM dataset").fetchone()[0]:
        return

    # The order of INOTEX_DATASET is the curated reading order the visitor
    # sees (overview, then date, venue, hours, ...). It used to survive only
    # as SQLite's insertion rowid, which is why it had to be reconstructed by
    # hand in migration 0004. Writing it down explicitly here means a fresh
    # install gets the intended order on either backend.
    cursor.executemany(
        "INSERT INTO dataset (id, title, text, video_url, title_en, text_en, position)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(item["id"], item["title"], item["text"], item["video_url"],
          item.get("title_en", ""), item.get("text_en", ""), (i + 1) * 10)
         for i, item in enumerate(INOTEX_DATASET)],
    )
    cursor.executemany(
        "INSERT INTO questions (question, dataset_id, video_url) VALUES (?, ?, '')",
        INOTEX_QUESTIONS,
    )


def seed_default_synonyms(cursor) -> None:
    """Seed default INOTEX synonyms, only when the table is empty."""
    if cursor.execute("SELECT COUNT(*) FROM synonyms").fetchone()[0]:
        return
    cursor.executemany(
        "INSERT INTO synonyms (source, target) VALUES (?, ?)",
        INOTEX_SYNONYMS,
    )
