<!--
SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
-->

# Docker Compose Setup

## Architecture

![Architecture](architecture.svg)

## Quick Setup

Run the interactive setup script to complete all prerequisites in one go:

```bash
./setup.sh
```

## Prerequisites

The setup script handles the steps below automatically. If you prefer to do them manually:

1. **Create `.env` file** with the following variables:

   ```bash
   NGC_API_KEY=<your-ngc-api-key>
   NVIDIA_API_KEY=<your-nvidia-api-key>
   LOCAL_NIM_CACHE=<path-to-nim-cache>
   HF_TOKEN=<hf-token>                  # Required for the NVFP4 (Blackwell) checkpoint and any HF model
   ARTIFACTORY_USER=<your-username>
   ARTIFACTORY_TOKEN=<your-artifactory-api-key>
   # MODEL_PATH=<override>              # Optional — usually picked automatically (see "GPU overlays" below)
   ```

2. **Set NIM cache permissions:**

   ```bash
   chown -R 1000:1000 $LOCAL_NIM_CACHE
   ```

3. **Set Artifactory credentials** for media downloads:

   First, [join the Artifactory DL group](https://dlrequest/GroupID/Groups/Properties?identity=NjAzNDY4MmEzYzAxNGY4ZWIxMDMwOTNlNTEwMjMwMTZ8Z3JvdXA=) to get access to the required resources.

   Then create a token in artifactory.nvidia.com and provide credentials via environment variables:

   ```bash
   ARTIFACTORY_USER=<your-username>
   ARTIFACTORY_TOKEN=<your-artifactory-api-key>
   ```

   The media server downloader uses these to authenticate with Artifactory.

4. **Create the shared media volume:**

   ```bash
   docker volume create via-media-data
   ```

   This volume is shared across all compose stacks to avoid re-downloading media files.

## Running

```bash
# Perf / benchmarking flow (most common): activate the `media` profile so
# media-server.yaml's downloader + nginx start. Without this the lvs
# container has nothing to fetch from http://media-server/<file>.
docker compose -f <compose-file>.yaml --profile media up -d
# or:
COMPOSE_PROFILES=media docker compose -f <compose-file>.yaml up -d
```

The `media` profile is opt-in to keep dev-CI integration tests (which don't read any media-server file) from blocking ~15 min on the multi-GB downloader. `compose/run_benchmark.sh` and the perf Jenkins pipeline activate it automatically; manual perf invocations need the flag explicitly.

### Remote LLM (e.g. RTX 4500, 1-GPU host with LLM hosted elsewhere)

[`x86-rtvi-cr-remote-llm_1gpu.yaml`](x86-rtvi-cr-remote-llm_1gpu.yaml) deploys LVS + RTVI-VLM (Cosmos-Reason2-8B) + Elasticsearch on a single local GPU and points LVS at an OpenAI-compatible LLM endpoint running on a separate machine. The LLM is **not** managed by this compose stack — bring your own (NIM, vLLM, or any OpenAI-compatible server).

```bash
# 1. Required env
export NGC_API_KEY=<your-ngc-key>
export REMOTE_LLM_BASE_URL=http://<llm-host>:<port>/v1            # e.g. http://10.0.0.5:9233/v1
export REMOTE_LLM_MODEL_NAME=nvidia/nvidia-nemotron-nano-9b-v2    # whatever the remote serves
export ASSET_DOWNLOAD_AUTH_TOKENS="artifactory.nvidia.com=Bearer <token>"

# Optional: only if the remote LLM requires a bearer token
# export REMOTE_LLM_API_KEY=<llm-api-key>

# 2. Sanity-check the remote LLM is reachable from this host
curl -fs "$REMOTE_LLM_BASE_URL/models" | head

# 3. Launch the stack (--profile media activates the in-stack media-server
#    used by perf/benchmark URLs http://media-server/<file>)
docker compose -f compose/x86-rtvi-cr-remote-llm_1gpu.yaml --profile media up -d
docker compose -f compose/x86-rtvi-cr-remote-llm_1gpu.yaml --profile media logs -f lvs

# 4. (optional) Run the benchmark against it
./run_benchmark.sh -f x86-rtvi-cr-remote-llm_1gpu.yaml -s single_file_test

# 5. Tear down
docker compose -f compose/x86-rtvi-cr-remote-llm_1gpu.yaml down
```

`wait-for-llm` polls `${REMOTE_LLM_BASE_URL}/health/ready` (with a `/models` fallback) and blocks LVS startup until the remote endpoint is reachable, so it's safe to launch this stack before the remote LLM has finished warming up.

## Running Benchmarks

Use `run_benchmark.sh` to launch a stack, wait for health, and run the benchmark in one command. The script auto-detects your GPU and applies the matching VLM model overlay (see [GPU overlays](#gpu-overlays) below).

```bash
# Quick test (GPU overlay auto-detected)
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -s quick_test

# Full benchmark, tear down after
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -d

# Override GPU assignment and use a custom config
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -v 0,1 -l 2,3 -c perf_benchmark_config.yaml

# Force a specific overlay (e.g. test the B200 NVFP4 build on a mixed host)
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -G b200

# Skip overlay auto-detect; use base compose default
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -G none
```

Run `./run_benchmark.sh -h` for all options.

### Uploading Results to MinIO After Review

To inspect results locally before publishing to the dashboard, run the benchmark with `-O <name>` (writes a schema-conformant `lvs_*.json` to `../perf/benchmark/vss-perf-report/`) and skip `UPLOAD_TO_MINIO`. When ready, push with `upload_perf_results.sh`:

```bash
# Upload everything in the report dir, stamped as 'sqa'
./upload_perf_results.sh ../perf/benchmark/vss-perf-report/ -T sqa

# Or a specific file, stamped as 'perf-lab'
./upload_perf_results.sh ../perf/benchmark/vss-perf-report/lvs_*.json -T perf-lab
```

- First arg is a single `lvs_*.json` file or a directory (globs `lvs_*.json` inside).
- `-T TRIGGER` stamps `metadata.run_info.triggered_by` in each JSON before upload (`ci_pipeline | manual | scheduled | sqa | perf-lab`). Omit to keep whatever's in the file.
- Uploads to the standard `10.111.52.248:9000` `perf-results` bucket (baked-in defaults). Reuses the perf venv bootstrapped by any prior `run_benchmark.sh` run.

Run `./upload_perf_results.sh -h` for all options.

## GPU overlays

The `x86-rtvi-cr-*` compose files default `MODEL_PATH` to the **FP8** Cosmos-Reason3 Nano checkpoint (most common x86 target). To run on a different device, chain a small device-specific overlay file from [`overlays/`](overlays/) after the base compose file.

An overlay picks the **precision** its architecture supports; the **model size** is a separate axis, set by `CR3_VARIANT` (`nano`, the default, or `super`). FP8 needs compute capability ≥ 8.9, so Blackwell parts get NVFP4 and Hopper/Ada get FP8:

| Overlay | GPU | Arch (compute cap) | Quantization |
|---|---|---|---|
| `gpu-h100.yaml` | NVIDIA H100 (80 GB) | Hopper (9.0) | FP8 |
| `gpu-h200.yaml` | NVIDIA H200 (141 GB) | Hopper (9.0) | FP8 |
| `gpu-gh200.yaml` | NVIDIA GH200 Grace Hopper | Hopper (9.0) | FP8 |
| `gpu-l40s.yaml` | NVIDIA L40S (48 GB) | Ada Lovelace (8.9) | FP8 |
| `gpu-b200.yaml` | NVIDIA B200 | Blackwell (10.0) | NVFP4 |
| `gpu-gb200.yaml` | NVIDIA GB200 Grace Blackwell | Blackwell (10.0) | NVFP4 |
| `gpu-rtx-pro-6000.yaml` | RTX Pro 6000 Blackwell Workstation | Blackwell (12.0) | NVFP4 |
| `gpu-rtx-pro-4500.yaml` | RTX Pro 4500 Blackwell | Blackwell (12.0) | NVFP4 |
| `gpu-thor.yaml` | Jetson Thor T5000 | Blackwell iGPU (11.0) | NVFP4 |
| `gpu-spark.yaml` | DGX Spark / GB10 | Blackwell (12.1) | NVFP4 |

Checkpoints resolve to `ngc:nim/nvidia/cosmos3-<variant>-reasoner:<version>`, where `<version>` is `modelopt-fp8-final_format_fix` or `modelopt-nvfp4-full-quantize-final_format_fix`. Ampere (A100, A40) and older have no overlay: compute 8.0/8.6 is below the 8.9 those checkpoints require, so they need a BF16 `MODEL_PATH` set explicitly.

`run_benchmark.sh -G` accepts: `auto` (default; match by `nvidia-smi` device name) | `none` (skip overlay, use base default) | `h100` | `h200` | `gh200` | `b200` | `gb200` | `l40s` | `thor` | `spark` | `rtx-pro-6000` | `rtx-pro-4500` | `/abs/path/to/custom.yaml`. GPUs without a device-specific overlay (A100, A40, V100, ...) fall through with no overlay applied → the base compose file's `MODEL_PATH` wins. There is no family fallback.

The `thor-*` and `spark-*` compose files ship with `MODEL_PATH` pre-set to NVFP4 — no overlay required.

Detect your GPU manually with:

```bash
nvidia-smi --query-gpu=name,compute_cap --format=csv,noheader
```

### Apply an overlay

```bash
# manual `docker compose` invocation
docker compose \
  -f compose/x86-rtvi-cr-nemo3-nano_4gpu.yaml \
  -f compose/overlays/gpu-h100.yaml \
  --profile media up -d

# or let run_benchmark.sh pick the overlay automatically (recommended)
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml
```

### Choose Nano or Super

`CR3_VARIANT` selects the model size; the precision comes from wherever `MODEL_PATH` is defaulted — the overlay if one is applied, the base compose file otherwise. Either way you never hand-match a checkpoint to a GPU:

```bash
# CR3 Super, at whatever precision the detected GPU supports
CR3_VARIANT=super ./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml

# works with no overlay too — falls back to the base file's precision
CR3_VARIANT=super ./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml -G none

# or persistently, in .env
echo 'CR3_VARIANT=super' >> .env
```

This applies to every RTVI compose file (`x86-*`, `thor-*`, `spark-*`). The `x86-rtvi-cr-nim-*` proxy stacks are unaffected — they pin `MODEL_PATH: openai-compat` and get their checkpoint from the separate VLM NIM.

To run a checkpoint the overlays don't cover (BF16, a Diffuser build, a local path), set `MODEL_PATH` outright — it wins over both:

```bash
echo 'MODEL_PATH=ngc:nim/nvidia/cosmos3-super-reasoner:bf16-final' >> .env
```

Precedence: **explicit `MODEL_PATH` (shell / `.env`) > overlay (`-f`) > base compose default**.

### Jetson Orin NX (ARM64 iGPU, Ampere — LLM remote)

[`orin-nx-rtvi-cr-remote-llm.yaml`](orin-nx-rtvi-cr-remote-llm.yaml) runs RTVI-VLM locally on the Orin NX iGPU and routes LLM calls to a remote OpenAI-compatible endpoint. Orin NX (Ampere SM 8.7) does not support FP8/NVFP4, so the VLM defaults to a BF16 checkpoint.

**Step 1 — launch a NIM on the remote (x86) machine:**

```bash
# On the x86 host (e.g. 10.78.6.24), GPU 0
docker run -d --name nim-llm \
  --runtime nvidia \
  --gpus '"device=0"' \
  -e NGC_API_KEY=<your-ngc-api-key> \
  -e NIM_SERVER_PORT=9233 \
  -e NIM_GPU_MEM_FRACTION=0.50 \
  -e NIM_MAX_NUM_SEQS=16 \
  -v /opt/<user>/.cache:/opt/nim/.cache \
  -p 9233:9233 \
  nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2:latest

# Wait for it to be ready
curl -s http://10.78.6.24:9233/v1/health/ready
```

**Step 2 — `.env` on the Orin NX** (place in `lvs-axon-perf/compose/.env`):

```bash
NGC_API_KEY=<your-ngc-api-key>
NVIDIA_API_KEY=<your-nvidia-api-key>
HF_TOKEN=<your-hf-token>
ASSET_DOWNLOAD_AUTH_TOKENS="artifactory.nvidia.com=Bearer <artifactory-token>"

# Remote LLM — x86 host running Nemotron-Nano-9B NIM
REMOTE_LLM_BASE_URL=http://10.78.6.24:9233/v1
REMOTE_LLM_MODEL_NAME=nvidia/nvidia-nemotron-nano-9b-v2

# NVFP4 Nano — validated on Orin NX 16GB JetPack 7.2 (nvbug 6509938)
# Do NOT set VLLM_USE_NVFP4_CT_EMULATIONS (causes fp8e4nv error on SM8.7)
MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-nvfp4-full-quantize-final_format_fix
```

**Step 3 — run on the Orin NX:**

```bash
git fetch origin feat/orin-nx-compose
git checkout feat/orin-nx-compose

# Start the stack (--profile media activates the media downloader)
docker compose -f compose/orin-nx-rtvi-cr-remote-llm.yaml --profile media up -d

# Tail logs
docker compose -f compose/orin-nx-rtvi-cr-remote-llm.yaml logs -f lvs rtvi-vlm

# Run a quick benchmark (-G none skips overlay auto-detect; BF16 MODEL_PATH in .env wins)
./compose/run_benchmark.sh -f orin-nx-rtvi-cr-remote-llm.yaml -s single_file_test -G none

# Tear down
docker compose -f compose/orin-nx-rtvi-cr-remote-llm.yaml down
```

## Running Multiple Benchmarks in Parallel

Each compose file has a `name:` field that automatically namespaces containers, networks, and volumes. To run two benchmarks simultaneously, use different ports (`-p`) and GPU assignments (`-v`, `-l`):

```bash
# Terminal 1 — RTVI stack on GPUs 4-7, default port 38111
./run_benchmark.sh -f x86-rtvi-cr-nemo3-nano_4gpu.yaml \
  -v 4,5 -l 6,7 -s quick_test -d

# Terminal 2 — RTVI+NIM-VLM stack on GPUs 0-3, port 39111
./run_benchmark.sh -f x86-rtvi-cr-nim-nemo3-nano_4gpu.yaml \
  -p 39111 -v 0,1 -l 2,3 -s quick_test -d
```

Each run writes results to a unique timestamped directory (e.g. `vss-perf-report-x86-rtvi-cr-nemo3-nano-4gpu-20260304-143000`), so outputs never conflict.

To manage stacks manually, pass the same `-f` flag (and `--profile media` for any operation that should also touch the media-server containers) to `docker compose`:

```bash
docker compose -f x86-rtvi-cr-nim-nemo3-nano_4gpu.yaml --profile media logs -f lvs
docker compose -f x86-rtvi-cr-nim-nemo3-nano_4gpu.yaml --profile media down
```

## Compose file reference

Choose the compose file that matches your **host topology** (single host vs separate LLM, GPU count, x86 vs ARM). The VLM checkpoint precision (FP8 / NVFP4 / BF16) is picked separately via [GPU overlays](#gpu-overlays), so a single `x86-*` compose file works on any Hopper / Ada / Blackwell / Ampere GPU.

- **RTVI** — VLM bundled inside `rtvi-vlm` (no separate VLM NIM)
- **RTVI+NIM-VLM** — `rtvi-vlm` runs in proxy mode, forwards inference to a separate Cosmos-Reason2-8B NIM

| Platform | Topology   | GPUs   | Compose file |
|----------|------------|--------|--------------|
| **x86 (Hopper / Ada / Blackwell / Ampere)** | RTVI | 2 GPU | [`x86-rtvi-cr-nemo3-nano_2gpu.yaml`](x86-rtvi-cr-nemo3-nano_2gpu.yaml) |
| **x86 (Hopper / Ada / Blackwell / Ampere)** | RTVI | 4 GPU | [`x86-rtvi-cr-nemo3-nano_4gpu.yaml`](x86-rtvi-cr-nemo3-nano_4gpu.yaml) |
| **x86 (Hopper / Ada / Blackwell / Ampere)** | RTVI | 8 GPU | [`x86-rtvi-cr-nemo3-nano_8gpu.yaml`](x86-rtvi-cr-nemo3-nano_8gpu.yaml) |
| **x86 (Hopper / Ada / Blackwell)** | RTVI | 1 GPU (Nemotron-3-Nano-30B-A3B) | [`x86-rtvi-cr-nemo3-nano_1gpu.yaml`](x86-rtvi-cr-nemo3-nano_1gpu.yaml) |
| **x86 (Hopper / Ada / Blackwell)** | RTVI | 1 GPU (Nemotron 9B) | [`x86-rtvi-cr-nemotron-9b_1gpu.yaml`](x86-rtvi-cr-nemotron-9b_1gpu.yaml) |
| **x86 (any GPU; LLM remote)** | RTVI + remote LLM | 1 GPU (VLM only) | [`x86-rtvi-cr-remote-llm_1gpu.yaml`](x86-rtvi-cr-remote-llm_1gpu.yaml) |
| **x86 (any GPU)** | RTVI+NIM-VLM | 2 GPU | [`x86-rtvi-cr-nim-nemo3-nano_2gpu.yaml`](x86-rtvi-cr-nim-nemo3-nano_2gpu.yaml) |
| **x86 (any GPU)** | RTVI+NIM-VLM | 4 GPU | [`x86-rtvi-cr-nim-nemo3-nano_4gpu.yaml`](x86-rtvi-cr-nim-nemo3-nano_4gpu.yaml) |
| **x86 (any GPU)** | RTVI+NIM-VLM | 8 GPU | [`x86-rtvi-cr-nim-nemo3-nano_8gpu.yaml`](x86-rtvi-cr-nim-nemo3-nano_8gpu.yaml) |
| **RTX Pro only** (not H100/L40S) | RTVI+NIM-VLM | 1 GPU (Nemotron 9B) | [`x86-rtvi-cr-nim-nemotron-9b_1gpu.yaml`](x86-rtvi-cr-nim-nemotron-9b_1gpu.yaml) |
| **DGX Spark** (ARM64 SBSA, Blackwell) | RTVI | 1 GPU (Nemotron 9B) | [`spark-rtvi-cr-nemotron-9b.yaml`](spark-rtvi-cr-nemotron-9b.yaml) |
| **DGX Spark** (ARM64 SBSA, Blackwell) | RTVI | 1 GPU (Nemotron-3-Nano-30B-A3B) | [`spark-rtvi-cr-nemo3-nano.yaml`](spark-rtvi-cr-nemo3-nano.yaml) |
| **Jetson Thor** (ARM64 iGPU, Blackwell) | RTVI | 1 iGPU (Nemotron 9B) | [`thor-rtvi-cr-nemotron-9b.yaml`](thor-rtvi-cr-nemotron-9b.yaml) |
| **Jetson Thor** (ARM64 iGPU, Blackwell) | RTVI | 1 iGPU (Nemotron-3-Nano-30B-A3B-NVFP4) | [`thor-rtvi-cr-nemo3-nano.yaml`](thor-rtvi-cr-nemo3-nano.yaml) |
| **Jetson Orin NX** (ARM64 iGPU, Ampere; LLM remote) | RTVI + remote LLM | 1 iGPU (VLM only, NVFP4 Nano) | [`orin-nx-rtvi-cr-remote-llm.yaml`](orin-nx-rtvi-cr-remote-llm.yaml) |

All stacks currently ship **Cosmos-Reason3 Nano**; `CR3_VARIANT=super` switches them to Cosmos-Reason3 Super. The precision is chosen by the GPU overlay:
- x86 files default to FP8 (correct for H100 / H200 / GH200 / L40S); chain `overlays/gpu-b200.yaml` (B200), `overlays/gpu-gb200.yaml` (GB200), `overlays/gpu-rtx-pro-6000.yaml` (RTX Pro 6000 Blackwell Workstation), or `overlays/gpu-rtx-pro-4500.yaml` (RTX Pro 4500 Blackwell) for NVFP4. GPUs without a device-specific overlay (A100, A40, V100, ...) use the base compose default.
- Thor / Spark files default to NVFP4 (Blackwell-locked).

GPU placement in any stack can be overridden via env vars documented in each compose file's header (typically `RTVI_GPU_*`, `VLM_NIM_GPU_*`, `LLM_GPU_*`).

Supporting stacks (optional): [`media-server.yaml`](media-server.yaml) (included by default), [`otel-stack.yaml`](otel-stack.yaml) (observability).
