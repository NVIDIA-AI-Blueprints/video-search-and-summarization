# Base Profile Reference

Profile: `base` | Blueprint: `bp_developer_base` | Mode: `2d`

Video upload, Q&A, and report generation with HITL (Human-in-the-Loop) feedback.

## Services Deployed

The `base` service list activates only the services below. Elasticsearch, Kafka, and VST MCP are **not** part of `base` — they ship with `search`, `lvs`, and `alerts` (see those profile references). If you see `VST_MCP_URL` / `VSS_VA_MCP_PORT` warnings during `docker compose config`, that's expected on `base` and not an error.

Container names below are exactly what `docker ps` reports (sourced from the `container_name:` keys in `deploy/docker/services/**/compose.yml`). The LLM NIM container is named after the selected model. The local VLM is served by the integrated `vss-rtvi-vlm` service; `VLM_NAME` must match the model id advertised by its `/v1/models` endpoint.

| Service | Container | Port | Purpose |
|---|---|---|---|
| VSS Agent | `vss-agent` | 8000 | Orchestrates tool calls and model inference |
| VSS Agent UI | `vss-agent-ui` | 3000 | Web UI — chat, video upload, views |
| HAProxy Ingress | `vss-haproxy-ingress` | 7777 | Browser-facing entry point — proxies UI + Agent API + VST |
| VIOS Ingress (VST) | `vss-vios-ingress` | 30888 | Video Storage Tool — ingest, record, playback |
| VIOS Postgres | `vss-vios-postgres` | — | VIOS metadata store |
| VIOS Sensor MS | `vss-vios-sensor` | — | VIOS sensor management |
| VIOS Stream Processing | `vss-vios-streamprocessing` | — | VIOS stream processing |
| LLM NIM (default) | `nvidia-nemotron-nano-9b-v2` | 30081 | Nemotron LLM for reasoning. Activated by `llm_<mode>_<slug>` COMPOSE_PROFILES; container name = `${LLM_NAME_SLUG}` (e.g. `nvidia-nemotron-nano-9b-v2-fp8`, `nemotron-3-nano`, `gpt-oss-20b`, `llama-3.3-nemotron-super-49b-v1.5`). |
| Integrated RT-VLM (default) | `vss-rtvi-vlm` | 8018 | Cosmos Reason VLM for vision. Activated by the `rtvi-vlm` Compose profile with `VLM_MODEL_TYPE=rtvi` and `VLM_NAME_SLUG=none`. |
| Redis | `redis` | 6379 | Cache |
| Phoenix | `phoenix` | 6006 | Observability / telemetry |

## Default Models

| Role | Model | Slug | Type |
|---|---|---|---|
| LLM | `nvidia/nvidia-nemotron-nano-9b-v2` | `nvidia-nemotron-nano-9b-v2` | nim |
| VLM | `nim_nvidia_cosmos3-nano-reasoner_bf16-final` | `none` | rtvi |

The base `.env` defaults both sides to shared local deployment:
`LLM_MODE=local_shared` and `VLM_MODE=local_shared`, with
`LLM_DEVICE_ID=0`, `VLM_DEVICE_ID=0`, and `RT_VLM_DEVICE_ID=0`.
`dev-profile.sh` writes the same mode when LLM/VLM device IDs match and no
remote flags are selected. Local VLM requests stay on RT-VLM:
`VLM_BASE_URL=http://rtvi-vlm:8000`, `VLM_MODEL_TYPE=rtvi`, and host port
`8018`.

**Alternate LLMs:** `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8`, `nvidia/nvidia-nemotron-nano-9b-v2-dgx-spark`, `nvidia/nemotron-3-nano`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `openai/gpt-oss-20b`

**Alternate VLMs:** `nvidia/cosmos-reason1-7b`, `nvidia/cosmos-reason2-8b`, `Qwen/Qwen3-VL-8B-Instruct`

## Sizing — GPU memory per model

