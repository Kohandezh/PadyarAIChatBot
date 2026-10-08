# راهنمای استقرار (Runbook)

> **استقرار روی سرور اوبونتو ۲۴.۰۴ (دو نصب + TTS):** اسکریپت‌ها و پیکربندی
> آماده در `deploy/` قرار دارند و `deploy/README.md` ترتیب اجرا را توضیح
> می‌دهد. آن مسیر شامل nginx، systemd، PostgreSQL، گواهی SSL از طریق
> Cloudflare DNS-01، و سرویس تبدیل متن به گفتار روی Tesla P40 است.

## استقرار تازه

```bash
pip install -r requirements.txt
cp .env.example .env        # سپس مقادیر واقعی: OPENAI_API_KEY، SECRET_KEY، ADMIN_*، ALLOWED_ORIGINS، COOKIE_SECURE=true
python main.py              # dev — یا در تولید:
gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 127.0.0.1:8000 app.main:app
```

 Docker: `docker compose up -d` (Dockerfile و docker-compose.yml موجود است).

## استقرار و به‌روزرسانی production (gpuserver)

نصب اولیه با `deploy/` (README همان پوشه). استقرار خودکار وجود ندارد. CI فقط
بررسی‌ها را روی runnerهای GitHub اجرا می‌کند و هیچ کاری روی سرور production
انجام نمی‌دهد. بعد از merge به `main` و سبز شدن CI، اپراتور روی سرور دستی
مستقر می‌کند:

```bash
sudo PADYAR_GIT_TOKEN=<توکن فقط-خواندنی> /usr/local/bin/padyar-deploy myevent 8010 <sha>
```

`myevent 8010` همان slug و `APP_PORT` نصب است. مخزن خصوصی است، پس fetch یک
اعتبار لازم دارد: یک توکن fine-grained فقط-خواندنی روی همین مخزن، یا یک deploy
key فقط-خواندنی برای کاربر اپ (آن‌وقت `PADYAR_GIT_TOKEN` لازم نیست). مراحل آن اسکریپت، به
ترتیب: پشتیبان pg_dump → checkout کد جدید (پروسهٔ قدیم هنوز سرو می‌کند) →
`pip install` → `apply_migrations.py` (هر فایل یک تراکنش) → ری‌استارت →
بررسی `/api/health` تا ۱۲ تلاش × ۵ ثانیه (یک دقیقه کامل؛ بوت با بارگذاری
dataset و ایندکسِ بازیابی کند است و پنجرهٔ کوتاه قبلاً کد سالم را
برمی‌گرداند — رخداد ۲۰۲۶-۰۸-۲۷).

**بازگشت کد خودکار است:** سلامت نرسید → reset به commit قبلی + ری‌استارت.
**بازگشت دیتابیس خودکار نیست و نباید باشد:** همهٔ مهاجرت‌های این پروژه افزوده
نیستند. `0006`، `0013` و `0028` ستون یا جدول حذف می‌کنند و `0029` یک ردیف
settings را پاک می‌کند. پس کد قدیمی ممکن است با schema ای روبه‌رو شود که انتظارش
را ندارد. قاعدهٔ «expand, then contract» در `docs/engineering/DATABASE.md` برای
همین است: حذف یک ستون یا جدول باید در یک deploy جدا و بعدی بیاید. این قاعده
همیشه رعایت نشده است (`0028` در همان deploy حذف کرد)، پس پیش از بازگشت فهرست
migrationها را ببینید. بازگردانی از پشتیبانِ
قبل از استقرار یک اقدام دستی و مصرحانه از پنل ادمین است (زیرساخت → پشتیبان‌ها).

**در زمان رویداد مستقر نکنید.** دو اجرای هم‌زمان با یک قفل روی سرور
(`/run/padyar-deploy-myevent.lock`) پشت‌سرهم می‌شوند؛ اجرای دوم تا ۱۵ دقیقه
منتظر می‌ماند و اولی را له نمی‌کند.

**«SUPERSEDED» در لاگ خطا نیست:** اگر `main` از `<sha>` داده‌شده جلوتر رفته
باشد، اسکریپت چیزی را تغییر نمی‌دهد و با پیام SUPERSEDED و کد موفق تمام
می‌شود. دوباره با sha جدیدتر اجرا کنید.

**چرا استقرار خودکار نداریم:** تا ۲۰۲۶-۱۰ یک job به نام `deploy` در
`.github/workflows/ci.yml` روی runner خودمیزبان همین سرور اجرا می‌شد. حذف شد
تا هیچ job ی از CI روی سرور production اجرا نشود.

تغییر خودِ اسکریپت deploy: فایل روی سرور root-owned است و از مخزن جدا نصب
شده؛ بعد از merge، یک بار روی سرور:

