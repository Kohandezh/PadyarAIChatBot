# BENCH: first measured numbers for a local LLM on the Tesla P40

**Status:** partial. M1, M2, M3 and M5 measured for three models. M4 (the
selection-tier run over the golden set) is **NOT RUN: pending owner decision**.
**Date:** 2026-09-30 (UTC 17:46 to 18:38).
**Measured by:** `llmbench-t1-impl` (Claude Opus 5.5, Claude Code), team
`llmbench-t1`, mission `20260930-db3-localllm`.
**Companion to:** `docs/features/local-inference/RESEARCH.md` (the spike). This
file does not change the spike. It replaces some of its estimates with
measurements and says which gates were checked.

Honesty rule used here: a number is "measured" only if this team ran the
command. Every number below names its command and its raw evidence file. The
evidence lives outside the repo, in
`/Users/sinashamsizadeh/foreman/20260930-db3-localllm/llmbench-t1/evidence/`
(written `evidence/` below), copied from `/var/lib/padyar/llm/results/` on the
server.

## 1. Summary

| | gemma-4-26B-A4B UD-Q4_K_M | gemma-4-12b Q4_K_M | Qwen3-14B Q4_K_M |
|---|---|---|---|
| Card used | GPU1 | GPU1 | GPU1 |
| M1 load to `/health` 200 | 61.5 s cold, 7.4 s warm | 22.6 s | 17.1 s |
| M1 layers on GPU | 31/31 | 49/49 | 41/41 |
| M2 prompt pp512 (t/s) | 1107.84 ± 6.62 | 566.49 ± 0.48 | 505.36 ± 1.98 |
| M2 generation tg128 (t/s) | 47.74 ± 0.04 | 28.07 ± 0.01 | 26.51 ± 0.00 |
| M3 card free after load + TTS | 3,875 MiB | 12,429 MiB | 10,831 MiB |
| Gate 3 VRAM floor (≥ 1,024 MiB) | pass | pass | pass |
| M5 JSON probe, thinking at server default | **fail** (both probes) | M5a pass, M5b **fail** | **fail** (both probes) |
| M5 JSON probe, thinking off | pass (both) | pass (both) | pass (both) |
| M4 selection run (golden set) | NOT RUN | NOT RUN | NOT RUN |

**Which model passes the spike's gates?** None has passed all of them, because
Step 5, Step 5b and Step 6 of spike §9.2 were not run (M4 is blocked, and the
two others are out of this bench's scope). All three pass Gate 3 (VRAM floor
and full offload). All three pass Gate 4 **only with thinking turned off**.
On the numbers measured so far, gemma-4-26B-A4B is the strongest candidate: it
is the fastest on both prompt and generation, and it still leaves 3.8 GiB free
on its card. Its correctness on the real selection task is unknown until M4 runs.

**The one finding that changes the config.** Both Gemma 4 models and Qwen3
think by default under llama-server. At the probe's 64-token budget the
thinking text uses the whole budget and the JSON never arrives. The selection
call's budget is 400 tokens, so the same risk applies there. The fix already
exists in the adapter: on the provider instance set config
`reasoning_param: "enable_thinking"` and set the route's reasoning to `off`.
The adapter then sends `chat_template_kwargs: {"enable_thinking": false}`
(`app/services/ai/adapters/openai_compatible.py`, `apply_reasoning_body`).
With that, every probe passed in 6 to 11 tokens.

## 2. Host facts (measured)

Evidence: `evidence/host/host-facts.txt` (command: `date; hostname; uname -r;
lsb_release -ds; nvidia-smi; lscpu; free -m; df -h /; docker --version` over the
shared SSH socket, 2026-09-30 17:46 UTC).

- Two Tesla P40 (24,576 MiB each), driver 580.173.02.
- 36 vCPU, Intel Xeon E5-2699 v3 (AVX2), 27 GiB RAM.
- TTS at rest before this bench: GPU0 4,311 MiB used, GPU1 3,283 MiB used.
- At the end: GPU0 4,611 MiB, GPU1 3,785 MiB (TTS grew while warm; no llama
  process left). Evidence: `evidence/host/end-state.txt`.
- The TTS service runs two workers, one per card (`cuda:0`, `cuda:1`), so a TTS
  generation can land on either card. It did both during this bench (§5).

## 3. Toolchain

**Route (a), the Docker build image, was used. Route (b), PyPI wheels, was
tried and dropped.**

- Why not (b): PyPI downloads ran at about 115 kB/s from the server (3.2 MB
  `nvidia-cuda-cccl-cu12` wheel took 25 s). The cuBLAS wheel is over 500 MB.
  A first `pip download` of the runtime wheels made no progress in 20 minutes
  and was stopped.
