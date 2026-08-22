"""Render a talking-head MP4 from a face asset + a WAV. The spike's entry point.

    python run_lipsync.py --face avatar.jpg --audio answer.wav --out answer.mp4

The face may be a still image or a short video loop of the presenter. The audio
is whatever the Chatterbox TTS service produced. Output is H.264 + AAC, ready to
drop into the same media/videos/ tree the pre-recorded answers already live in.

DESIGN NOTES — read these before "improving" anything:

* **float32 everywhere, no autocast.** On a Tesla P40 (GP102, sm_61) FP16 runs
  at 1/64 of FP32 rate. Half precision on this card is not an optimisation, it
  is a 64x slowdown. Same reason deploy/tts/server.py is float32.

* **The face box is found ONCE, not per frame.** The avatar is a fixed studio
  asset, framed once, and a per-frame detector both costs time and jitters the
  crop, which reads as the head vibrating. If the presenter moves in the loop,
  pass --face-box explicitly or use a tighter loop. This is a deliberate
  simplification, not an oversight.

* **The detector is OpenCV's bundled Haar cascade, not S3FD.** Upstream Wav2Lip
  downloads S3FD weights from a university URL at first run — unusable on an
  offline host in Iran, and one more third-party licence. The cascade ships
  inside opencv-python under BSD-3 and needs no network. It is a worse detector,
  but we only need one box on one cooperative frontal face, once.

* **Batching is the only real speed knob.** The network is tiny (~36M params);
  on a P40 the bottleneck is kernel launch overhead and PCIe, not FLOPs. Raise
  --batch-size until VRAM or throughput stops improving.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import cv2
import numpy as np
import torch

from wav2lip import (IMG_SIZE, load_wav2lip, load_wav_16k_mono,
                     mel_chunks_for_fps, melspectrogram)


# --- face source ------------------------------------------------------------

def read_face_frames(path: str, max_frames: int = 500) -> tuple[list[np.ndarray], float]:
    """Return (frames as BGR uint8, source fps). A still image gives one frame.

    The whole loop is held in RAM, so the cap is not arbitrary: at 768x768 a
    frame is 1.7 MB, and this box has 28 GB shared with two chatbots, Postgres
    and the TTS. 500 frames (20 s at 25 fps) is ~0.9 GB and far longer than any
    idle loop needs to be.
    """
    if not os.path.exists(path):
        sys.exit(f"face asset not found: {path}")

    image = cv2.imread(path)
    if image is not None:
        return [image], 0.0

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        sys.exit(f"could not read {path} as either an image or a video")
    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    frames = []
    while len(frames) < max_frames:
        ok, frame = capture.read()
        if not ok:
            break
        frames.append(frame)
    capture.release()
    if not frames:
        sys.exit(f"{path} contained no decodable frames")
    return frames, fps


def loop_indices(frame_count: int, needed: int) -> list[int]:
    """Ping-pong (0,1,2,...,n-1,n-2,...,1,0,...) rather than restarting at 0.

    A plain loop jump-cuts every time it wraps — the presenter's head teleports.
    Playing the loop forwards then backwards is seamless for any clip that does
    not contain directional motion, which a talking-head idle loop does not.
    """
    if frame_count == 1:
        return [0] * needed
    cycle = list(range(frame_count)) + list(range(frame_count - 2, 0, -1))
    return [cycle[i % len(cycle)] for i in range(needed)]


def detect_face_box(frame: np.ndarray, pads: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """(x1, y1, x2, y2) of the face crop to drive, from one frame."""
    cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    cascade = cv2.CascadeClassifier(cascade_path)
    grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(grey, scaleFactor=1.1, minNeighbors=5,
                                     minSize=(60, 60))
    if len(faces) == 0:
        sys.exit("no face found in the first frame. Pass --face-box x1,y1,x2,y2 "
                 "(the crop must contain the mouth and the chin).")
    # Largest box: on an exhibition backdrop the cascade sometimes fires on
    # background texture, and the presenter is always the biggest face present.
    x, y, w, h = max(faces, key=lambda f: f[2] * f[3])

    top, bottom, left, right = pads
    height, width = frame.shape[:2]
    return (max(0, x - left), max(0, y - top),
            min(width, x + w + right), min(height, y + h + bottom))


def feathered_paste(frame: np.ndarray, patch: np.ndarray,
                    box: tuple[int, int, int, int], feather: int) -> None:
    """Paste the generated mouth region back with a soft edge, in place.

    Upstream pastes a hard rectangle, which leaves a visible seam wherever the
    generator's colour statistics differ slightly from the source frame — very
    obvious on a large exhibition screen. A few pixels of alpha ramp costs
    nothing and removes it.
    """
    x1, y1, x2, y2 = box
    height, width = y2 - y1, x2 - x1
    if feather <= 0:
        frame[y1:y2, x1:x2] = patch
        return

    ramp_y = np.minimum(np.arange(height), height - 1 - np.arange(height))
    ramp_x = np.minimum(np.arange(width), width - 1 - np.arange(width))
    alpha = np.minimum(np.minimum(ramp_y[:, None], ramp_x[None, :]) / feather, 1.0)
    alpha = alpha[:, :, None].astype(np.float32)
    region = frame[y1:y2, x1:x2].astype(np.float32)
    frame[y1:y2, x1:x2] = (alpha * patch.astype(np.float32)
                           + (1 - alpha) * region).astype(np.uint8)


# --- rendering --------------------------------------------------------------

def render(args) -> dict:
    timings: dict[str, float] = {}

    t0 = time.perf_counter()
    wav = load_wav_16k_mono(args.audio)
    mel = melspectrogram(wav)
    if np.isnan(mel).any():
        sys.exit("mel contains NaN — the audio is probably silent or corrupt.")
    audio_seconds = len(wav) / 16000.0
    timings["audio"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    source_frames, source_fps = read_face_frames(args.face)
    fps = args.fps or (source_fps if source_fps > 1 else 25.0)
    mel_chunks = mel_chunks_for_fps(mel, fps)
    if not mel_chunks:
        sys.exit("audio is shorter than the model's 0.2s context window.")
    order = loop_indices(len(source_frames), len(mel_chunks))

    if args.face_box:
        parts = args.face_box.split(",")
        if len(parts) != 4 or not all(p.strip().lstrip("-").isdigit() for p in parts):
            sys.exit(f"--face-box must be four integers x1,y1,x2,y2 — got {args.face_box!r}")
        box = tuple(int(p) for p in parts)
    else:
        box = detect_face_box(source_frames[0], tuple(args.pads))
    x1, y1, x2, y2 = box
    if x2 - x1 < 32 or y2 - y1 < 32:
        sys.exit(f"face box {box} is too small to drive.")
    timings["face_prep"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    model = load_wav2lip(args.checkpoint, args.device)
    timings["model_load"] = time.perf_counter() - t0

    height, width = source_frames[0].shape[:2]
    encoder = start_encoder(args.out, width, height, fps, args.audio, args.crf)

    infer_seconds = 0.0
    device = torch.device(args.device)
    try:
        for start in range(0, len(mel_chunks), args.batch_size):
            chunk_slice = mel_chunks[start:start + args.batch_size]
            frame_slice = [source_frames[i] for i in order[start:start + args.batch_size]]

            faces = np.stack([
                cv2.resize(f[y1:y2, x1:x2], (IMG_SIZE, IMG_SIZE))
                for f in frame_slice
            ]).astype(np.float32) / 255.0
            masked = faces.copy()
            # The lower half is blanked: the network's job is to INVENT the
            # mouth from the audio, and leaving it visible lets it copy the
            # source frame's mouth instead of listening.
            masked[:, IMG_SIZE // 2:] = 0.0
            face_input = torch.from_numpy(
                np.concatenate((masked, faces), axis=3).transpose(0, 3, 1, 2)
            ).to(device)
            mel_input = torch.from_numpy(
                np.stack(chunk_slice)[:, None, :, :]
            ).to(device)

            t_infer = time.perf_counter()
            with torch.no_grad():
                pred = model(mel_input, face_input)
            if args.device == "cuda":
                torch.cuda.synchronize()
            infer_seconds += time.perf_counter() - t_infer

            patches = (pred.cpu().numpy().transpose(0, 2, 3, 1) * 255.0) \
                .clip(0, 255).astype(np.uint8)
            for source, patch in zip(frame_slice, patches):
                frame = source.copy()
                feathered_paste(frame, cv2.resize(patch, (x2 - x1, y2 - y1)),
                                box, args.feather)
                encoder.stdin.write(frame.tobytes())
    finally:
        encoder.stdin.close()
        if encoder.wait() != 0:
            sys.exit("ffmpeg failed to write the output file")

    timings["inference"] = infer_seconds
    timings["audio_seconds"] = audio_seconds
    timings["frames"] = len(mel_chunks)
    timings["fps"] = fps
    return timings


def start_encoder(out_path: str, width: int, height: int, fps: float,
                  audio_path: str, crf: int) -> subprocess.Popen:
    """One ffmpeg process: raw BGR frames in on stdin, muxed H.264+AAC out.

    Writing frames to a temp AVI and re-encoding (what upstream does) doubles
    the disk I/O and loses a generation of quality for no benefit.
    """
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    return subprocess.Popen(
        ["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}",
         "-r", f"{fps}", "-i", "-",
         "-i", audio_path,
         "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
         # yuv420p + faststart: anything else and Safari on an exhibition
         # tablet shows a black rectangle.
         "-pix_fmt", "yuv420p", "-movflags", "+faststart",
         "-c:a", "aac", "-b:a", "128k", "-shortest", out_path],
        stdin=subprocess.PIPE,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--face", required=True, help="still image or short video loop")
    parser.add_argument("--audio", required=True, help="WAV/MP3 from the TTS service")
    parser.add_argument("--out", required=True, help="output .mp4")
    parser.add_argument("--checkpoint", default=os.getenv("LIPSYNC_CHECKPOINT", "weights/wav2lip_gan.pth"))
    parser.add_argument("--device", default=os.getenv("LIPSYNC_DEVICE", "cuda"),
                        choices=["cuda", "cpu"])
    parser.add_argument("--fps", type=float, default=0.0,
                        help="output fps (default: the source video's, or 25 for a still)")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--face-box", default="",
                        help="x1,y1,x2,y2 — skips detection. Use for a fixed avatar.")
    parser.add_argument("--pads", type=int, nargs=4, default=[0, 20, 0, 0],
                        metavar=("TOP", "BOTTOM", "LEFT", "RIGHT"),
                        help="grow the detected box; the default adds chin, which "
                             "the cascade cuts off and the model needs")
    parser.add_argument("--feather", type=int, default=6,
                        help="pixels of alpha ramp when pasting back (0 = hard edge)")
    parser.add_argument("--crf", type=int, default=20)
    args = parser.parse_args()

    if not os.path.exists(args.checkpoint):
        sys.exit(f"checkpoint not found: {args.checkpoint}\n"
                 "See README.md — it must be staged manually; nothing here downloads.")
    if args.device == "cuda" and not torch.cuda.is_available():
        sys.exit("--device cuda but torch reports no CUDA. Use --device cpu, or "
                 "check the driver (deploy/21-verify-gpu.sh).")

    wall = time.perf_counter()
    t = render(args)
    wall = time.perf_counter() - wall

    print(f"\n{args.out}")
    print(f"  {t['frames']} frames @ {t['fps']:.0f} fps "
          f"= {t['audio_seconds']:.2f}s of video")
    print(f"  model load : {t['model_load']:6.2f}s (paid once per process)")
    print(f"  audio+mel  : {t['audio']:6.2f}s")
    print(f"  face prep  : {t['face_prep']:6.2f}s")
    print(f"  inference  : {t['inference']:6.2f}s")
    print(f"  wall clock : {wall:6.2f}s")
    # The number that decides the architecture: below ~1 you could generate in
    # front of a visitor; above it, you must pre-render. See README.
    print(f"\n  seconds of compute per second of video: "
          f"{(wall - t['model_load']) / t['audio_seconds']:.2f}")


if __name__ == "__main__":
    main()
