> **Note (2026-10-29):** this draft was written on 2026-09-30, before PRs #145-#185. It is outdated. Update it from `STATUS.md` before using it.

# پیش‌نویس پاسخ فرم شماره 1، درخواست بررسی مجدد دانش‌بنیان (محصول: پادیار)

> **[baraye Sina, na baraye form]** In file pish-nevis ast. Har bakhsh in shekl ra darad:
> soal -> matn-e pishnahadi (Farsi-ye rasmi) -> shavahed (baraye check-e shoma) -> "vabaste be PR-e merge-nashode?".
> Faghat matn-e zir-e "پاسخ" be Google Doc miravad. Shavahed va yaddasht-ha na.
> Tarikh-e snapshot: 2026-09-30, commit-e main `3a4a415`. Hich PR-i az track-haye 1..7 hanuz baz nashode.
> Har `[...]` yek jaye khali ast ke faghat ba adad-e vaghei ya tasmim-e shoma por mishavad.

---

## 0. Mavared-e ghabl az har chiz (baraye Sina)

1. **Band-e 4 dar safhe-ye avval-e form:** «شرکتهای که تمایل به بررسی فنی ذیل حوزههای ... نرم افزار را دارند، نیازی به تکمیل فرم شماره 1 ندارند. باید در قالب یک نامه درخواست خود را در سامانه بررسی مجدد ارسال نمایند». Yani baraye hoze-ye narm-afzar shayad masir-e dorost **name + porseshname-ye makhsus** bashad, na form-e 1. Lotfan ghabl az ersal ba dabirkhane check konid. Man tebgh-e brief form-e 1 ra neveshtam; matn-ha dar porseshname-ye narm-afzar ham ghabel-e estefade hastand.
2. **Porkonande-ye form:** form migoyad «تکمیل کننده فرم باید نفر فنی شرکت بوده». Alan «محمد علی کهن دژ، مدیرعامل» neveshte shode. Tasmim ba shomast.
3. **Chand chiz dar repo ke evaluator mitavanad bebinad va ba matn-e ma tanaghoz darad** (ghabl az ersal bayad dorost shavad; kar-e track 6 va 7 ast, man dast nazadam):
   - `CHANGELOG.md` migoyad 0.1.0 «first tagged release» ast. Hich tag-i va hich GitHub Release-i vojud nadarad. Hamchenin migoyad CI «eval gate» darad; commit `4fa5c47` an ra hazf karde.
   - `ci.yml`, `DEPLOYMENT_RUNBOOK.md` va `README.md` migoyand har deploy approval-e reviewer mikhahad. Environment-e `production` dar GitHub hich protection rule-i nadarad.
   - Deploy-e khodkar az 2026-09-19 kharab ast: `DEPLOY_SLUG and DEPLOY_PORT repository variables are not set`.
   - `SECURITY_MODEL.md` hanuz «SHA-256+salt» migoyad; code bcrypt ast.
   - Bakhsh-e restore dar `DEPLOYMENT_RUNBOOK.md` hanuz SQLite-ist.
   - Backup-e off-site be tor-e pishfarz khamush ast (`OFFSITE_BACKUP_TARGET` comment shode).
   - Branch protection ruye `main` nist (404). 0 az 140 PR-e merge-shode review-e GitHub darad.
4. Man dalil-haye rad ra az tarjome-ye `_common.md` bardashtam. Baraye bakhsh-e «د» matn-e dagigh-e payam-e dabirkhane (kartabl -> archive -> payam-e dabirkhane) ra check konid ke tartib va kalamat yeki bashad.

---

## 1. حوزه فناوری محصول

**پاسخ:** معیارهای تفصیلی حوزه فناوری اطلاعات و ارتباطات و نرم‌افزارهای رایانه‌ای. ☑

> [Sina] Form ye «این» kenar-e satr-e «داروهای پیشرفته» darad ke mesl-e bagh-mande-ye template ast. Dast nazadam.

---

## 2. الف- مرحله تولید

**سؤال:** دلایل تایید مرحله تولید را توضیح دهید (برای خدمات: فروش و اسناد فروش). شرحی از محصول، اجزا و کارکردهای آن.

**پاسخ پیشنهادی:**

پادیار یک سامانه مدیریت محتوای دستیار هوشمند گفتگو (چت‌بات) به زبان فارسی و انگلیسی است که به‌صورت نصب اختصاصی (On-Premise، هر مشتری یک نصب مستقل) روی سرور مشتری مستقر می‌شود. مشتری دانش خود (پرسش و پاسخ، اطلاعات سازمان، ویدئو) را در پنل مدیریت وارد یا بارگذاری می‌کند و دستیار به پرسش کاربران نهایی به‌صورت متنی، صوتی و ویدئویی پاسخ می‌دهد.

اجزای اصلی محصول:

1. **موتور پاسخ چندلایه (Tiered Answer Pipeline):** هر پرسش ابتدا از لایه‌های محلی و قطعی عبور می‌کند و فقط در صورت کافی نبودن اطمینان، به مدل زبانی ارجاع می‌شود. لایه‌ها عبارت‌اند از: تشخیص انتخاب از فهرست قبلی، تطبیق با بانک پرسش‌ها، بازیابی ترکیبی لغوی و معنایی، دسته‌بند قصد آموزش‌دیده روی داده همان مشتری، و در انتها لایه انتخاب با مدل زبانی. (پیاده‌سازی: `app/routers/chat.py`)
2. **پردازش متن فارسی:** یکسان‌سازی نویسه‌های عربی و فارسی، مدیریت نیم‌فاصله، حذف عبارات احوالپرسی از پرسش، و گسترش مترادف‌ها از جدول مترادف‌های مشتری در یک گذر. (`app/utils/normalizer.py`)
3. **بازیابی ترکیبی:** بازیاب لغوی Okapi BM25 پیاده‌سازی‌شده در داخل شرکت، بازیاب معنایی با بردارهای جاسازی محلی (model2vec، بدون فراخوانی سرویس خارجی) و بازرتبه‌ساز ویژگی‌محور که نتایج دو بازیاب را تلفیق می‌کند. (`app/services/bm25.py`، `embeddings.py`، `rerank.py`)
4. **دسته‌بند قصد اختصاصی هر نصب:** یک مدل رگرسیون لجستیک که روی پرسش‌های همان مشتری آموزش می‌بیند و با هر تغییر در دانش، خودکار بازآموزی می‌شود. (`app/services/intent.py`)
5. **لایه انتخاب زمین‌خورده (Grounded Selection):** مدل زبانی فقط شناسه رکوردهای پیشنهادی را انتخاب می‌کند و متن پاسخ از پایگاه داده نوشته می‌شود؛ دو دیوار کنترلی مانع ورود عدد، پیوند یا نام ساختگی به پاسخ می‌شوند. (`app/services/answer.py`)
6. **صفحه کنترل ارائه‌دهندگان هوش مصنوعی:** مسیریابی هر وظیفه بین چند ارائه‌دهنده (10 نوع ارائه‌دهنده فعال، شامل هر سرور سازگار با OpenAI API مانند مدل محلی)، قطع‌کننده مدار مشترک بین پردازش‌ها، تلاش مجدد و جایگزینی، و ثبت هزینه و توکن. (`app/services/ai/`)
7. **گفتار:** تبدیل متن به گفتار فارسی روی کارت‌های گرافیک خود سرور با امکان شبیه‌سازی صدا و فرهنگ تلفظ قابل ویرایش؛ و ورودی صوتی کاربر. (`app/routers/tts.py`، `app/services/tts_lexicon.py`، `deploy/tts/`)
8. **پنل مدیریت و ماژول‌ها:** مدیریت دانش، مترادف‌ها، گفتگوها و صف پاسخ‌های نادرست، برندینگ سفید (White-label)، پشتیبان‌گیری و بازگردانی، پایش، گزارش‌ها، ثبت‌نام با پیامک و ماژول جذب سرنخ. معماری ماژولار است: 6 ماژول هسته و 9 ماژول اختیاری که در زمان نصب برای هر مشتری فعال می‌شوند. (`app/modules/registry.py`)
9. **زیرساخت:** PostgreSQL 16، مهاجرت‌های نسخه‌دار، پشتیبان‌گیری خودکار، اسکریپت‌های استقرار و پایش. (توضیح در بخش «د»)

دلایل تایید مرحله تولید: [شرح فروش / قرارداد / نصب عملیاتی، توسط شرکت تکمیل شود]. محصول در حال حاضر روی سرور عملیاتی نصب و از طریق اینترنت در دسترس است. فیلم کوتاه دمو در ضمایم سامانه بارگذاری می‌شود.

**شواهد:** مسیرهای ذکرشده در متن؛ `app/` شامل 112 ماژول پایتون و حدود 38,000 خط کد؛ `docs/engineering/ARCHITECTURE.md`.

> [Sina] **Sale va gharardad kar-e shomast**: form baraye khadamat sanad-e forush (gharardad, factor) mikhahad. Man hich moshtari ya gharardadi nanveshtam. Az memory midanam 2 nasb ruye gpuserver live ast; agar mikhahid esm-e moshtari ya namayeshgah biayad, khodetan benevisid.
> Film-e demo ham kar-e shomast.
> Vabaste be PR? Na. Hame-ye in ajza ruye `main` hastand. Faghat «مدل محلی» dar band-e 6 (track 3): adapter-e `openai_compatible` ruye main hast, vali nasb-e model-e mahalli hanuz anjam nashode.

---

## 3. ب- سطح فناوری

### 3.1 کدام بخش یا فناوری در محصول دارای پیچیدگی فنی است؟ چرا؟

**پاسخ پیشنهادی:**

پیچیدگی اصلی محصول در **موتور پاسخ‌گویی قابل اتکا به زبان فارسی روی داده اختصاصی هر مشتری** است؛ یعنی پاسخ درست بدهد، پاسخ نادرست با اطمینان ندهد، و وابستگی به سرویس خارجی را به حداقل برساند. این پیچیدگی در چهار بخش است:

1. **بازیابی ترکیبی و بازرتبه‌سازی برای زبان فارسی.** زبان فارسی نیم‌فاصله، نویسه‌های عربی هم‌شکل، املای چندگانه و ترکیب واژه فارسی و انگلیسی دارد. بازیاب لغوی به‌تنهایی مترادف را نمی‌فهمد و بازیاب معنایی به‌تنهایی نام خاص و عدد را از دست می‌دهد. محصول دو بازیاب مستقل (Okapi BM25 با پارامترهای k1=1.5 و b=0.75، و بردارهای جاسازی چندزبانه محلی با کالیبراسیون امتیاز شباهت) را اجرا می‌کند و بازرتبه‌ساز ویژگی‌محور آن‌ها را با چهار ویژگی تلفیق می‌کند: امتیاز معنایی، امتیاز لغوی، پوشش واژه‌های محتوایی پرسش، و توافق دو بازیاب. نامزدی که هیچ واژه محتوایی پرسش را پوشش ندهد جریمه می‌شود. این طراحی با اندازه‌گیری روی مجموعه ارزیابی انتخاب شده است (بند 3.2).
2. **دروازه‌های اطمینان و تصمیم «پاسخ بدهم یا نه».** سخت‌ترین مسئله در چت‌بات سازمانی، پاسخ نادرست با اطمینان است. موتور پیش از رسیدن به مدل زبانی، چند دروازه قطعی دارد: تشخیص نام ناشناخته (واژه‌ای که در دانش مشتری نیست، با تحمل یک خطای املایی)، تشخیص متن بی‌معنا، تشخیص پرسش مبهم که به دو موجودیت اشاره دارد، و آستانه‌های کالیبره‌شده برای هر لایه (0.70 برای پاسخ مستقیم، 0.6 برای دسته‌بند قصد). وقتی مدل زبانی در دسترس نیست، سیستم فقط بالای آستانه‌های جداگانه (0.45 و 0.60) پاسخ محلی می‌دهد و در غیر این صورت صادقانه اعلام می‌کند که پاسخ ندارد.
3. **جلوگیری از توهم مدل زبانی (Grounding).** در لایه انتخاب، مدل زبانی متن پاسخ نمی‌نویسد؛ فقط از میان حداکثر 8 رکورد بازیابی‌شده، شناسه انتخاب می‌کند و خروجی JSON ساخت‌یافته برمی‌گرداند. هر شناسه‌ای که در مجموعه نامزدها نباشد حذف می‌شود و تمام محتوای پاسخ از پایگاه داده نوشته می‌شود. برای جمله کوتاهی که مدل بالای فهرست می‌نویسد، دیوار کنترلی اول شش شرط را بررسی می‌کند (طول، نبود عدد، نبود پیوند، نبود عدد حروفی، مجاز بودن تک‌تک واژه‌ها، نبود نام رکورد). برای پاسخ متنی آزاد، دیوار دوم هر عدد و پیوند را با واقعیت‌های ثبت‌شده مقایسه می‌کند و پاسخ ناهمخوان رد می‌شود. محدودیت: ادعای نادرست بدون عدد یا پیوند را این دیوار تشخیص نمی‌دهد و این محدودیت در مستندات ثبت شده است.
4. **مکالمه چندنوبتی روی کیوسک مشترک.** کاربر می‌تواند «دومی»، «3» یا «بیشتر» بگوید و سیستم آن را بدون فراخوانی مدل زبانی به فهرست نوبت قبل وصل می‌کند. وضعیت فقط 15 دقیقه نگه داشته می‌شود تا کاربر بعدی کیوسک وضعیت کاربر قبلی را به ارث نبرد.