Sizing for `base` is per-model. The default pair is the integrated Cosmos3 Nano BF16 RT-VLM checkpoint + `nvidia-nemotron-nano-9b-v2` (LLM). Keep `VLM_NAME_SLUG=none`; `dev-profile.sh` maps supported local `--vlm` choices to `RTVI_VLM_MODEL_PATH` and the advertised `VLM_NAME`. The compose system selects the LLM through `llm_<mode>_<slug>` and always includes `rtvi-vlm` for the local base VLM.

The tables below give the **VRAM cost per model** (weights × 1.3 overhead). Use this with the [Sizing math](#sizing-math) section to decide whether a (LLM, VLM, GPU) combo fits. 

### LLMs (compose files under `deploy/docker/services/nim/`)

| Model | Type | Compose file | Params | Precision | Est. VRAM (weights × 1.3) |
|---|---|---|---|---|---|
| `nvidia/nvidia-nemotron-nano-9b-v2` (default) | NIM (`nvcr.io/nim/...:1`) | `nim/nvidia-nemotron-nano-9b-v2/compose.yml` | 9 B | FP16 | **23.4 GB** |
| `nvidia/nvidia-nemotron-nano-9b-v2-dgx-spark` | NIM (`nvcr.io/nim/...:1.0.0-variant`, DGX Spark only) | not in tree - see `edge.md` | 9 B | NVFP4 | ~5.9 GB |
| `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` | DLFW vLLM (`nvcr.io/nvidia/vllm:25.12.post1-py3`) | `nim/nvidia-nemotron-nano-9b-v2-fp8/compose.yml` | 9 B | FP8 | **11.7 GB** |
| `nvidia/nemotron-3-nano` | NIM | `nim/nemotron-3-nano/compose.yml` | ~3 B | FP16 | ~7.8 GB |
| `nvidia/llama-3.3-nemotron-super-49b-v1.5` | NIM | `nim/llama-3.3-nemotron-super-49b-v1.5/compose.yml` | 49 B | FP16 | **127 GB** (needs tp≥2 to fit on H100/L40S) |
| `openai/gpt-oss-20b` | NIM | `nim/gpt-oss-20b/compose.yml` | 20 B | FP16 | **52 GB** |
| `nvidia/NVIDIA-Nemotron-Edge-4B-v2.1-EA-020126_FP8` | DLFW vLLM (standalone, edge only) | not in tree — see `edge.md` | 4 B | FP8 | **5.2 GB** |

### VLM checkpoints served by integrated RT-VLM

Base does not activate standalone `vlm_<mode>_<slug>` services. Supported
local VLM choices are loaded inside `vss-rtvi-vlm`; `VLM_NAME_SLUG` remains
`none` for every row.

| `--vlm` selection | `RTVI_VLM_MODEL_PATH` | RT-VLM backend | Params | Precision | Est. VRAM (weights × 1.3) |
|---|---|---|---|---|---|
| `nvidia/cosmos3-reasoner` (default) | `ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final` | `cosmos-reason3` | nano | BF16 | validate empirically |
| `nvidia/cosmos-reason2-8b` | `ngc:nim/nvidia/cosmos-reason2-8b:hf-0303` | `cosmos-reason2` | 8 B | FP16 | **20.8 GB** |
| `nvidia/cosmos-reason1-7b` | `ngc:nim/nvidia/cosmos-reason1-7b:1.1-fp8-dynamic` | `cosmos-reason1` | 7 B | FP8 dynamic | validate empirically |
| `Qwen/Qwen3-VL-8B-Instruct` | `git:https://huggingface.co/Qwen/Qwen3-VL-8B-Instruct` | `vllm-compatible` | 8 B | model-defined | validate empirically |

`VLM_NAME` is the API id derived from the selected model path and advertised
by RT-VLM `/v1/models`; it is not necessarily the friendly `--vlm` value.
Always probe `/v1/models` after deployment.

### GPU VRAM reference


| GPU | VRAM | 85% usable | Notes |
|---|---|---|---|
| H100 SXM / PCIe | 80 GB | 68 GB | Default for shared mode |
| H200 | 141 GB | 119.85 GB | Plenty of headroom for any pair |
| B200 / GB200 | 192 GB | 163.2 GB | Newest, highest-capacity |
| RTX PRO 6000 (Blackwell) | 96 GB | 81.6 GB | Workstation Blackwell |
| GB10 (DGX Spark) | 128 GB unified | ~108 GB | Shared with system; cap aggressively |
| AGX/IGX Thor | 128 GB unified | ~108 GB | Edge unified memory |
| L40S / L40 / RTX 6000 Ada | 48 GB | 40.8 GB | Too small for LLM + VLM shared at FP16 |
| A100 80 GB | 80 GB | 68 GB | Hopper-era 80 GB option |

The "85% usable" column is the budget you have for weights + KV cache + activations. we reserve the remaining 15% for framework/CUDA overhead (`SINGLE_GPU_MEMORY_THRESHOLD = 0.85`).

## Sizing math


```text
weights_GB     = (num_params_B × bits_per_param) / 8
total_GB       = weights_GB × 1.3                          # +30% for KV cache + activations
fits_dedicated = total_GB                ≤  0.85 × gpu_vram_GB
fits_shared    = total_GB(LLM) + total_GB(VLM)
                                         ≤  0.85 × gpu_vram_GB

# In single-GPU shared mode, KV / GPU-mem fraction per service:
fraction       = (this_num_params / total_num_params) × 0.85
# Set this in the LLM NIM's effective knob and in
# RTVI_VLLM_GPU_MEMORY_UTILIZATION for the integrated VLM.
```

`bits_per_param` = 16 for FP16/BF16, 8 for FP8/INT8, 4 for INT4/MXFP4.

### GPU-memory fraction ↔ GB on common GPUs

The GPU-mem fraction knob is a fraction (0.0–1.0) of **total GPU VRAM** the
serving container may consume (weights + KV cache + activations included).

> **The two base services use different knobs:**
> - **LLM NIM** (`nim_llm_sdk`: nemotron / llama / gpt-oss) — `NIM_KVCACHE_PERCENT=<v>` *and* `NIM_GPU_MEM_FRACTION=<v>` (set both; version-dependent).
> - **Integrated RT-VLM** — `RTVI_VLLM_GPU_MEMORY_UTILIZATION=<v>`.
>   Standalone VLM-NIM knobs do not control the base VLM.

| Fraction | H100 / A100-80 (80 GB) | H200 (141 GB) | RTX PRO 6000 (96 GB) | GB10 / Thor (128 GB) | L40S (48 GB) |
|---|---|---|---|---|---|
| 0.25 | 20 GB | 35.25 GB | 24 GB | 32 GB | 12 GB |
| 0.40 | 32 GB | 56.4 GB | 38.4 GB | 51.2 GB | 19.2 GB |
| 0.50 | 40 GB | 70.5 GB | 48 GB | 64 GB | 24 GB |
| 0.70 (default dedicated for VLM) | **56 GB** | 98.7 GB | 67.2 GB | 89.6 GB | 33.6 GB |
| 0.85 (max safe) | 68 GB | 119.85 GB | 81.6 GB | 108.8 GB | 40.8 GB |

Read this as: a fraction of `0.7` on an H100 allows that service 56 GB total.
For the LLM, write the value to both NIM variables above; for RT-VLM, write
it to `RTVI_VLLM_GPU_MEMORY_UTILIZATION`.

### Worked example — Nemotron Nano 9B + Cosmos3 Reasoner Nano BF16 on H100 80 GB shared

```text
H100 max safe shared budget = 0.85 × 80 GB = 68 GB

LLM fraction = 0.40  →  NIM_KVCACHE_PERCENT=0.40            →  32 GB cap
VLM fraction = 0.40  →  RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.40 → 32 GB cap

shared check: 32 + 32 = 64 GB ≤ 68 GB ✓ fits
reserved     = 1 - (0.40 + 0.40) = 0.20  (framework/CUDA buffer)
```

The deployment generator writes `0.4` for shared H100 and RTX PRO 6000
Blackwell RT-VLM, and `0.7` for a dedicated local RT-VLM. Validate the BF16
checkpoint empirically rather than applying old standalone Cosmos NIM knobs.

## Choosing dedicated vs shared

| Available GPUs | Strategy |
|---|---|
| **2+ GPUs** | **Dedicated** — put the LLM NIM and RT-VLM on separate GPUs. Set `LLM_MODE=local`, `VLM_MODE=local`, `LLM_DEVICE_ID=0`, `VLM_DEVICE_ID=1`, and `RT_VLM_DEVICE_ID=1`. |
| **1 GPU + the pair fits** | **Shared** — set `LLM_MODE=local_shared`, `VLM_MODE=local_shared`, and `LLM_DEVICE_ID`, `VLM_DEVICE_ID`, and `RT_VLM_DEVICE_ID` to the same index. Tune the LLM NIM knobs and `RTVI_VLLM_GPU_MEMORY_UTILIZATION` per the formula above. |
| **1 GPU but the pair doesn't fit** | **Stop and ask the user about a remote endpoint** — see [When to use remote LLM/VLM](#when-to-use-remote-llmvlm). Don't silently switch to a smaller / lower-precision model; the user picked the model for a reason. |
| **0 local GPUs** | **`remote-all`** — both `LLM_MODE=remote` and `VLM_MODE=remote`. Sizing math doesn't apply locally. |

Rule of thumb: a config is **`single_gpu_viable`** iff every service has `gpu_count=1` AND the sum of all services' total VRAM ≤ 0.85 × GPU VRAM. If false, the agent must escalate to the user (don't auto-pick a smaller local fallback).