```bash
sudo install -m 0755 -o root -g root \
  /opt/padyar-myevent/deploy/padyar-deploy.sh /usr/local/bin/padyar-deploy
```

**خواندن PDF در ماژول `ingest`:** اسکریپت راه‌اندازی `poppler-utils` را نصب می‌کند، اما روی سرورهای موجود یک بار `sudo apt-get install -y poppler-utils` لازم است؛ تا آن موقع آپلود PDF با پیام «خواندن PDF روی این سرور فعال نیست» رد می‌شود و بقیهٔ قالب‌ها کار می‌کنند.

## بررسی سلامت
- Liveness: `GET /api/health` (ارزان، بدون فراخوانی خارجی)
- Readiness: `GET /api/ready` — تا آماده‌شدن ایندکس بازیابی 503 می‌دهد؛
  `?deep=true` سرویس خارجی AI را هم می‌سنجد (فقط دستی/مانیتورینگ کم‌بسامد).
- لاگ ساخت‌یافته: `LOG_FORMAT=json` در env.

## به‌روزرسانی دانش
```bash
# ویرایش دانش از پنل ادمین (dataset + questions + synonyms) یا
# به‌روزرسانی app/default_content.py برای مقادیر پیش‌فرض بسته‌بندی‌شده
python3 scripts/reset-content-to-defaults.py   # پشتیبان خودکار + seed جدید
# ری‌استارت سرویس تا ایندکس بازسازی شود
```

## پشتیبان و بازیابی
- خودکار: زمان‌بندی پنل ادمین (app/services/backup.py) → هر dump در
  `backups/postgres/<backup_id>/` (فرمت `pg_dump --format=custom`، `app/services/pg_backup.py`)
- کپی خارج از سرور (2026-09-14): اگر `OFFSITE_BACKUP_TARGET` در env تنظیم
  شده باشد، هر dump که verify موفق داشته باشد خودکار به مقصد دوم کپی
  می‌شود — `rsync:user@host:/path` (با `rsync -a --chmod=F600` روی ssh؛
  کلید بدون رمز لازم است چون ساعت ۳ صبح بدون تعامل انسانی اجرا می‌شود)
  یا `dir:/mnt/...` (مسینت از قبل mount شده). نتیجهٔ هر کپی در بلوک
  `offsite` همان manifest.json ثبت می‌شود. شکست کپی هرگز پشتیبان محلی را
  باطل نمی‌کند؛ فقط رخداد `backup.offsite.failed` در لاگ سرویس ثبت می‌شود
  — هر شب مقصد را از روی همین رخداد ببینید، نه با فرض.
  نوع سوم `sftp:` است: کپی رمزشده به یک حساب فقط-SFTP. مقصد `sftp:` را اپراتور
  در پنل ادمین تنظیم می‌کند (Infrastructure → Backups). مقصد پنل بر env برنده
  است. پایین، «کپی رمزشده بیرون از سرور» را ببینید.
- پیش از هر استقرار: `deploy/padyar-deploy.sh` (گام ۱) قبل از هر تغییر یک dump
  با `reason=deploy` می‌گیرد. اگر dump شکست بخورد، استقرار هیچ چیز را تغییر نمی‌دهد.
- دستی: پنل ادمین → Infrastructure → Backups → ساخت پشتیبان.
- بازیابی (پایگاه داده PostgreSQL 16 است، نه SQLite): پنل ادمین → Infrastructure →
  Backups → بازیابی. باید دقیقاً `RESTORE BACKUP <id>` تایپ شود
  (`app/services/pg_backup.py` تابع `restore()`). خود برنامه این مراحل را انجام می‌دهد:
  تأیید سلامت dump، روشن‌کردن حالت تعمیر، ساخت پشتیبان ایمنی از وضعیت فعلی،
  `pg_restore --single-transaction`، اعتبارسنجی. شناسهٔ پشتیبان ایمنی در نتیجه
  برمی‌گردد. اگر چند پروسه اجرا می‌شود، بعد از بازیابی بقیه را ری‌استارت کنید.
  سپس `/api/ready` و شمارش dataset در `/api/health` را بررسی کنید.

### کپی رمزشده بیرون از سرور (`sftp:`)

**امروز هیچ مقصدی وجود ندارد.** نه در پنل مقصدی ذخیره شده و نه روی سرور
`OFFSITE_BACKUP_TARGET` تنظیم شده است. پس همهٔ پشتیبان‌ها روی همین سرور هستند. اگر سرور از دست برود، همه‌چیز از دست
می‌رود. صفحهٔ Backups همین را با یک خط قرمز می‌گوید: «هیچ نسخه‌ای بیرون از سرور
نیست.» کد آماده است و job `offsite-sftp` در CI آن را در برابر یک سرور SFTP موقت
ثابت می‌کند. روزی که حساب SFTP ساخته شد، راه‌اندازی فقط قدم‌های زیر است.

