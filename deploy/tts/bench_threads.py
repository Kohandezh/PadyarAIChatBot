"""Measure CPU thread scaling and produce a listenable Persian sample."""
import os
import sys
import time

import torch
import torchaudio
from chatterbox import mtl_tts
from chatterbox.mtl_tts import ChatterboxMultilingualTTS

mtl_tts.SUPPORTED_LANGUAGES.setdefault("fa", "Persian")
CKPT = "/var/lib/padyar/tts/models/chatterbox-mtl"
THREADS = int(sys.argv[1]) if len(sys.argv) > 1 else 32
TEXT = "سلام! به غرفه اینوتکس خوش آمدید."

torch.set_num_threads(THREADS)
print(f"threads={THREADS} (torch reports {torch.get_num_threads()})")

model = ChatterboxMultilingualTTS.from_local(CKPT, device="cpu")
print(f"loaded, sr={model.sr}")

# Warm-up: the first generation pays for lazy init and would distort the number.
model.generate("سلام", language_id="fa")

t0 = time.perf_counter()
wav = model.generate(TEXT, language_id="fa")
el = time.perf_counter() - t0
dur = wav.squeeze(0).shape[-1] / model.sr
print(f"RESULT threads={THREADS} audio={dur:.2f}s wall={el:.1f}s RTF={el/dur:.2f}")

out = f"/tmp/sample_fa_{THREADS}t.wav"
torchaudio.save(out, wav.cpu(), model.sr)
print("wrote", out, os.path.getsize(out), "bytes")