## When to use remote LLM/VLM

Two — and only two — triggers should put either side into `remote` mode.

### Trigger 1 — User supplied an endpoint

The user's prompt names an LLM and/or VLM endpoint URL (e.g. *"deploy with remote LLM at `http://launchpad:11571` serving `nvidia/nvidia-nemotron-nano-9b-v2`"*) or asks for `remote-all`. Action:

- Set `LLM_MODE=remote` (and/or `VLM_MODE=remote`) in `dev-profile-base/generated.env`.
- Set `LLM_BASE_URL` (no trailing `/v1`), `LLM_NAME`, and `NVIDIA_API_KEY` if the endpoint requires auth.
- Local sizing math doesn't apply for the remote side.
- See [Env Overrides — Common Scenarios](#env-overrides--common-scenarios) below for full recipes.

### Trigger 2 — Local GPU can't fit the model the user wants

The sizing math says the user's chosen LLM/VLM (or pair) doesn't fit on the available GPUs. **Stop the deploy and ask the user**:

> The host has `<N>` × `<GPU>` (`<VRAM>` GB each). The model `<LLM_NAME>` needs `~<X>` GB at `<precision>`, which doesn't fit alongside `<VLM_NAME>` (`~<Y>` GB).
>
> Options:
> 1. **Switch to a remote LLM (or VLM)** — give me the endpoint URL and the model name served there. NVIDIA's public API is `https://integrate.api.nvidia.com` if you have an `NVIDIA_API_KEY`.
> 2. **Switch to a lower-precision build** of the same model (e.g. `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` instead of FP16).
> 3. **Use `remote-all`** — both LLM and VLM at remote endpoints; no local GPU used.

Wait for the user to pick. **Don't silently substitute a different local model** — the user chose the original for a reason (eval consistency, behavior parity, license, etc.).

### Hard rules

- **L40S (48 GB) shared mode is tight.** Use the sizing math for the selected LLM/VLM pair. If the pair exceeds 40.8 GB usable, use a 2-GPU L40S host (one model per GPU), or escalate to the user per Trigger 2.
- **DGX Spark shared mode must use the DGX Spark Nano 9B NIM path in `edge.md`.** Run `nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2-dgx-spark:1.0.0-variant` as a standalone local NIM on port `30081` and set `LLM_MODE=remote`, `LLM_BASE_URL=http://localhost:30081`, and `LLM_NAME_SLUG=none`. The image is not wired into compose yet. Do not use the standard `nvcr.io/nim/nvidia/nvidia-nemotron-nano-9b-v2:1` image on DGX Spark.
- **AGX/IGX Thor shared mode: Edge 4B is the LLM; the VLM still runs via RT-VLM.** The Edge 4B fallback in `edge.md` (standalone vLLM + `HF_TOKEN`) is the **LLM** path — this skill has no verified Thor-supported Nano 9B NIM, so keep it unless the user supplies a verified remote LLM endpoint. The **VLM** on base+Thor is *not* a standalone NIM: `dev-profile.sh` deploys RT-VLM with the integrated Cosmos Reason3 Nano BF16 checkpoint (`VLM_MODEL_TYPE=rtvi`, `RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:bf16-final`, `RTVI_VLM_MODEL_TO_USE=cosmos-reason3`, `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.35`).
- **Llama 3.3 49B FP16 doesn't fit on a single 80 GB GPU.** 49 × 16 / 8 × 1.3 = 127 GB > 68 GB usable. Either run dedicated with tensor parallelism (`tp=2` on two H100s → 63.7 GB/GPU) or use H200 (141 GB) / B200 (192 GB) — or escalate per Trigger 2.
- **`HARDWARE_PROFILE` is a tuning selector, not a sizing oracle.** It selects
  the LLM NIM env file and lets the deployment generator choose the RT-VLM
  fraction. The correctness check is the **resolved compose**: the selected
  LLM service and `vss-rtvi-vlm` must use the intended devices and fractions.
- **Remote side — no model-hosting GPU needed.** A remote LLM skips its local
  NIM. A remote VLM keeps local RT-VLM as an OpenAI-compatible proxy but sets
  `RTVI_VLM_MODEL_PATH=none`, so RT-VLM itself does not load model weights.

## Tuning workflow

`HARDWARE_PROFILE` chooses the LLM NIM env file and the generator's RT-VLM
defaults. The values that reach the resolved compose are authoritative:

1. **Compute** the start fraction from [Sizing math](#sizing-math). Round to 2 decimal places.
2. **Write** the LLM fraction to
   `deploy/docker/services/nim/<model-slug>/hw-<HARDWARE_PROFILE>(-shared).env`
   as both `NIM_KVCACHE_PERCENT` and `NIM_GPU_MEM_FRACTION`. Write the VLM
   fraction to `RTVI_VLLM_GPU_MEMORY_UTILIZATION` in `generated.env`.
3. **Re-resolve and deploy**: `docker compose --env-file <stable-env> --env-file <generated-env> config > resolved.yml && docker compose --env-file <stable-env> --env-file <generated-env> -f resolved.yml up -d`. Both `--env-file` arguments are required on `up` too. Before running `up -d`, verify `resolved.yml` includes the selected LLM service and `vss-rtvi-vlm`, with the intended device IDs and sizing values.
4. **Watch container logs** (`docker logs <llm-container>` and
   `docker logs vss-rtvi-vlm`) for model-load and memory reports:
   - **OOM at model load** → lower fraction by 0.05 and redeploy.
   - **OOM mid-inference** (after a few requests, on long prompts) → also lower `NIM_MAX_MODEL_LEN` / `--max-model-len` and `NIM_MAX_NUM_SEQS` (e.g. from `4096`/`16` to `2048`/`4`).
   - **Container starts but "Out of memory for chunked prefill"** → lower `NIM_MAX_NUM_SEQS` only.
   - **Plenty of headroom** (KV cache reports < 30% utilization under load) → raise fraction by 0.05 and redeploy to extract more concurrency.
5. **Save** the working LLM env-file values and RT-VLM generated override so
   the pair is reproducible.

> **Don't tune past 0.85.** The default 15% reserved is what NIMs/vLLM need for CUDA graphs, framework overhead, and activation buffers. Going higher reliably OOMs under non-trivial load.

## Swapping a different LLM/VLM

The two model paths are intentionally different:

- LLM choices select a standalone service through
  `LLM_NAME_SLUG` + `llm_<mode>_<slug>`.
- Local VLM choices stay inside `vss-rtvi-vlm`; always keep
  `VLM_NAME_SLUG=none`, `VLM_MODEL_TYPE=rtvi`, and the `rtvi-vlm` Compose
  profile.

### Swap the LLM

In-tree LLM slugs under `deploy/docker/services/nim/` are
`nvidia-nemotron-nano-9b-v2`, `nvidia-nemotron-nano-9b-v2-fp8`,
`nemotron-3-nano`, `llama-3.3-nemotron-super-49b-v1.5`, and
`gpt-oss-20b`. Set the matching pair in `generated.env`:

```bash
# Example: switch LLM to Nano 9B FP8
LLM_NAME=nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8
LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2-fp8
```

The slug must match the directory name. Re-resolve and confirm the selected
LLM service plus `vss-rtvi-vlm` are both present. Adding a new local LLM still
uses the existing NIM/DLFW compose patterns; it does not change the base VLM
path.

### Swap the local VLM

Use the mapping in [VLM checkpoints served by integrated
RT-VLM](#vlm-checkpoints-served-by-integrated-rt-vlm). Set all RT-VLM fields
together. For example, Cosmos Reason1:

```bash
VLM_MODE=local
VLM_MODEL_TYPE=rtvi
VLM_NAME=nim_nvidia_cosmos-reason1-7b_1_1-fp8-dynamic
VLM_NAME_SLUG=none
VLM_BASE_URL=http://rtvi-vlm:8000
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos-reason1-7b:1.1-fp8-dynamic
RTVI_VLM_MODEL_TO_USE=cosmos-reason1
COMPOSE_PROFILES=phoenix,redis,vss-haproxy-ingress,vss-ui,vss-agent,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG},rtvi-vlm
```

After deployment, require `docker ps` to contain `vss-rtvi-vlm` and require
its `/v1/models` result to contain `VLM_NAME`. Do not set a VLM slug or add a
`vlm_<mode>_<slug>` token for base.

If a requested VLM is not supported as an integrated checkpoint, stop and ask
for a supported choice or a remote OpenAI-compatible endpoint. Remote VLM
still keeps RT-VLM in the request path as a proxy:
`VLM_NAME_SLUG=none`, `RTVI_VLM_MODEL_PATH=none`,
`RTVI_VLM_MODEL_TO_USE=openai-compat`, and
`RTVI_VLM_ENDPOINT=<remote-root>/v1`.

> **Edge note.** On DGX Spark / Thor, follow `edge.md` for the LLM. The base
> VLM remains integrated RT-VLM on those platforms.

### Picking GPU-memory fractions quickly

For shared mode, compute it via the formula. As sanity-check defaults / in-tree precedents:

| Co-residency | LLM NIM fraction | `RTVI_VLLM_GPU_MEMORY_UTILIZATION` | Source |
|---|---|---|---|
| Nano 9B v2 + Cosmos3 Reasoner Nano BF16 (shared) | 0.40 | 0.40 | `dev-profile.sh` base shared defaults |
| DGX Spark Nano 9B NIM + Cosmos3 Reasoner Nano BF16 on DGX Spark | 0.40 | 0.40 | `edge.md` |
| Edge 4B + RT-VLM on Thor | 0.25 | RT-VLM default 0.35 | `edge.md` Thor fallback |
| Qwen3-VL 8B + Nano 9B (shared) | 0.40 | start at 0.40 and validate | integrated RT-VLM mapping |

Rules of thumb when adding a new model:

- **FP8 / INT8 weights:** start at 0.40 shared, 0.85 dedicated.
- **BF16 / FP16 weights:** start at 0.40–0.50 shared (only if the pair fits per the formula), 0.85 dedicated.
- **Edge unified memory (DGX Spark / Thor):** cap aggressively. Start with `0.40` for the DGX Spark Nano 9B NIM recipe and `0.25` for the Thor Edge 4B vLLM fallback; lower by `0.05` if startup or first inference reports memory pressure.
- **OOM at startup** → lower by 0.05. **OOM mid-inference** → also lower `NIM_MAX_MODEL_LEN` / `--max-model-len` and `NIM_MAX_NUM_SEQS`.

If you're unsure what fits, ask for remote LLM/VLM endpoints. Base keeps
RT-VLM as the remote-VLM proxy, but it does not load local VLM weights.

## Env Overrides — Common Scenarios

### Minimal deploy (auto-detect hardware)

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "VSS_APPS_DIR": "<repo>/deploy/docker",
  "VSS_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "NGC_CLI_API_KEY": "<from env>"
}
```

> **Note on base URLs**: `LLM_BASE_URL` / `VLM_BASE_URL` must NOT end in `/v1`.
> The agent config appends `/v1` automatically. If the user gives you a URL
> with `/v1`, strip it before writing to the env.

### Remote LLM + local VLM

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "VSS_APPS_DIR": "<repo>/deploy/docker",
  "VSS_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "NGC_CLI_API_KEY": "<from env>",
  "LLM_MODE": "remote",
  "LLM_BASE_URL": "https://integrate.api.nvidia.com",
  "NVIDIA_API_KEY": "<key>"
}
```

### Remote LLM + remote VLM (`remote-all` — no local GPU for inference)

Fire this recipe when the user says *"deploy in remote-all mode"*,
*"both LLM and VLM are remote"*, or supplies two endpoint URLs (one per
role). Both mode vars MUST flip from the `.env` defaults
(`LLM_MODE=local_shared`, `VLM_MODE=local_shared`) to `remote`; leaving either
at `local_shared` keeps the local shared NIM `COMPOSE_PROFILES` active.

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "VSS_APPS_DIR": "<repo>/deploy/docker",
  "VSS_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "LLM_MODE": "remote",
  "LLM_BASE_URL": "<llm-endpoint-from-user>",
  "LLM_NAME":     "<llm-model-from-user>",
  "VLM_MODE": "remote",
  "VLM_BASE_URL": "<vlm-endpoint-from-user>",
  "VLM_NAME":     "<vlm-model-from-user>",
  "NVIDIA_API_KEY": "<key if endpoints require auth>"
}
```

If the user didn't provide endpoint URLs/models, **ask them** — don't
guess. For NVIDIA's public API: `https://integrate.api.nvidia.com` (strip
any trailing `/v1`). For launchpad-style internal endpoints, use the
exact URL they gave you.

