# Deployment kit — one chatbot + Persian TTS on one Ubuntu 24.04 host

Target: `gpu@192.168.100.6`, 40 vCPU / 27 GB RAM / 2× Tesla P40.

The kit is instance-agnostic: every install is named by a **slug**
(lowercase letters, digits, hyphens — e.g. `myevent`), and everything else
derives from it — app dir `/opt/padyar-<slug>`, DB and role `padyar_<slug>`,
Linux user and service `padyar-<slug>`. The examples below use
slug `myevent`, domain `myevent.example.com`, port `8010`.

| Install | Domain | Port | DB | Linux user |
|---|---|---|---|---|
| `myevent` (example) | `myevent.example.com` | 8010 (APP_PORT) | `padyar_myevent` | `padyar-myevent` |
| TTS | *(loopback only)* | 8003 | — | `padyar-tts` |

**Pick one port per install and use the same number everywhere** — it is
`APP_PORT` in `/opt/padyar-<slug>/.env` (what the systemd unit and the
watchdog read), and the `<port>` argument to the nginx/watchdog/verify
scripts and to `padyar-deploy`. A mismatch fails loudly (health checks probe
the wrong port), never silently.

Everything here is idempotent — re-running a script is safe.

## Order of operations

```bash
# On the server, as a user with sudo:
git clone https://github.com/Kohandezh/PadyarAIChatBot.git /tmp/padyar-deploy
cd /tmp/padyar-deploy

sudo bash deploy/00-bootstrap-server.sh myevent   # packages, users, dirs, PG, UFW, fail2ban
sudo bash deploy/05-create-databases.sh myevent   # 1 DB + role — SAVE THE PRINTED PASSWORD
# 05 needs deploy/05-connect-isolation.sql next to it: run it from the checkout, not as a lone copy

# Fill in the .env file before installing:
sudo install -m 0600 deploy/env/instance.env.template /opt/padyar-myevent/.env
sudo nano /opt/padyar-myevent/.env                # every <PLACEHOLDER>, incl. SECRET_KEY and APP_PORT

sudo bash deploy/10-install-app.sh myevent
sudo bash deploy/15-nginx-and-ssl.sh myevent 8010 myevent.example.com   # needs a Cloudflare API token, see below
sudo MAINTENANCE_TITLE="چت‌بات رویداد من" \
  bash deploy/17-watchdog.sh myevent 8010 myevent.example.com           # down-SMS watchdog + maintenance page
# After deploy/40-cloudflare-tunnel.sh has published the site ("Going public" below):
sudo bash deploy/55-monitoring.sh myevent   # host monitoring; needs METRICS_TOKEN in the .env, see "Monitoring"

# GPU + TTS (independent of the app above):
sudo bash deploy/20-gpu-driver.sh
sudo reboot
bash deploy/21-verify-gpu.sh
sudo HF_TOKEN=hf_xxx bash deploy/25-install-tts.sh

bash deploy/30-verify.sh myevent 8010 myevent.example.com   # end-to-end smoke test
```

## Deploying a new version

There is no auto-deploy. CI runs the checks on GitHub-hosted runners and
never touches the server. After a PR is merged to `main` and CI is green,
deploy it by hand on the server:

```bash
sudo PADYAR_GIT_TOKEN=<read-only token> /usr/local/bin/padyar-deploy myevent 8010 <sha>
```

`myevent 8010` is the install's slug and its `APP_PORT`, and `<sha>` is the
merged commit on `main`. If `main` has moved past `<sha>`, the script stops
with `SUPERSEDED` and changes nothing; run it again with the newer sha. Its steps, in order: backup, fetch, deps, migrate, restart,
health check, and a code rollback when the health check fails.

The repository is private, so the fetch needs a credential.
`PADYAR_GIT_TOKEN` is a fine-grained GitHub token with read-only access to
contents of this one repository. It reaches git through a one-shot credential
helper and is never stored. A read-only deploy key for the app user works
too; then leave `PADYAR_GIT_TOKEN` out.

During an event: do not deploy.