**دو راه برای مقصد.** راه اپراتور **پنل ادمین** است: Infrastructure → Backups،
کارت «جای نگه‌داری نسخه‌ها بیرون از سرور» (قدم ۵). راه دوم env است و فقط وقتی به
کار می‌رود که در پنل مقصدی ذخیره نشده باشد. اگر هر دو باشند، پنل برنده است
(`app/services/offsite_destination.py`). کلید عمومی gpg در هر دو راه روی سرور
است: قدم ۱ و ۲، و دو خط env در قدم ۵.

**کار این کد** (`app/services/backup_offsite.py`): بعد از هر dump که verify موفق
داشته باشد، dump را با **کلید عمومی** gpg رمز می‌کند و فقط فایل
`padyar_<slug>.pg_<تاریخ>_<ساعت>_<شناسه>.dump.gpg` را آپلود می‌کند. بخش اول نام،
نام دیتابیس همین نصب از `DATABASE_URL` است. بعد اندازهٔ فایل در مقصد را با
فایل محلی مقایسه می‌کند و sha256 فایل رمزشده را در بلوک `offsite` همان
manifest.json می‌نویسد. سپس فقط `OFFSITE_REMOTE_KEEP` نسخهٔ جدیدتر را در مقصد نگه
می‌دارد (خالی یعنی همان عدد نگه‌داری محلی، پیش‌فرض ۱۴). فقط فایل‌هایی پاک می‌شوند که
با نام دیتابیس **همین نصب** شروع می‌شوند. پس اگر دو نصب این سرور به یک مسیر بنویسند،
هیچ‌کدام نسخه‌های دیگری را پاک نمی‌کند. فایل‌هایی که نسخهٔ قبلی این کد بدون نام
دیتابیس نوشته (`pg_<...>.dump.gpg`) هرگز پاک نمی‌شوند؛ اگر لازم شد، دستی پاکشان
کنید. هر شکست در manifest و رخداد `backup.offsite.failed` ثبت می‌شود و
پشتیبان محلی را خراب نمی‌کند.

سرور فقط کلید عمومی دارد. پس خود سرور نمی‌تواند نسخه‌های بیرونی را باز کند، و کسی
هم که سرور را بگیرد نمی‌تواند. کلید خصوصی فقط روی کاغذ، در گاوصندوق است.

**۱. ساختن کلید gpg، بیرون از سرور.** روی یک کامپیوتر جدا، اگر ممکن است بدون
اینترنت. کلید اصلی فقط امضا می‌کند و یک زیرکلید فقط رمز می‌کند:

```bash
export GNUPGHOME=$(mktemp -d)
gpg --batch --pinentry-mode loopback --passphrase "" --quick-gen-key "padyar-backup" ed25519 cert never
FPR=$(gpg --with-colons --list-keys padyar-backup | awk -F: '/^fpr/{print $10; exit}')
gpg --batch --pinentry-mode loopback --passphrase "" --quick-add-key "$FPR" cv25519 encr never
gpg --armor --export "$FPR" > backup-public.asc            # فقط این به سرور می‌رود
gpg --batch --pinentry-mode loopback --passphrase "" --armor --export-secret-keys "$FPR" > backup-secret.asc
echo "$FPR"                                                # fingerprint، 40 حرف
```

**۲. کلید خصوصی روی کاغذ.** `backup-secret.asc` و fingerprint را چاپ کنید (حدود
۷۲۰ بایت متن، آزمایش 6 در `docs/features/db-maturity/RESEARCH.md`). دو نسخه چاپ
کنید و در گاوصندوق بگذارید. خط آخرِ پیش از `END` که با `=` شروع می‌شود، checksum
است: اگر هنگام تایپ دوباره یک حرف اشتباه شود، gpg خطا می‌دهد.

کاغذ را **همین حالا** آزمایش کنید، نه روز حادثه. روی یک کامپیوتر دیگر، متن کاغذ را
در `typed-secret.asc` تایپ کنید و:

```bash
export GNUPGHOME=$(mktemp -d)
gpg --batch --import typed-secret.asc
echo test > t.txt
gpg --batch --recipient-file backup-public.asc --encrypt --output t.gpg t.txt
gpg --batch --decrypt t.gpg                                # باید «test» چاپ کند
```

بعد از آزمایش، `backup-secret.asc`، `typed-secret.asc` و هر دو `GNUPGHOME` را پاک
کنید. هیچ نسخهٔ دیجیتالی از کلید خصوصی نماند.