- Why (a) works: Docker Hub downloads were fast (the 15.9 GB image pulled in a
  few minutes). The compile needs no GPU. The binaries then run natively.

Pins:

| Item | Value |
|---|---|
| llama.cpp | tag `v0.5.0`, tag object `c13fcbf684171d5e0bca3fc5c34be6a99174b05f`, commit `7fe450e19305b828c199d602c23a8337aaa1f03b` ("llama.cpp : bump version to 0.5.0 (#29333)") |
| Build image | `nvidia/cuda:12.9.1-devel-ubuntu24.04`, digest `sha256:020bc241a628776338f4d4053fed4c38f6f7f3d7eb5919fecb8de313bb8ba47c` |
| Compiler | nvcc 12.9.86 (V12.9.86), GNU 13.3.0, cmake 3.28.3 |

Exact commands (the future `deploy/27-install-llm.sh` can copy these):

```bash
# 1. source, pinned
git clone --depth 1 --branch v0.5.0 https://github.com/ggml-org/llama.cpp.git /opt/padyar-llm/src
git -C /opt/padyar-llm/src rev-parse HEAD   # 7fe450e19305b828c199d602c23a8337aaa1f03b

# 2. build image
docker pull nvidia/cuda:12.9.1-devel-ubuntu24.04

# 3. build (the whole of /opt/padyar-llm/build-llama.sh)
docker run --rm --cpus 16 \
  -v /opt/padyar-llm/src:/src -v /opt/padyar-llm:/dst nvidia/cuda:12.9.1-devel-ubuntu24.04 bash -euc "
  rm -f /etc/apt/sources.list.d/cuda*.list   # developer.download.nvidia.com is 403 here
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cmake ninja-build >/dev/null
  # no driver in a build container: link against the CUDA stub libcuda
  ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1
  cmake -S /src -B /src/build-cuda -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=61 -DGGML_NATIVE=ON \
    -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DGGML_CUDA_NCCL=OFF \
    -DCMAKE_EXE_LINKER_FLAGS=-Wl,-rpath-link,/usr/local/cuda/lib64/stubs
  cmake --build /src/build-cuda --target llama-server llama-bench -j 16
  mkdir -p /dst/llama/bin /dst/llama/lib
  cp /src/build-cuda/bin/llama-server /src/build-cuda/bin/llama-bench /dst/llama/bin/
  cp -P /src/build-cuda/bin/*.so* /dst/llama/lib/
  for l in libcudart.so.12 libcublas.so.12 libcublasLt.so.12; do
    cp -L /usr/local/cuda/lib64/\$l /dst/llama/lib/
  done
  chown -R 1000:1000 /src/build-cuda /dst/llama"

# 4. run: only the NVIDIA driver is needed on the host
LD_LIBRARY_PATH=/opt/padyar-llm/llama/lib CUDA_DEVICE_ORDER=PCI_BUS_ID \
  /opt/padyar-llm/llama/bin/llama-server --list-devices   # lists both P40s
```

Three build failures were hit and fixed. Each is a thing the installer must do:

1. `apt-get update` fails inside the image: the image lists NVIDIA's apt repo,
   which returns HTTP 403 from this network. Fix: delete that list first.
   Evidence: `evidence/toolchain/build-logs.txt` (attempt 1).
2. Linking `llama-bench` failed with `libcuda.so.1 ... not found` and undefined
   `cuMemCreate` etc.: there is no driver in a build container. Fix: link
   against the image's stub `libcuda` (`-rpath-link` plus a `libcuda.so.1`
   symlink in the throwaway container). Attempt 2.
3. The binary needed `libnccl.so.2` at run time (the image has NCCL, so the
   build enabled it). Fix: `-DGGML_CUDA_NCCL=OFF`. Each model runs on one card,
   so NCCL buys nothing here. Attempt 3.

Runtime libraries shipped next to the binaries: `libcudart.so.12`,
`libcublas.so.12`, `libcublasLt.so.12` (855,087,968 bytes together; the whole
`lib/` folder is 877 MB). Sizes: `evidence/toolchain/install-sizes.txt`. sha256
of every binary and library: `evidence/toolchain/toolchain.txt`. The PyPI
attempt: `evidence/toolchain/pypi-route-b.txt`.

`llama-server --version` prints `commit unknown` because git was not installed
in the build container. The pin above comes from the clone, not from the binary.

## 4. Models

