"""Wav2Lip inference, self-contained: model architecture + audio front-end.

WHY THIS FILE EXISTS AT ALL, instead of `git clone Rudrabha/Wav2Lip`:

1. **Offline.** The exhibition host is in Iran and runs with HF_HUB_OFFLINE=1.
   The upstream repo's inference path downloads the S3FD face detector from a
   university URL on first run. A file that cannot phone home is the only kind
   we can deploy.

2. **Dependency surface.** Upstream pulls librosa (-> numba, llvmlite,
   soundfile) purely for a mel spectrogram, and face_alignment (-> its own
   torch pin) purely for a bounding box. On a Pascal box where torch is frozen
   at 2.6.0 forever, every transitive torch pin is a live grenade. The mel is
   ~40 lines of numpy; the bounding box, for a fixed studio avatar, is a
   constant. See run_lipsync.py.

3. **Licence hygiene.** None of the upstream GPL-adjacent inference plumbing is
   copied here; this is a re-implementation of the published architecture so
   that the ONLY licensed artifact in play is the checkpoint itself, which is
   exactly the thing the README's licence section is about.

The architecture below must match the published checkpoint's state_dict keys
and shapes EXACTLY — it is loaded with strict=True precisely so that a silent
mismatch is impossible. If you change a layer here, the load fails loudly.

Reference: Prajwal et al., "A Lip Sync Expert Is All You Need for Speech to Lip
Generation In The Wild", ACM Multimedia 2020.
"""
from __future__ import annotations

import subprocess

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

# --- audio hyper-parameters -------------------------------------------------
# These are NOT tunable. They are the values the checkpoint was trained with
# (upstream hparams.py); changing any one of them feeds the model a mel it has
# never seen and the mouth stops tracking the speech. They are inlined rather
# than exposed as CLI flags for that reason.
SAMPLE_RATE = 16000
N_FFT = 800
HOP_SIZE = 200          # -> exactly 80 mel frames per second of audio
WIN_SIZE = 800
NUM_MELS = 80
FMIN = 55
FMAX = 7600
PREEMPHASIS = 0.97
MIN_LEVEL_DB = -100.0
REF_LEVEL_DB = 20.0
MAX_ABS_VALUE = 4.0

IMG_SIZE = 96           # the face crop the network was trained on
MEL_STEP_SIZE = 16      # mel frames per generated video frame (0.2s of context)


# --- audio front-end --------------------------------------------------------

def load_wav_16k_mono(path: str) -> np.ndarray:
    """Decode any audio file to float32 mono @16 kHz using ffmpeg.

    ffmpeg is already a hard dependency (muxing the result), so using it as the
    decoder too removes librosa/soundfile from the install entirely. On an
    offline box, a dependency you do not have cannot fail to install.
    """
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
         "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg could not decode {path}: "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


def _mel_filterbank() -> np.ndarray:
    """Slaney-normalised mel filterbank — numerically identical to
    librosa.filters.mel(sr, n_fft, n_mels, fmin, fmax) with librosa's defaults
    (htk=False, norm='slaney'), which is what upstream calls.

    Verified during the spike against librosa 1.0.0: max absolute difference
    across the whole (80, 401) matrix was 1.6e-09, i.e. float64 rounding.
    Reproduce with verify_audio.py if you ever touch this.
    """
    def hz_to_mel(hz):
        # Slaney: linear below 1000 Hz, logarithmic above.
        f_min, f_sp = 0.0, 200.0 / 3
        mels = (hz - f_min) / f_sp
        min_log_hz, min_log_mel = 1000.0, (1000.0 - f_min) / f_sp
        logstep = np.log(6.4) / 27.0
        mels = np.where(hz >= min_log_hz,
                        min_log_mel + np.log(hz / min_log_hz) / logstep, mels)
        return mels

    def mel_to_hz(mel):
        f_min, f_sp = 0.0, 200.0 / 3
        freqs = f_min + f_sp * mel
        min_log_hz, min_log_mel = 1000.0, (1000.0 - f_min) / f_sp
        logstep = np.log(6.4) / 27.0
        return np.where(mel >= min_log_mel,
                        min_log_hz * np.exp(logstep * (mel - min_log_mel)), freqs)

    n_freqs = N_FFT // 2 + 1
    fft_freqs = np.linspace(0, SAMPLE_RATE / 2.0, n_freqs)
    mel_pts = np.linspace(hz_to_mel(FMIN), hz_to_mel(FMAX), NUM_MELS + 2)
    hz_pts = mel_to_hz(mel_pts)

    fdiff = np.diff(hz_pts)
    ramps = hz_pts[:, None] - fft_freqs[None, :]
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0.0, np.minimum(lower, upper))

    # Slaney normalisation: each filter integrates to 1 in frequency.
    enorm = 2.0 / (hz_pts[2:NUM_MELS + 2] - hz_pts[:NUM_MELS])
    return (weights * enorm[:, None]).astype(np.float64)