**۳. حساب SFTP در مقصد.** فقط SFTP، بدون shell، با chroot (همان شکل آزمایش 5).
پنل هم با رمز عبور کار می‌کند و هم با کلید. کلید امن‌تر است، چون رمزی برای حدس
زدن نیست. نمونهٔ زیر فقط کلید را می‌پذیرد؛ برای ورود با رمز، در همین بلوک
`PasswordAuthentication yes` بگذارید:

```text
Match User backup
    ChrootDirectory /srv/sftp/backup
    ForceCommand internal-sftp
    AllowTcpForwarding no
    PasswordAuthentication no
```

پوشهٔ قابل نوشتن داخل chroot: `/upload`.

**هر نصب یک مسیر جدا.** این سرور دو نصب دارد. برای هر نصب یک پوشهٔ جدا بسازید، مثلاً
`/upload/myevent`، و همان را در پنل (فیلد «پوشه روی سرور») یا در
`OFFSITE_BACKUP_TARGET` بگذارید. نام فایل‌ها هم
نام دیتابیس نصب را دارد، پس حتی با یک مسیر مشترک، prune یک نصب به فایل‌های نصب دیگر
دست نمی‌زند. مسیر جدا یک لایهٔ حفاظت دوم است. کد پوشه را نمی‌سازد، پس یک بار بسازید:

```bash
sudo -u padyar-myevent sh -c 'echo "mkdir /upload/myevent" | sftp -b - \
  -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/opt/padyar-myevent/offsite/known_hosts \
  -i /opt/padyar-myevent/offsite/id_ed25519 -P <port> backup@<host>'
```

(این دستور بعد از قدم ۴ کار می‌کند، وقتی کلید و known_hosts آماده است. با پنل،
پوشه را با هر برنامهٔ SFTP بسازید، یا از سرویس‌دهنده بخواهید. اگر پوشه نباشد،
«آزمایش اتصال» می‌گوید در این پوشه نمی‌توان فایل نوشت.)

برای راه env، روی سرور، کلید SSH برای کاربر اپ (با پنل، متن کلید خصوصی را در
فرم بچسبانید و این فایل لازم نیست):

```bash
sudo -u padyar-myevent install -d -m 700 /opt/padyar-myevent/offsite
sudo -u padyar-myevent ssh-keygen -t ed25519 -N "" -f /opt/padyar-myevent/offsite/id_ed25519
```

محتوای `id_ed25519.pub` را در `authorized_keys` حساب `backup` در مقصد بگذارید.

**۴. کلیدهای میزبان مقصد.** پنل فقط **اثر انگشت** یکی از کلیدهای میزبان را
می‌خواهد (`SHA256:...`، از هر نوعی). برنامه هر بار همهٔ کلیدهای سرور را می‌گیرد،
فقط کلیدی را نگه می‌دارد که همین اثر انگشت را دارد، و فقط با همان نوع کلید وصل
می‌شود. راه env یک فایل known_hosts با **همهٔ** کلیدها لازم دارد، نه فقط ed25519
(در آزمایش 5، کلاینت نوع دیگری را انتخاب کرد):

```bash
ssh-keyscan -p <port> <host> > /opt/padyar-myevent/offsite/known_hosts
ssh-keygen -lf /opt/padyar-myevent/offsite/known_hosts
```

fingerprintها را با مدیر مقصد مقایسه کنید (`ssh-keygen -lf /etc/ssh/ssh_host_*_key.pub`
روی مقصد). به خروجی `ssh-keyscan` بدون مقایسه اعتماد نکنید: ممکن است کسی وسط راه
باشد. کپی با `StrictHostKeyChecking=yes` اجرا می‌شود. پس کلید ناشناخته یا عوض‌شده
اتصال را رد می‌کند و چیزی آپلود نمی‌شود.

**۵. وصل کردن مقصد در پنل (راه اپراتور).** دو خط gpg را یک بار در
`/opt/padyar-myevent/.env` بگذارید و سرویس را ری‌استارت کنید. پنل کلید gpg را
نگه نمی‌دارد:

```bash
OFFSITE_GPG_PUBLIC_KEY=/opt/padyar-myevent/offsite/backup-public.asc
OFFSITE_GPG_FINGERPRINT=<FPR از قدم ۱>
```

بقیه در پنل است. نشانی، درگاه، نام کاربری، پوشه، روش ورود (رمز عبور یا کلید
خصوصی) و اثر انگشت کلید سرور را وارد کنید. «ذخیره» و بعد «آزمایش اتصال» را بزنید.

- رمز و کلید به‌صورت رمزگذاری‌شده (`enc:`) در جدول `settings` می‌مانند و هیچ‌وقت
  به مرورگر برنمی‌گردند. صفحه فقط «ذخیره شده» نشان می‌دهد. فیلد خالی یعنی همان
  قبلی بماند. برای پاک کردن هر دو، تیک «رمز و کلید ذخیره‌شده با زدن ذخیره پاک
  شوند» را بزنید و ذخیره کنید.
