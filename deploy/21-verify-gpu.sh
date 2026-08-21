#!/usr/bin/env bash
# Post-reboot GPU verification. Run as any user.
set -uo pipefail
fail=0
ok()   { printf '  \033[1;32mOK\033[0m   %s\n' "$*"; }
bad()  { printf '  \033[1;31mFAIL\033[0m %s\n' "$*"; fail=1; }

echo "== NVIDIA driver =="
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  ok "nvidia-smi runs"
  nvidia-smi --query-gpu=index,name,memory.total,driver_version --format=csv,noheader | sed 's/^/       /'
  count=$(nvidia-smi --query-gpu=index --format=csv,noheader | wc -l)
  [[ "$count" -eq 2 ]] && ok "both P40s present" || bad "expected 2 GPUs, found ${count}"
else
  bad "nvidia-smi does not run — see the .vmx large-MMIO note in 20-gpu-driver.sh"
fi

echo
echo "== PyTorch sm_61 support =="
TTS_PY=/opt/padyar-tts/.venv/bin/python
if [[ -x "$TTS_PY" ]]; then
  "$TTS_PY" - <<'PY'
import torch
print(f"       torch {torch.__version__}")
print(f"       arch list: {torch.cuda.get_arch_list()}")
assert 'sm_61' in torch.cuda.get_arch_list(), \
    "This torch build has NO sm_61 code. Reinstall torch==2.6.0 with --index-url .../cu124."
assert torch.cuda.is_available(), "CUDA not available to torch"
x = torch.randn(2048, 2048, device='cuda')
print(f"       matmul on {torch.cuda.get_device_name(0)}: {(x @ x).sum().item():.1f}")
print("       kernel launch OK")
PY
  [[ $? -eq 0 ]] && ok "torch can launch kernels on the P40" || bad "torch cannot use the P40"
else
  echo "       (skipped — /opt/padyar-tts/.venv not built yet)"
fi

echo
[[ $fail -eq 0 ]] && echo "All GPU checks passed." || { echo "GPU checks FAILED."; exit 1; }
