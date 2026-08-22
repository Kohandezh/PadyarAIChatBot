# Lipsync spike

**Status: spike, not a feature.** Nothing here is imported by `app/`, nothing is
added to the project's `requirements.txt`, nothing is wired into `deploy/`.
`pytest.ini` sets `testpaths = tests`, so this directory is never collected.
It exists to answer one question — *can we deliver a video answer with a talking
face when no clip was pre-recorded?* — with numbers instead of opinions.

**The short version.** The engineering is easy and the licence is hard. A
Wav2Lip-class network runs on a Tesla P40 at roughly **0.1–0.17 seconds of
compute per second of video**, which is 6–10× faster than real time — but its
weights are **not licensed for commercial use**, and no permissively-licensed
retrain exists anywhere. The model we should actually ship is **MuseTalk 1.5**,
which is the only credible candidate whose authors explicitly grant commercial
use of the weights; it costs an estimated **4–8 seconds of compute per second of
video** on this hardware, roughly 100× more than Wav2Lip and still fine, because
this has to be a pre-render architecture either way.

The spike implements **Wav2Lip**, deliberately: it is the one I could run and
verify without a GPU, it establishes the speed floor, and every part of it
except the network itself — face handling, mel front-end, batching, ffmpeg
muxing, the cache shape — is what MuseTalk would need too.

---

## 1. What was actually run, and where

Honesty first, because everything below depends on which of these you can lean on.

| Claim | Status |
|---|---|
| The Wav2Lip port loads both official checkpoints with `strict=True` | **Measured** — `verify_port.py` |
| The audio front-end matches librosa to float32 rounding | **Measured** — `verify_port.py` |
| The pipeline produces an MP4 with correctly articulating lips | **Measured** — rendered, then inspected frame by frame |
| Wav2Lip = 7.93 GFLOP per generated frame | **Measured** — layer-by-layer FLOP count |
| MuseTalk = ~790–1330 GFLOP per generated frame | **Measured** — same FLOP count, on the published architecture config (no weights needed) |
| Cost per second of video on **CPU** | **Measured** — 0.64–1.26 s/s, Apple Silicon, fp32 |
| Cost per second of video on a **Tesla P40** | **Extrapolated** from those FLOP counts — §5 |
| VRAM on a P40 | **Calculated** from tensor sizes, not measured |
| Licence findings | **Verified against primary sources** — LICENSE files, the BBC dataset contract, the repo owner's own statement, byte-exact checkpoint sizes |
| Pascal toolchain findings | **Verified against** PyTorch's wheel build scripts, NVIDIA's tuning guide and datasheets |
| Every other model's speed | **Published figures + arithmetic**, not run here |

> **I have no GPU and no Linux box.** Every "measured" line above was measured on
> an Apple Silicon Mac, in float32, on the CPU. A Pascal card was never touched.
> §5's P40 figures are arithmetic, not observation. Run `benchmark.py` on the
> real host before anyone commits to a design.

---

## 2. The hardware decides this before anything else

Two Tesla P40s. Pascal, `sm_61`, 24 GB each, GPU1 idle.

The fact that eliminates most of the field: **GP102 has no usable FP16.** NVIDIA's
own Pascal Tuning Guide says half-precision throughput on this compute-capability
tier is *"1/64th that of FP32"* — and NVIDIA's P40 datasheet quietly declines to
print an FP16 number at all while headlining 12 TFLOPS FP32 and 47 TOPS INT8.
Enabling fp16 here makes things dramatically slower, not faster.

What follows from that, all verified against upstream build scripts:

| | On `sm_61` |
|---|---|
| flash-attention | **No.** `setup.py` emits gencodes for sm_80/90/100/120 only |
| xformers | Last Pascal wheel is `0.0.30`. `0.0.31+` dropped it; `memory_efficient_attention` then raises `NotImplementedError` with no fallback |
| `F.scaled_dot_product_attention` | **Works** — the math and cutlass mem-efficient backends accept sm_61; the flash and cuDNN backends reject it and warn |
| `torch.compile` (inductor) | **No.** `torch/utils/_triton.py` requires `major >= 7` |
| bfloat16 | **No** (emulated, pathologically slow). `autocast(bfloat16)` may not even raise — it just runs emulated |
| fp16 generally | Runs without error, which is the trap. Assert fp32 explicitly rather than trusting a flag: on Pascal, fp16 diffusion pipelines are reported to silently emit **black frames** |
| TensorRT | **No.** Removed after 8.5.x; TRT 10 fails with `Unsupported SM: 0x601` |
| onnxruntime-gpu | Yes, up to **1.26.x**. CUDA 13.x builds dropped sm_60/70 |
| cuDNN | ⚠️ **Pin it.** torch 2.6.0 pulls `nvidia-cudnn-cu12==9.1.0.70`, which is fine. Field reports put the last Pascal-working cuDNN at ~9.10.2 |

