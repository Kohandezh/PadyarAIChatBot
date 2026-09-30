# کارت مدل: طبقه‌بند intent هر نصب

| Field | Value |
|-------|-------|
| Created | 2026-09-30 |
| Updated | 2026-09-30 |
| کد | `app/services/intent.py` |
| طراحی | `docs/features/intent-model/SPEC.md`، ADR-022 |

این کارت فقط عددهایی را می‌گوید که با یک فرمان واقعی اندازه گرفته شده‌اند.
هر عدد کنار فرمان، تاریخ و پیکره‌اش آمده. **هیچ عددی در این کارت عدد
production یا عدد یک مشتری نیست.**

## ۱. مدل چیست

- **نوع:** رگرسیون لجستیک چندکلاسه (`sklearn.linear_model.LogisticRegression`).
- **ورودی:** embedding نرمال‌شدهٔ یک پرسش (مدل ایستای
  `minishlab/potion-multilingual-128M`، revision
  `73908c3438cf03b6a01bcb9611d62b23d0726f08`، محلی، بدون API بیرونی).
- **خروجی:** `dataset_id` یک رکورد پایگاه دانش، با یک احتمال.
- **hyperparameterها:** `C=50`، `max_iter=3000`، holdout برابر ۱۵٪
  (`random_state=7`)، کف آموزش ۲ کلاس و ۲۰ نمونه. همه در
  `intent.HYPERPARAMETERS` و در sidecar.
- **جای آن در پاسخ‌دهی:** Tier 1.5 در `app/routers/chat.py`. فقط با
  احتمال ≥ `INTENT_TRUST_THRESHOLD` (0.6) پاسخ می‌دهد. اگر دقت holdout
  کمتر از 0.7 باشد، فقط با احتمال ≥ 0.85 (`search.classify_intent_local`).

## ۲. روی چه داده‌ای آموزش می‌بیند

روی دادهٔ **خود همان نصب**: هر ردیف جدول `questions` یک نمونه است (متن
پرسش پس از نرمال‌سازی فارسی و synonymها) و برچسبش `dataset_id` همان ردیف.
هیچ دادهٔ بیرونی و هیچ گفتگوی بازدیدکننده وارد آموزش نمی‌شود. محتوای
پیش‌فرض برنامه (`app/default_content.py`) خالی است، پس یک نصب تازه تا
وقتی ادمین پرسش وارد نکند مدلی ندارد.

## ۳. فرمان آموزش

از ریشهٔ نصب، با env همان نصب:

```bash
set -a && . ./.env && set +a && .venv/bin/python scripts/train/train_intent_model.py
```

یک reindex با ثبت روشن اجرا می‌کند، دقیقاً مثل اپ. اگر مدل ذخیره‌شده
ثابت‌شده مال همین داده است، آن را load می‌کند (آموزش دوباره همان بایت‌ها را
می‌ساخت). وگرنه از همان تابع `intent.train` آموزش می‌دهد و نسخهٔ بعدی را ثبت
می‌کند. sidecar را روی stdout چاپ می‌کند. برای آموزش حتمی، **فقط**
`intent-classifier.npz` را جابه‌جا کنید و `intent-classifier.history.jsonl`
را نگه دارید: شمارهٔ نسخهٔ بعدی از تاریخچه حساب می‌شود، و بدون آن شمارش از
۱ دوباره شروع می‌شود. لازم نیست کسی این را اجرا کند: اپ در هر reindex خودش
همین کار را می‌کند.

## ۴. عددهای اندازه‌گیری‌شده

**پیکره:** پیکرهٔ بنچمارک مصنوعی `data/eval/corpus.json` در commit
`420eb1e` (Track 1، هنوز merge نشده). ۲۵ رکورد و ۵۰ پرسش، دقیقاً ۲ پرسش
برای هر رکورد، دربارهٔ یک نمایشگاه خیالی. این پیکره داده‌ای ساختگی است و
در منشأ خودش هم این را می‌گوید. **عدد یک مشتری نیست.**

**تاریخ:** 2026-09-30 (UTC)، اجرای دوم پس از بازبینی اول (02:17Z). عددها
همان اجرای اول‌اند. sha256 عوض شد، چون فایل وزن اکنون عددهای holdout را هم
دارد. **ماشین:** Mac توسعه. scikit-learn 1.9.1،
numpy 2.5.3، Python 3.14.

**فرمان‌ها** (فقط از خود مخزن، با مدل embedding واقعی). گزینهٔ `--corpus`
پیکره را در یک دیتابیس SQLite دورریختنی در یک پوشهٔ موقت تازه بارگذاری
می‌کند و مدل را هم همان‌جا ثبت می‌کند. دیتابیس و `INTENT_MODEL_DIR` نصب را
نه می‌خواند نه می‌نویسد. مسیر پوشهٔ موقت روی stderr چاپ می‌شود:

```bash
git show 420eb1e:data/eval/corpus.json > corpus.json
.venv/bin/python scripts/train/train_intent_model.py --corpus corpus.json
```

خروجی واقعی (2026-09-30T03:04:54Z):