Post-write sanity check:
```bash
grep -E '^(LLM_MODE|VLM_MODE|LLM_BASE_URL|VLM_BASE_URL|LLM_NAME|VLM_NAME)=' \
  deploy/docker/developer-profiles/dev-profile-base/generated.env
```
Expect six lines, all non-empty; `LLM_MODE=remote` and `VLM_MODE=remote`
must both appear. If either is `local_shared` or `local`, you did not
overwrite the template default — re-run the `sed` with the correct value.

### Dedicated GPUs (2-GPU system)

```json
{
  "HARDWARE_PROFILE": "<detected>",
  "VSS_APPS_DIR": "<repo>/deploy/docker",
  "VSS_DATA_DIR": "<repo>/data",
  "HOST_IP": "<detected>",
  "NGC_CLI_API_KEY": "<from env>",
  "LLM_MODE": "local",
  "VLM_MODE": "local",
  "LLM_DEVICE_ID": "0",
  "VLM_DEVICE_ID": "1"
}
```

### Different LLM model

```json
{
  "LLM_NAME": "nvidia/llama-3.3-nemotron-super-49b-v1.5",
  "LLM_NAME_SLUG": "llama-3.3-nemotron-super-49b-v1.5"
}
```

## COMPOSE_PROFILES (computed — do not set directly)