So the real question is not *which lipsync model is best*. It is:

> Which model is a **float32 network with no diffusion sampler, no fp16
> assumption, no flash-attention, and weights that can sit on disk offline** —
> *and* whose weights we are allowed to sell?

Those two halves have different answers, which is the whole story of this spike.

### Two toolchain findings worth acting on outside this spike

1. **`torch.cuda.get_arch_list()` on the cu124 wheel returns
   `['sm_50','sm_60','sm_70','sm_75','sm_80','sm_86','sm_90']` — no `sm_61`.**
   The card works anyway via CUDA's binary-compatibility rule (a cubin built for
   *X.y* runs on *X.z* where `z ≥ y`), so the **sm_60** cubin executes on the
   sm_61 device. It is not PTX JIT: no arch in that list carries `+PTX`, so
   there is no PTX in the wheel to JIT from.
   **Consequence for `deploy/25-install-tts.sh`:** its check
   `if "sm_61" not in arches: sys.exit("FATAL: ...")` will abort on a host where
   everything is actually fine. It has not fired yet only because the script
   skips the check when CUDA is unavailable, which is how that host was first
   provisioned. Re-running the installer now would fail. Not touched here —
   out of this spike's scope — but it should be `sm_60`, or a real kernel launch.

2. **The `cu126` index still ships Maxwell/Pascal kernels — through torch 2.13.0.**
   The `cu124` line ends at 2.6.0, but PyTorch's build script carries the comment
   *"Only 12.6 includes legacy Maxwell/Pascal/Volta"* and still lists `5.0;6.0`
   at tag v2.12.1. cu128 never had Pascal, from its first release. So the ceiling
   is **CUDA 12.6, not torch 2.6.0** — there is more headroom than the current
   pin assumes. This spike keeps `torch==2.6.0` anyway, to match
   `deploy/tts/requirements.txt` and to change one thing at a time.

---

## 3. The survey

Judged on four gates, in this order: **(1) can we sell it, (2) does it run on
sm_61 in fp32, (3) is it fast enough, (4) does it work offline.** Gate 1 first,
because failing it makes the other three irrelevant.

| Model | Weights licence | Runs on P40 fp32? | Verdict |
|---|---|---|---|
| **MuseTalk 1.5** | **MIT.** README: *"The trained model are available for any purpose, even commercially."* | Yes — fp32 is the **default** in `scripts/inference.py`; the weights are natively fp32; no xformers, no flash-attn | ✅ **Recommended** |
| **SadTalker** | Apache-2.0; the non-commercial clause was deliberately removed | Yes — fp32, single pass | ✅ Fallback — and the only one that animates a **still photo** |
| **Wav2Lip** | ❌ **Non-commercial only.** No LICENSE file at all | Yes — the best of the lot, pure conv fp32 | ❌ **This spike. Cannot ship.** |
| LatentSync 1.5 | ⚠️ OpenRAIL++, **plus a hard `insightface==0.7.3` pin** (non-commercial weights) on the inference path | Runs — it auto-selects fp32 when `capability[0] > 7` is false. 1.6 @512 needs ~28–31 GB fp32 and will not fit 24 GB | ⚠️ ~25–35 min per 10 s clip. Legal work + batch only |
| EchoMimic V1 | ⚠️ OpenRAIL-M via its base model; its advertised fp32 path is broken upstream (`mutual_self_attention.py` defaults `dtype=float16`) | Needs a patch | ⚠️ Weak |
| EchoMimic V2 | ❌ **No weights licence of any kind** — no HF tag, no LICENSE, no card section, empty on the ModelScope mirror | — | ❌ Undefined is worse than restrictive: it is unpleadable |
| EchoMimic V3 | ✅ Apache-2.0 end to end (Wan2.1 base, no SD lineage) — the cleanest licence in the survey | ❌ **Impossible.** All 1076 tensors are BF16 on disk; `assert dtype in half_dtypes`; unconditional flash-attn 2; 33–38 GB | ❌ |
| Hallo / Hallo2 | ❌ MIT code, blocked transitively by InsightFace/antelopev2 | — | ❌ |
| VideoReTalking, Diff2Lip, DINet, TalkLip, Sonic, FLOAT | ❌ Non-commercial, or no LICENSE at all (Diff2Lip has none at top level) | — | ❌ |
| Ditto / LivePortrait / JoyVASA | ⚠️ MIT/Apache code, InsightFace on the path | Yes | ⚠️ Viable only after a detector swap |
| IP-LAP | ⚠️ Apache-2.0 **code**, LRS2-trained weights | — | ❌ The code grant does not cure the dataset |
| Sonic | ❌ CC BY-NC-SA. Its HF tag says `afl-3.0` — **wrong in the permissive direction** | — | ❌ |
| Linly-Talker | ❌ MIT badge is meaningless — it is a wrapper that inherits Wav2Lip | — | ❌ |
| HeyGem | ❌ Licence caps use at **1,000 MAU** — effectively non-commercial for a sold product | — | ❌ |
| Wan2.2-S2V, MultiTalk, InfiniteTalk, FantasyTalking | ✅ Genuinely Apache-2.0 in code *and* weights | ❌ 14B params, ~56 GB fp32 | ❌ |

