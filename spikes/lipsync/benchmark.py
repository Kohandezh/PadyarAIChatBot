"""Measure what Wav2Lip actually costs on THIS machine.

Every published Wav2Lip speed number was measured on a card with a working
float16 path. A Tesla P40 has none — FP16 on GP102 runs at 1/64 of FP32 — so
the only figure worth designing against is the one this script prints on the
host you will deploy on. Same reasoning as deploy/tts/benchmark.py.

    python benchmark.py --checkpoint weights/wav2lip_gan.pth --face avatar.jpg

The number that decides the architecture is "seconds of compute per second of
video" (call it C):

  C < 0.3   -> a 20 s answer renders in 6 s; live generation is arguable
  C ~ 1     -> a 20 s answer renders in 20 s; a visitor will not wait
  C > 2     -> pre-render only, no argument

For context, the TTS in front of this stage already measures ~1.7 on the same
box, and those two costs ADD UP: audio must exist before a frame can be drawn.
"""
from __future__ import annotations

import argparse
import time

import cv2
import numpy as np
import torch

from wav2lip import IMG_SIZE, load_wav2lip


def bench_batch(model, device: str, batch: int, iterations: int) -> float:
    """Seconds per generated frame at this batch size."""
    face = torch.randn(batch, 6, IMG_SIZE, IMG_SIZE, device=device)
    mel = torch.randn(batch, 1, 80, 16, device=device)

    # Warm-up: the first call pays for CUDA context creation, cuDNN algorithm
    # selection and memory-pool growth. Including it would spoil every average.
    with torch.no_grad():
        model(mel, face)
    if device == "cuda":
        torch.cuda.synchronize()

    start = time.perf_counter()
    with torch.no_grad():
        for _ in range(iterations):
            model(mel, face)
    if device == "cuda":
        torch.cuda.synchronize()
    return (time.perf_counter() - start) / (iterations * batch)


def bench_frame_io(face_path: str, iterations: int) -> float:
    """Seconds per frame for the CPU work that surrounds the network.

    Measured separately because on a P40 it is NOT negligible: crop, resize,
    paste-back and H.264 encode all run on the same host that is already
    serving two chatbots and a TTS. A GPU number alone would overpromise.
    """
    image = cv2.imread(face_path)
    if image is None:
        raise SystemExit(f"could not read {face_path}")
    box = (0, 0, min(256, image.shape[1]), min(256, image.shape[0]))
    x1, y1, x2, y2 = box

    start = time.perf_counter()
    for _ in range(iterations):
        crop = cv2.resize(image[y1:y2, x1:x2], (IMG_SIZE, IMG_SIZE))
        arr = crop.astype(np.float32) / 255.0
        masked = arr.copy()
        masked[IMG_SIZE // 2:] = 0
        np.concatenate((masked, arr), axis=2).transpose(2, 0, 1)
        patch = cv2.resize(crop, (x2 - x1, y2 - y1))
        image[y1:y2, x1:x2] = patch
    return (time.perf_counter() - start) / iterations


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="weights/wav2lip_gan.pth")
    parser.add_argument("--face", default="", help="a real frame, for the CPU-side measurement")
    parser.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    parser.add_argument("--fps", type=float, default=25.0)
    parser.add_argument("--batches", type=int, nargs="+", default=[1, 8, 16, 32, 64, 128])
    parser.add_argument("--iterations", type=int, default=10)
    args = parser.parse_args()

    if args.device == "cuda" and not torch.cuda.is_available():
        raise SystemExit("no CUDA. Use --device cpu.")

    print(f"torch {torch.__version__}")
    if args.device == "cuda":
        print(f"gpu   {torch.cuda.get_device_name(0)}")
        major, minor = torch.cuda.get_device_capability(0)
        print(f"arch  sm_{major}{minor}")
        # A Pascal card is not in the wheel's arch list on some builds and runs
        # anyway (CUDA minor-version compatibility). Printing both means a
        # future reader can tell "wrong wheel" from "wrong expectation".
        print(f"wheel arches: {torch.cuda.get_arch_list()}")
        if major == 6:
            print("note: Pascal. float32 only — do NOT add autocast/half() to "
                  "make this faster; it makes it 64x slower.")

    t0 = time.perf_counter()
    model = load_wav2lip(args.checkpoint, args.device)
    print(f"model load: {time.perf_counter() - t0:.1f}s")
    if args.device == "cuda":
        print(f"weights vram: {torch.cuda.memory_allocated() / 1e6:.0f} MB\n")

    best = None
    print(f"{'batch':>6} {'ms/frame':>10} {'frames/s':>10} {'peak VRAM':>11}   C (compute s / video s)")
    for batch in args.batches:
        if args.device == "cuda":
            torch.cuda.reset_peak_memory_stats()
        try:
            per_frame = bench_batch(model, args.device, batch, args.iterations)
        except torch.cuda.OutOfMemoryError:
            print(f"{batch:>6}  OOM — this is the ceiling on this card")
            break
        vram = torch.cuda.max_memory_allocated() / 1e9 if args.device == "cuda" else 0.0
        compute_ratio = per_frame * args.fps
        print(f"{batch:>6} {per_frame * 1000:>10.2f} {1 / per_frame:>10.1f} "
              f"{vram:>10.2f}G   {compute_ratio:>6.2f}")
        if best is None or per_frame < best[1]:
            best = (batch, per_frame)

    if args.face:
        io_per_frame = bench_frame_io(args.face, 200)
        print(f"\ncpu-side per frame (crop/resize/paste): {io_per_frame * 1000:.2f} ms")
    else:
        io_per_frame = 0.0
        print("\n(pass --face to also measure the CPU-side cost; it is not free)")

    batch, per_frame = best
    total = (per_frame + io_per_frame) * args.fps
    print(f"\nbest batch: {batch}")
    print(f"seconds of compute per second of video: {total:.2f}  (network + cpu, "
          f"excluding H.264 encode)")
    if total < 0.3:
        verdict = "fast enough that live generation is worth costing out"
    elif total < 1.0:
        verdict = ("faster than real time, but the visitor still waits for the "
                   "WHOLE clip before the first frame plays — pre-render")
    else:
        verdict = "pre-render only; never generate in front of a visitor"
    print(f"verdict: {verdict}")


if __name__ == "__main__":
    main()