The profile's `overrides.env` sets this to an explicit list of service names (each service carries its own `profiles: ["<name>"]`):

```
COMPOSE_PROFILES=phoenix,redis,vss-haproxy-ingress,vss-ui,vss-agent,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_${LLM_MODE}_${LLM_NAME_SLUG},rtvi-vlm
```

Example resolved value:
```
phoenix,redis,vss-haproxy-ingress,vss-ui,vss-agent,centralizedb,vst-ingress,sensor-ms,streamprocessing-ms,llm_local_shared_nvidia-nemotron-nano-9b-v2,rtvi-vlm
```

The agent sets the upstream variables — `COMPOSE_PROFILES` is derived automatically.

## Endpoints (after deploy)

**Report the deployed public origin, not a raw container port.** Read it
directly from the running stack — `docker inspect vss-agent` exposes
`VSS_AGENT_EXTERNAL_URL`, the fully-assembled `proto://host:port` the agent
actually serves (orchestrator equivalent: `docker_read`). Don't synthesize a
`<HOST_IP>:<port>` URL — that surfaces an unreachable internal IP on Brev,
where this origin is the `https://7777-<id>.brevlab.com` secure link (see
[`brev.md`](brev.md)). Call that value `PUBLIC` below; everything is routed
through the HAProxy ingress at that origin.