**Two structural traps, and they explain the whole table:**

1. **The InsightFace tax.** InsightFace's code is MIT but its *models* are
   *"for non-commercial research purposes only"*, and it sits on the inference
   path of LatentSync, Ditto, LivePortrait, JoyVASA and Hallo — all otherwise
   permissive in both halves. Escapable, but it is a code change (LatentSync's
   alignment is hardcoded to InsightFace's 106-point landmark indices), not a
   download swap.
2. **The LRS2 tax is a *dataset* restriction**, which is why an Apache-2.0 code
   grant cannot cure it (see IP-LAP) and why forking cannot launder it.

**And the answer to "does any lipsync model have genuinely permissive weights?"**
Yes — Wan2.2-S2V, MultiTalk, InfiniteTalk, FantasyTalking and EchoMimicV3 are all
Apache-2.0 in code *and* weights. Every one of them is a 14B-parameter or
bf16-only diffusion model that this hardware cannot run. **MuseTalk is the only
model in the intersection of "permissive weights" and "runs on a P40."**

### Persian / Farsi

Not a differentiator, and worth saying explicitly so nobody spends time on it.
Every model here is **audio-driven, not phoneme- or text-driven**: Wav2Lip
conditions on a raw mel window, MuseTalk on Whisper *encoder* features. Neither
consumes a grapheme, a phoneme string, or a language ID, so there is no English
viseme table to be wrong about Persian. The residual risk is corpus bias — both
were trained overwhelmingly on English speakers — which shows up as slightly
under-articulated extremes, not as wrong mouth shapes. Persian's emphatics and
its `خ`/`ق` are articulated behind the lips and are visually near-invisible
anyway. **Verify by watching one rendered Persian answer; do not budget work for
it in advance.**

---

## 4. The licence position, plainly

### Wav2Lip — non-commercial. Not ambiguous, not fixable, not negotiable in practice.

There is no LICENSE file in the repository; GitHub's licence API returns 404.
The terms are README prose:

> "This repository can only be used for personal/research/non-commercial
> purposes. […] As the models are trained on the LRS2 dataset, any form of
> commercial use is strictly prohibited."

**Why it is so widely misreported: it really was MIT.** The 2020-09-04 Wayback
snapshot reads *"The software is licensed under the MIT License."* It was removed
about a month later, and the repo owner said why in issue #104 (2020-10-09,
`author_association: OWNER`): *"we thought it will be best to remove because our
model is trained on LRS2 and should not be used for commercial purposes."* Every
fork cut in that window carries the MIT text, and that claim has propagated ever
since. An MIT grant on a published version is generally irrevocable *for that
version* — but that argument only ever covered the **code**. The weights were
never in git.

**The weights are governed by a signed BBC contract**, and it is aimed
squarely at our business model:

> "The BBC's content must not be used for training any existing or new
> technology, algorithms or models **that will be sold commercially**."
>
> "To the extent that copyright, database rights or any other form of
> intellectual property rights come into existence as a result of your use of
> our content, **we will own those future rights**."

Most dataset licences restrict use of the *data*. This one restricts training
models that are *sold* — and asserts ownership of the resulting weights.