علاوه بر این، **صفحه کنترل ارائه‌دهندگان** (مسیریابی بین ارائه‌دهندگان، قطع‌کننده مدار با وضعیت مشترک در پایگاه داده بین چند پردازش، تست نیمه‌باز با قفل تک‌مالک) و **تبدیل متن به گفتار فارسی روی GPU** (یک نمونه مدل برای هر کارت گرافیک، فرهنگ تلفظ برای جبران نانوشته بودن مصوت‌های کوتاه فارسی) از نظر مهندسی سیستم پیچیده‌اند.

**شواهد:** `app/services/rerank.py` (L34-37، L125-134)، `app/services/bm25.py` (L16-17)، `app/services/embeddings.py`، `app/services/search.py` (`_within_edit1` L155، `unknown_salient_tokens` L175-206)، `app/routers/chat.py` (L407-1294)، `app/services/answer.py` (`select_records` L907-944، allowlist L988-993، `frame_is_grounded` L446-523، `generated_prose_is_grounded` L526-598)، `app/services/ai/circuit.py`، ADR-003، ADR-004، ADR-005، ADR-012، ADR-018.

> [Sina] Vabaste be PR? Na, hame ruye main ast.
> **Sadegh budan:** vazn-haye rerank (0.62 / 0.23 / 0.15) dasti tanzim shode-and, yad-gerefte nashode-and. Man nanveshtam «یادگرفته». Agar track 2 reranker-e yadgirande (learned) besazad, in jomle ra update mikonim.
> Stemming va spell-correction dar code nist; man ham nanveshtam.

### 3.2 چند مورد از خواص/عملکردهای پیچیده محصول که آن را از محصول با سطح فناوری پایین متمایز می‌کند

**پاسخ پیشنهادی:**

1. **اندازه‌گیری تکرارپذیر کیفیت بازیابی.** محصول یک مجموعه ارزیابی ثابت دارد (پیکره دوزبانه ساختگی، بدون داده واقعی مشتری، و 67 پرسش در 7 دسته شامل پرسش خارج از دامنه، تزریق دستور و بازنویسی فارسی) و یک اسکریپت که روی یک پایگاه داده موقت و بدون هیچ فراخوانی هوش مصنوعی اجرا می‌شود. نتایج اندازه‌گیری‌شده (2026-09-20): recall@1 = 0.854، recall@3 = 0.979، MRR = 0.903، تأخیر بازیابی p50 = 0.3 میلی‌ثانیه و p95 = 0.5 میلی‌ثانیه؛ صفر پاسخ با اطمینان نادرست روی پرسش‌های خارج از دامنه و تزریق، صفر نشت اطلاعات محرمانه. فرمان بازتولید: `python scripts/run_eval.py --golden data/eval/golden.json --recall-k 1,3,5,8,13`. این ارزیابی در CI اجرا می‌شود.
2. **سهم هر لایه در کیفیت (مطالعه حذفی).** جدول مقایسه BM25 به‌تنهایی، بازیاب معنایی به‌تنهایی، ترکیب با بازرتبه‌ساز، و ترکیب به‌همراه دسته‌بند قصد: [جدول پس از اندازه‌گیری].
3. **مدل اختصاصی هر نصب.** دسته‌بند قصد روی پرسش‌های همان مشتری آموزش می‌بیند (رگرسیون لجستیک روی بردارهای جاسازی، با 15 درصد داده برای آزمون)، با هر ویرایش دانش بازآموزی می‌شود، و اگر دقت آزمون زیر 0.7 باشد، آستانه اعتماد خودکار به 0.85 بالا می‌رود. مدل آموزش‌دیده به‌صورت فایل نسخه‌دار همراه با شناسنامه (تاریخ آموزش، تعداد نمونه، دقت آزمون، نسخه مدل جاسازی) ذخیره و دقت آن در پایش منتشر می‌شود. [دقت آزمون: عدد پس از اندازه‌گیری]
4. **اجرای کامل بدون سرویس ابری.** لایه‌های بازیابی، بازرتبه‌سازی و دسته‌بند قصد کاملاً محلی‌اند. لایه مدل زبانی از طریق آداپتور سازگار با OpenAI API به مدل زبانی متن‌باز روی کارت‌های گرافیک سرور مشتری (llama.cpp) متصل می‌شود و ارائه‌دهنده ابری فقط جایگزین اختیاری است. [سرعت تولید توکن، تأخیر p50/p95 و نرخ JSON معتبر: عدد پس از اندازه‌گیری روی سخت‌افزار هدف]
5. **پاسخ ساخت‌یافته و قابل ممیزی.** هر پاسخ همراه با لایه‌ای که آن را تولید کرده، امتیاز اطمینان، شناسه رکورد، توکن و هزینه ثبت می‌شود و در پنل مدیریت صف «پاسخ‌های نادرست» برای اصلاح دانش وجود دارد.
6. **تاب‌آوری عملیاتی.** قطع‌کننده مدار (5 خطا در 120 ثانیه، سرد شدن 60 ثانیه، خطای احراز هویت 600 ثانیه)، جایگزینی خودکار بین ارائه‌دهندگان، و محدودکننده نرخ دوسطحی (هر نشست و هر IP) که در پایگاه داده نگه داشته می‌شود تا با راه‌اندازی مجدد یا چند پردازش از بین نرود.
7. **تبدیل متن به گفتار فارسی روی GPU خود سرور** با شبیه‌سازی صدا از نمونه 5 تا 20 ثانیه‌ای، پیش‌تولید پاسخ‌های پرتکرار، و اجرای موازی روی دو کارت Tesla P40 (اندازه‌گیری‌شده: 4 درخواست همزمان در 25.8 تا 46.4 ثانیه).

