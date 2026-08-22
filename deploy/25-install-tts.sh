#!/usr/bin/env bash
# Install the Chatterbox Persian TTS service on 127.0.0.1:8003.
#
#   sudo bash deploy/25-install-tts.sh
#
# Run AFTER 20-gpu-driver.sh and the reboot, and after 21-verify-gpu.sh shows
# both cards. Model weights come from Hugging Face; if this host cannot reach
# huggingface.co, stage them on a machine that can and set:
#
#   sudo TTS_MODEL_SRC=/path/to/staged/dir bash deploy/25-install-tts.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then echo "Run with sudo: sudo bash $0" >&2; exit 1; fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR=/opt/padyar-tts
MODEL_DIR=/var/lib/padyar/tts/models/chatterbox-fa
USER=padyar-tts
BASE_REPO=ResembleAI/chatterbox
FA_REPO=Thomcles/Chatterbox-TTS-Persian-Farsi

log() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }

log "Installing the service code into ${APP_DIR}"
install -o "$USER" -g "$USER" -m 0644 "${HERE}/tts/server.py" "${APP_DIR}/server.py"
install -o "$USER" -g "$USER" -m 0644 "${HERE}/tts/requirements.txt" "${APP_DIR}/requirements.txt"
install -o "$USER" -g "$USER" -m 0644 "${HERE}/tts/benchmark.py" "${APP_DIR}/benchmark.py"

log "Building the virtualenv"
if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  sudo -u "$USER" python3 -m venv "${APP_DIR}/.venv"
fi
sudo -u "$USER" "${APP_DIR}/.venv/bin/pip" install --upgrade pip wheel

log "Installing torch 2.6.0 + cu124 (the last build with sm_61 kernels)"
sudo -u "$USER" "${APP_DIR}/.venv/bin/pip" install \
  torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124

log "Verifying this torch can actually drive a P40"
# NOT a string match on get_arch_list(). The cu124 wheel ships sm_50, sm_60,
# sm_70+ and NO sm_61, yet it drives a P40 correctly: CUDA guarantees binary
# compatibility within one major compute capability, so sm_60 cubins run on
# sm_61. An earlier version of this file asserted sm_61 was listed and would
# therefore have refused to install on exactly the hardware it was written for
# — it never fired only because the GPUs were broken when the host was first
# provisioned, so the check was skipped. server.py and 21-verify-gpu.sh were
# corrected; this one was missed. The only honest test is to launch a kernel.
sudo -u "$USER" "${APP_DIR}/.venv/bin/python" - <<'PY'
import sys, torch
print("torch", torch.__version__)
if not torch.cuda.is_available():
    print("WARNING: CUDA is not available (no working driver yet).")
    print("         Install continues; the service will run on CPU until")
    print("         deploy/21-verify-gpu.sh passes and TTS_DEVICE=cuda is set.")
    sys.exit(0)
cap = torch.cuda.get_device_capability(0)
name = torch.cuda.get_device_name(0)
print(f"device: {name} (sm_{cap[0]}{cap[1]})   arches: {torch.cuda.get_arch_list()}")
try:
    probe = torch.zeros(64, 64, device="cuda")
    (probe @ probe).sum().item()
    torch.cuda.synchronize()
except Exception as exc:
    sys.exit(f"FATAL: CUDA kernels will not run on {name} with torch "
             f"{torch.__version__}: {exc}")
print("kernel launch OK — this build can drive this card")
PY

log "Installing chatterbox-tts and the service dependencies"
sudo -u "$USER" "${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

log "Staging model weights into ${MODEL_DIR}"
install -d -o "$USER" -g "$USER" -m 0755 "$MODEL_DIR" /var/lib/padyar/tts/voices

if [[ -n "${TTS_MODEL_SRC:-}" ]]; then
  echo "  copying from ${TTS_MODEL_SRC}"
  rsync -a "${TTS_MODEL_SRC}/" "${MODEL_DIR}/"
  chown -R "$USER:$USER" "$MODEL_DIR"
else
  if [[ -z "${HF_TOKEN:-}" ]]; then
    cat >&2 <<'MSG'