**There is no permissively-licensed alternative checkpoint.** Every candidate was
checked:

- `primepake/wav2lip_288x288` — MIT code, **zero weights**; `checkpoints/` holds
  a README saying "place your checkpoints here"
- `Easy-Wav2Lip` — no LICENSE, repo **archived**; its `Wav2Lip_GAN.pth` is
  435,801,865 bytes, byte-exact for the restricted original. A re-host, not a retrain
- `wav2lip-onnx-HQ`, `wav2lip-hq` — no LICENSE; format conversion is not laundering
- `Lip_Wise`, `sd-wav2lip-uhq` — Apache-2.0 **on the wrapper**, original weights
  inside. This is the trap
- 52 Hugging Face repos — the permissively-tagged ones have ~25-byte READMEs and
  no provenance. An uploader cannot grant rights they never held

`wav2lip.pth` vs `wav2lip_gan.pth` is a quality choice (the GAN variant adds a
visual-quality discriminator), **not** a licence choice — both are LRS2-only.
And the authors do not answer licensing mail: issues #370, #551, #581 and #623
all have zero maintainer replies.

### MuseTalk 1.5 — commercial use explicitly granted, with two caveats to handle

The LICENSE is **MIT** (Tencent Music Entertainment), and unusually the README
addresses the weights separately from the code:

> "`code`: […] There is no limitation for both academic and commercial usage."
> "`model`: **The trained model are available for any purpose, even commercially.**"

**Why it is able to say that** — this is the part that matters, because it is
exactly what Wav2Lip cannot claim:

- It was trained on **HDTF (CC BY 4.0)** plus private data. **Not LRS2.** The
  BBC clause below simply does not attach.
- Its config sets **`random_init_unet: True`** — the UNet was trained from
  scratch. Only Stable Diffusion's *architecture* was borrowed, not its weights,
  so there is no OpenRAIL inheritance through the UNet.
- Repo-wide grep for InsightFace: **zero hits.**

That is three independent contaminations avoided, which is why it is the only
fast model left standing.

Dependency chain, each checked individually:

| Component | Licence | |
|---|---|---|
| `sd-vae-ft-mse` | **MIT** on the HF model card — not OpenRAIL | ✅ |
| `whisper-tiny` (encoder only) | MIT, OpenAI | ✅ |
| DWPose | Apache-2.0 | ✅ |
| **`face-parse-bisent` (`79999_iter.pth`)** | Code is MIT, but the checkpoint is trained on **CelebAMask-HQ**: *"available for non-commercial research purposes only"* | ⚠️ **Fix required** |
| **s3fd face detector** | No LICENSE anywhere upstream; trained on **WIDER FACE (CC BY-NC-ND)** | ⚠️ **Fix required** |
| `latentsync_syncnet.pt` | OpenRAIL++ | Training only — **do not download it** |

Both ⚠️ items are peripheral and replaceable:

- **s3fd → MediaPipe BlazeFace.** Apache-2.0, trained on Google's own data, not
  WIDER FACE. It returns a bounding box; nothing else depends on it. `cog-Wav2Lip`
  already did exactly this swap, and it is also where most of the fork
  ecosystem's speedups came from. *(Calibration: OpenCV ships YuNet, also
  WIDER-FACE-trained, under MIT — so the industry position is that weights are
  not derivative works of training images. Real but unsettled risk, and it does
  not differentiate the candidates.)*
- **face-parsing** is used only to soften the blend seam. This spike shows a
  feathered alpha ramp doing that job in ten lines of numpy with no weights at all.

One more thing for counsel, not for engineering: MuseTalk's **HF model card
carries a `creativeml-openrail-m` tag** that contradicts the more permissive
README text. OpenRAIL-M does permit commercial use, but adds behavioural
use-restrictions that must be **propagated to our customers** in our own terms.
Budget a contract clause; get the discrepancy resolved in writing.

### SadTalker — the fallback, and it answers a different question

Worth stating clearly because it changes what avatar asset we need to produce.
**MuseTalk re-dubs an existing clip**: it inpaints the mouth region of frames you
give it, so it needs a video of the presenter (an idle loop is fine) and it will
not invent head motion. **SadTalker animates a still photograph**, generating
head pose and blinks from scratch.