- «آزمایش اتصال» تنظیمات **ذخیره‌شده** را می‌آزماید. همان قدم‌های یک کپی واقعی
  (`put`، `chmod`، `rename`، `ls`، `rm`) را روی یک فایل کوچک آزمایشی انجام می‌دهد
  و آن را پاک می‌کند. هیچ dump فرستاده نمی‌شود. هر مدیر در ۱۰ دقیقه حداکثر ۵ بار.
  جواب یک جملهٔ ساده است: اثر انگشت نادرست، رمز یا کلید نادرست، پوشهٔ غیرقابل
  نوشتن، سرور در دسترس نیست، پاسخ نداد، یا فعلاً اتصال را نمی‌پذیرد. اگر آزمایش
  نیمه‌کاره بماند (مثلاً وقت تمام شود)، فایل آزمایشی با یک اتصال دوم پاک می‌شود.
- «آزمایش اتصال» کلید gpg روی سرور را هم بررسی می‌کند (همان بررسی‌ای که کپی
  شبانه پیش از رمزگذاری انجام می‌دهد). اگر کلید نباشد یا با اثر انگشتش نخواند،
  جواب «سبز» نیست، حتی اگر اتصال کار کند. خط بالای صفحهٔ Backups هم در این حالت
  به‌جای «تنظیم شده» می‌گوید رمزگذاری آماده نیست و هیچ نسخه‌ای بیرون از سرور نیست.
- خالی کردن «نشانی سرور» و ذخیره، مقصد پنل را حذف می‌کند. بعد از آن، اگر env
  مقصدی داشته باشد، همان به کار می‌رود.
- **اطلاعات مقصد را هم بیرون از سرور نگه دارید**، کنار کلید کاغذی: نشانی، درگاه،
  نام کاربری، رمز یا کلید، و اثر انگشت. پنل آن‌ها را در پایگاه داده‌ای نگه می‌دارد
  که با خود سرور از دست می‌رود، و برای بازیابی لازم‌اند.
- اگر بعد از چند رمز اشتباه «آزمایش اتصال» بگوید «سرور مقصد فعلاً اتصال این سرور
  را نمی‌پذیرد»، sshd مقصد (OpenSSH 9.8 به بعد، `PerSourcePenalties`) نشانی این
  سرور را مدتی رد می‌کند. هر ورود ناموفق ۵ ثانیه جریمه دارد، هر اتصال بدون ورود
  ۱ ثانیه، و رد کردن وقتی شروع می‌شود که جمع جریمه از ۱۵ ثانیه بیشتر شود. در این
  حالت sshd به‌جای سلام همیشگی‌اش فقط «Not allowed at this time» می‌فرستد. یک
  دقیقه صبر کنید و دوباره امتحان کنید.

**۵ب. راه env (فقط وقتی پنل خالی است).** در `/opt/padyar-myevent/.env`، بعد
ری‌استارت سرویس:

```bash
OFFSITE_BACKUP_TARGET=sftp:backup@<host>:<port>:/upload/myevent
OFFSITE_SFTP_IDENTITY_FILE=/opt/padyar-myevent/offsite/id_ed25519
OFFSITE_SFTP_KNOWN_HOSTS=/opt/padyar-myevent/offsite/known_hosts
OFFSITE_GPG_PUBLIC_KEY=/opt/padyar-myevent/offsite/backup-public.asc
OFFSITE_GPG_FINGERPRINT=<FPR از قدم ۱>
OFFSITE_REMOTE_KEEP=
```

کپی بیرون از سرور فقط بعد از یک **بررسی سلامت موفق** انجام می‌شود
(`pg_backup.verify()`). پشتیبان خودکار شبانه خودش بررسی می‌کند، پس هر شب کپی
می‌شود (`app/services/backup.py:197`). ولی «گرفتن نسخهٔ پشتیبان جدید» در پنل فقط
dump می‌گیرد و چیزی کپی نمی‌کند. پس برای آزمودن:

1. با مقصد پنل، اول «آزمایش اتصال» باید سبز باشد.
2. در صفحهٔ Backups، «گرفتن نسخهٔ پشتیبان جدید» را بزنید.
3. در ردیف همان پشتیبان، «بررسی سلامت» را بزنید. کپی همین‌جا انجام می‌شود.
4. صفحه را دوباره باز کنید. خط بالای صفحه باید «آخرین کپی موفق بود» را نشان دهد.

اگر «ناموفق» دید، علت در بخش گزارش‌ها است (رخداد `backup.offsite.failed`).

