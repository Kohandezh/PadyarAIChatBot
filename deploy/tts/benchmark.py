"""Measure what Chatterbox actually costs on this Tesla P40.

Run this before designing anything around a latency number. The figures
published for Chatterbox (~0.5 real-time factor, sub-150 ms first sound) are
RTX 4090 float16 numbers. A P40 has no usable float16 path, so the only
number that matters is the one this script prints on this machine.

    sudo -u padyar-tts /opt/padyar-tts/.venv/bin/python /opt/padyar-tts/benchmark.py

RTF (real-time factor) = generation seconds / audio seconds.
  RTF < 1  -> faster than real time; live synthesis is viable
  RTF ~ 1-2 -> live synthesis is a visible pause; pre-render the dataset
  RTF > 3  -> live synthesis is unusable in front of a visitor; pre-render only
"""
import statistics
import time

import torch

from server import load_model, normalize, split_for_synthesis

# Representative of what the chatbot actually says: a greeting, a short
# factual answer, and a long one that will be split into chunks.
SAMPLES = [
    "سلام! به غرفه اینوتکس خوش آمدید.",
    "ساعت کاری نمایشگاه از ساعت نه صبح تا هجده است.",
    "برای ثبت‌نام در نمایشگاه کافی است شماره موبایل خود را وارد کنید و کد "
    "تأیید را دریافت نمایید. پس از تأیید شماره، اطلاعات شغلی و حوزه‌های مورد "
    "علاقه خود را انتخاب کنید تا مسیر بازدید اختصاصی شما ساخته شود.",
]


def main() -> None:
    print("Loading model...")
    t0 = time.perf_counter()
    model = load_model()
    print(f"  load time: {time.perf_counter() - t0:.1f}s")
    if torch.cuda.is_available():
        print(f"  gpu: {torch.cuda.get_device_name(0)}")
        print(f"  vram in use: {torch.cuda.memory_allocated() / 1e9:.2f} GB")
    print(f"  sample rate: {model.sr} Hz\n")

    # One throwaway generation: the first call pays for CUDA context setup and
    # kernel autotuning, and including it would flatter or spoil every average.
    print("Warm-up generation (discarded)...")
    model.generate("سلام")

    rtfs = []
    for text in SAMPLES:
        text = normalize(text)
        chunks = split_for_synthesis(text)
        t0 = time.perf_counter()
        total_samples = 0
        for chunk in chunks:
            wav = model.generate(chunk)
            total_samples += wav.squeeze(0).shape[-1]
        elapsed = time.perf_counter() - t0
        audio_seconds = total_samples / model.sr
        rtf = elapsed / audio_seconds if audio_seconds else float("inf")
        rtfs.append(rtf)
        print(f"{len(text):>4} chars in {len(chunks)} chunk(s): "
              f"{elapsed:6.2f}s generated {audio_seconds:6.2f}s audio  ->  RTF {rtf:.2f}")

    median = statistics.median(rtfs)
    print(f"\nmedian RTF: {median:.2f}")
    if median < 1:
        verdict = "faster than real time — live synthesis is viable"
    elif median < 2:
        verdict = "a visible pause per answer — pre-render every dataset answer"
    else:
        verdict = "too slow to run in front of a visitor — pre-render only, and " \
                  "keep the AI-fallback answers short"
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
