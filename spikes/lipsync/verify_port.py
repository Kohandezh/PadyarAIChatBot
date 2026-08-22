"""Prove that wav2lip.py is a faithful port, not a plausible-looking one.

wav2lip.py re-implements two things upstream gets from libraries: the mel
front-end (upstream: librosa) and the network (upstream: models/wav2lip.py).
Both failure modes are SILENT — a slightly wrong mel or a slightly wrong layer
still produces a video of a face moving its mouth, just not to these words.
This script is the only thing standing between us and that.

    pip install librosa            # dev-only; NOT in requirements.txt
    python verify_port.py --checkpoint weights/wav2lip_gan.pth

Run it after any change to wav2lip.py, and once on the target host after
install, because torch's STFT is a different implementation per platform.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
import torch

import wav2lip


def check_mel_filterbank() -> bool:
    import librosa
    theirs = librosa.filters.mel(sr=wav2lip.SAMPLE_RATE, n_fft=wav2lip.N_FFT,
                                 n_mels=wav2lip.NUM_MELS, fmin=wav2lip.FMIN,
                                 fmax=wav2lip.FMAX)
    diff = np.abs(wav2lip._MEL_BASIS - theirs).max()
    print(f"  mel filterbank   max |diff| = {diff:.2e}   (float64 noise is ~1e-9)")
    return diff < 1e-7


def check_melspectrogram() -> bool:
    import librosa

    def reference(wav):
        """Upstream Wav2Lip audio.py, transcribed. pad_mode is passed
        EXPLICITLY: librosa changed its default from 'reflect' to 'constant' in
        0.10, and upstream was written against 0.7. Leaving it to the default
        compares against the wrong thing.
        """
        y = np.append(wav[0], wav[1:] - wav2lip.PREEMPHASIS * wav[:-1])
        spec = librosa.stft(y=y, n_fft=wav2lip.N_FFT, hop_length=wav2lip.HOP_SIZE,
                            win_length=wav2lip.WIN_SIZE, pad_mode="reflect")
        mel = librosa.filters.mel(sr=wav2lip.SAMPLE_RATE, n_fft=wav2lip.N_FFT,
                                  n_mels=wav2lip.NUM_MELS, fmin=wav2lip.FMIN,
                                  fmax=wav2lip.FMAX) @ np.abs(spec)
        db = 20 * np.log10(np.maximum(1e-5, mel)) - wav2lip.REF_LEVEL_DB
        norm = (2 * wav2lip.MAX_ABS_VALUE) * \
            ((db - wav2lip.MIN_LEVEL_DB) / -wav2lip.MIN_LEVEL_DB) - wav2lip.MAX_ABS_VALUE
        return np.clip(norm, -wav2lip.MAX_ABS_VALUE, wav2lip.MAX_ABS_VALUE)

    # Amplitude-modulated tone plus noise: has onsets and silences, which is
    # where a padding or windowing mistake actually shows up.
    rng = np.random.default_rng(0)
    t = np.arange(wav2lip.SAMPLE_RATE * 3) / wav2lip.SAMPLE_RATE
    wav = (0.4 * np.sin(2 * np.pi * 180 * t) * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))
           + 0.02 * rng.standard_normal(t.size)).astype(np.float32)

    diff = np.abs(wav2lip.melspectrogram(wav) - reference(wav)).max()
    print(f"  melspectrogram   max |diff| = {diff:.2e}   on a [-4, +4] scale "
          f"(float32 noise is ~1e-5)")
    return diff < 1e-3


def check_checkpoint(path: str) -> bool:
    # strict=True inside load_wav2lip: any missing, extra or mis-shaped
    # parameter raises. Reaching the print at all is the assertion.
    model = wav2lip.load_wav2lip(path, "cpu")
    params = sum(p.numel() for p in model.parameters())
    with torch.no_grad():
        out = model(torch.randn(2, 1, 80, 16), torch.randn(2, 6, 96, 96))
    ok = tuple(out.shape) == (2, 3, 96, 96) and 0.0 <= out.min() <= out.max() <= 1.0
    print(f"  checkpoint       loaded strict=True, {params / 1e6:.2f}M params, "
          f"forward -> {tuple(out.shape)}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", default="weights/wav2lip_gan.pth")
    parser.add_argument("--skip-audio", action="store_true",
                        help="skip the two librosa comparisons")
    args = parser.parse_args()

    print("verifying the Wav2Lip port")
    results = []
    if not args.skip_audio:
        try:
            results.append(check_mel_filterbank())
            results.append(check_melspectrogram())
        except ImportError:
            sys.exit("librosa is not installed. `pip install librosa`, or pass "
                     "--skip-audio to check only the checkpoint.")
    results.append(check_checkpoint(args.checkpoint))

    print("\nPASS" if all(results) else "\nFAIL — do not trust this port")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
