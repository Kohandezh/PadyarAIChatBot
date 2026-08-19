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
        "text": "بازدید هدفمند یعنی به‌جای گشتن تصادفی در نمایشگاه، ابتدا غرفه‌ها و برنامه‌های مرتبط با کار و علاقهٔ خودتان را بشناسید. با زدن دکمهٔ «بازدید هوشمند» و تکمیل شغل، سمت و زمینه‌های مورد علاقه‌تان، دستیار می‌تواند غرفه‌ها و رویدادهای مرتبط را به شما معرفی کند تا در زمان محدودِ بازدید، اول سراغ چیزهایی بروید که به کارتان می‌آید. تکمیل این اطلاعات اختیاری است و بدون آن هم می‌توانید هر سؤالی درباره اینوتکس بپرسید. پیشنهادها بر اساس بخش‌ها و برنامه‌های رسمی اعلام‌شده در https://inotex.com/ ساخته می‌شود؛ فهرست غرفه‌داران هنوز روی سایت رسمی منتشر نشده است.",
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
]

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