**شواهد:** Track 1 commit `420eb1e` (branch `feat/eval-golden-set`)، `DECISIONS.md` L254-296 در آن branch؛ `app/services/intent.py`، `app/services/search.py` L660-663؛ `app/services/ai/circuit.py`؛ migration `0007_security_hardening.sql`؛ ADR-012.

> [Sina] **Vabastegi-ha:**
> - Band 1: adad-ha vagheian andaze-giri shode-and (2026-09-20), vali ruye commit `420eb1e` ke **hanuz push nashode va PR nadarad**. Agar track 1 ba'd az review adad ra avaz konad, inja ham avaz shavad. Jomle-ye «این ارزیابی در CI اجرا می‌شود» faghat ba'd az merge-e track 1 dorost ast. **Ehtiat:** alan gate-e CI faghat ruye contamination va secret leak fail mishavad, na ruye recall. Track 1 bayad floor ezafe konad; agar nakard, jomle ra be «و نشت اطلاعات و آلودگی پاسخ در CI کنترل می‌شود» kam kon.
> - Recall@1 baraye soal-haye englisi shakanande ast (7 az 13 zir-e cosine floor). Dar matn nayavordam; agar evaluator soal konad, sadegh javab bedim.
> - Band 2 (ablation): track 1, hanuz andaze-giri nashode.
> - Band 3: tamrin-e model ruye main hast; «فایل نسخه‌دار همراه با شناسنامه» faghat dar track 2 ast (uncommitted, review nashode, va hanuz load/fallback nadarad). Adad-e deghat khali ast.
> - Band 4: track 3. Hich andaze-giri ruye P40 anjam nashode. STT hanuz API-e birouni ast (`whisper-1`). Agar track 3 STT-e mahalli (faster-whisper) ra ship nakonad, kalame-ye «کامل» ra bardar.
> - Band 7: adad-e TTS az ADR-012 ast, vaghei.

---

## 4. ج- طراحی مبتنی بر تحقیق و توسعه

**گزینه:** ☑ یکپارچه سازی محصول یا زیرسیستم‌های محصول

> [Sina] Baraye narm-afzar gozine-ye «فرآیند/تجهیزات پیچیده تولید» monaseb nist. Pishnahad-e man faghat gozine-ye avval ast.

### 4.1 نام دقیق بخش‌ها و فعالیت‌های طراحی و تحقیق و توسعه، مرحله به مرحله

**پاسخ پیشنهادی:**

فرایند توسعه هر قابلیت در شرکت مستند و مرحله‌ای است: پژوهش اولیه (RESEARCH/SPIKE)، مشخصات (SPEC)، سند تصمیم معماری (ADR)، برنامه اجرا، پیاده‌سازی با آزمون، بازبینی و استقرار. قالب هر مرحله در `docs/engineering/templates/` و گردش کار در `docs/engineering/FEATURE_DEVELOPMENT_WORKFLOW.md` ثبت شده است. تاکنون 21 سند تصمیم معماری ثبت شده است.

**زیرسیستم 1: موتور بازیابی و رتبه‌بندی فارسی**
1. اندازه‌گیری مشکل: نسخه اولیه از روش کلاسیک وزن‌دهی واژه کلاسیک استفاده می‌کرد و در مترادف و بازنویسی فارسی ضعیف بود.
2. طراحی جایگزین: بازیاب لغوی BM25 پیاده‌سازی‌شده در داخل شرکت، به‌علاوه بازیاب معنایی با بردارهای جاسازی محلی.
3. کالیبراسیون امتیاز شباهت بر اساس اندازه‌گیری روی پیکره (ADR-004).
4. طراحی بازرتبه‌ساز ویژگی‌محور و دروازه پوشش واژه‌ها.
5. رفع خطای کشف‌شده با اندازه‌گیری: تکرار واژه در گسترش مترادف، امتیاز معنایی را به صفر می‌رساند؛ گسترش به یک گذر با حذف تکرار تغییر کرد (2026-08-26).
6. حذف کامل روش کلاسیک وزن‌دهی واژه از کد محصول (تنها استفاده باقی‌مانده از scikit-learn، رگرسیون لجستیک دسته‌بند قصد است).
7. ساخت مجموعه ارزیابی تکرارپذیر و ثبت نتایج (بند 3.2).

**زیرسیستم 2: مدل‌های اختصاصی**
1. طراحی دسته‌بند قصد آموزش‌پذیر روی داده هر نصب، با دروازه پویا بر اساس دقت آزمون (ADR-005).
2. آزمایش ریزتنظیم و تقطیر مدل جاسازی روی داده دامنه روی GPU: مدل‌های تقطیرشده روی 120 پرسش آزمون به hit@1 = 0.875 رسیدند، در حالی که مدل پایه 0.933 داشت؛ بنابراین بر اساس اندازه‌گیری رد شدند و مدل پایه حفظ شد. (`docs/features/chat-training/RESEARCH.md`)
3. ذخیره مدل آموزش‌دیده به‌صورت فایل نسخه‌دار با شناسنامه مدل.
4. [گام بعد: بازرتبه‌ساز یادگیرنده یا ریزتنظیم مدل جاسازی، انتخاب بر اساس نتیجه مطالعه]

**زیرسیستم 3: لایه انتخاب زمین‌خورده و ضد توهم**
1. شناسایی مشکل در محیط عملیاتی: پاسخ متنی آزاد مدل زبانی، گاهی عدد یا مکان نادرست تولید می‌کرد.
2. تصمیم معماری «مدل انتخاب می‌کند، نمی‌نویسد» (ADR-018).
3. اندازه‌گیری تعداد رکورد لازم برای لایه انتخاب؛ مقدار 8 از روی منحنی recall@k انتخاب شد.
4. طراحی دو دیوار کنترلی و آزمون‌های اختصاصی آن‌ها.