**Why there is no auto-deploy.** Until 2026-10 a `deploy` job in
`.github/workflows/ci.yml` ran on a self-hosted runner on this server
(`deploy/50-install-github-runner.sh`). It was removed so that no CI job
runs on the production server. The runner script is kept for reference only.
A server that still has the runner installed can remove it with the steps
under "TO REMOVE" at the top of that script, with one change: keep
`/usr/local/bin/padyar-deploy`, because the manual deploy above uses it.
Remove only the runner service, the `gh-runner` user and
`/etc/sudoers.d/gh-runner-deploy`.

### Rolling back

Two ways. Both move the code only. The database is never rolled back.

1. **Normal: `git revert` on `main`.** Revert the bad merge, wait for green
   CI, then deploy the revert's sha exactly like above. Use this whenever
   there is time for a merge. The revert goes through review and CI.
2. **No time for a merge: `--rollback`.**

   ```bash
   sudo PADYAR_GIT_TOKEN=<read-only token> /usr/local/bin/padyar-deploy myevent 8010 --rollback <old-sha>
   ```

   `<old-sha>` must be a commit that `main` contains and that is behind the
   running commit. Before it changes anything, the script fetches `main` and
   runs `deploy/rollback-plan.sh`. That planner refuses any other target, and
   lists every file in `migrations/` the old code has never seen, each marked
   `additive` or `destructive`. If the list is not empty, the script stops and
   prints the command to run again with `PADYAR_ROLLBACK_CONFIRM=<old-sha>`
   (the same full or short sha). Then it runs the deploy steps without
   migrations: backup, checkout, deps, restart, health check. A red health
   check puts back the commit that was running before.

   Listed migrations stay applied, because old code cannot undo them. An
   `additive` one is harmless to old code. A `destructive` one means the old
   code may look for a column or table that is gone, and data the newer code
   wrote may be lost. Then the way back is the pre-deploy dump of the deploy
   that applied it: admin panel, Infrastructure > Backups. The rule that keeps
   this list harmless is "Destructive migrations: expand, then contract" in
   `docs/engineering/DATABASE.md`.

   `--rollback` needs the running commit to contain
   `deploy/rollback-plan.sh`. On an older install it stops and changes
   nothing; use `git revert` instead.

## Things that will bite you, and why

**`PADYAR_ENV=production` makes the app refuse to boot on a bad config.**
That is deliberate (`app/prodcheck.py`). It blocks on: `COOKIE_SECURE` not
true, a non-PostgreSQL backend, a placeholder or passwordless `DATABASE_URL`,
empty or `*` `ALLOWED_ORIGINS`, a placeholder `ADMIN_PASSWORD`, and
`OTP_DELIVERY=dev`. The refusal names every problem at once.

`OTP_DELIVERY=dev` blocks **even when the install has the registration
module disabled** — the gate reads the environment variable, not the module
list. The template sets `OTP_DELIVERY=asanak` for that reason.

**`SECRET_KEY` must be pinned, and must differ per install.** The key that
decrypts stored `enc:` secrets (provider keys, SMS credentials) is derived from
it. Leave it empty and the app generates one into the database; rebuild that
database and every stored secret becomes undecryptable.

**Migrations do not run themselves.** `scripts/apply_migrations.py` must run
before first boot; the app does not create production tables at runtime.
`10-install-app.sh` does this for you.

**`WorkingDirectory` is mandatory in the systemd unit.** `app/main.py` mounts
`StaticFiles(directory="media"|"static"|"LOGO"|"data")` by *relative* path, so
a wrong CWD raises at import.

**`media/` is a symlink.** `app/config.py:32` hardcodes
`VIDEO_DIR = BASE_DIR/media/videos`, so the directory must live inside the
checkout. `10-install-app.sh` symlinks it to `/var/lib/padyar/<slug>/media`,
which is what keeps uploaded video out of the git tree and safe across upgrades.

### Cloudflare

The name resolves to `172.67.141.4` — Cloudflare's edge — and today
returns **HTTP 525** (Cloudflare cannot complete TLS with the origin). Two
consequences:

1. **Certificates use DNS-01, not `--nginx`.** An http-01 challenge has to
   survive the edge; a dns-01 challenge does not care whether the origin is
   even reachable yet. `15-nginx-and-ssl.sh` needs a token from
   <https://dash.cloudflare.com/profile/api-tokens> with *Zone → DNS → Edit* on
   your Cloudflare zone, at `/root/.secrets/cloudflare.ini`. Pass
   `CERT_MODE=http` to use webroot instead.

