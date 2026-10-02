#!/bin/sh
# Build llama.cpp (pinned source in /opt/padyar-llm/src) for Tesla P40 (sm_61)
# inside the CUDA 12.9 devel image, then place binaries + the CUDA runtime
# libraries they need under /opt/padyar-llm/llama/{bin,lib}. The host needs
# only the NVIDIA driver to RUN them (no nvcc, no cmake on the host).
set -eu
IMG=nvidia/cuda:12.9.1-devel-ubuntu24.04
docker run --rm --cpus 16 \
  -v /opt/padyar-llm/src:/src -v /opt/padyar-llm:/dst "$IMG" bash -euc "
  rm -f /etc/apt/sources.list.d/cuda*.list  # developer.download.nvidia.com is 403 here
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cmake ninja-build >/dev/null
  # no driver in a build container: link against the CUDA stub libcuda
  ln -sf /usr/local/cuda/lib64/stubs/libcuda.so /usr/local/cuda/lib64/stubs/libcuda.so.1
  cmake --version | head -1; nvcc --version | tail -2
  cmake -S /src -B /src/build-cuda -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=61 -DGGML_NATIVE=ON \
    -DLLAMA_CURL=OFF -DLLAMA_BUILD_TESTS=OFF -DGGML_CUDA_NCCL=OFF \\
    -DCMAKE_EXE_LINKER_FLAGS=-Wl,-rpath-link,/usr/local/cuda/lib64/stubs
  cmake --build /src/build-cuda --target llama-server llama-bench -j 16
  mkdir -p /dst/llama/bin /dst/llama/lib
  cp /src/build-cuda/bin/llama-server /src/build-cuda/bin/llama-bench /dst/llama/bin/
  cp -P /src/build-cuda/bin/*.so* /dst/llama/lib/
  for l in libcudart.so.12 libcublas.so.12 libcublasLt.so.12; do
    cp -L /usr/local/cuda/lib64/\$l /dst/llama/lib/
  done
  chown -R 1000:1000 /src/build-cuda /dst/llama
"
echo BUILD_OK