Its licence checks out: Apache-2.0, and the README changelog records that *"the
license has been updated to Apache 2.0, and we've removed the non-commercial
restriction."* The Basel Face Model — the thing usually cited as SadTalker's
licence blocker — **is not on the inference path**: `BFM_Fitting.zip` is
commented out of `download_models.sh`, and inference loads only
`similarity_Lm3D_all.mat`, a 994-byte 5-point landmark template.

Two conditions: run it **without `--enhancer`** (that path pulls GFPGAN — see
§8), and note its LICENSE says *"except for the third-party components listed
below"* and then lists nothing, which is sloppy rather than sinister but should
be raised with counsel alongside the MuseTalk tag question.

> **Bottom line for the owner:** exactly the same thing that happened with the
> Persian TTS checkpoint (CC BY-NC 4.0) has happened again, one layer down. The
> pattern to internalise is that **in this field the weights and the code almost
> never share a licence**, and the weights are the part we are selling.

---

## 5. What a second of video costs

### Measured, on CPU (Apple Silicon, fp32)

```
199 frames @ 25 fps = 8.09 s of video
  model load :   0.41 s   (once per process)
  audio+mel  :   0.09 s
  face prep  :   0.07 s
  inference  :   7.54 s
  wall clock :   8.62 s
  -> 1.01 seconds of compute per second of video
```

Batch sweep on CPU: 0.72 (b=1), **0.64 (b=4)**, 1.12 (b=16), 1.09 (b=64), 1.26
(b=128). Note the shape — on a memory-bandwidth-bound CPU, batching *hurts*.
**On a GPU it will be the opposite**, so do not carry these batch numbers over;
that is what `benchmark.py` is for.

Supporting measurements: x264 encode of 768×768 costs 0.03 s/s (ultrafast) to
0.06 s/s (medium) — negligible either way. The crop/resize/paste-back path costs
0.50 ms/frame = 0.0125 s/s.

### Extrapolated to a P40, fp32

Both figures below start from a FLOP count I ran layer by layer, so only the
efficiency assumption is a guess:

| | GFLOP / frame | TFLOP per second of 25 fps video |
|---|---|---|
| **Wav2Lip** | **7.93** | 0.198 |
| **MuseTalk** (UNet 169.6 + VAE decode 620.0) | **~790** | 19.7 |
| MuseTalk if avatar latents are *not* cached (+2 VAE encodes) | ~1330 | 33.2 |

A P40 peaks at **11.76 TFLOPS fp32** (3840 cores × 2 × 1531 MHz). Convolution
kernels at these small spatial sizes realistically reach 20–30 % of peak, i.e.
**2.4–3.5 TFLOPS effective**:

| | GPU, s per s of video | + CPU & x264 | 20 s answer, end to end |
|---|---|---|---|
| **Wav2Lip** | 0.06 – 0.08 | **0.10 – 0.17** | ~2 – 3.5 s |
| **MuseTalk**, avatar latents cached | 4 – 8 | **4 – 8** | 80 – 170 s |
| MuseTalk, nothing cached | 10 – 14 | 10 – 14 | 3 – 5 min |

The CPU column adds the measured 0.0125 s/s of crop/paste work and 0.03–0.06 s/s
of x264 encode. It roughly doubles Wav2Lip's cost and is lost in the noise for
MuseTalk — which is the general shape of this: once the network is fast enough,
the surrounding plumbing becomes the bill.

Sanity checks on the efficiency assumption: the CPU measurement above implies
~220 GFLOPS sustained on this Mac, a similar fraction of its fp32 peak. And a
published RTX 4070 Ti figure puts the Wav2Lip generator at 4.50 ms/frame in
PyTorch fp32 — a P40 (11.76 TFLOPS fp32) should land in the same order, since an
fp32 PyTorch path never touches the newer card's tensor cores.

**Two non-obvious cost facts, both from published profiling and confirmed by
both projects' maintainers:**

1. **Face detection dominates, not the network.** In the same 4070 Ti study the
   generator is 4.50 ms/frame and the s3fd detector is **22.2 ms/frame** — 5×
   more. One extension reports 13 s of detection against 2 s of generation on a
   10 s clip. Easy-Wav2Lip's famous 6m53s → 25s speedup is *entirely* detector
   replacement plus box caching.
   **This is why `run_lipsync.py` detects the face box exactly once.** Our avatar
   is a fixed studio asset; there is no reason to pay per frame, ever.
