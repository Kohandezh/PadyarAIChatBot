"""Work out how to drive the Persian fine-tune, empirically.

Two unknowns the model card does not answer (its usage lives in a Colab
notebook that requires a Google sign-in):
  1. does the Persian T3 load into the multilingual model at all?
  2. `fa` is NOT one of the base's 23 SUPPORTED_LANGUAGES — so what
     language_id should Persian text be tokenised under?
"""
import sys
import time

import torch
from chatterbox import mtl_tts
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

CKPT = "/var/lib/padyar/tts/models/chatterbox-mtl"
TEXT = "سلام! به آزمایش تبدیل متن به گفتار خوش آمدید."

print("== 1. load ==")
t0 = time.perf_counter()
try:
    model = ChatterboxMultilingualTTS.from_local(CKPT, device="cpu")
except Exception as exc:                          # noqa: BLE001
    sys.exit(f"  LOAD FAILED: {type(exc).__name__}: {exc}")
print(f"  loaded in {time.perf_counter()-t0:.1f}s, sample rate {model.sr} Hz")

emb = model.t3.text_emb.weight.shape
print(f"  text_emb: {tuple(emb)}   (2454 = Persian fine-tune)")

print("\n== 2. how does the tokenizer treat Persian under each language_id? ==")
# 'fa' is not in the base list, so add it before asking the tokenizer.
mtl_tts.SUPPORTED_LANGUAGES.setdefault("fa", "Persian")
results = {}
for lang in ("fa", "ar", "en", "tr"):
    try:
        toks = model.tokenizer.text_to_tokens(TEXT, language_id=lang)
        ids = toks.flatten().tolist()
        results[lang] = ids
        print(f"  {lang}: {len(ids)} tokens  head={ids[:12]}")
    except Exception as exc:                      # noqa: BLE001
        print(f"  {lang}: FAILED {type(exc).__name__}: {str(exc)[:80]}")

if "fa" in results:
    same = [l for l in results if l != "fa" and results[l] == results["fa"]]
    print(f"  identical to 'fa': {same or 'none — language_id changes tokenisation'}")
    unk = results["fa"].count(0)
    print(f"  zero/UNK tokens under 'fa': {unk}")

print("\n== 3. generate a short sample ==")
torch.set_num_threads(8)
short = "سلام"
t0 = time.perf_counter()
try:
    wav = model.generate(short, language_id="fa")
    dur = wav.squeeze(0).shape[-1] / model.sr
    el = time.perf_counter() - t0
    print(f"  OK: {dur:.2f}s of audio in {el:.1f}s  (RTF {el/dur:.2f})")
    import torchaudio
    torchaudio.save("/tmp/probe_fa.wav", wav.cpu(), model.sr)
    print("  wrote /tmp/probe_fa.wav")
except Exception as exc:                          # noqa: BLE001
    print(f"  GENERATE FAILED: {type(exc).__name__}: {str(exc)[:200]}")
