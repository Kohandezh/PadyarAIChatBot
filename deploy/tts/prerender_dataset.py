"""Pre-render every dataset answer into the TTS service's disk cache.

WHY THIS IS A SEPARATE BATCH JOB AND NOT AN API CALL
----------------------------------------------------
The same model, on the same host, runs at RTF ~5 in a standalone process and
RTF ~16 inside the uvicorn service. Thread count, OMP/MKL env, a dedicated
generation thread, dropping the worker supervisor and the order of
set_num_threads() were all ruled out; the cause is still unknown. Bulk
rendering is genuinely batch work, so it runs here at the faster figure and
the service is left to do what it is good at — serving cache hits instantly.

The cache key and the audio encoding are IMPORTED from server.py rather than
reimplemented. If they ever drift apart, the service silently regenerates
everything and this job becomes pointless.

Usage:
    prerender_dataset.py texts.json          # render
    prerender_dataset.py texts.json --dry-run
"""
import json
import os
import sys
import time

sys.path.insert(0, "/opt/padyar-tts")

import numpy as np                                     # noqa: E402
from server import (                                   # noqa: E402
    LANGUAGE,
    cache_key,
    cache_path,
    load_model,
    normalize,
    split_for_synthesis,
    to_wav_bytes,
)

DRY = "--dry-run" in sys.argv
texts = json.load(open(sys.argv[1], encoding="utf-8"))
texts = [t for t in (normalize(t or "") for t in texts) if t]
print(f"{len(texts)} answer(s) to consider, language={LANGUAGE}")

todo = []
for text in texts:
    key = cache_key(text, "", 0.5, 0.5, LANGUAGE)
    if os.path.exists(cache_path(key)):
        continue
    todo.append((key, text))

print(f"  already cached: {len(texts) - len(todo)}")
print(f"  to render:      {len(todo)}")
chars = sum(len(t) for _, t in todo)
print(f"  total characters: {chars}")
if DRY or not todo:
    sys.exit(0)

model = load_model()
print(f"model ready (sr={model.sr})\n")

done = failed = 0
audio_total = wall_total = 0.0
for i, (key, text) in enumerate(todo, 1):
    chunks = split_for_synthesis(text)
    preview = text[:46].replace("\n", " ")
    print(f"[{i}/{len(todo)}] {len(text)} chars, {len(chunks)} chunk(s): {preview}…",
          flush=True)
    t0 = time.perf_counter()
    try:
        pieces = []
        for chunk in chunks:
            wav = model.generate(chunk, language_id=LANGUAGE,
                                 exaggeration=0.5, cfg_weight=0.5)
            pieces.append(wav.squeeze(0).detach().cpu().numpy())
            pieces.append(np.zeros(int(model.sr * 0.12), dtype=np.float32))
        audio = to_wav_bytes(np.concatenate(pieces), model.sr)
    except Exception as exc:                            # noqa: BLE001
        print(f"    FAILED: {type(exc).__name__}: {str(exc)[:120]}")
        failed += 1
        continue

    path = cache_path(key)
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(audio)
    os.replace(tmp, path)                               # atomic

    el = time.perf_counter() - t0
    secs = (len(audio) - 44) / (model.sr * 2)
    audio_total += secs
    wall_total += el
    done += 1
    print(f"    {secs:.1f}s audio in {el:.0f}s (RTF {el/secs:.1f}) -> {key[:12]}…",
          flush=True)

print(f"\nrendered {done}, failed {failed}")
if audio_total:
    print(f"total {audio_total:.0f}s audio in {wall_total/60:.1f} min "
          f"(average RTF {wall_total/audio_total:.1f})")