Downloaded with `curl -C -` from `https://huggingface.co/<repo>/resolve/main/<file>`
(script and log: `evidence/host/model-downloads.txt`). Each sha256 below was
computed on the server with `sha256sum` and matches the Hugging Face LFS hash
(`x-linked-etag`).

| Model | File | Bytes | sha256 |
|---|---|---|---|
| `unsloth/gemma-4-26B-A4B-it-GGUF` | `gemma-4-26B-A4B-it-UD-Q4_K_M.gguf` | 16,947,541,728 | `f2c28b3dc4776931ac6f879e11f203dec637ea0f14267a86ec8f6165f63f293f` |
| `unsloth/gemma-4-12b-it-GGUF` | `gemma-4-12b-it-Q4_K_M.gguf` | 7,121,861,440 | `0a270ec9fe6b34f4a0d33992b6135117b484ebc4766ab76b51d4ae8c457e4c42` |
| `Qwen/Qwen3-14B-GGUF` | `Qwen3-14B-Q4_K_M.gguf` | 9,001,752,960 | `500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0` |

`google/gemma-3-12b-it` (spike §6.2 #3): **NOT RUN**, the repo is gated.

## 5. Results per model

All per-model steps come from one script, `/opt/padyar-llm/llm-bench.sh`
(copy: `evidence/toolchain/llm-bench.sh`), run over the shared SSH socket:

```bash
llm-bench.sh start <alias> <gguf> -lv 4   # M1 + M3 before/after load
llm-bench.sh probe <alias>                # warm-up, one TTS generation, M3 after TTS, M5
llm-bench.sh json  <alias> <suffix>       # M5 again (EXTRA= adds request fields)
llm-bench.sh stop  <alias>
llm-bench.sh bench <alias> <gguf> <gpu>   # M2
```

Server command (exact line per model in `evidence/<model>/m1-command.txt`):

```bash
CUDA_VISIBLE_DEVICES=<card with most free memory> CUDA_DEVICE_ORDER=PCI_BUS_ID \
LD_LIBRARY_PATH=/opt/padyar-llm/llama/lib /opt/padyar-llm/llama/bin/llama-server \
  -m <gguf> --alias <alias> --host 127.0.0.1 --port 8010 \
  --api-key-file /var/lib/padyar/llm/api-key -ngl 999 --split-mode layer -fa on \
  -c 16384 -np 4 -ctk q8_0 -ctv q8_0 -lv 4
```

`-lv 4` is needed: at the default log level (3) llama-server v0.5.0 does not
print the `offloaded N/N layers` line the gate reads
(`evidence/gemma-4-26b-a4b/attempt1-lv3/server.log` has 11 lines and no offload
line). No `--reasoning-format none` was used. `-c 16384 -np 4` gives 4 slots of
4,096 tokens each (log: `n_ctx_seq = 4096`).

M2 command: `CUDA_VISIBLE_DEVICES=<gpu> llama-bench -m <gguf> -ngl 999 -fa 1
-ctk q8_0 -ctv q8_0 -p 512 -n 128 -r 3 -o md`, run after the server was
stopped, on the same card. Values are mean ± std over 3 repetitions.

The MMQ question (spike U11): **not answered**. Neither the server log at
`-lv 4` nor llama-bench states whether the int8 MMQ kernels or cuBLAS ran.

### 5.1 gemma-4-26B-A4B-it UD-Q4_K_M (`evidence/gemma-4-26b-a4b/`)

| Measure | Value | Evidence file |
|---|---|---|
| M1 load, first start (cold page cache) | 61.5 s | `attempt1-lv3/m1-load.txt` |
| M1 load, second start (warm) | 7.4 s | `m1-load.txt` |
| M1 offload | `load_tensors: offloaded 31/31 layers to GPU` | `server.log`, `m1-load.txt` |
| M1 memory plan | CUDA0 model 16,147.43 MiB, KV 170.00 + 637.50 MiB, compute 146.80 MiB; `CPU_Mapped` 748 MiB | `server.log` |
| M2 pp512 / tg128 | 1107.84 ± 6.62 / 47.74 ± 0.04 t/s | `m2-llama-bench.txt` |
| M3 GPU1 used/free before load | 3,283 / 21,157 MiB | `m3-vram.txt` |
| M3 GPU1 after load | 20,551 / 3,889 MiB | `m3-vram.txt` |
| M3 GPU1 after one TTS generation | 20,565 / **3,875 MiB** (TTS ran on GPU0: 4,311 to 4,505 MiB) | `m3-vram.txt`, `m3-tts.txt` (HTTP 200, 39.2 s) |
| M5a spike Gate 4 curl, server default | **fail**: `finish_reason: length` at 64 tokens, content `{"ok`, thinking in `reasoning_content` | `m5-json-probe.txt` |
| M5b health.py form, server default | **fail**: `length`, content empty | `m5-json-probe.txt` |
| M5a / M5b, `enable_thinking: false` | pass / pass: `{"ok": true}`, 11 tokens, strict parse OK | `m5-json-probe-nothink.txt` |

Measured 2026-09-30 18:29 to 18:34 UTC.

### 5.2 gemma-4-12b-it Q4_K_M (`evidence/gemma-4-12b/`)

| Measure | Value | Evidence file |
|---|---|---|
| M1 load (first start after download; page cache state not controlled) | 22.6 s | `m1-load.txt` |
| M1 offload | `offloaded 49/49 layers to GPU` | `m1-load.txt` |
| M1 memory plan | CUDA0 model 6,776.91 MiB; `CPU_Mapped` 540 MiB | `m1-load.txt` |
| M2 pp512 / tg128 | 566.49 ± 0.48 / 28.07 ± 0.01 t/s | `m2-llama-bench.txt` |
| M3 GPU1 before / after load / after TTS (free) | 21,157 / 12,931 / **12,429 MiB** (TTS ran on GPU1) | `m3-vram.txt`, `m3-tts.txt` (HTTP 200, 19.6 s) |
| M5a, server default | pass: `{"ok": true}`, but 59 of 64 tokens used (thinking) | `m5-json-probe.txt` |
| M5b, server default | **fail**: `length`, content empty | `m5-json-probe.txt` |
| M5a / M5b, `enable_thinking: false` | pass / pass, 11 tokens | `m5-json-probe-nothink.txt` |

Measured 2026-09-30 18:34 to 18:36 UTC.

### 5.3 Qwen3-14B Q4_K_M (`evidence/qwen3-14b/`)

For Qwen3 the spec asks for thinking off the adapter's way, so the main probe
run sends `chat_template_kwargs: {"enable_thinking": false}`. The
server-default result is recorded next to it for comparison.

| Measure | Value | Evidence file |
|---|---|---|
| M1 load (first start after download; page cache state not controlled) | 17.1 s | `m1-load.txt` |
| M1 offload | `offloaded 41/41 layers to GPU` | `m1-load.txt` |
| M1 memory plan | CUDA0 model 8,161.75 MiB; `CPU_Mapped` 417.30 MiB | `m1-load.txt` |
| M2 pp512 / tg128 | 505.36 ± 1.98 / 26.51 ± 0.00 t/s | `m2-llama-bench.txt` |
| M3 GPU1 before / after load / after TTS (free) | 20,655 / 10,831 / **10,831 MiB** (TTS ran on GPU0: 4,505 to 4,611 MiB) | `m3-vram.txt`, `m3-tts.txt` (HTTP 200, 11.5 s) |
| M5a / M5b, `enable_thinking: false` | pass / pass: `{"ok": true}`, 6 tokens | `m5-json-probe.txt` |
| M5a / M5b, server default | **fail** / **fail**: `length`, content empty | `m5-json-probe-thinking-default.txt` |

Measured 2026-09-30 18:36 to 18:37 UTC.

### 5.4 Reading the numbers

- gemma-4-26B-A4B generates 1.7x faster than the dense 12B and 14B models
  (47.7 vs 28.1 and 26.5 t/s), as its 3.8B active parameters predict. It also
  processes the prompt about 2x faster.
- The spike estimated the 26B-A4B would need 16.13 GiB on one card. llama.cpp's
  own plan was 17,101 MiB on the card (`common_params_fit_impl: projected to use
  17101 MiB`). It still fits, with 3.8 GiB left after a TTS generation.
- The TTS generation time varied from 11.5 s to 39.2 s across the three runs.
  This bench did not study that and makes no claim about its cause.
- Arithmetic, **not a measurement**: a selection prompt of about 1,500 tokens
  plus a 20-token reply would take roughly 1.4 s + 0.4 s on gemma-4-26B-A4B at
  the M2 rates, with thinking off. M4 is the measurement that would replace
  this line.

## 6. M4: the selection-tier harness (built, tested, NOT RUN on a real model)

**M4 status: NOT RUN: pending owner decision.** This includes the 4-request
concurrency probe that belongs to M4.

Why: the harness must run where both the code and the llama-server API key
are. Moving the key from the server to the developer Mac, and moving this
branch's code to the server, were both refused by the session's permission
policy. The leader (`db3-llm`) ruled that M4 waits for the owner, and no other
route was tried.

The harness itself is in this branch and tested:

- `scripts/bench_local_llm.py`, tests in `tests/test_bench_local_llm.py`
  (36 tests, all passing locally; a fake OpenAI-compatible server on
  127.0.0.1, no GPU, no network).
- `method`: the real `select_records` with the real `build_selection_prompt`,
  the real parse and the real grounding gate. Only `padyar_ai.generate` is
  replaced for the run, by a direct call to the real
  `OpenAICompatibleAdapter.invoke`, because the wrapper resolves the `chat`
  route from the DB-backed provider store. So there is no routing engine: no
  retry, no failover, no circuit breaker. A subclass overrides only
  `BaseAdapter.http`, to read llama-server's `timings` from the raw reply (the
  adapter drops that field). Tokens/s is the server's generation rate
  (`server_timings`) whenever `timings` is present, else it is labelled
  `end_to_end`.