**زیرسیستم 4: استقلال از ارائه‌دهنده و اجرای محلی مدل زبانی**
1. سازگاری با OpenAI API برای استقلال از فروشنده (ADR-007).
2. صفحه کنترل با 10 نوع ارائه‌دهنده فعال، مسیریابی هر وظیفه، قطع‌کننده مدار مشترک.
3. پژوهش انتخاب زمان‌اجرا و مدل زبانی متن‌باز برای کارت‌های Tesla P40 (معماری Pascal) و انتخاب llama.cpp؛ [مدل انتخابی و نتایج اندازه‌گیری روی سخت‌افزار هدف].
4. [استقرار مدل زبانی و گفتار به متن محلی با اسکریپت استقرار و پایش سلامت]

**زیرسیستم 5: تبدیل متن به گفتار فارسی**
1. پژوهش مدل‌های متن‌باز گفتار فارسی و اندازه‌گیری سرعت روی P40 (458 نویسه در 76.3 ثانیه؛ هزینه شبیه‌سازی صدا حدود دو برابر).
2. طراحی یک نمونه مدل برای هر کارت گرافیک (ADR-012)، خروجی mp3 (ADR-011)، کلید کش وابسته به اثر انگشت فایل صدای مرجع (ADR-009).
3. فرهنگ تلفظ قابل ویرایش توسط اپراتور.

**زیرسیستم 6: پایگاه داده، استقرار و پایش**
1. مهاجرت از SQLite به PostgreSQL 16 با دو schema و 29 فایل مهاجرت نسخه‌دار با کنترل checksum.
2. پشتیبان‌گیری خودکار روزانه با راستی‌آزمایی، بازگردانی با تأیید تایپی و نسخه ایمنی پیش از بازگردانی.
3. استقرار شش‌مرحله‌ای خودکار با بازگشت خودکار کد در صورت شکست بررسی سلامت.
4. [بازیابی به نقطه زمانی (PITR)، تمرین بازگردانی زمان‌بندی‌شده، محیط staging]
5. نقطه پایش Prometheus و [داشبورد و قواعد هشدار].

**شواهد:** `docs/engineering/DECISIONS.md` (ADR-001 تا ADR-021)، `docs/features/*/` (13 پوشه: RESEARCH/SPEC/PRD)، `docs/features/grounded-selection/RESEARCH.md`، `docs/features/chat-training/RESEARCH.md`، `docs/features/text-to-speech/RESEARCH.md`، `migrations/` (29 فایل).

> [Sina] **Vabastegi-ha:**
> - Zirsystem 2 gam 3: track 2 (uncommitted). Gam 4: track 2, hanuz shoru nashode.
> - Zirsystem 2 gam 2: adad-ha vaghei hastand vali arzyabi **kharej az repo** (`/home/gpu/train-work`) anjam shode. Agar evaluator script bekhahad, nadarim. Mitavanid band ra negah darid ba in ehtiat.
> - Zirsystem 4 gam 3-4: track 3. RESEARCH.md hanuz uncommitted ast; hich adad-i ruye P40 nist.
> - Zirsystem 6 gam 4-5: track 7 va track 4, hanuz shoru nashode / uncommitted.
> - «حذف کامل روش کلاسیک وزن‌دهی واژه» ruye main dorost ast (grep dar `app/` hich chiz nadad).

### 4.2 عنوان مستندات فنی (برای بارگذاری در ضمایم)

**پاسخ پیشنهادی:**

1. سند معماری سامانه (`docs/engineering/ARCHITECTURE.md`)
2. اسناد تصمیم معماری ADR-001 تا ADR-021 (`docs/engineering/DECISIONS.md`)
3. پژوهش لایه انتخاب زمین‌خورده و منحنی recall@k (`docs/features/grounded-selection/RESEARCH.md`)
4. پژوهش آموزش مدل جاسازی و نتایج آزمایش (`docs/features/chat-training/RESEARCH.md`)
5. پژوهش تبدیل متن به گفتار فارسی و اندازه‌گیری سرعت (`docs/features/text-to-speech/RESEARCH.md`)
6. پژوهش مقاوم‌سازی بار و آزمون فشار: p50 = 16 میلی‌ثانیه و p99 = 565 میلی‌ثانیه با 40 کاربر همزمان و صفر خطای 5xx (`docs/features/exhibition-perf-hardening/RESEARCH.md`)
7. گزارش نتایج ارزیابی تکرارپذیر و مطالعه حذفی (`docs/features/eval-benchmark/`)
8. شناسنامه مدل‌های آموزش‌دیده [پس از تکمیل]
9. پژوهش و مشخصات اجرای محلی مدل زبانی (`docs/features/local-inference/`)
10. اساسنامه مهندسی و استانداردهای API، امنیت، پایگاه داده و آزمون (`docs/engineering/ENGINEERING_CONSTITUTION.md` و اسناد هم‌پوشه)
11. راهنمای استقرار و بازگردانی (`docs/engineering/DEPLOYMENT_RUNBOOK.md`)
12. سند پایش (`docs/engineering/MONITORING.md`)
13. سند آزمون PostgreSQL (`docs/engineering/POSTGRES_TESTING.md`)
14. گزارش ممیزی وابستگی‌ها (`docs/engineering/DEPENDENCY_AUDIT.md`)
15. گزارش اجرای CI در GitHub Actions (تصویر یا خروجی آخرین اجرا)

> [Sina] 7, 8, 9: vabaste be track 1, 2, 3 (hanuz vojud nadarand ruye main).
> 6: load test ruye laptop va SQLite anjam shode, na production. Dar matn nayavordam ke production ast.
> 11: bakhsh-e restore-e an stale (SQLite) ast. Ghabl az upload dorost shavad (track 7).
> 15: CI-e akhar ruye main job-e `deploy` ra fail karde. Ghabl az screenshot dorost shavad.

### 4.3 مستندات سیر تحقیق و توسعه