HF_TOKEN is not set.

Thomcles/Chatterbox-TTS-Persian-Farsi is a GATED repository: you must open
it in a browser, accept the contact-sharing condition, and then use a token
from https://huggingface.co/settings/tokens

  sudo HF_TOKEN=hf_xxx bash deploy/25-install-tts.sh

If this server cannot reach huggingface.co, download the six files listed
below on another machine and use TTS_MODEL_SRC instead.
MSG
    exit 1
  fi
  sudo -u "$USER" "${APP_DIR}/.venv/bin/pip" install --quiet "huggingface_hub[cli]"
  sudo -u "$USER" env HF_TOKEN="$HF_TOKEN" HF_HOME=/var/lib/padyar/tts/models/hf \
    "${APP_DIR}/.venv/bin/python" - "$MODEL_DIR" "$BASE_REPO" "$FA_REPO" <<'PY'
import shutil, sys
from huggingface_hub import hf_hub_download

model_dir, base_repo, fa_repo = sys.argv[1], sys.argv[2], sys.argv[3]

# The Persian repo ships ONLY the fine-tuned T3 (t3_fa.safetensors, 2.14 GB).
# Everything else — the voice encoder, the S3 generator, the tokenizer and the
# built-in conditionals — still has to come from the base Chatterbox release.
for name in ("ve.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt"):
    src = hf_hub_download(repo_id=base_repo, filename=name)
    shutil.copyfile(src, f"{model_dir}/{name}")
    print(f"  base: {name}")

# ChatterboxTTS.from_local() loads the T3 from 't3_cfg.safetensors', so the
# Persian checkpoint is installed under that name. The English original is
# kept beside it so a bad fine-tune can be undone with one mv.
base_t3 = hf_hub_download(repo_id=base_repo, filename="t3_cfg.safetensors")
shutil.copyfile(base_t3, f"{model_dir}/t3_cfg.en.safetensors")
print("  base: t3_cfg.safetensors -> t3_cfg.en.safetensors (kept for rollback)")

fa_t3 = hf_hub_download(repo_id=fa_repo, filename="t3_fa.safetensors")
shutil.copyfile(fa_t3, f"{model_dir}/t3_cfg.safetensors")
print("  persian: t3_fa.safetensors -> t3_cfg.safetensors (active)")
PY
  chown -R "$USER:$USER" /var/lib/padyar/tts
fi

log "Checking the checkpoint set is complete"
missing=0
for f in ve.safetensors t3_cfg.safetensors s3gen.safetensors tokenizer.json; do
  if [[ -s "${MODEL_DIR}/${f}" ]]; then
    printf '  %-24s %s\n' "$f" "$(du -h "${MODEL_DIR}/${f}" | cut -f1)"
  else
    echo "  MISSING: ${f}" >&2; missing=1
  fi
done
[[ -s "${MODEL_DIR}/conds.pt" ]] || echo "  note: conds.pt absent — a reference voice will be required per request"
[[ $missing -eq 0 ]] || { echo "Checkpoint set incomplete; not starting the service." >&2; exit 1; }

log "Installing the systemd unit"
install -m 0644 "${HERE}/systemd/padyar-tts.service" /etc/systemd/system/padyar-tts.service
systemctl daemon-reload
systemctl enable padyar-tts
systemctl restart padyar-tts

log "Waiting for the model to load (this takes a few minutes on a P40)"
for i in $(seq 1 300); do
  body=$(curl -fsS --max-time 3 http://127.0.0.1:8003/health 2>/dev/null || true)
  if [[ -n "$body" ]]; then
    echo "$body" | python3 -m json.tool
    if echo "$body" | tr -d ' ' | grep -q '"model_loaded":true'; then
      log "TTS is up. Now measure it:  sudo -u padyar-tts /opt/padyar-tts/.venv/bin/python /opt/padyar-tts/benchmark.py"
      exit 0
    fi
  fi
  sleep 2
done

echo "TTS did not become ready. Last 40 log lines:" >&2
journalctl -u padyar-tts -n 40 --no-pager >&2
exit 1