**بازیابی بعد از از دست رفتن سرور:**

1. سرور تازه را با `deploy/README.md` برپا کنید (`00`، `05`، `10`).
2. کلید SSH سرور قبلی با خود سرور رفته است، و مقصد پنل هم با پایگاه داده‌اش.
   اطلاعات مقصد را از کنار کلید کاغذی بردارید. با رمز عبور وارد شوید، یا یک کلید
   تازه در `authorized_keys` مقصد بگذارید. بعد جدیدترین نسخه را دانلود کنید:

   ```bash
   sftp -P <port> backup@<host>
   sftp> ls -l /upload/myevent
   sftp> get /upload/myevent/padyar_myevent.pg_<تاریخ>_<ساعت>_<شناسه>.dump.gpg
   ```

3. روی کامپیوتری که کلید کاغذی در آن تایپ شده (قدم ۲)، باز کنید و بررسی کنید:

   ```bash
   gpg --batch --output padyar.dump --decrypt padyar_myevent.pg_<...>.dump.gpg
   pg_restore --list padyar.dump | head                     # باید فهرست جدول‌ها را نشان دهد
   ```

   gpg خودش سلامت فایل را بررسی می‌کند: فایل دست‌کاری‌شده یا ناقص خطا می‌دهد.
4. `padyar.dump` را در `/opt/padyar-myevent/` بگذارید (مالک: کاربر اپ) و با همان
   flagهای پنل، با نقش خود نصب، برگردانید.

   **رمز دیتابیس نباید روی خط فرمان باشد.** هر کاربر سرور خط فرمان هر پروسه را با
   `ps` یا `/proc/<pid>/cmdline` می‌بیند، ولی محیط (environment) یک پروسه را فقط
   خود همان کاربر و root می‌بینند. پس `pg_restore --dbname "$DATABASE_URL"` رمز را لو
   می‌دهد. خود برنامه هم همین قاعده را دارد: رمز را در `PGPASSWORD` می‌گذارد
   (`app/services/pg_backup.py`، `_conn_parts()` و `_env()`). دستور زیر همان دو تابع را
   صدا می‌زند، پس `DATABASE_URL` را دقیقاً مثل برنامه تجزیه می‌کند:

   ```bash
   sudo systemctl stop padyar-myevent
   cd /opt/padyar-myevent && sudo -u padyar-myevent bash -c \
     'set -a; . ./.env; set +a; .venv/bin/python - padyar.dump' <<'PY'
   import os, sys
   from app.services import pg_backup
   p = pg_backup._conn_parts()
   os.execvpe("pg_restore", ["pg_restore", "--host", p["host"], "--port", p["port"],
       "--username", p["user"], "--dbname", p["dbname"], "--clean", "--if-exists",
       "--no-owner", "--no-privileges", "--single-transaction", sys.argv[1]],
       pg_backup._env(p))
   PY
   sudo systemctl start padyar-myevent
   ```

5. `/api/ready` و شمارش dataset در `/api/health` را بررسی کنید. پس از بازیابی،
   `padyar.dump` و فایل باز‌شده را از کامپیوتر کمکی پاک کنید: داده‌های شخصی در آن است.

## بازگشت (Rollback)
- بازگشت دانش (reset): `scripts/reset-content-to-defaults.py` پیش از هر کار با
  `pg_backup.create(reason="reset-content")` یک dump می‌گیرد
  (`scripts/reset-content-to-defaults.py:80`). بازگشت = همان مسیر بازیابی بالا
  با آن dump.
- بازگشت کد بعد از استقرار قرمز: خودکار است. اگر `/api/health` پس از ری‌استارت
  سالم نشود، `padyar-deploy.sh` کد را به commit قبلی برمی‌گرداند. دیتابیس برنگردانده
  می‌شود (گام ۶ در اسکریپت).