2. **`conf.d/cloudflare-realip.conf` is not optional.** Without it every request
   arrives from a Cloudflare address, so `app/auth/security.py` rate-limits the
   entire exhibition as one visitor (20 requests per 60 seconds, shared) and one
   password-guessing bot locks every admin out at once. Refresh the ranges with
   `deploy/refresh-cloudflare-ips.sh`.

After the certificates are live, set **SSL/TLS → Overview → Full (strict)** in
the Cloudflare dashboard. "Flexible" leaves Cloudflare→origin traffic in clear
text, which makes `COOKIE_SECURE=true` a decoration.

### Going public: Cloudflare Tunnel, not a port-forward

This host has **no inbound path from the internet**, and that was measured, not
assumed:

* a probe from outside Iran to `46.100.15.28:443` is **refused** (TCP reset,
  `ECONNREFUSED`) — not dropped, actively rejected;
* a packet capture on the server recorded **zero inbound SYNs** on 80/443
  during that probe;
* meanwhile nginx serves the site correctly on the LAN.

So the refusal happens at the router or the ISP, one hop above the machine,
and no origin-side change can fix it. `deploy/40-cloudflare-tunnel.sh` installs
`cloudflared`, which dials **out** to Cloudflare — no inbound port, no
port-forward rule, no static IP, unaffected by ISP inbound policy or CGNAT.

Two things that go with it, both already in this kit:

* `nginx/00-default-server.conf` — returns 444 for any unmatched `Host`.
  Without it a bare-IP request is served by the one defined site, which
  matters once the host is public and being
  scanned. Note its `listen` repeats `http2`: nginx takes protocol options from
  the first `listen` for an address:port and `conf.d/` loads before
  `sites-enabled/`, so omitting it silently disables HTTP/2 for the site.
* `set_real_ip_from 127.0.0.1` in `nginx/cloudflare-realip.conf` — cloudflared
  runs on this host, so tunnelled requests arrive from loopback rather than a
  Cloudflare range. Without it `CF-Connecting-IP` is ignored and every visitor
  is logged as 127.0.0.1, collapsing the rate limit into a single bucket.

Point the tunnel at **HTTPS `127.0.0.1:443`** with the hostname as *Origin
Server Name*, not at the uvicorn ports — that keeps nginx in the path, so media
serving, the 500m upload limit and the proxy timeouts still apply.

### The nginx settings that are not defaults

| Directive | Default | Why it changes |
|---|---|---|
| `client_max_body_size 500m` | 1 MB | every admin video upload would 413 |
| `proxy_read_timeout 120s` | 60 s | the Tier-2 AI fallback can outlast 60 s |
| `X-Forwarded-For $remote_addr` | *(append)* | `app/auth/security.py:62` reads the **first** entry, so appending lets a visitor forge it and rotate past the rate limit |
| `location /media/` → `alias` | proxied | a video streamed through uvicorn holds a worker for the whole playback |
| `location = /metrics { return 404; }` | proxied | Prometheus metrics stay off the internet. The scraper reads the app's loopback port, so nothing legitimate needs this path through nginx |

### GPU

The two P40s are Pascal, and getting them working came down to one thing:
**the VM must use EFI firmware.** On BIOS firmware the hypervisor never maps
the 24GB aperture, and the driver refuses both cards:

    NVRM: This PCI I/O region assigned to your NVIDIA device is invalid:
    NVRM: BAR1 is 0M @ 0x0

Reading the device's config space showed BAR1 as `0x0000000c` — a 64-bit
prefetchable BAR whose every address bit was zero, i.e. size zero. A PCI
remove+rescan changed nothing. That is the signature of a hypervisor-side
problem, not a driver one: Linux cannot size a BAR the device does not
advertise. `pciPassthru.use64bitMMIO=TRUE` and `64bitMMIOSizeGB=128` are
required but are **inert on a BIOS VM**.

After switching the VM to EFI:

    Region 1: Memory at 1ff000000000 (64-bit, prefetchable) [size=32G]