**پاسخ پیشنهادی:** تاریخچه کامل توسعه در مخزن git نگهداری می‌شود: 482 commit و 140 درخواست ادغام (Pull Request) از 2026-08-19 تا امروز، هر تغییر با پیام توضیحی و ارجاع به سند طراحی. خروجی تاریخچه، فهرست اسناد RESEARCH/SPEC و ثبت جلسات توسعه با کمک هوش مصنوعی (`docs/engineering/AI_ASSISTANCE_LOG.md`) در ضمایم بارگذاری می‌شود.

> [Sina] Tarikh-e shoru-e git 2026-08-19 ast (history-e ghabli import nashode). Agar kar-e R&D ghabl az an ra darid (masalan az 1404), khodetan ezafe konid.

---

## 5. د- توضیحات در مورد دلایل فنی اعلامی

### دلیل 1: «محصول بیشتر یک پوسته/هماهنگ‌کننده روی سرویس‌های آماده است تا فناوری اختصاصی هوش مصنوعی»

**پاسخ پیشنهادی:**

بخش عمده پرسش‌ها بدون مدل زبانی خارجی پاسخ داده می‌شود. پیش از هر فراخوانی مدل زبانی، حدود 20 لایه قطعی و محلی در موتور پاسخ اجرا می‌شود (`app/routers/chat.py`) که شامل بازیاب لغوی و معنایی، بازرتبه‌ساز، دسته‌بند قصد آموزش‌دیده روی داده مشتری و دروازه‌های اطمینان است. سهم لایه‌های محلی از پاسخ‌ها به تفکیک لایه، با شمارنده `chat_tier_served_total` در نقطه پایش ثبت می‌شود: [درصد پاسخ‌های محلی در بازه اندازه‌گیری]. حتی در لایه‌ای که مدل زبانی فعال است، مدل فقط از میان رکوردهای بازیابی‌شده توسط موتور محصول انتخاب می‌کند و متن پاسخ را موتور محصول از پایگاه داده می‌نویسد (ADR-018). بنابراین مدل زبانی یک جزء قابل تعویض در یک معماری اختصاصی است، نه هسته محصول. اثبات سهم هر لایه در مطالعه حذفی آمده است: [جدول پس از اندازه‌گیری].

> [Sina] «حدود 20 لایه»: agent-e man 24 marhale shomord (shamel-e fallback-ha); «حدود 20 لایه قبل از LLM» monsefane ast.
> Darsad-e pasokh-e mahalli: az production bayad az `/metrics` ya `chat_logs` dar-avard. Man be server dast nazadam. Agar bekhahid, ba ejaze-ye shoma ye query-e read-only ruye `chat_logs` (source ro group by) mizanam.

### دلیل 2: «اتکا به API‌های OpenAI/GapGPT، روش کلاسیک وزن‌دهی واژه کلاسیک، ورود دستی دانش، نبود مدل اختصاصی»

**پاسخ پیشنهادی:**

- **روش کلاسیک وزن‌دهی واژه:** به‌طور کامل از کد محصول حذف شده و با BM25 پیاده‌سازی‌شده در داخل شرکت به‌علاوه بازیابی معنایی و بازرتبه‌ساز جایگزین شده است. در پوشه `app/` هیچ استفاده‌ای از روش کلاسیک وزن‌دهی واژه باقی نمانده است.
- **اتکا به API خارجی:** صفحه کنترل ارائه‌دهندگان از 10 نوع ارائه‌دهنده پشتیبانی می‌کند و هیچ ارائه‌دهنده‌ای در کد ثابت نشده است (ADR-007). محصول می‌تواند مدل زبانی متن‌باز را روی کارت‌های گرافیک سرور مشتری (llama.cpp) اجرا کند و در این حالت هیچ درخواستی به خارج از سرور ارسال نمی‌شود؛ ارائه‌دهنده ابری فقط جایگزین اختیاری است. [نتایج اندازه‌گیری مدل محلی]. در صورت در دسترس نبودن کامل مدل زبانی، سیستم با لایه‌های محلی به کار ادامه می‌دهد.
- **مدل اختصاصی:** دسته‌بند قصد روی داده هر مشتری آموزش می‌بیند، با هر تغییر دانش بازآموزی می‌شود و به‌صورت فایل نسخه‌دار با شناسنامه مدل ذخیره می‌شود. بازیاب BM25، بازرتبه‌ساز و دیوارهای کنترلی نیز پیاده‌سازی داخلی هستند. [مدل دوم: بازرتبه‌ساز یادگیرنده یا مدل جاسازی ریزتنظیم‌شده، با نتیجه اندازه‌گیری]. آزمایش ریزتنظیم مدل جاسازی نیز انجام شده و چون بهبود اندازه‌گیری‌شده نداشت، رد شد (بند 4.1).
- **ورود دستی دانش:** به دلیل 3 مراجعه شود.

> [Sina] Vabaste be track 2 (model artifact, model-e dovom) va track 3 (LLM-e mahalli). Jomle-ye «هیچ درخواستی به خارج از سرور ارسال نمی‌شود» faghat vaghti dorost ast ke STT ham mahalli shode bashad. Alan STT `whisper-1` ast (API-e birouni).
> Dar matn negoftam ke model-e embedding ya TTS male khodemun ast; har do open-weight-e third-party hastand.

### دلیل 3: «دانش، پرسش‌ها و مترادف‌ها به‌صورت دستی مدیریت می‌شوند»

**پاسخ پیشنهادی:**

ورود دانش نیمه‌خودکار است و فقط تأیید نهایی انسانی است (ADR-006، انتشار دانش با دروازه انسانی؛ این دروازه عمداً برای جلوگیری از انتشار اطلاعات نادرست در سامانه سازمانی حفظ شده است):