2. **MuseTalk's cost is 78 % VAE decode**, not the UNet — 620 of ~790 GFLOP. Any
   future optimisation effort belongs there (e.g. TAESD-class tiny decoder),
   not in the UNet.

### VRAM — GPU1's 24 GB is not close to a constraint

Calculated from tensor sizes:

| | Weights (fp32) | Peak, upper bound |
|---|---|---|
| Wav2Lip | 145 MB | 1.0 GB @ batch 16 · 7.0 GB @ batch 128 |
| MuseTalk 1.5 | ~3.4 GB | comfortably under 8 GB at sane batches |

Those are upper bounds — `no_grad` frees intermediates as it goes. **24 GB is
enough for the model several times over.** The honest framing is that VRAM is
this card's one genuine advantage and it is wasted here; the constraint is
latency and fp32 throughput, not capacity. There is room to run two or three
render workers on GPU1 if throughput ever matters more than latency.

---

## 6. This must be pre-rendered. It cannot be live.

Not a close call, and it does not depend on which model wins.

The costs **add up in series** — audio must exist before a frame can be drawn:

| | s of compute per s of video |
|---|---|
| Chatterbox TTS (measured on the real box) | ~1.7 |
| Wav2Lip (extrapolated, end to end) | 0.10 – 0.17 |
| MuseTalk (extrapolated) | 4 – 8 |
| **Total, MuseTalk** | **~6 – 10** |

A 20-second Persian answer is therefore **2–3.5 minutes of GPU time**. And that
is the *idle* case. The measured load test already shows the TTS queueing badly
— 20 requests at concurrency 8 left the last one waiting 8 minutes — because
generation is serialised on a 2016 card. Putting lipsync in front of a visitor
would compound that, not replace it.

The architecture is therefore the one `deploy/tts` already established, extended
one stage:

```
admin saves a dataset answer
        │
        ▼
  pre-render: TTS  ->  WAV in the TTS cache      (already built: /prerender)
        │
        ▼
  pre-render: lipsync  ->  MP4 keyed by (wav hash, avatar id, model version)
        │
        ▼
  visitor asks  ->  cache hit  ->  static file   (measured: 174 req/s for cached audio)
```

Which gives the concrete design consequences:

- **Cache key**: hash of the audio bytes + avatar identity + model version, in
  the shape of `deploy/tts/server.py::cache_key()` — every input that changes
  the output, and nothing else. Changing the avatar or the model must invalidate.
- **Warm at save time**, from `deploy/45-prerender.sh`, not at first visit.
- **Detect and cache the avatar's face box once per avatar**, not per render.
  For MuseTalk, cache the avatar's VAE latents too — that is the difference
  between the 4–8 and 10–14 rows in §5.
- **GPU1, `CUDA_VISIBLE_DEVICES=1`**, mirroring how the TTS unit pins GPU0. The
  two services must not contend; the card is passively cooled and already runs
  at 81 °C, which is 11 °C below the P40's **92 °C hardware slowdown** point.
  Consider `nvidia-smi -pl 180` — Pascal's perf/watt curve is steep at the top,
  so ~10 % throughput typically buys ~15 °C.
- **The AI-fallback path (Tier 2) cannot be pre-rendered**, because the text is
  generated per visitor. Either those answers stay text/audio-only, or they get
  a generic "thinking" loop. Decide this deliberately — it is a product
  decision, not a technical one.

---

## 7. Running the spike

### Install (target host: Ubuntu 24.04, P40)

```bash
sudo apt install -y ffmpeg python3-venv
python3 -m venv .venv && . .venv/bin/activate

# torch FIRST, from the cu124 index — matching deploy/tts/requirements.txt.
pip install torch==2.6.0 --index-url https://download.pytorch.org/whl/cu124
pip install -r requirements.txt
```

Then stage the checkpoint by hand. **Nothing in this directory downloads
anything, ever** — that is a design constraint, not an omission:

```bash
mkdir -p weights
# wav2lip_gan.pth is 435,801,865 bytes. Upstream hosts it on Google Drive,
# which is unreliable from Iran; source it out of band.
scp you@elsewhere:wav2lip_gan.pth weights/
```

Verify the install before trusting a render:

```bash
python verify_port.py --checkpoint weights/wav2lip_gan.pth --skip-audio
```

### Render

```bash
python run_lipsync.py \
  --face  avatar.jpg \     # a still, or a short idle-loop MP4
  --audio answer.wav \     # whatever the Chatterbox TTS service produced
  --out   answer.mp4
```