- بازگشت دستی کد دو راه دارد. هر دو فقط کد را برمی‌گردانند و دیتابیس را دست
  نمی‌زنند.
  1. **راه عادی: `git revert` روی `main`.** commit بد را revert کنید، صبر کنید CI
     سبز شود، سپس sha همان revert را مثل یک استقرار عادی مستقر کنید:

     ```bash
     sudo PADYAR_GIT_TOKEN=<توکن فقط-خواندنی> /usr/local/bin/padyar-deploy myevent 8010 <sha-revert>
     ```

     هر وقت برای merge وقت هست، همین راه را بروید. revert از بازبینی و CI رد
     می‌شود. صدا زدن استقرار عادی با sha قدیمی بازگشت نیست: برای هر sha که نوک
     `main` نباشد با پیام `SUPERSEDED` بدون تغییر خارج می‌شود
     (`deploy/padyar-deploy.sh`، بررسی `FETCHED`).
     اثر روی دیتابیس: migrationهای revert (اگر داشته باشد) مثل هر استقرار اجرا
     می‌شوند. revert یک فایل migration آن را از دیتابیس برنمی‌گرداند.
  2. **وقتی برای merge وقت نیست: `--rollback`.**

     ```bash
     sudo PADYAR_GIT_TOKEN=<توکن فقط-خواندنی> /usr/local/bin/padyar-deploy myevent 8010 --rollback <sha-قدیمی>
     ```

     `<sha-قدیمی>` باید commit ی باشد که `main` آن را دارد و پشت commit در حال
     اجراست. پیش از هر تغییری، اسکریپت `main` را fetch می‌کند و
     `deploy/rollback-plan.sh` را اجرا می‌کند. این برنامه هر هدف دیگری را رد
     می‌کند و هر فایل `migrations/` را که کد قدیمی ندیده، با برچسب `additive` یا
     `destructive` فهرست می‌کند. اگر فهرست خالی نباشد، اسکریپت می‌ایستد و دستور
     اجرای دوباره با تأیید را چاپ می‌کند:

     ```bash
     sudo PADYAR_GIT_TOKEN=<توکن فقط-خواندنی> PADYAR_ROLLBACK_CONFIRM=<sha-قدیمی> \
       /usr/local/bin/padyar-deploy myevent 8010 --rollback <sha-قدیمی>
     ```

     `PADYAR_ROLLBACK_CONFIRM` همان sha است، کامل یا کوتاه. بعد همان گام‌های
     استقرار اجرا می‌شود، **بدون migration**: پشتیبان، checkout، `pip install`،
     ری‌استارت، بررسی سلامت. اگر سلامت نرسد، کد به commit ی برمی‌گردد که قبلاً
     اجرا می‌شد. همان قفل استقرار را می‌گیرد، پس هم‌زمان با یک استقرار اجرا نمی‌شود.
     اثر روی دیتابیس: migrationهای فهرست‌شده اعمال‌شده می‌مانند، چون کد قدیمی
     نمی‌تواند آن‌ها را برگرداند. `additive` برای کد قدیمی بی‌خطر است.
     `destructive` یعنی کد قدیمی ممکن است دنبال ستون یا جدولی بگردد که دیگر
     نیست، و داده‌ای که کد جدید نوشته ممکن است از دست برود.
     اگر commit در حال اجرا `deploy/rollback-plan.sh` را نداشته باشد (نصبی قدیمی‌تر
     از این حالت)، اسکریپت بدون تغییر می‌ایستد؛ راه 1 را بروید.
- اگر استقرارِ برگشت‌خورده migration مخرب داشت (مثل `0028`)، هیچ‌کدام از دو راه
  ستون یا جدول حذف‌شده را برنمی‌گرداند. dump گام ۱ همان استقرار لازم است (مسیر
  بازیابی بالا).

## عیب‌یابی سریع
| علامت | اقدام |
|---|---|
| /api/ready → 503 | لاگ «Embedding backend init failed» را ببین؛ بازیابی به BM25 واژگانی برمی‌گردد؛ model2vec و data/models را بررسی کن |
| 503 از /chat | سرویس خارجی AI در دسترس نیست و تطبیق محلی قوی وجود ندارد — کلید/base را در پنل ادمین بررسی کن |
| پاسخ‌های قدیمی پس از تغییر تم/CSS | cache-buster خودکار است؛ hard-refresh مرورگر |
| قفل «database is locked» | WAL فعال است؛ اگر تکرار شد پروسه‌های موازی نویسنده را بررسی کن |

## Speech-to-text credentials (since the AI Control Plane landed)

Transcription resolves its key through the AI Control Plane, not the legacy
`ai_api_key` setting. Resolution order (`app/services/ai/stt.py`):

1. An explicit binding, `ai_stt_provider_instance_id`, if set.
2. Otherwise the single enabled, secret-bearing, STT-capable provider instance.
   This is why rotating the key in **Admin → AI → Providers** now fixes voice
   as well as chat, with no extra configuration.
3. Only if neither exists, the legacy `ai_api_base` / `ai_api_key` settings —
   a compatibility path for an install that never migrated.

Only OpenAI-shaped providers serve `/audio/transcriptions`, so binding is
restricted to `openai` and `openai_compatible` (`STT_CAPABLE_TYPES`). Anthropic
and Gemini are not eligible and must not be listed as if they were.

The transcription model is `ai_model_stt` (default `whisper-1`), still editable
under Settings → AI.

## Model selection is under AI → Routing