Converting an installed BIOS system to EFI needs an ESP, and this disk had no
free space (the root LV had already been extended to fill the volume group).
The fix was a second small virtual disk holding a 1GB ESP. `grub-pc` is
deliberately left installed and the `bios_grub` partition untouched, so
flipping the firmware back to BIOS restores the previous boot exactly — the
rollback is one setting, not a repair.

Three things that cost a boot attempt each, worth setting up front:

- **Secure Boot must be OFF.** `grub-efi-amd64-bin` produces an *unsigned*
  GRUB; Secure Boot rejects it silently, with no message on the console.
- **Install `--removable` LAST.** A later `grub-install --bootloader-id=ubuntu`
  removes `\EFI\BOOT\BOOTX64.EFI`, which is the only path VMware's firmware
  finds when there is no NVRAM entry yet.
- **Raise video memory above 4MB and disable 3D.** The UEFI GOP framebuffer
  needs more than the BIOS console did, and 3D reserves MMIO for nothing.

**Do NOT assert `sm_61` is in `torch.cuda.get_arch_list()`.** The cu124 wheel
lists `sm_50, sm_60, sm_70…` and no `sm_61`, yet drives a P40 correctly: CUDA
guarantees binary compatibility within one major compute capability, so sm_60
cubins run on sm_61. An earlier version of this kit refused to start on exactly
the hardware it was written for. `deploy/21-verify-gpu.sh` and the service now
launch a real kernel instead of matching strings.

Driver 580 is still the last branch supporting Pascal and is held at that
version; torch stays pinned at 2.6.0+cu124.

### TTS

`deploy/tts/server.py` serves `127.0.0.1:8003` with `POST /tts`, `POST
/prerender`, `GET /health`, `GET /voices`. It is loopback-only and has no
authentication — nginx never proxies to it.

The Persian repository ships **only** `t3_fa.safetensors` (2.14 GB, the
fine-tuned T3). The voice encoder, S3 generator, tokenizer and conditionals
still come from `ResembleAI/chatterbox`; the install script fetches both and
installs the Persian checkpoint as `t3_cfg.safetensors`, keeping the English
original as `t3_cfg.en.safetensors` so a rollback is one `mv`.

`Thomcles/Chatterbox-TTS-Persian-Farsi` is **CC BY-NC 4.0 — non-commercial**.
Fine for this project's own install and for evaluation; it cannot ship in an
installation sold to a customer without permission from the author.

**Measured on this host.** GPU (P40, float32): RTF ~1.7 warm, ~3.1 cold.
CPU: RTF ~5 in a standalone process but ~16 through the uvicorn service — a
gap that thread count, OMP/MKL env, a dedicated generation thread, dropping
the worker supervisor and initialisation order all failed to explain. That is
why `deploy/45-prerender.sh` renders in a standalone process rather than
calling the API. Cache hits are ~80ms regardless of device.

**Measure before you design around it.** Run
`/opt/padyar-tts/.venv/bin/python /opt/padyar-tts/benchmark.py`. The published
Chatterbox latencies are RTX 4090 float16 figures and will not transfer. If the
median RTF is above ~1, use `/prerender` to warm every dataset answer at save
time and keep live synthesis for the Tier-2 fallback only.

## Day-2

```bash
systemctl status padyar-myevent padyar-tts
journalctl -u padyar-myevent -f
curl -s localhost:8010/api/health | jq        # liveness only: {"status":"ok"}
curl -s localhost:8010/api/ready | jq        # 503 until the retrieval index is built
curl -s localhost:8003/health | jq

# upgrade the install (re-runs migrations, restarts the service)
sudo bash deploy/10-install-app.sh myevent
```

When `deploy/systemd/padyar-app.service.template` changes, the manual deploy
(`deploy/padyar-deploy.sh`) only restarts the service. It does not re-render
the unit. Re-render it by hand (more in `docs/engineering/MONITORING.md`):
```bash
TMP="$(mktemp)"
if ! sed "s/{{SLUG}}/myevent/g" /opt/padyar-myevent/deploy/systemd/padyar-app.service.template > "$TMP"; then
  echo "STOP: could not render the unit. Nothing was installed."
elif grep -qF '{{' "$TMP" || ! grep -q '^ExecStart=' "$TMP"; then
  echo "STOP: the rendered unit is incomplete. Nothing was installed."
else
  sudo install -m 0644 "$TMP" /etc/systemd/system/padyar-myevent.service
  sudo systemctl daemon-reload && sudo systemctl restart padyar-myevent
fi
rm -f "$TMP"
```

