"""Stage the Chatterbox MULTILINGUAL base next to the Persian fine-tune.

The Persian checkpoint (t3_fa.safetensors) has a 2454-token text embedding.
The English Chatterbox base has 704. Pairing them fails at load with a
size mismatch, which is how we learned this fine-tune descends from
Chatterbox Multilingual, not from the English release.

ChatterboxMultilingualTTS.from_local() also expects different filenames:
ve.pt / s3gen.pt / grapheme_mtl_merged_expanded_v1.json / conds.pt /
Cangjie5_TC.json, plus the T3 as t3_mtl23ls_v2.safetensors.
"""
import os
import shutil
import sys

from huggingface_hub import hf_hub_download

DEST = sys.argv[1] if len(sys.argv) > 1 else "/var/lib/padyar/tts/models/chatterbox-mtl"
REPO = "ResembleAI/chatterbox"
BASE_FILES = [
    "ve.pt",
    "s3gen.pt",
    "grapheme_mtl_merged_expanded_v1.json",
    "conds.pt",
    "Cangjie5_TC.json",
]

os.makedirs(DEST, exist_ok=True)
failed = []
for name in BASE_FILES:
    target = os.path.join(DEST, name)
    if os.path.exists(target) and os.path.getsize(target) > 0:
        print(f"  have: {name}")
        continue
    try:
        src = hf_hub_download(repo_id=REPO, filename=name)
        shutil.copyfile(src, target)
        print(f"  ok:   {name}  ({os.path.getsize(target)/1e6:.1f} MB)")
    except Exception as exc:                      # noqa: BLE001
        print(f"  FAIL: {name}  {type(exc).__name__}: {str(exc)[:100]}")
        failed.append(name)

# The Persian T3 takes the place of the multilingual one.
fa = "/var/lib/padyar/tts/models/chatterbox-fa/t3_cfg.safetensors"
if os.path.exists(fa):
    shutil.copyfile(fa, os.path.join(DEST, "t3_mtl23ls_v2.safetensors"))
    print("  ok:   t3_fa -> t3_mtl23ls_v2.safetensors (Persian checkpoint active)")
else:
    print(f"  FAIL: Persian checkpoint not found at {fa}")
    failed.append("t3_fa")

print("\nRESULT:", "incomplete: " + ", ".join(failed) if failed else "complete")
sys.exit(1 if failed else 0)