```text
throwaway install: /var/folders/.../padyar-intent-corpus-n4rg17ly
[intent] trained on 50 questions / 25 intents, holdout accuracy=0.520
[intent] recorded model version 1 at .../intent-model/intent-classifier.npz (28928 bytes, sha256 6ee72fc4969e)
model_version 1 (trained)
"holdout_accuracy": 0.52, "holdout_size": 25, "sample_count": 50, "class_count": 25,
"model_sha256": "6ee72fc4969e3da7ac8554e4360442b1bdfd25bb74027ab3528531517e755b51",
"training_fingerprint": "c22e52bb5562b7aef9e8dea544199f25b18e3ee0107d60569e9871450d4a7ba3"
```

همان sha256 و همان fingerprint اجرای قبلی (با یک بارگذار موقت بیرون از
مخزن). پس عدد با فرمان بالا دقیقاً بازتولید می‌شود. commit `420eb1e` هنوز
روی main نیست.

| سنجه | مقدار |
|---|---|
| `sample_count` (کل پیکره) | 50 |
| `class_count` | 25 |
| `holdout_size` | 25 |
| `holdout_accuracy` | **0.52** (۱۳ از ۲۵) |
| `model_version` در اجرای اول | 1 |
| آموزش در اجرای اول | یک بار (`trained on 50 questions / 25 intents`) |
| اجرای دوم روی همان داده | مدل load شد، آموزش نشد، `model_version` همان 1 |

این عدد کمتر از 0.7 است. پس روی این پیکره، دروازهٔ اعتماد فقط پیش‌بینی‌های
با احتمال ≥ 0.85 را قبول می‌کند و بقیه به tier بعدی می‌روند. این همان رفتار
طراحی‌شده برای پیکرهٔ کوچک است.

**این عدد باید روی یک نصب واقعی دوباره اندازه گرفته شود.** روی سرور همان
نصب، فرمان بخش ۳ را اجرا کنید، یا فقط `intent-classifier.json` را بخوانید
(`holdout_accuracy`، `holdout_size`، `sample_count`)، یا gauge
`intent_holdout_accuracy` را از `/metrics` بخوانید. عدد را با تاریخ و
`model_version` همین‌جا ثبت کنید.

## ۵. holdout چه چیزی را می‌سنجد و چه چیزی را نه

- **می‌سنجد:** یک نمونهٔ طبقه‌بندی‌شده (stratified) از پرسش‌ها کنار گذاشته
  می‌شود (۱۵٪، ولی دست‌کم یکی برای هر کلاس)، یک مدل آزمایشی روی بقیه آموزش
  می‌بیند، و دقت آن روی کنارگذاشته‌ها اندازه گرفته می‌شود. یعنی: «یک پرسش
  با عبارت‌بندی دیگر، به رکورد درست می‌رسد؟». روی پیکرهٔ بالا یعنی مدل با
  **یک** مثال برای هر رکورد آموزش دید و روی مثال دوم آزموده شد.
- **نمی‌سنجد:** مدلی که سرو می‌شود. آن مدل بعد از سنجش روی **همهٔ** داده
  دوباره آموزش می‌بیند. دقت خود آن مدل اندازه گرفته نمی‌شود، چون داده‌ای
  بیرون از آموزشش باقی نمی‌ماند.
- **نمی‌سنجد:** پرسش‌های واقعی بازدیدکننده‌ها، پرسش‌های بیرون از دامنه
  (مدل همیشه یکی از رکوردها را برمی‌گرداند، فقط احتمالش پایین است)، یا
  کیفیت کل خط پاسخ. کیفیت کل خط با `scripts/run_eval.py` سنجیده می‌شود.

## ۶. محدودیت‌های شناخته‌شده

- **کلاس‌های کوچک.** یک رکورد با یک یا دو پرسش، مدل را با تقریباً هیچ مثالی
  می‌سازد. اگر کلاسی فقط یک پرسش داشته باشد، holdout اصلاً اجرا نمی‌شود و
  `holdout_accuracy` تهی (`null`) است.
- **embedding ایستا.** مدل embedding یک مدل ایستای کلمه‌ای است: ترتیب
  کلمه‌ها و نفی را نمی‌فهمد. تغییر آن مدل یا revision آن، کل مدل را دوباره
  آموزش می‌دهد.
- **نسخهٔ کتابخانهٔ model2vec در fingerprint نیست.** revision مدل embedding
  وزن‌های آن را ثابت می‌کند، و نسخهٔ scikit-learn و numpy در fingerprint
  هستند. اگر یک ارتقای model2vec خروجی embedding را عوض کند، مدل قبلی تا
  تغییر بعدی داده load می‌شود.
- **نصب دوباره با `rsync --delete` تاریخچه را پاک می‌کند.** مسیر پیش‌فرض
  (`data/intent-model`) داخل پوشهٔ کد است. نصب دوباره با
  `deploy/10-install-app.sh` از `PADYAR_SRC` (`rsync -a --delete`) این پوشه
  را با وزن‌ها و تاریخچه پاک می‌کند و `model_version` از ۱ شروع می‌شود.
  deploy معمولی (`git reset --hard`) فایل‌های ignore‌شده را نگه می‌دارد. برای
  نگه‌داشتن تاریخچه، `INTENT_MODEL_DIR` را بیرون از پوشهٔ کد بگذارید
  (`/var/lib/padyar/<slug>/intent-model`).
