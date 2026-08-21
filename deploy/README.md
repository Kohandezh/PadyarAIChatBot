# Deployment kit — two chatbots + Persian TTS on one Ubuntu 24.04 host

Target: `gpu@192.168.100.6`, 40 vCPU / 27 GB RAM / 2× Tesla P40.

| Install | Domain | Port | DB | Linux user |
|---|---|---|---|---|
| INOTEX | `inotex.padyar.com` | 8001 | `padyar_inotex` | `padyar-inotex` |
| ELECOMP | `elecomp.padyar.com` | 8002 | `padyar_elecomp` | `padyar-elecomp` |
| TTS | *(loopback only)* | 8003 | — | `padyar-tts` |

Everything here is idempotent — re-running a script is safe.

## Order of operations

```bash
# On the server, as a user with sudo:
git clone https://github.com/Kohandezh/PadyarAIChatBot.git /tmp/padyar-deploy
cd /tmp/padyar-deploy

sudo bash deploy/00-bootstrap-server.sh      # packages, users, dirs, PG, UFW, fail2ban
sudo bash deploy/05-create-databases.sh      # 2 DBs + roles — SAVE THE PRINTED PASSWORDS

# Fill in the two .env files before installing:
sudo install -m 0600 deploy/env/inotex.env.template  /opt/padyar-inotex/.env
sudo install -m 0600 deploy/env/elecomp.env.template /opt/padyar-elecomp/.env
sudo nano /opt/padyar-inotex/.env            # every <PLACEHOLDER>, incl. SECRET_KEY
sudo nano /opt/padyar-elecomp/.env

sudo bash deploy/10-install-app.sh inotex
sudo bash deploy/10-install-app.sh elecomp
sudo bash deploy/15-nginx-and-ssl.sh         # needs a Cloudflare API token, see below

# GPU + TTS (independent of the two apps above):
sudo bash deploy/20-gpu-driver.sh
sudo reboot
bash deploy/21-verify-gpu.sh
sudo HF_TOKEN=hf_xxx bash deploy/25-install-tts.sh

bash deploy/30-verify.sh                     # end-to-end smoke test
```

## Things that will bite you, and why

**`PADYAR_ENV=production` makes the app refuse to boot on a bad config.**
That is deliberate (`app/prodcheck.py`). It blocks on: `COOKIE_SECURE` not
true, a non-PostgreSQL backend, a placeholder or passwordless `DATABASE_URL`,
empty or `*` `ALLOWED_ORIGINS`, a placeholder `ADMIN_PASSWORD`, and
`OTP_DELIVERY=dev`. The refusal names every problem at once.

`OTP_DELIVERY=dev` blocks **even on the ELECOMP install where the registration
module is disabled** — the gate reads the environment variable, not the module
list. Both templates set `OTP_DELIVERY=asanak` for that reason.

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

Both names resolve to `172.67.141.4` — Cloudflare's edge — and today both
return **HTTP 525** (Cloudflare cannot complete TLS with the origin). Two
consequences:

1. **Certificates use DNS-01, not `--nginx`.** An http-01 challenge has to
   survive the edge; a dns-01 challenge does not care whether the origin is
   even reachable yet. `15-nginx-and-ssl.sh` needs a token from
   <https://dash.cloudflare.com/profile/api-tokens> with *Zone → DNS → Edit* on
   `padyar.com`, at `/root/.secrets/cloudflare.ini`. Pass `CERT_MODE=http` to
   use webroot instead.

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
* meanwhile nginx serves both sites correctly on the LAN.

So the refusal happens at the router or the ISP, one hop above the machine,
and no origin-side change can fix it. `deploy/40-cloudflare-tunnel.sh` installs
`cloudflared`, which dials **out** to Cloudflare — no inbound port, no
port-forward rule, no static IP, unaffected by ISP inbound policy or CGNAT.

Two things that go with it, both already in this kit:

* `nginx/00-default-server.conf` — returns 444 for any unmatched `Host`.
  Without it a bare-IP request is served by whichever site loaded first
  (alphabetically elecomp), which matters once the host is public and being
  scanned. Note its `listen` repeats `http2`: nginx takes protocol options from
  the first `listen` for an address:port and `conf.d/` loads before
  `sites-enabled/`, so omitting it silently disables HTTP/2 for both sites.
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
Fine for INOTEX's own install and for evaluation; it cannot ship in an
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
systemctl status padyar-inotex padyar-elecomp padyar-tts
journalctl -u padyar-inotex -f
curl -s localhost:8001/api/health | jq
curl -s localhost:8001/api/ready | jq        # 503 until the retrieval index is built
curl -s localhost:8003/health | jq

# upgrade an install (re-runs migrations, restarts the service)
sudo bash deploy/10-install-app.sh inotex
```

Backups: schedule them in the admin panel (Backup Centre). It shells out to
`pg_dump --format=custom`, which `00-bootstrap-server.sh` installs.