- Candidates: `search.find_top_matches(strip_leading_greeting(q),
  k=ANSWER_TOPK)` over the corpus, seeded into a throwaway SQLite database.
  Never the developer database, never PostgreSQL.
- Not reproduced from `app/routers/chat.py`: the unknown-salient-token skip and
  the conversational gate. Every query with candidates is sent. History is
  empty; `lang` is `fa` when the query has Arabic-script letters, else `en`.

Definitions the harness uses, per model reply:

| Field | Definition |
|---|---|
| `json_valid` | `finish_reason` is not `length` AND `_parse_json_object` returns a dict. A cut-off reply counts invalid even when it parses, because `select_records` discards it first |
| `mode_valid` | `json_valid` and `mode` is one of `answer`, `options`, `converse`, `none` |
| `grounded` | `mode_valid`, every id is a string from the candidate list, and `answer`/`options` name at least one id |
| `correct` | read from the decision `select_records` returns. Expect set: `answer` with `ids[0] == expect`, or `options` with `expect` in the ids. Expect null (out of scope): `none` or `converse`. A `None` decision is never correct |

The command to run once the owner decides (Track 1's golden set is 67 queries):

```bash
.venv/bin/python scripts/bench_local_llm.py \
  --base-url http://127.0.0.1:18010/v1 --model gemma-4-26b-a4b \
  --api-key-file <0600 key file> --disable-thinking \
  --golden <goldeneval-t1>/data/eval/golden.json \
  --corpus <goldeneval-t1>/data/eval/corpus.json \
  --out result-gemma-4-26b-a4b.json --repeat 3
# concurrency probe: the same with --repeat 1 --concurrency 4
```

`--disable-thinking` is needed for all three models (see §1), not only Qwen3.

## 7. What was NOT run, and why

| Item | Why |
|---|---|
| M4 golden-set run, all models | pending owner decision (§6) |
| M4 concurrency probe (4 parallel requests) | part of M4 |
| `google/gemma-3-12b-it` | gated repo |
| Gate 4 through the app (`POST /admin/api/ai/providers/{id}/test-json`) | needs a provider instance in a running install; no app deploy in this bench. The curl probes use the same prompt as `app/services/ai/health.py` |
| Spike Steps 5, 5b, 6 | out of scope here; Step 5 and 6 need the model wired into a running install, Step 5b needs a native-speaker rater |
| MMQ vs cuBLAS path | not stated in any log (§5) |
| Controlled cold-load times for gemma-4-12b and Qwen3-14B | the page cache was not dropped before those loads |

## 8. What is left on the server

Nothing replaced, no service stopped or restarted, no llama process left
(`evidence/host/end-state.txt`: `pgrep llama` none, port 8010 free, TTS and
inotex health 200, `padyar-tts`, `padyar-inotex`, `padyar-elecomp`,
`cloudflared` active).

| Path | Content | Size |
|---|---|---|
| `/opt/padyar-llm/src` | llama.cpp v0.5.0 source + `build-cuda/` | part of 1.3 GB |
| `/opt/padyar-llm/llama/{bin,lib}` | `llama-server`, `llama-bench`, ggml/llama libs, CUDA runtime libs | part of 1.3 GB |
| `/opt/padyar-llm/build-llama.sh`, `llm-bench.sh` | the two scripts above | |
| `/opt/padyar-llm/venv` | empty Python venv from the dropped PyPI route | small |
| `/var/lib/padyar/llm/models` | the three GGUF files + `SHA256SUMS` | 31 GB |
| `/var/lib/padyar/llm/{api-key,auth-header}` | 0600, owner `gpu`; never printed or copied | |
| `/var/lib/padyar/llm/{logs,results}` | build/download logs, per-model results | |
| Docker image `nvidia/cuda:12.9.1-devel-ubuntu24.04` | route (a) build image, in Docker's own store | 15.9 GB |