- **یک split.** holdout یک تقسیم تصادفی ثابت است (`random_state=7`)، نه
  cross-validation. روی پیکرهٔ کوچک، عدد حساس به همان یک تقسیم است. روی
  پیکرهٔ بالا هر پرسش کنارگذاشته ۴٪ دقت را جابه‌جا می‌کند.
- **ترتیب ردیف‌ها.** fingerprint جفت‌ها را مرتب می‌کند. اگر فقط ترتیب
  ردیف‌ها عوض شود، مدل قبلی load می‌شود. مدل نهایی روی همان مجموعه داده
  آموزش دیده، ولی `holdout_accuracy` ثبت‌شده از تقسیم ترتیب قبلی است.

## ۷. کی دوباره آموزش می‌بیند

در هر reindex: هنگام boot، بعد از هر ویرایش محتوا در پنل، و در poll نسخهٔ
index بین workerها. در هر reindex اثر انگشت آموزش (fingerprint) دادهٔ فعلی
حساب می‌شود:

- **برابر با مدل ثبت‌شده، و sha256 وزن‌ها درست:** مدل load می‌شود، آموزش
  نمی‌بیند، نسخه همان می‌ماند.
- **هر حالت دیگر** (یک پرسش یا یک نگاشت عوض شده، synonymی که متن را عوض
  کند، مدل embedding دیگر، ارتقای scikit-learn یا numpy، فایل خراب یا
  دستکاری‌شده، sidecarی که عددهایش با فایل وزن نمی‌خواند): آموزش، نسخه +۱،
  یک خط تازه در `intent-classifier.history.jsonl` (حداکثر ۵۰۰ خط).
- **خطای گذرا** (embedding در این boot نیست، خواندن سؤال‌ها شکست خورد): این
  reindex مدلی ندارد، ولی فایل‌ها دست نمی‌خورند و boot سالم بعدی همان نسخه
  را load می‌کند.
- **رکورد پاک می‌شود** فقط وقتی آموزش روی این داده اجرا شد و مدلی نداد:
  داده زیر کف آموزش است، یا خود آموزش خطا داد (مثلاً کمبود حافظه در
  lbfgs). در هر دو حالت boot بعدی دوباره آموزش می‌دهد و نسخه +۱ می‌گیرد.

روی دیسک دو چیز می‌نویسند: اپ در حال اجرا، و
`scripts/train/train_intent_model.py`. هر اسکریپتی که اپ را با `TestClient`
بالا بیاورد (مثل `scripts/run_eval.py --conversations`)، lifespan را اجرا
می‌کند و ثبت را روشن می‌کند. پس باید اول `INTENT_MODEL_DIR` را به یک پوشهٔ
موقت ببرد. `run_eval.py` این کار را می‌کند و یک تست
(`test_every_script_that_boots_the_app_redirects_the_artifact_directory`)
هر اسکریپتی را که نکند رد می‌کند. تست‌ها با fixture در `tests/conftest.py`
به پوشهٔ موقت خودشان می‌روند.

## ۸. وارسی یک artifact با دست

فایل‌ها در `INTENT_MODEL_DIR` هستند (پیش‌فرض `data/intent-model`):

```bash
cd data/intent-model
cat intent-classifier.json                      # دقت، اندازهٔ پیکره، نسخه، sha256
shasum -a 256 intent-classifier.npz             # باید با model_sha256 برابر باشد
grep model_sha256 intent-classifier.json
cat intent-classifier.history.jsonl             # یک خط برای هر نسخه
```

نمونهٔ واقعی از اندازه‌گیری بخش ۴:

```text
$ shasum -a 256 intent-classifier.npz
6ee72fc4969e3da7ac8554e4360442b1bdfd25bb74027ab3528531517e755b51  intent-classifier.npz
$ grep model_sha256 intent-classifier.json
  "model_sha256": "6ee72fc4969e3da7ac8554e4360442b1bdfd25bb74027ab3528531517e755b51",
```

وزن‌ها بدون pickle خوانده می‌شوند، پس بازکردنشان کدی اجرا نمی‌کند:

```bash
.venv/bin/python -c "import numpy as np; d=np.load('intent-classifier.npz', allow_pickle=False); print(d.files, d['coef'].shape, d['holdout'])"
# ['coef', 'intercept', 'classes', 'holdout'] (25, 256) [ 0.52 25.   50.  ]
```

`holdout` همان دقت، اندازهٔ holdout و اندازهٔ پیکره است، این بار زیر sha256.
اپ دقت را از همین‌جا می‌خواند، نه از sidecar، چون دروازهٔ اعتماد به آن
وابسته است. اگر sidecar عدد دیگری بگوید، مدل load نمی‌شود.

sha256 یک checksum است، نه امضا. ثابت می‌کند وزن‌ها همان‌اند که sidecar
توصیف می‌کند. ثابت نمی‌کند چه کسی آن‌ها را نوشته.
