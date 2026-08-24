اگر قرار است **دو Chatbot را روی یک Ubuntu Server 24.04 LTS** اجرا کنی و مدل LLM لوکال روی همین سرور Run نشود، GPU لازم نداری. معماری پیشنهادی من این است:

```text
Ubuntu 24.04 LTS
│
├── Nginx
│   ├── chatbot-1.domain.com → 127.0.0.1:8001
│   └── chatbot-2.domain.com → 127.0.0.1:8002
│
├── PadyarAIChatBot #1
│   ├── Python venv
│   └── systemd service
│
├── PadyarAIChatBot #2
│   ├── Python venv
│   └── systemd service
│
└── PostgreSQL 16
    ├── database_chatbot_1
    └── database_chatbot_2
```

Ubuntu 24.04 به‌صورت رسمی PostgreSQL 16 را دارد.

## 1. System Requirements

برای **دو Chatbot همزمان**:

| Resource | Minimum | پیشنهادی من |
|---|---:|---:|
| CPU | 4 vCPU | **8 vCPU** |
| RAM | 8 GB | **16 GB** |
| Storage | 60 GB SSD | **150–200 GB NVMe** |
| Network | 100 Mbps | **1 Gbps** |
| Public IP | 1 | 1 |
| GPU | ❌ لازم نیست | ❌ لازم نیست |
| OS | Ubuntu 24.04 LTS | **Ubuntu 24.04 LTS** |

اگر MP4، Avatar video، Upload صوت و Backup زیاد داری، من **حداقل 200GB NVMe** می‌گیرم.

### Production پیشنهادی

```text
8 vCPU
16 GB RAM
200 GB NVMe
1 Gbps Network
Ubuntu Server 24.04 LTS
Static Public IP
```

برای Exhibition کاملاً مناسب است.

---

# 2. اولین Packageهایی که باید نصب شوند

قبل از Clone کردن پروژه:

```bash
sudo apt update
sudo apt upgrade -y

sudo apt install -y \
  git \
  curl \
  wget \
  ca-certificates \
  gnupg \
  software-properties-common \
  build-essential \
  pkg-config \
  unzip \
  zip \
  rsync \
  jq \
  htop \
  tmux \
  nano \
  vim
```

---

# 3. Python Runtime

Ubuntu 24.04 را با **Python 3.12** نگه دار؛ برای هر Chatbot venv مستقل بساز.

نصب:

```bash
sudo apt install -y \
  python3 \
  python3-venv \
  python3-pip \
  python3-dev
```

بعداً:

```text
/opt/padyar-chatbot-1/.venv
/opt/padyar-chatbot-2/.venv
```

**یک venv مشترک برای دو پروژه نگذار.**

---

# 4. PostgreSQL 16

ضروری است:

```bash
sudo apt install -y \
  postgresql \
  postgresql-contrib \
  libpq-dev \
  postgresql-client
```

Ubuntu 24.04/Noble دارای PostgreSQL 16 است.

برای دو Chatbot پیشنهاد می‌کنم:

```text
PostgreSQL Instance: مشترک

Database 1:
padyar_chatbot_1

Database 2:
padyar_chatbot_2

User 1:
padyar_chatbot_1

User 2:
padyar_chatbot_2
```

یعنی **دو Database جدا**؛ نه اینکه هر دو را داخل یک DB بریزی.

---

# 5. Nginx

ضروری:

```bash
sudo apt install -y nginx
```

ساختار:

```text
Internet
   ↓
Nginx :443
   │
   ├── Domain 1 → 127.0.0.1:8001
   │
   └── Domain 2 → 127.0.0.1:8002
```

خود Uvicorn/Gunicorn را مستقیم روی Internet expose نکن.

---

# 6. SSL / HTTPS

نصب:

```bash
sudo apt install -y \
  certbot \
  python3-certbot-nginx
```

بعداً برای هر Domain:

```text
chatbot1.example.com
chatbot2.example.com
```

SSL جدا با Let's Encrypt.

این برای `COOKIE_SECURE=true` هم لازم است.

---

# 7. FFmpeg

برای Voice/Audio/Video حتماً نصب کن:

```bash
sudo apt install -y ffmpeg
```

Ubuntu 24.04 بسته FFmpeg 6.1.x دارد.

برای Padyar مهم است چون با:

- Audio upload
- STT
- Video
- Media processing

سر و کار داریم.

---

# 8. Security Packages

قبل از Public کردن Server:

```bash
sudo apt install -y \
  ufw \
  fail2ban
```

Firewall پیشنهادی:

```text
22   SSH
80   HTTP
443  HTTPS

5432 PostgreSQL → PUBLIC نباشد
8001 → PUBLIC نباشد
8002 → PUBLIC نباشد
```

یعنی:

```text
Internet ─X→ PostgreSQL
Internet ─X→ Uvicorn 8001
Internet ─X→ Uvicorn 8002
Internet → Nginx 443 → Apps
```

---

# 9. PostgreSQL Security

روی Production دیگر:

```text
trust
```

نمی‌خواهم.

استفاده کن از:

```text
scram-sha-256
```

و PostgreSQL فقط:

```text
localhost
```

را Listen کند، مگر اینکه DB جدا باشد.

---

# 10. systemd

چیزی برای نصب لازم نیست؛ داخل Ubuntu هست.

برای هر Chatbot یک Service:

```text
padyar-chatbot-1.service
padyar-chatbot-2.service
```

مثلاً:

```text
systemctl start padyar-chatbot-1
systemctl start padyar-chatbot-2

systemctl enable padyar-chatbot-1
systemctl enable padyar-chatbot-2
```

بعد از Reboot هر دو خودکار بالا می‌آیند.

PostgreSQL و Nginx هم همین‌طور.

---

# 11. Userهای Linux

بهتر است App را با `root` اجرا نکنی.

مثلاً:

```text
padyar1
padyar2
```

یا حتی یک:

```text
padyar
```

ولی برای isolation بیشتر ترج

یح من:

```text
padyar1
padyar2
```

است.

---

# 12. Directory Structure

من روی Server این ساختار را پیشنهاد می‌کنم:

```text
/opt/
├── padyar-chatbot-1/
│   ├── app/
│   ├── .venv/
│   └── .env
│
└── padyar-chatbot-2/
    ├── app/
    ├── .venv/
    └── .env


/var/lib/padyar/
├── chatbot-1/
│   ├── uploads/
│   ├── videos/
│   └── backups/
│
└── chatbot-2/
    ├── uploads/
    ├── videos/
    └── backups/


/var/log/padyar/
├── chatbot-1/
└── chatbot-2/
```

---

# 13. Packageهایی که فعلاً لازم نیست

برای وضعیت فعلی Padyar:

| Software | نیاز؟ |
|---|---|
| Docker | ❌ الزامی نیست |
| Kubernetes | ❌ |
| Redis | ❌ فعلاً |
| RabbitMQ | ❌ |
| MongoDB | ❌ |
| MySQL | ❌ |
| Node.js | ❌ مگر build frontend لازم داشته باشیم |
| CUDA | ❌ |
| NVIDIA Driver | ❌ |
| Ollama | ❌ مگر بخواهی مدل local |
| vLLM | ❌ مگر بخواهی مدل local |
| pgvector | ❌ فعلاً ضروری نیست |

برای Exhibition من عمداً Stack را ساده نگه می‌دارم.

---

# 14. Checklist نصب اولیه Server

قبل از اینکه اصلاً پروژه را Deploy کنیم، این‌ها باید آماده باشند:

```text
✅ Ubuntu Server 24.04 updated

✅ Git
✅ curl / wget
✅ build-essential
✅ Python 3
✅ python3-venv
✅ python3-dev
✅ pip
✅ PostgreSQL 16
✅ PostgreSQL client
✅ libpq-dev
✅ Nginx
✅ FFmpeg
✅ Certbot
✅ UFW
✅ Fail2ban
✅ jq
✅ rsync
✅ tmux
✅ OpenSSL / CA certificates

✅ Static IP
✅ DNS domains
✅ Ports 80/443 reachable
✅ PostgreSQL running
✅ Nginx running
✅ systemd available
```

## پیشنهاد نهایی من برای همین دو Chatbot نمایشگاه

```text
OS          Ubuntu Server 24.04 LTS
CPU         8 vCPU
RAM         16 GB
Disk        200 GB NVMe
Network     1 Gbps
GPU         None

Python      3.12
Database    PostgreSQL 16
Proxy       Nginx
Process     systemd
Media       FFmpeg
SSL         Certbot / Let's Encrypt
Firewall    UFW
Protection  Fail2ban
Git         GitHub
```

**دو App + دو venv + دو PostgreSQL database + دو systemd service + یک Nginx**؛ برای این مرحله ساده‌ترین و مطمئن‌ترین معماری است.