1. **بارگذاری سند و نشانی وب:** مدیر فایل PDF، DOCX، متن، XLSX/CSV یا نشانی وب‌سایت می‌دهد؛ سامانه متن را به‌صورت محلی استخراج و قطعه‌بندی می‌کند و مدخل‌های دانش، پرسش‌های هم‌معنا و مترادف‌ها را پیشنهاد می‌دهد، تکراری‌ها را با دانش موجود مقایسه می‌کند و پس از تأیید مدیر، نمایه را بازسازی می‌کند. [ماژول بارگذاری دانش]
2. **پیشنهاد مترادف با هوش مصنوعی** از روی پرسش‌های بی‌پاسخ یا کم‌اطمینان کاربران، با اعتبارسنجی خودکار و تأیید مدیر (`app/services/synonym_suggest.py`).
3. **پیشنهاد پرسش‌های هم‌معنا** برای هر مدخل (حداکثر 10 پیشنهاد، فیلتر طول، زبان و تکرار) با تأیید مدیر (`app/services/question_assist.py`).
4. **برداشت خودکار پرسش‌های واقعی کاربران** که با اطمینان بالا پاسخ گرفته‌اند و تبدیل آن‌ها به پرسش‌های آموزشی (`scripts/train/harvest_chat_questions.py`).
5. **ورود انبوه** از CSV/JSON و اسکریپت‌های واردکردن از وب‌سایت (`app/routers/dataset.py`، `scripts/import-content.py`، `scripts/import-guide-from-crawl.py`).
6. **صف پاسخ‌های نادرست** در پنل گفتگوها برای یافتن شکاف دانش.

> [Sina] Band 1 kamel vabaste be **track 5** ast ke **hanuz shoru nashode** (branch khali). Agar ghabl az ersal ship nashod, band 1 ra hazf kon; band-haye 2 ta 6 ruye main hastand.
> Band 4: script ast, na UI. Dar halat-e pishfarz dry-run ast.

### دلیل 4: «پایگاه داده و استقرار برای سطح سازمانی بالغ نیست»

**پاسخ پیشنهادی:**

- **پایگاه داده:** PostgreSQL 16 با دو schema جدا (`app` و `observability`)، مخزن اتصال (min 2، max 10)، نقش پایگاه داده جدا برای هر نصب بدون دسترسی superuser و با محدودیت زمان اجرای query. 29 فایل مهاجرت نسخه‌دار که با checksum SHA-256 کنترل می‌شوند؛ اگر فایل اعمال‌شده‌ای تغییر کند، استقرار متوقف می‌شود.
- **پشتیبان‌گیری:** خودکار روزانه با `pg_dump`، راستی‌آزمایی checksum و فهرست محتوا، نگهداری 14 نسخه، کپی خارج از سرور، و پشتیبان پیش از هر استقرار. بازگردانی فقط با تایپ دقیق عبارت تأیید، پشتیبان ایمنی پیش از بازگردانی، حالت نگهداری و اعتبارسنجی پس از بازگردانی انجام می‌شود.
- **بازیابی به نقطه زمانی:** [WAL archiving و PITR با ابزار انتخابی]. هدف‌ها: RPO = [عدد]، RTO = [عدد پس از تمرین بازگردانی]. تمرین بازگردانی خودکار زمان‌بندی‌شده نسخه آخر را در پایگاه داده موقت بازمی‌گرداند و تعداد ردیف‌ها را بررسی می‌کند؛ نتیجه در پایش و پنل مدیریت نمایش داده می‌شود.
- **استقرار:** اسکریپت‌های مرحله‌ای (`deploy/00` تا `deploy/50`) برای آماده‌سازی سرور، پایگاه داده، nginx و SSL، سرویس systemd سخت‌شده، ناظر سلامت با هشدار پیامکی، GPU و تونل. استقرار خودکار شش‌مرحله‌ای پس از سبز شدن CI: پشتیبان‌گیری، دریافت نسخه دقیق، نصب وابستگی‌ها، اعمال مهاجرت‌ها، راه‌اندازی مجدد و بررسی سلامت با بازگشت خودکار کد در صورت شکست. [محیط staging و جریان staging به production]

> [Sina] **Vabastegi-ha:**
> - PITR, RPO/RTO, restore drill, staging: track 7, **hanuz shoru nashode**. Alan hich-kodam vojud nadarand.
> - «کپی خارج از سرور»: code hast vali dar nasb-e shoma **khamush** ast. Ya roshan konid, ya bebinid «قابلیت کپی خارج از سرور».
> - «استقرار خودکار ... پس از سبز شدن CI»: alan az 2026-09-19 fail mishavad (variable-ha set nashode). Ghabl az ersal dorost shavad.
> - Replica/standby ra nanveshtam chon nadarim.

### دلیل 5: «بخش زیادی از کد با هوش مصنوعی و بدون مستند طراحی، آزمون کافی، بازبینی انسانی، تحلیل معماری و مالکیت فنی مشخص تولید شده است»

**پاسخ پیشنهادی:**

شرکت استفاده از ابزار هوش مصنوعی در کدنویسی را پنهان نمی‌کند و آن را به‌صورت شفاف ثبت می‌کند (`docs/engineering/AI_ASSISTANCE_LOG.md`). کنترل کیفیت به این صورت است:

- **مستند طراحی:** اساسنامه مهندسی با 9 اصل الزامی، گردش کار پژوهش، مشخصات، ADR، برنامه و وظایف با قالب ثابت، 21 سند تصمیم معماری، 13 پوشه مستند قابلیت و استانداردهای API، امنیت، پایگاه داده، آزمون و رابط کاربری.
- **آزمون:** 2630 آزمون خودکار در 153 فایل، شامل 122 آزمون یکپارچگی روی PostgreSQL واقعی، آزمون‌های مرورگر (Playwright)، و آزمون‌های امنیتی اختصاصی (CSRF، XSS، نشت اطلاعات محرمانه، زمان‌بندی ورود، قفل ورود، حریم خصوصی کیوسک، دیوارهای کنترلی پاسخ). یک آزمون ساختاری نیز مانع جداسازی یا پنهان‌سازی آزمون‌های مرورگر می‌شود.
- **بازبینی انسانی:** هر تغییر از طریق درخواست ادغام (Pull Request) با قالب ثابت ثبت می‌شود که علت ریشه‌ای، پیوند سند طراحی، آزمون‌ها، ملاحظات امنیتی و «آنچه انسان راستی‌آزمایی کرده» را الزامی می‌کند. ادغام در شاخه اصلی نیازمند یک تأیید بازبین و سبز بودن CI است. مالک هر زیرسیستم در فایل CODEOWNERS و سند مالکیت کد مشخص است. بازبینی انسانی کد موجود بر اساس فهرست زیرسیستم‌ها به ترتیب ریسک انجام و در GitHub ثبت می‌شود: [تعداد زیرسیستم‌های بازبینی‌شده تا تاریخ ارسال].
- **مالکیت فنی:** [نام و سمت مالک فنی هر زیرسیستم، توسط شرکت تکمیل شود].
- **تحلیل معماری:** سند معماری، راهنمای یک‌ساعته معماری برای مهندس جدید، و ADR برای هر تصمیم معماری.