| Service | URL to report (through ingress) |
|---|---|
| Agent UI | `${PUBLIC}/` |
| Agent REST API | `${PUBLIC}/api` |
| Reports | `${PUBLIC}/static/agent_report_<DATE>.md` |
| Phoenix telemetry | `${PUBLIC}/phoenix` |

**Direct service ports — internal only** (on-host `curl` debugging; not
browser-reachable on Brev, never report these as the access URL):

| Service | Direct port |
|---|---|
| Agent UI (direct) | `http://<HOST_IP>:3000/` |
| Agent REST API (direct) | `http://<HOST_IP>:8000/` |
| Integrated RT-VLM (direct) | `http://<HOST_IP>:8018/v1/models` |
| Swagger UI | `http://<HOST_IP>:8000/docs` — not routed through the ingress; direct/port-forward only |
| Phoenix (direct) | `http://<HOST_IP>:6006/` |

## Env File Location

```
<repo>/deploy/docker/developer-profiles/dev-profile-base/.env
<repo>/deploy/docker/developer-profiles/dev-profile-base/generated.env
```

## Debugging

After a base deploy is up, confirm the full pipeline (VST upload → VLM →
agent report) by driving a real query through the agent — e.g. ask it over
the REST API or UI to describe a video you've uploaded to VST. If the
agent returns a non-empty answer, the upload → ingest → inference → reply
path is healthy.

Common failure modes and what they mean for base:

| Symptom | Likely cause |
|---|---|
| `POST /api/v1/videos` HTTP 500 | Agent not finished starting — poll `/health` longer |
| VST `sensor/streams` stays empty | VST container unhealthy — check `docker logs vss-vios-ingress` |
| VST returns empty `sensor/streams` but VST container is healthy | Check Postgres health/logs with `docker logs vss-vios-postgres`. Current compose uses the named volume `vios_pg_data` for PGDATA, not a `$VSS_DATA_DIR` Postgres bind mount. See [`data-directory.md`](data-directory.md) before removing any volume. |
| WebSocket query returns `error_message` | LLM NIM or RT-VLM not healthy — `docker logs nvidia-nemotron-nano-9b-v2` / `docker logs vss-rtvi-vlm` |
| HITL prompt never arrives | `vss-agent` misconfigured HITL config — check `config.yml` |
| Empty report | VLM unreachable from inside `vss-agent` container — check `VLM_BASE_URL` in resolved compose env |

## Known Issues

- Reports are in-memory by default — lost on container restart (mount a volume to persist)