Backups: schedule them in the admin panel (Backup Centre). It shells out to
`pg_dump --format=custom`, which `00-bootstrap-server.sh` installs.

Off-site copy: today no destination exists, so every backup sits on this
server, and Infrastructure > Backups says so in red. The day an SFTP-only
account exists:

1. The gpg PUBLIC key goes in the same panel card, section "کلید عمومی
   رمزگذاری" (paste it or upload the `.asc` file). The PUBLIC key only: the
   private key lives on paper. Each dump is gpg-encrypted before upload. A panel
   key wins. Fallback, only when the panel has no key: set
   `OFFSITE_GPG_PUBLIC_KEY` and `OFFSITE_GPG_FINGERPRINT` once in
   `/opt/padyar-<slug>/.env`, then restart.
2. The operator sets the destination in the admin panel: Infrastructure >
   Backups, card "جای نگه‌داری نسخه‌ها بیرون از سرور". Host, port, user,
   folder (one per install, e.g. `/upload/<slug>`), password or private key,
   and the server's host-key fingerprint (`SHA256:...`, as `ssh-keygen -lf`
   prints it). Then "Save" and "Test connection". The secret is stored
   encrypted and never shown again; the host key is pinned to that
   fingerprint.

The env form still works, and is used only when the panel has no
destination (a panel destination wins):
`OFFSITE_BACKUP_TARGET=sftp:user@host[:port]:/path`,
`OFFSITE_SFTP_IDENTITY_FILE`, `OFFSITE_SFTP_KNOWN_HOSTS` (all host keys of
the destination). `OFFSITE_REMOTE_KEEP` is optional in both forms. Copies are
named after the install's database, so two installs on one path never prune
each other's. Key creation, the paper key, keeping the destination details
off the server, and restore after losing the server are in
`docs/engineering/DEPLOYMENT_RUNBOOK.md` ("کپی رمزشده بیرون از سرور").

## Monitoring

`deploy/55-monitoring.sh` installs one Prometheus, one Alertmanager and three
exporters for the whole host, from the Ubuntu archive, under systemd, every
one on `127.0.0.1` only. Full description: `docs/engineering/MONITORING.md`.

```bash
# 1. Once per host, and again after every rule change. Run the FIRST time
#    outside event hours: it restarts cloudflared, and every site on the host
#    drops for a few seconds.
sudo bash deploy/55-monitoring.sh myevent [otherevent ...] [--host-alerts myevent]

# 2. An install without a usable METRICS_TOKEN stops the run before anything
#    is installed, with the exact command for that install. Run it (it
#    restarts the app), then step 1 again. The two forms it prints:
#    installs created before the key was in the template (no line at all):
echo "METRICS_TOKEN=$(openssl rand -hex 32)" | sudo tee -a /opt/padyar-myevent/.env >/dev/null \
  && sudo systemctl restart padyar-myevent
#    the line is there but empty:
sudo sed -i "s/^METRICS_TOKEN=.*/METRICS_TOKEN=$(openssl rand -hex 32)/" /opt/padyar-myevent/.env \
  && sudo systemctl restart padyar-myevent

# 3. Look at it from your own machine, through SSH only. There is no public URL.
ssh -N -L 9090:127.0.0.1:9090 -L 9093:127.0.0.1:9093 <user>@<host>
#    http://127.0.0.1:9090  Prometheus (Alerts, Graph)
#    http://127.0.0.1:9093  Alertmanager (user operator, password in /root/.secrets/alertmanager-operator.pass)

# Silence an alert before planned work (maintenance mode answers 503):
sudo amtool silence add alertname=PadyarHigh5xxRate install=myevent --duration=2h --comment="maintenance"
```

Firing alerts show in those two UIs. Alerts labelled `page="sms"` are also
texted by each install's watchdog (`docs/engineering/MONITORING.md`, "Watchdog
alert SMS"). The watchdog's own "the app is down" SMS works as before.

