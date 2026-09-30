# فرایند بازبینی کد

آخرین به‌روزرسانی: 2026-09-30. طراحی: `docs/features/review-governance/SPEC.md`
و ADR-022 در `docs/engineering/DECISIONS.md`.

بخش بزرگی از کد این مخزن را عامل‌های AI نوشته‌اند. این به‌تنهایی مشکل نیست.
مشکل وقتی است که هیچ انسانی آن کد را نخوانده باشد و این خواندن جایی ثبت نشده
باشد. این سند می‌گوید چه کسی بازبینی می‌کند، چه چیزی را، چطور، و کجا ثبت
می‌شود.

**وضعیت امروز:** این فرایند تازه است. تا امروز هیچ PR ادغام‌شده‌ای review
ثبت‌شده در گیت‌هاب ندارد (پنج PR آخر، #140 تا #144، صفر review دارند). هیچ
زیرسیستمی هنوز بازبینی انسانی نشده است (`docs/engineering/CODE_OWNERSHIP.md`).

## ۱. چه کسی بازبینی می‌کند

- بازبین‌ها دو حساب فایل `.github/CODEOWNERS` هستند: `@Kohandezh` و
  `@sinashamsizadeh`. approve یکی از این دو کافی است.
- گیت‌هاب اجازه نمی‌دهد نویسندهٔ یک PR، PR خودش را approve کند.
- عامل‌های AI PR را با حساب `sinashamsizadeh` باز می‌کنند. پس **PR یک عامل را
  حساب `@Kohandezh` approve می‌کند.**
- یک عامل AI هرگز بازبین نیست. گزارش یک عامل بازبین (reviewer agent) کمک است،
  نه بازبینی انسانی، و در بخش `## AI assistance` می‌آید، نه در
  `## Human review`.

## ۲. چه چیزی بازبینی می‌شود

- **هر PR که `app/` یا `migrations/` را عوض می‌کند:** بازبینی کامل (بخش ۳).
  این همان محدوده‌ای است که `scripts/check_pr_governance.py` بررسی می‌کند.
- **PRهای فقط سند یا فقط تست:** خواندن دیف کافی است. بررسی خودکار برای آن‌ها
  همیشه سبز است.
- **هر PR که `.github/workflows/`، `deploy/` یا
  `scripts/check_pr_governance.py` را عوض می‌کند:** خط به خط خوانده می‌شود.
  این فایل‌ها روی سرور production یا روی خود قاعدهٔ بازبینی اثر دارند. اگر یک
  PR اسکریپت بررسی را عوض کند، سبز بودن آن check چیزی را ثابت نمی‌کند، چون
  workflow همان اسکریپت عوض‌شده را اجرا می‌کند.
- **کد قدیمی که هرگز بازبینی نشده:** طبق `docs/engineering/REVIEW_PLAN.md`،
  زیرسیستم به زیرسیستم، از پرخطرترین.

## ۳. کد نوشتهٔ AI چطور بررسی می‌شود

بازبین این پنج کار را انجام می‌دهد. هیچ‌کدام را به حرف خود PR نمی‌سپارد.

1. **کل دیف را می‌خواند.** همهٔ فایل‌ها، نه فقط خلاصهٔ PR. با
   `gh pr diff <شماره>` یا تب «Files changed».
2. **تست ادعاشده را بازتولید می‌کند.** تستی که بخش `## Tests` نام برده را
   خودش اجرا می‌کند، یا دست‌کم در خروجی CI همان PR پیدا می‌کند که اجرا شده و
   سبز است. اگر PR می‌گوید «این تست روی main شکست می‌خورد»، یک بار روی main
   امتحان می‌کند.
3. **سند طراحی را با کد مقایسه می‌کند.** سندی که بخش `## Design doc` نام برده
   را باز می‌کند و می‌بیند کد همان کاری را می‌کند که سند می‌گوید. اگر نوشته
   «No design needed»، می‌سنجد که دلیلش واقعاً درست است.
4. **بخش‌های قالب PR را بررسی می‌کند.** `## Root cause` علت را می‌گوید یا فقط
   تغییر را؟ `## Security` برای این دیف درست است؟ `## AI assistance` صادق است؟
   check `pr-governance` سبز است؟
5. **چک‌لیست `## Human review` را تیک می‌زند.** فقط کارهایی که واقعاً انجام
   داده.

اگر یکی از این‌ها رد شد، بازبین در گیت‌هاب «Request changes» ثبت می‌کند، نه
approve.

## ۴. بازبینی کجا ثبت می‌شود

بازبینی انسانی در سه جا ثبت می‌شود. هر سه را خود انسان انجام می‌دهد، نه عامل.

1. **review گیت‌هاب:** یک review از نوع «Approve» روی PR. این همان شاهدی است
   که یک ارزیاب بیرونی در گیت‌هاب می‌بیند.
2. **بخش `## Human review` متن PR:** بازبین چک‌لیست را تیک می‌زند و نام حساب و
   تاریخ را می‌نویسد. اگر عامل آن بخش را خالی گذاشته، بازبین چک‌لیست را از
   `.github/pull_request_template.md` کپی می‌کند.
3. **سندهای مخزن:** وقتی بازبینی یک نشست AI یا یک زیرسیستم کامل شد، انسان
   `pending` را در `docs/engineering/AI_ASSISTANCE_LOG.md` یا
   `docs/engineering/CODE_OWNERSHIP.md` با تاریخ و لینک review یا کامنت عوض
   می‌کند.

**قانون:** هیچ عامل AI هرگز بخش `## Human review` را پر نمی‌کند، هرگز
`pending` را عوض نمی‌کند، و هرگز نام مالکی را در `CODE_OWNERSHIP.md` نمی‌نویسد.

## ۵. بررسی خودکار متن PR

- workflow: `.github/workflows/pr-governance.yml`، اسکریپت:
  `scripts/check_pr_governance.py`.
- برای هر PR که `app/` یا `migrations/` را عوض کند، این پنج بخش باید پر باشند:
  `Root cause`، `Design doc`، `Tests`، `Security`، `AI assistance`.
- `Design doc` یک مسیر موجود زیر `docs/` می‌خواهد، یا یک خط
  `No design needed: <دلیل دست‌کم ۱۵ نویسه>`.
- `Human review` بررسی نمی‌شود. اگر اجباری بود، عامل‌ها آن را پر می‌کردند.
- این check **مشورتی** است (ADR-022): ضربدر قرمز نشان می‌دهد، ادغام را قفل
  نمی‌کند. بازبین یک PR قرمز را approve نمی‌کند.

اجرای دستی، پیش از باز کردن PR:

```bash
git -c core.quotePath=false diff --no-renames --name-only main...HEAD > /tmp/changed.txt
python scripts/check_pr_governance.py --body-file /tmp/pr-body.md --changed-files /tmp/changed.txt
```

## ۶. محافظت شاخه (branch protection)

**وضعیت: آماده شده، اجرا نشده.** دستور زیر هرگز اجرا نشده است. اجرای آن
تصمیم Sina است، چون شیوهٔ ادغام همه را عوض می‌کند.

### محدودیت امروز

مخزن `Kohandezh/PadyarAIChatbot` خصوصی است و پلن فعلی حساب `Kohandezh`
محافظت شاخه را برای مخزن خصوصی ندارد. در 2026-09-30 این پاسخ‌ها واقعاً
گرفته شد:

- `GET /repos/Kohandezh/PadyarAIChatbot/rulesets`: HTTP 403،
  «Upgrade to GitHub Pro or make this repository public to enable this feature.»
- `GET /repos/Kohandezh/PadyarAIChatbot/rules/branches/main`: همان 403.
- `GET /repos/Kohandezh/PadyarAIChatbot/branches/main/protection` با حساب
  `sinashamsizadeh`: HTTP 404 (این حساب نقش write دارد، نه admin).

پس پیش از اجرای دستور یکی از این دو لازم است: GitHub Pro روی حساب
`Kohandezh`، یا عمومی کردن مخزن. دستور را باید حساب `Kohandezh` اجرا کند،
چون فقط آن حساب نقش admin دارد.

یک نکتهٔ دیگر که آزموده نشده: اینکه گیت‌هاب روی این مخزن خصوصی با پلن فعلی از
روی `.github/CODEOWNERS` خودکار درخواست review بفرستد، بررسی نشده است.

### بدنهٔ JSON

در یک فایل، مثلاً `/tmp/main-protection.json`:

```json
{
  "required_status_checks": {
    "strict": false,
    "contexts": ["test", "postgres-tests", "dependency-audit", "secret-scan", "identity-guard"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null
}
```

### دستور

```bash
gh auth switch --user Kohandezh
gh api --method PUT \
  -H "Accept: application/vnd.github+json" \
  repos/Kohandezh/PadyarAIChatbot/branches/main/protection \
  --input /tmp/main-protection.json
```

`gh auth switch` فقط وقتی کار می‌کند که حساب `Kohandezh` قبلاً روی همان
ماشین با `gh auth login` وارد شده باشد. برای برگشتن به حساب قبلی:
`gh auth switch --user sinashamsizadeh`.

### چرا هر انتخاب

- `contexts`: پنج job مسدودکنندهٔ `.github/workflows/ci.yml`. همان پنج jobی که
  `deploy` به آن‌ها `needs` دارد. نام check در گیت‌هاب همان شناسهٔ job است،
  چون این jobها `name:` جدا ندارند.
- `pr-governance` در فهرست نیست: مشورتی است (ADR-022).
- `deploy` در فهرست نیست: فقط روی `main` بعد از ادغام اجرا می‌شود و روی PR
  هرگز اجرا نمی‌شود. اگر اجباری بود، هیچ PRی ادغام‌شدنی نبود.
- `strict: false`: لازم نیست شاخه پیش از ادغام با `main` هم‌روز باشد. چند
  کار موازی روی شاخه‌های جدا هست و `strict: true` هر کدام را پس از هر ادغام
  به rebase و اجرای دوبارهٔ CI مجبور می‌کرد.
- `required_approving_review_count: 1`: یک approve انسانی کافی است. تیم
  کوچک است.
- `require_code_owner_reviews: true`: approve باید از یکی از دو حساب
  CODEOWNERS باشد، نه از هر کسی که دسترسی write دارد.
- `dismiss_stale_reviews: true`: اگر بعد از approve کامیت تازه‌ای آمد، approve
  باطل می‌شود. کدی که بازبین ندیده، با approve قدیمی ادغام نمی‌شود.
- `enforce_admins: false`: حساب admin (`Kohandezh`) در یک وضعیت اضطراری
  production می‌تواند بدون approve ادغام کند. چنین ادغامی در گیت‌هاب بدون
  review دیده می‌شود، پس پنهان نمی‌ماند.
- `restrictions: null`: محدودیتی روی اینکه چه کسی push می‌کند نمی‌گذاریم.
  کنترل اصلی همان review است.

### بازگشت

اگر لازم شد محافظت برداشته شود (باز هم با حساب `Kohandezh`):

```bash
gh api --method DELETE repos/Kohandezh/PadyarAIChatbot/branches/main/protection
```

## ۷. سندهای مرتبط

- چک‌لیست پیش از انتشار: `docs/engineering/HUMAN_REVIEW_CHECKLIST.md`.
- چک‌لیست هر PR: بخش `## Human review` در `.github/pull_request_template.md`.
- ترتیب بازبینی کد قدیمی: `docs/engineering/REVIEW_PLAN.md`.
- مالکیت و وضعیت: `docs/engineering/CODE_OWNERSHIP.md`.
- مسیر یک‌ساعتهٔ آشنایی با کد: `docs/engineering/ARCHITECTURE_WALKTHROUGH.md`.