For a fixed avatar, pass `--face-box x1,y1,x2,y2` and skip detection entirely.

### Measure on the real card

```bash
python benchmark.py --checkpoint weights/wav2lip_gan.pth --face avatar.jpg
```

This replaces §5's estimates with facts. Do not take the estimates into a
design meeting without it.

---

## 8. What this spike could not verify

- **Anything on Pascal.** No GPU was available. Every P40 number is arithmetic.
- **MuseTalk end to end** — the recommended model was *not* run. It needs ~10 GB
  of staged weights and a GPU, and its install is genuinely hostile: `preprocessing.py`
  calls `mmpose.apis.init_model()` at *import* time, so mmcv/mmpose/mmdet are hard
  requirements, and mmcv is the single most likely thing to eat a day.
  **Try MuseTalk issue #409's mediapipe patch first** — it replaces the mmpose
  dependency outright and removes the whole build fight. If that fails, build
  **`mmcv==2.1.0`** from source (`MMCV_WITH_OPS=1 FORCE_CUDA=1 pip install
  mmcv==2.1.0 --no-build-isolation`) against this exact torch and ship the
  `.whl` into the offline environment; do not attempt `mim install mmcv==2.0.1`.
  A pip-installed mmcv gives `mmcv._ext undefined symbol` — its CUDA extensions
  are ABI-locked to the torch they were compiled against.
  Also mirror `face-parse-bisent`'s `79999_iter.pth` by hand: it comes via
  `gdown` from Google Drive, which is unreachable from Iran.
- **Two MuseTalk patches are needed and are untested by me**: the three hardcoded
  `.half()` calls in `scripts/realtime_inference.py` (~line 366) must go, and
  `torch.load` needs `weights_only=False` because torch 2.6 flipped that default.
  The same `torch.load` change is why this spike's `wav2lip.py` passes
  `weights_only=True` explicitly and strips the optimizer state instead — safer,
  since these files come from third-party mirrors.
- **Output quality at exhibition scale.** Verified by rendering the same clip
  with the face filling the frame: Wav2Lip generates a **96×96** crop, so on a
  large screen the mouth is visibly soft and the teeth turn to mush. Keep the
  face small-to-medium in frame, or budget a super-resolution pass — and note
  that **GFPGAN is not the answer**: its LICENSE carves out StyleGAN2 under
  NVIDIA's non-commercial terms and DFDNet under CC BY-NC-SA, and GitHub
  classifies the repo `NOASSERTION`, not Apache-2.0. Real-ESRGAN (BSD-3) is
  clean but is a generic upscaler, not a face restorer. MuseTalk at 256×256 is
  ~7× the pixel count and should be materially better here.
- **A moving presenter.** The single fixed face box was tested against a gentle
  synthetic sway (±8 px) and held. A loop where the head actually moves needs a
  tracker, and that reintroduces the per-frame detector cost §5 warns about.
- **Whether a Wav2Lip commercial licence is purchasable.** The README points at
  `@synclabs.so`, but every licensing issue on the tracker is unanswered. Worth
  one email; worth zero planning.

---

## 9. Files

| File | Why it exists |
|---|---|
| `wav2lip.py` | Model + mel front-end, re-implemented. Upstream downloads its face detector from a university URL at first run (`HF_HUB_OFFLINE` does **not** stop it — it is a raw `torch.hub` call), and pulls librosa and face_alignment, each carrying a torch pin that will eventually fight our frozen 2.6.0 |
| `run_lipsync.py` | The CLI: face + WAV → MP4 |
| `verify_port.py` | Proves the re-implementation is faithful. A wrong mel is a *silent* quality bug — the mouth still moves, just not to these words |
| `benchmark.py` | Measures on the host you deploy on, because every published number is fp16 |
| `requirements.txt` | Pinned for `sm_61`. A separate venv — not the project's |
| `.gitignore` | Stops a 435 MB non-commercially-licensed checkpoint from entering git history, which is the one mistake here that would be expensive to undo |

Also found during the port and worth knowing if anyone runs upstream Wav2Lip
directly: **librosa changed `stft`'s default `pad_mode` from `reflect` to
`constant` in 0.10**, and upstream was written against 0.7. Anyone running it on
a modern librosa is feeding the model a different mel in the first and last two
frames of every clip — measured here at 0.91 of difference on a [-4, +4] scale.
This port pins `reflect` explicitly.