The `location = /metrics { return 404; }` block is in
`deploy/nginx/instance.conf.template`. If the script warns that `/metrics` is
reachable through nginx, that install's vhost was not re-rendered since the
block was added. Re-render it with the install's own visitor-facing name (see
"Closing `/metrics` on an existing host" below):

```bash
sudo MAINTENANCE_TITLE='<visitor-facing name>' bash deploy/17-watchdog.sh myevent 8010 myevent.example.com
```

`MAINTENANCE_TITLE` is required: without it the maintenance page visitors see
goes back to the default title. Removing the stack:
`docs/engineering/MONITORING.md`, "Removing the stack".

Running it again with fewer slugs never removes another install's scrape job
or probe. Passwords and token files are created only when missing.
`deploy/padyar-deploy.sh` does not touch the stack: new rules reach the host
only by re-running `55-monitoring.sh`.

## Watchdog & maintenance page

`deploy/17-watchdog.sh` installs a systemd timer that probes each install's
`/api/health` every 60 s. Three consecutive failures (≈3 minutes of real
downtime) send one SMS to the number configured in that install's admin
panel; a 30-minute reminder follows while the outage lasts. Meanwhile nginx
replaces 502/504 with a Persian "we'll be back" page (`MAINTENANCE_TITLE`,
default چت‌بات پایدیار) that reloads itself every 30 s — the app's own 503
(in-app maintenance JSON) passes through untouched.

Test it end-to-end once after install:

```bash
sudo systemctl stop padyar-myevent
journalctl -u padyar-watchdog@myevent.service -f   # one cycle per minute; SMS on the 3rd fail
sudo systemctl start padyar-myevent                # after ≥3 min; recovery is silent by design
```

The alert phone and the SMS-credit threshold live in each install's admin
panel — تنظیمات → ثبت‌نام و پیامک, card «هشدارهای بحرانی» — and are re-read
from the database on every cycle (no restart needed). Empty phone = alerts
off; threshold default 300,000.

Asanak reports the wallet in **rial**; the threshold is typed in **toman**.
The watchdog compares rial against toman × 10, so a 300,000-toman threshold
means 3,000,000 rial at the gateway.

Deploys under ~3 minutes intentionally never SMS: a ~60 s deploy restart
shows the maintenance page but cannot reach the 3-failure threshold. The
page covers the visitor for that window; the phone is reserved for outages
that need a human.

## Closing `/metrics` on an existing host

The vhost template answers `/metrics` with a 404 from nginx (see
`docs/engineering/MONITORING.md`, "Reverse proxy"). A host installed before
that change still proxies `/metrics` to the app, and a normal deploy does not
fix it: `padyar-deploy.sh` never re-renders the vhost. Re-render it once per
install with one of these two scripts. Both rebuild
`/etc/nginx/sites-available/<domain>.conf` from the template, run `nginx -t`,
and reload nginx.

```bash
# Option 1: the nginx script. It skips the certificate when one exists, but in
# the default DNS mode it still stops if /root/.secrets/cloudflare.ini is missing.
sudo bash deploy/15-nginx-and-ssl.sh myevent 8010 chat.example.com

# Option 2: the watchdog script. ALWAYS pass MAINTENANCE_TITLE.
sudo MAINTENANCE_TITLE='<visitor-facing name of the install>' \
  bash deploy/17-watchdog.sh myevent 8010 chat.example.com
```

Why `MAINTENANCE_TITLE` matters: `17-watchdog.sh` also re-renders the
maintenance page that visitors see when the app is down (502/504). Without
the variable it writes the default title, چت‌بات پایدیار, over the install's
own name. Use the same title the install had before. Read it first:

```bash
grep -o '<title>[^<]*' /var/www/padyar/maintenance/myevent/__maintenance.html
```

Check it from the host itself, once per domain:

```bash
curl -sk -o /dev/null -w '%{http_code}\n' --resolve chat.example.com:443:127.0.0.1 https://chat.example.com/metrics   # 404
curl -s -o /dev/null -w '%{http_code}\n' localhost:8010/metrics   # 403 without a token: the app is still there for the scraper
```