_MEL_BASIS = _mel_filterbank()


def melspectrogram(wav: np.ndarray) -> np.ndarray:
    """(80, T) mel in the exact scale the checkpoint expects: dB, normalised to
    [-4, +4]. Anything else and the mouth will move, but not to the words.
    """
    # Pre-emphasis, as a one-tap IIR: y[n] = x[n] - 0.97 * x[n-1]. This is FIR,
    # so lfilter and this are the same thing; scipy is not worth a dependency.
    emphasised = np.append(wav[0], wav[1:] - PREEMPHASIS * wav[:-1])

    # Centred, REFLECT-padded, periodic Hann — the librosa 0.7 defaults that
    # upstream was written against.
    #
    # pad_mode is not a detail: librosa changed its stft default from 'reflect'
    # to 'constant' in 0.10, so anyone running upstream Wav2Lip on a modern
    # librosa is silently feeding the model a different mel in the first and
    # last two frames of every clip. Measured here: 0.91 of difference on a
    # [-4, +4] scale at the clip edges. With 'reflect' the whole mel matches
    # librosa to 1.2e-05, which is float32 rounding.
    spec = torch.stft(
        torch.from_numpy(emphasised.astype(np.float32)),
        n_fft=N_FFT, hop_length=HOP_SIZE, win_length=WIN_SIZE,
        window=torch.hann_window(WIN_SIZE), center=True,
        pad_mode="reflect", normalized=False, onesided=True,
        return_complex=True,
    )
    magnitude = spec.abs().numpy().astype(np.float64)

    mel = _MEL_BASIS @ magnitude
    # amp -> dB, floored so that digital silence does not become -inf.
    mel_db = 20.0 * np.log10(np.maximum(1e-5, mel)) - REF_LEVEL_DB
    normalised = (2 * MAX_ABS_VALUE) * ((mel_db - MIN_LEVEL_DB) / -MIN_LEVEL_DB) \
        - MAX_ABS_VALUE
    return np.clip(normalised, -MAX_ABS_VALUE, MAX_ABS_VALUE).astype(np.float32)


def mel_chunks_for_fps(mel: np.ndarray, fps: float) -> list[np.ndarray]:
    """Slice the mel into one (80, 16) window per output video frame.

    80 mel frames = 1 second, so frame i starts at i * 80/fps. The final window
    is taken from the END of the mel rather than zero-padded: a partial window
    makes the last frame's mouth snap shut, which reads as a glitch.
    """
    step = 80.0 / fps
    chunks = []
    i = 0
    while True:
        start = int(i * step)
        if start + MEL_STEP_SIZE > mel.shape[1]:
            if mel.shape[1] >= MEL_STEP_SIZE:
                chunks.append(mel[:, -MEL_STEP_SIZE:])
            break
        chunks.append(mel[:, start:start + MEL_STEP_SIZE])
        i += 1
    return chunks


# --- model ------------------------------------------------------------------

class Conv2d(nn.Module):
    def __init__(self, cin, cout, kernel_size, stride, padding, residual=False):
        super().__init__()
        self.conv_block = nn.Sequential(
            nn.Conv2d(cin, cout, kernel_size, stride, padding),
            nn.BatchNorm2d(cout),
        )
        self.act = nn.ReLU()
        self.residual = residual

    def forward(self, x):
        out = self.conv_block(x)
        if self.residual:
            out = out + x
        return self.act(out)


class Conv2dTranspose(nn.Module):
    def __init__(self, cin, cout, kernel_size, stride, padding, output_padding=0):
        super().__init__()
        self.conv_block = nn.Sequential(
            nn.ConvTranspose2d(cin, cout, kernel_size, stride, padding, output_padding),
            nn.BatchNorm2d(cout),
        )
        self.act = nn.ReLU()

    def forward(self, x):
        return self.act(self.conv_block(x))