Settings → AI no longer offers "chat model" / "classification model" inputs.
Those wrote `ai_model_chat` / `ai_model_classify`, which the runtime stopped
reading when routing moved to the Control Plane — the form reported success and
changed nothing. Per-task model and provider order now live in
**AI → Routing**. The endpoint still accepts the old fields so a cached admin
page does not break, but no longer persists them.

## Duplicate dataset id

`POST /admin/api/dataset` with an existing id returns **409 Conflict** (it
returned 400 before, and 500 on PostgreSQL). The existing row is never
overwritten. Backend-neutral detection lives in `app/db/dberrors.py`.

## Running the PostgreSQL integration tests

The default suite runs on SQLite for speed. Production-critical PostgreSQL
tests run in CI on every push (the `postgres-tests` job) and are also
available locally, opt-in:

```bash
RUN_POSTGRES_TESTS=1 .venv/bin/python -m pytest tests/postgres -q
```

## Environment marker and the production gate

`PADYAR_ENV` (`development` | `staging` | `production`) is the only thing that
decides whether the production configuration gate runs. It is deliberately
independent of every setting the gate checks.

A production install **refuses to start** on: `COOKIE_SECURE` not true ·
non-PostgreSQL backend · passwordless or placeholder `DATABASE_URL` · empty or
`*` `ALLOWED_ORIGINS` · `OTP_DELIVERY=dev` · placeholder `ADMIN_PASSWORD`. The
refusal names every problem at once and never prints a value.

Staging evaluates the same rules and logs what would block, but boots.
Development skips them. An unrecognised value is an error, not a fallback.

Warnings (never fatal): unpinned `SECRET_KEY` · remote DSN without `sslmode` ·
`DB_POOL_MAX_SIZE × WEB_CONCURRENCY` above the connection budget · placeholder
`visit-taxonomy.json`.

`SECRET_KEY` is generated and persisted in `settings.app_secret_key`, so it does
not rotate on restart. Pin it explicitly before running a second host or
rebuilding the database — otherwise each host mints its own and stored `enc:`
secrets (provider keys, SMS credentials) stop decrypting.

## Provider endpoint security

Provider base URLs are validated by `app/services/ai/endpoint_policy.py` under
two trust classes: `public` (https, public addresses only) and `internal`
(privileged; permits RFC1918 and loopback, and plain http, for on-prem Ollama /
vLLM / LiteLLM servers).

**Cloud instance metadata is denied in every trust class**, by an explicit list
checked before any trust-class branch: `169.254.169.254` (AWS/Azure/GCP),
`169.254.170.2` (ECS task role), `fd00:ec2::254` (IMDSv2 over IPv6), plus three
that live outside link-local space and each escaped by a different route —
`100.100.100.200` (Alibaba, CGNAT, not reported private, so reachable from
*both* classes), `192.0.0.192` (Oracle OCI, reported private, so `public`
refused it but `internal` did not) and `168.63.129.16` (Azure WireServer,
reported *global*, so it looked like ordinary internet to both).

The list is enumerated rather than derived from address class, so it needs a new
entry when a cloud adds an endpoint. Ordinary CGNAT is NOT banned: blocking
100.64/10 to stop one address would break on-prem installs that use it.

**DNS rebinding is closed by pinning.** `endpoint_policy.pin()` resolves once,
validates *every* answer, and the adapter connects to those exact IPs while
sending `Host:` (the original host **and port**) and TLS `sni_hostname` (the
original hostname) — so SNI and certificate verification are unchanged. TLS
verification is never disabled. Redirects are not followed
(`follow_redirects=False`); honouring one must go through
`assert_safe_redirect()`.

Pinning must not cost the address fallback. `pin()` returns `connect_urls` —
every validated address in resolution order — and the adapter tries them in
turn, because `localhost` resolves to `['::1', '127.0.0.1']` and an Ollama
server bound to 127.0.0.1 (its default) is unreachable if only the first is
tried. The resolver call runs in a worker thread; on the event loop a slow DNS
server would stall every concurrent request in the process.

## Circuit-breaker observability

State changes emit `llm.circuit.opened`, `llm.circuit.half_open` and
`llm.circuit.closed` through `applog`. Transitions only — a successful request
on an already-closed circuit logs nothing — and the half-open event is emitted
by the worker that wins the probe lease, so racing workers cannot each log the
same transition. The recovery event is gated the same way — on the conditional
UPDATE's `rowcount`, not on a pre-read — because two workers whose probes both
succeed would otherwise each report a recovery that happened once. That race is
only reproducible on PostgreSQL (SQLite has a single writer), so it is pinned by
`tests/postgres/test_circuit_recovery_concurrency.py`.

A failing probe (`half_open → open`) is logged too: without it an operator sees
`half_open` and then silence, which is indistinguishable from a probe still in
flight. All circuit logging is best-effort — a logging failure must never stop
a circuit from opening or recovering.