> [Sina] **In hassas-tarin bakhsh ast.**
> - Alan 0 az 140 PR review-e GitHub darad. CODEOWNERS, PR template va branch protection **vojud nadarand** (track 6, shoru nashode). Branch protection faghat ba ejaze-ye shoma run mishavad.
> - Jomle-haye «ادغام ... نیازمند یک تأیید بازبین» va «مالک ... مشخص است» faghat ba'd az merge-e track 6 **va** run-e branch protection dorost hastand.
> - Esm-e malek ra man nemineviam. Agar hanuz review-e vagheie anjam nashode, jaye khali ra «0» nagozarid; behtar ast jomle-ye «بازبینی ... انجام و ثبت می‌شود» ra be «برنامه بازبینی ... تدوین شده است» taghir dahid.
> - Tip: yek mas'ale-ye dige: agar branch protection 1 approval bekhahad va shoma tanha developer bashid, nemitavanid PR-e khodetan ra approve konid. Track 6 bayad in ra hal konad.
> - «2630 آزمون»: az `pytest --collect-only` (2026-09-30, main). 153 file, 122 postgres, 9 test-e browser (3 file). Vaghei ast.

### دلیل 6: «شواهد کافی از بلوغ محصول، پایداری عملیاتی، فرایند استاندارد استقرار، پایش، آزمون گسترده، CI/CD و مستندات رسمی وجود ندارد»

**پاسخ پیشنهادی:**

- **CI/CD:** در GitHub Actions روی هر push و هر درخواست ادغام: اجرای کامل آزمون‌ها با گزارش پوشش کد، آزمون یکپارچگی روی PostgreSQL 16 واقعی با اعمال مهاجرت‌های واقعی (مسدودکننده)، ارزیابی کیفیت بازیابی، ممیزی آسیب‌پذیری وابستگی‌ها، اسکن اطلاعات محرمانه با آزمون صحت خود اسکنر، و در انتها استقرار خودکار روی سرور. نسخه‌گذاری با فایل VERSION و CHANGELOG و انتشار خودکار با برچسب نسخه.
- **پایش:** نقطه Prometheus (`/metrics`) با احراز هویت (توکن یا نشست مدیر) و شاخص‌های درخواست HTTP، تأخیر، لایه پاسخ، فراخوانی و قطع‌کننده مدار هوش مصنوعی، پشتیبان‌گیری و امتیاز سلامت. بررسی سلامت و آمادگی (`/api/health`، `/api/ready`) روی 10 زیرسیستم. ناظر مستقل که پس از 3 بررسی ناموفق پیامک هشدار می‌فرستد. [Prometheus و Grafana با داشبورد و قواعد هشدار، راهنمای رخداد و سند SLO]
- **ثبت رخداد و ممیزی:** چهار جدول گزارش (عملیاتی، ممیزی، امنیتی، سرویس) با پوشاندن اطلاعات محرمانه و شماره تلفن و شناسه درخواست.
- **امنیت:** bcrypt، عامل دوم ورود، قفل پس از 5 تلاش، محافظت CSRF با آزمون انطباق مسیرها، محدودیت نرخ، رمزنگاری اطلاعات محرمانه در حالت سکون، و بررسی پیکربندی ناامن که مانع راه‌اندازی نصب عملیاتی می‌شود.
- **مستندات رسمی:** بند 4.2.

> [Sina] **Vabastegi-ha:**
> - «ارزیابی کیفیت بازیابی» dar CI: track 1 (alan dar `ci.yml` nist).
> - «انتشار خودکار با برچسب نسخه»: `release.yml` hast vali hargez run nashode (hich tag nist). Ya yek tag-e `v0.1.0` bezanid (ba ejaze-ye shoma), ya jomle ra be «فرایند انتشار نسخه تعریف شده است» kam konid.
> - Prometheus/Grafana/alerts/runbook/SLO: track 4 (uncommitted, runbook va SLO hanuz nist).
> - «عامل دوم ورود»: yek soal-e amniyati ast (knowledge factor), na TOTP. Agar evaluator bepursad, sadegh bashim.
> - Uptime ya adad-e pay-dari nanveshtam chon andaze-giri nashode.

---

## 6. Mavaredi ke man por nakardam (kar-e Sina)

| Bakhsh | Chera |
| --- | --- |
| Sabeghe-ye arzyabi (tayid / adam-e tayid baraye مرحله تولید، سطح فناوری، طراحی) | Natije-ye arzyabi-ye ghabli; dar kartabl ast |
| Esm, shomare va semat-e porkonande | Shakhsi; form migoyad bayad fard-e fanni bashad |
| Dalil-e marhale-ye tolid: forush, gharardad, factor | Tejari |
| Film-e demo va tasavir | Upload dar samane |
| Malek-e fanni-ye har zirsystem | Faghat shoma |
| Form-e shomare 2 (kol-e an) | Mali; kharej az scope |
| Tasmim: form-e 1 ya name baraye porseshname-ye narm-afzar (band 4) | Ravie-ye dabirkhane |

## 7. Jaye-khali-ha ke adad mikhahand

| Jaye khali | Montazer-e | Vaziat-e alan |
| --- | --- | --- |
| Jadval-e ablation | Track 1 | Andaze-giri nashode |
| Deghat-e holdout-e intent | Track 2 | Faghat runtime, sabt nashode |
| Model-e dovom va natije | Track 2 | Shoru nashode |
| tokens/s, p50/p95, JSON validity-ye LLM-e mahalli | Track 3 | Faghat takhmin; ba ejaze-ye shoma ruye gpuserver |
| Darsad-e pasokh-e mahalli dar production | Query ruye `chat_logs` | Ba ejaze-ye shoma |
| RPO / RTO | Track 7 | Shoru nashode |
| Tedad-e review-e ensani | Shoma + track 6 | 0 |