class Wav2Lip(nn.Module):
    """U-net over the masked face, conditioned on a 0.2 s mel window.

    Note what this is NOT: there is no attention anywhere, no transformer, no
    diffusion sampler. It is 2D convolutions and batch-norm end to end — which
    is exactly why it is the only candidate in this class that a 2016 Pascal
    card can run at a sane speed in float32. See the README.
    """

    def __init__(self):
        super().__init__()

        self.face_encoder_blocks = nn.ModuleList([
            nn.Sequential(Conv2d(6, 16, 7, 1, 3)),                                # 96,96

            nn.Sequential(Conv2d(16, 32, 3, 2, 1),                                # 48,48
                          Conv2d(32, 32, 3, 1, 1, residual=True),
                          Conv2d(32, 32, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2d(32, 64, 3, 2, 1),                                # 24,24
                          Conv2d(64, 64, 3, 1, 1, residual=True),
                          Conv2d(64, 64, 3, 1, 1, residual=True),
                          Conv2d(64, 64, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2d(64, 128, 3, 2, 1),                               # 12,12
                          Conv2d(128, 128, 3, 1, 1, residual=True),
                          Conv2d(128, 128, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2d(128, 256, 3, 2, 1),                              # 6,6
                          Conv2d(256, 256, 3, 1, 1, residual=True),
                          Conv2d(256, 256, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2d(256, 512, 3, 2, 1),                              # 3,3
                          Conv2d(512, 512, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2d(512, 512, 3, 1, 0),                              # 1,1
                          Conv2d(512, 512, 1, 1, 0)),
        ])

        self.audio_encoder = nn.Sequential(
            Conv2d(1, 32, 3, 1, 1),
            Conv2d(32, 32, 3, 1, 1, residual=True),
            Conv2d(32, 32, 3, 1, 1, residual=True),

            Conv2d(32, 64, 3, (3, 1), 1),
            Conv2d(64, 64, 3, 1, 1, residual=True),
            Conv2d(64, 64, 3, 1, 1, residual=True),

            Conv2d(64, 128, 3, 3, 1),
            Conv2d(128, 128, 3, 1, 1, residual=True),
            Conv2d(128, 128, 3, 1, 1, residual=True),

            Conv2d(128, 256, 3, (3, 2), 1),
            Conv2d(256, 256, 3, 1, 1, residual=True),

            Conv2d(256, 512, 3, 1, 0),
            Conv2d(512, 512, 1, 1, 0),
        )

        self.face_decoder_blocks = nn.ModuleList([
            nn.Sequential(Conv2d(512, 512, 1, 1, 0)),

            nn.Sequential(Conv2dTranspose(1024, 512, 3, 1, 0),                    # 3,3
                          Conv2d(512, 512, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2dTranspose(1024, 512, 3, 2, 1, output_padding=1),  # 6,6
                          Conv2d(512, 512, 3, 1, 1, residual=True),
                          Conv2d(512, 512, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2dTranspose(768, 384, 3, 2, 1, output_padding=1),   # 12,12
                          Conv2d(384, 384, 3, 1, 1, residual=True),
                          Conv2d(384, 384, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2dTranspose(512, 256, 3, 2, 1, output_padding=1),   # 24,24
                          Conv2d(256, 256, 3, 1, 1, residual=True),
                          Conv2d(256, 256, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2dTranspose(320, 128, 3, 2, 1, output_padding=1),   # 48,48
                          Conv2d(128, 128, 3, 1, 1, residual=True),
                          Conv2d(128, 128, 3, 1, 1, residual=True)),

            nn.Sequential(Conv2dTranspose(160, 64, 3, 2, 1, output_padding=1),    # 96,96
                          Conv2d(64, 64, 3, 1, 1, residual=True),
                          Conv2d(64, 64, 3, 1, 1, residual=True)),
        ])

        self.output_block = nn.Sequential(
            Conv2d(80, 32, 3, 1, 1),
            nn.Conv2d(32, 3, 1, 1, 0),
            nn.Sigmoid(),
        )

    def forward(self, audio_sequences: torch.Tensor, face_sequences: torch.Tensor):
        """audio: (B, 1, 80, 16)   face: (B, 6, 96, 96)   -> (B, 3, 96, 96)"""
        audio_embedding = self.audio_encoder(audio_sequences)  # (B, 512, 1, 1)

        feats = []
        x = face_sequences
        for block in self.face_encoder_blocks:
            x = block(x)
            feats.append(x)

        x = audio_embedding
        for block in self.face_decoder_blocks:
            x = block(x)
            # The skip connection is concatenated, so a shape mismatch here is a
            # silent quality bug rather than a crash — assert it.
            skip = feats.pop()
            if x.shape[2:] != skip.shape[2:]:
                raise RuntimeError(f"decoder/encoder shape mismatch: "
                                   f"{tuple(x.shape)} vs {tuple(skip.shape)}")
            x = torch.cat((x, skip), dim=1)

        return self.output_block(x)


def load_wav2lip(checkpoint_path: str, device: str = "cpu") -> Wav2Lip:
    """Load a published Wav2Lip checkpoint.

    strict=True on purpose. A checkpoint that does not match this architecture
    is a different model, and silently ignoring the difference would produce a
    video of a face making plausible-looking nonsense.
    """
    # weights_only=True: these .pth files come from third-party mirrors, and an
    # unpickle is arbitrary code execution. There is no reason to allow it.
    blob = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    state = blob["state_dict"] if "state_dict" in blob else blob
    # Upstream saved these from a DataParallel wrapper.
    state = {k.replace("module.", "", 1): v for k, v in state.items()}

    model = Wav2Lip()
    model.load_state_dict(state, strict=True)
    return model.to(device).eval()
