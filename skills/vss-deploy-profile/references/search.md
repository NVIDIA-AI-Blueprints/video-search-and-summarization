# VSS Search Profile — Reference

Profile: `search` | Blueprint: `bp_developer_search` | Mode: `2d`

> **Alpha feature** — not recommended for production.

Semantic video search via Cosmos Embed1 embeddings indexed in Elasticsearch. RT-VLM is always part of the search profile: it serves the optional **Critique agent** and the always-available `video_understanding` tool. Critic verification is controlled per request with `use_critic`.

## What's different from `base` and `lvs`

- **Four always-on GPU services:** `rtvi-cv` (DeepStream perception), `rtvi-embed` (Cosmos Embed1 embeddings), the **LLM**, and `vss-rtvi-vlm`. Search does not deploy a standalone Cosmos VLM NIM.
- **RT-VLM serves two consumers.** The Critique agent uses it when `use_critic=true` (default), and `video_understanding` uses it independently.
- **Default local layout uses two GPUs.** GPU 0 hosts `RT_CV_DEVICE_ID=0` plus `RT_VLM_DEVICE_ID=0`; GPU 1 hosts `RT_EMBED_DEVICE_ID=1` and `LLM_DEVICE_ID=1`. Both GPUs are shared, so RT-VLM must leave headroom for RT-CV and the LLM must leave headroom for RT-Embed.
- **Remote VLM still uses a local RT-VLM proxy.** `vss-rtvi-vlm` runs in `openai-compat` mode and forwards inference to the selected remote endpoint.

## What gets deployed

Container names below are the actual `container_name:` keys from `deploy/docker/services/**/compose.yml`. The LLM NIM container is named after `LLM_NAME_SLUG`; the VLM is always `vss-rtvi-vlm`.

| Service | Container | Port | Purpose |
|---|---|---|---|
| RT-CV (DeepStream perception) | `vss-rtvi-cv` | 9000 | Object detection / tracking on incoming streams; default model family `rtdetr-warehouse` |
| RT-Embed (Cosmos Embed1) | `vss-rtvi-embed` | 8017 | Video + text embedding generation |
| LLM NIM (default) | `nvidia-nemotron-nano-9b-v2` | 30081 | Same options as `base` (Nano 9B v2 default). Container name = `${LLM_NAME_SLUG}`. |
| RT-VLM | `vss-rtvi-vlm` | 8018 | Local Cosmos3 inference or an OpenAI-compatible proxy to a remote VLM; serves Critique and `video_understanding` |
| VSS Agent | `vss-agent` | 8000 | Orchestrates tool calls, embed search, critique |
| VSS Agent UI | `vss-agent-ui` | 3000 | Search tab |
| VST Ingress | `vss-vios-ingress` | 30888 | Video storage + ingest |
| Elasticsearch + Logstash + Kibana | `elasticsearch`, `logstash`, `kibana` | 9200, 5601 | Index, ingest pipeline, dashboards |
| Kafka | `kafka` | 9092 | Embedding pipeline message bus |
| Phoenix | `phoenix` | 6006 | Observability |

## Default models

| Role | Model | Slug | Served by |
|---|---|---|---|
| LLM | `nvidia/nvidia-nemotron-nano-9b-v2` | `nvidia-nemotron-nano-9b-v2` | NIM (port 30081) |
| Embed (RT-Embed) | `nvidia/Cosmos-Embed1-448p-anomaly-detection` | — | RT-Embed (port 8017), `MODEL_PATH=git:https://huggingface.co/nvidia/Cosmos-Embed1-448p-anomaly-detection` |
| Perception (RT-CV) | siglip2 v1.1 + RTDETR (warehouse) | — | RT-CV (DeepStream pipeline) |
| VLM (RT-VLM) | `ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix` (default local checkpoint; FP8 so it fits alongside RT-CV on GPU 0) | `VLM_NAME_SLUG=none`; activated via the `rtvi-vlm` compose profile | RT-VLM (port 8018) |

## VLM placement

RT-VLM is always selected with `VLM_MODEL_TYPE=rtvi` and `VLM_NAME_SLUG=none`; the `vss-rtvi-vlm` container is activated by the explicit `rtvi-vlm` compose profile (no `vlm_*` NIM profile), the same way `base`/`lvs` deploy RT-VLM. Choose whether RT-VLM loads the model locally or proxies a remote endpoint.

```
User supplied or approved a remote VLM endpoint?     → Path A: RT-VLM remote proxy
   │
   ▼
DEFAULT — at least 2 GPUs available?                 → Path B: local RT-VLM on GPU 0
   │
   ▼
Only 1 GPU available?                                → Path A: remote VLM required
```

The default is **Path B**, with RT-CV + RT-VLM on GPU 0 and RT-Embed + LLM on GPU 1. On a single-GPU Brev host, use Path A; `dev-profile.sh` rejects local RT-VLM there rather than silently overcommitting the only GPU.

### Path A — RT-VLM proxy to a remote VLM

Triggered when the user provides a VLM endpoint URL, asks for `remote-vlm` / `remote-all`, or approves remote placement because fewer than two GPUs are available. RT-VLM remains local as the media-processing/OpenAI-compatible proxy:

```bash
VLM_MODE=remote
VLM_BASE_URL=<remote-endpoint>                           # no trailing /v1
VLM_NAME=<model-name-served-there>
VLM_NAME_SLUG=none                                      # rtvi-vlm is activated by the explicit rtvi-vlm compose profile
VLM_MODEL_TYPE=rtvi
VLM_PORT=30082                                          # compatibility value for remote mode
RTVI_VLM_ENDPOINT=<remote-endpoint>/v1
RTVI_VLM_MODEL_TO_USE=openai-compat
RTVI_VLM_MODEL_PATH=none
RT_VLM_DEVICE_ID=0                                      # proxy still requests the configured GPU runtime
NVIDIA_API_KEY=<key if required>
```

The resolved compose must include profile `rtvi-vlm` and container `vss-rtvi-vlm`. `VLM_BASE_URL` has no `/v1`; `RTVI_VLM_ENDPOINT` includes `/v1`.

### Path B — Default: local RT-VLM sharing GPU 0 with RT-CV

Use this on a host with at least two free GPUs:

```bash
RT_CV_DEVICE_ID=0
RT_EMBED_DEVICE_ID=1
LLM_DEVICE_ID=1                                          # LLM shares GPU 1 with RT-Embed
VLM_DEVICE_ID=0                                          # RT-VLM shares GPU 0 with RT-CV
RT_VLM_DEVICE_ID=0
LLM_MODE=local_shared
VLM_MODE=local_shared
VLM_NAME=nim_nvidia_cosmos3-nano-reasoner_modelopt-fp8-final_format_fix
VLM_NAME_SLUG=none
VLM_MODEL_TYPE=rtvi
VLM_PORT=8018
RTVI_VLM_MODEL_TO_USE=cosmos-reason3
RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos3-nano-reasoner:modelopt-fp8-final_format_fix
RTVI_VLLM_GPU_MEMORY_UTILIZATION=<hardware-derived value>  # 0.4 on H100/RTX PRO 6000 in local_shared
```

`VLM_MODE` derives to `local_shared` rather than `local` because `VLM_DEVICE_ID=0` is listed in `FIXED_SHARED_DEVICE_IDS`. That is what selects the shared-GPU memory fraction, so do not hand-set `VLM_MODE=local` here — it would let RT-VLM claim the fraction meant for a dedicated GPU and starve RT-CV.

The resolved compose must include profile `rtvi-vlm` and container `vss-rtvi-vlm`. Use `RTVI_VLLM_GPU_MEMORY_UTILIZATION`, not `NIM_KVCACHE_PERCENT`, to size RT-VLM.

## Sizing — RT-Embed and RT-CV knobs

For VLM and LLM weight cost + the general formula, see [`base.md` § Sizing math](base.md#sizing-math). RT-Embed and RT-CV add their own knobs.

### RT-Embed sizing

Image: `ghcr.io/nvidia-ai-blueprints/vss/vss-rt-embed:develop-latest` (multi-architecture manifest). Compose: `deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml`.

Per the upstream `perf/benchmark/rtvi_embed_gpu_initial_stream_counts.json`, the **dedicated-GPU ceiling** — max concurrent streams when RT-Embed has the GPU to itself with **no co-resident** model:

| GPU | Max streams (RT-Embed dedicated) |
|---|---|
| H100 80 GB SXM / HBM3 | **140** |
| H100 80 GB PCIe | 100 |
| H100 NVL | 100 |
| RTX PRO 6000 (Blackwell) | 120 |
| L40S | 60 |
| A40 | 30 |
| Thor / GB10 (DGX Spark) | 30 |

These are upper bounds for the dedicated case (any layout where you give RT-Embed its own GPU and nothing else co-locates). The default search layout always has the LLM co-resident on RT-Embed's GPU, so the practical ceiling is lower — but with the 10-GB RT-Embed budget in [Worked example](#worked-example--llm--rt-embed-on-gpu-1), `NUM_STREAMS=16` runs comfortably on all H100/RTX PRO 6000 configs, and `NUM_STREAMS=8` is the safe value on L40S / Thor / GB10.

Knobs (in `dev-profile-search/.env` unless noted):

| Var | Inside-container | Default | Effect |
|---|---|---|---|
| `MODEL_PATH` | `MODEL_PATH` | `git:https://huggingface.co/nvidia/Cosmos-Embed1-448p-anomaly-detection` | Embedding checkpoint. Variants: `Cosmos-Embed1-224p`, `-336p`, `-448p` (smaller resolution = smaller VRAM). |
| `RTVI_EMBED_MODEL` | (label) | `cosmos-embed1-448p-anomaly-detection` | Identifier used by the agent. |
| `NUM_STREAMS` | (RT-CV only — see below) | `16` | Concurrent stream count target for the whole pipeline. |
| `RTVI_EMBED_NUM_VLM_PROCS` | `NUM_VLM_PROCS` | `10` | Parallel embedding workers. More procs = more throughput, more VRAM per process. |
| `VLM_BATCH_SIZE` | `VLM_BATCH_SIZE` | auto (3 / 16 / 64 / 128 by GPU mem) | Batch size for inference. Auto-clamps to GPU capacity. |
| `RTVI_EMBED_NUM_GPUS` / `VSS_NUM_GPUS_PER_VLM_PROC` | `NUM_GPUS` | empty (1) | Multi-GPU distribution per embed process. |
| `RT_EMBED_DEVICE_ID` | (compose `device_ids`) | `1` | Which GPU RT-Embed pins to. |
| `VSS_RT_EMBED_TAG` | (image tag) | `develop-latest` | Pin a promoted or immutable image tag when required. |

**Default Cosmos-Embed1 deployment runs on Triton (ONNX), not vLLM.** From `start_rtvi_embed.sh:47-49` and `src/models/custom/samples/cosmos-embed1/inference.py:55-56`, the default `VLM_MODEL_TO_USE=custom` loads Cosmos-Embed1 via Triton-served ONNX models (`text_embeddings`, `video_embeddings`). For that path:

- **No KV cache** — embedding inference is single-pass through an encoder; there's no autoregressive generation, so vLLM's KV-cache concepts don't apply. There is nothing to disable.
- **`VLLM_GPU_MEMORY_UTILIZATION` is a no-op** when serving the default Cosmos-Embed1. The start script sets it to 0.7 for ≤50 GB GPUs and the Python wrapper's fallback is also 0.7, but the Triton/ONNX path doesn't read it.
- **Memory is governed by Triton runtime + ONNX weights + per-stream activation buffers**, scaling with `NUM_STREAMS`, `NUM_VLM_PROCS`, and `VLM_BATCH_SIZE`. Cosmos-Embed1 (~1 B params at FP16 ≈ 2 GB weights) is small; the dominant cost on big concurrency is per-stream buffers and the decoder workers.

**`VLLM_GPU_MEMORY_UTILIZATION` IS relevant** only when `VLM_MODEL_TO_USE=vllm-compatible` is set — i.e. when RT-Embed is loading a vLLM-served model instead of Cosmos-Embed1 (uncommon for Search; relevant for the LVS Nemotron Omni path). In that case the same `weights + KV + activations` semantics as [`base.md`](base.md#nim_kvcache_percent--gb-on-common-gpus) apply, and the shared-GPU override discussion in [Worked example](#worked-example--llm--rt-embed-on-gpu-1) below applies.

**For the default search shared layout (LLM + Cosmos-Embed1 on GPU 1)**, **budget 10 GB for RT-Embed and give the LLM the rest** — `NIM_KVCACHE_PERCENT = (GPU_VRAM - 10) / GPU_VRAM - 0.15`. See the [worked example](#worked-example--llm--rt-embed-on-gpu-1) for the per-GPU table. No RT-Embed util override is needed; the env var is a no-op for the default Cosmos-Embed1 model.

### RT-CV sizing

Image: the managed GHCR coordinate `VSS_RT_CV_IMAGE`:`VSS_RT_CV_TAG` (SBSA: same image, tag `develop-latest-sbsa`). Compose: `deploy/docker/services/rtvi/rtvi-cv/compose.yaml`.

RT-CV is a **DeepStream perception pipeline**, not a vLLM container. It has no `--gpu-memory-utilization`-style knob. Memory scales with stream count and the active model family.

Knobs (in `dev-profile-search/.env`):

| Var | Default | Effect |
|---|---|---|
| `NUM_STREAMS` | `16` | Concurrent video streams in the perception pipeline. Single biggest VRAM driver. |
| `DS_MODEL_FAMILY` | `rtdetr-warehouse` | Detection model family. Other variants change weight footprint. |
| `DS_MODE_FLAG` | `1` | DeepStream mode. |
| `DS_MESSAGE_RATE` | `1` | Inference messages per second per stream. |
| `DS_TRACKER_REID` | `false` | Enable re-identification (extra VRAM). |
| `VISION_ENCODER_MODEL` | `siglip_v2` | Vision encoder downloaded by ds-start phase 0. |
| `RT_CV_DEVICE_ID` | `0` | Which GPU RT-CV pins to. |
| `VSS_RT_CV_TAG` | `3.3.0-26.07.2` | Image tag (use `-sbsa-` variant on DGX Spark). |

The upstream perf guide doesn't publish a single GB number — it publishes per-GPU max stream counts (consistent with the table above for RT-Embed). Treat **`NUM_STREAMS=16`** as a starting point on H100 / RTX PRO 6000 / L40S; lower it on smaller GPUs or when co-locating with a VLM.

## Worked example — LLM + RT-Embed on GPU 1

Default layout, Nano 9B v2 LLM + Cosmos-Embed1 on GPU 1.

**RT-Embed budget rule of thumb: 10 GB.** Cosmos-Embed1 weights are ~2 GB (1 B params at FP16); the rest is per-stream activation buffers, decoder workers, and Triton/ONNX runtime overhead. 10 GB is a comfortable budget for `NUM_STREAMS=16` on any GPU. Reserve those 10 GB and give the LLM the rest, leaving the standard 15% framework headroom.

| GPU | VRAM | RT-Embed reserved | Framework (15%) | LLM gets | `NIM_KVCACHE_PERCENT` |
|---|---|---|---|---|---|
| H100 / A100-80 | 80 GB | 10 GB | 12 GB | 58 GB | **0.72** |
| H200 | 141 GB | 10 GB | 21 GB | 110 GB | **0.78** |
| RTX PRO 6000 (Blackwell) | 96 GB | 10 GB | 14 GB | 72 GB | **0.75** |
| L40S | 48 GB | 10 GB | 7 GB | 31 GB | **0.65** (tight — verify under load) |

Formula: `NIM_KVCACHE_PERCENT = (GPU_VRAM - 10) / GPU_VRAM - 0.15`, rounded to 2 decimals.

Two writes:

```bash
# 1. In deploy/docker/services/nim/nvidia-nemotron-nano-9b-v2/hw-H100-shared.env
NIM_KVCACHE_PERCENT=0.72             # LLM gets ~58 GB; leaves 10 GB for RT-Embed + 12 GB framework

# 2. In deploy/docker/developer-profiles/dev-profile-search/generated.env
RT_EMBED_DEVICE_ID=1
LLM_DEVICE_ID=1
LLM_MODE=local_shared
NUM_STREAMS=16
RTVI_EMBED_NUM_VLM_PROCS=            # leave default (10)
# No VLLM_GPU_MEMORY_UTILIZATION override needed — Cosmos-Embed1 uses Triton/ONNX
# (the env var is a no-op for the default model). Override only if you switch
# RT-Embed to VLM_MODEL_TO_USE=vllm-compatible.
```

That's it. No compose-file tweak required for the default Cosmos-Embed1 deployment.

**If you've switched RT-Embed to a vllm-compatible model** (rare — would happen if you load a vLLM-served embedding model instead of Cosmos-Embed1), then you also need to cap RT-Embed's `VLLM_GPU_MEMORY_UTILIZATION`. Compute it from the 10 GB budget: `10 / GPU_VRAM` ≈ `0.13` on H100. Add a passthrough to `rtvi-embed-docker-compose.yml`'s `environment:` block (`VLLM_GPU_MEMORY_UTILIZATION: "${RTVI_EMBED_VLLM_GPU_MEMORY_UTILIZATION:-}"`) and set `RTVI_EMBED_VLLM_GPU_MEMORY_UTILIZATION=0.13` in the profile env.

> **Verifying under load.** Watch `docker logs vss-rtvi-embed` and `nvidia-smi -l 5` on GPU 1 while pushing `NUM_STREAMS=16` of test video. If RT-Embed's resident memory exceeds ~12 GB, raise the budget (e.g. 12 → 15 GB → recompute LLM `NIM_KVCACHE_PERCENT`). If the LLM OOMs at startup, it usually means RT-Embed grabbed more than 10 GB before the LLM allocated; constrain RT-Embed by lowering `NUM_STREAMS` or `RTVI_EMBED_NUM_VLM_PROCS` (10 → 4).

RT-VLM shares GPU 0 with RT-CV in the default search layout, so its budget and the RT-CV stream count now compete for the same device. Size RT-VLM with `RTVI_VLLM_GPU_MEMORY_UTILIZATION` (0.4 on H100 / RTX PRO 6000 in `local_shared`) and leave the remainder for RT-CV; continue to size the LLM + RT-Embed pair on GPU 1 with the table above. If RT-CV fails to build engines or drops streams, lower the RT-VLM fraction before touching `NUM_STREAMS`.

## Hard rules

- **RT-VLM must always be reachable.** Disabling Critique does not remove this requirement because `video_understanding` still uses RT-VLM.
- **Default local search requires two GPUs.** On a single-GPU host, use the remote-proxy path for the VLM.
- **L40S search requires a remote LLM.** There is no `hw-L40S-shared.env` for a local-shared NIM, so the LLM cannot share GPU 1 with RT-Embed. Local RT-VLM may share GPU 0 with RT-CV. The LLM and VLM still cannot occupy the same GPU. The L40S row in the [worked example](#worked-example--llm--rt-embed-on-gpu-1) table applies only to layouts that give the LLM its own GPU.
- **Edge platforms (DGX Spark / Thor) are not supported for `search` yet** — track upstream blueprint for support. Use SBSA image tags (`-sbsa-`) when they land.
- **`RESERVED_DEVICE_IDS` and `FIXED_SHARED_DEVICE_IDS` come from defaults** in `dev-profile-search/.env` (`''` and `'0,1'` respectively). Nothing is reserved because both GPUs are shared, and listing both devices as shared is what makes the LLM and RT-VLM derive `local_shared` memory fractions. The skill works at the env-file level, so leave them as-is unless changing the layout meaningfully (e.g. swapping which GPU hosts RT-CV vs RT-Embed).
- **`/v1` quirk** — `LLM_BASE_URL` / `VLM_BASE_URL` have no `/v1` (the client appends it). In remote-proxy mode, `RTVI_VLM_ENDPOINT` does include `/v1`.

## Key capabilities

- Upload videos; embeddings are generated automatically by RT-Embed.
- Natural language queries (e.g. "find all instances of forklifts") use Cosmos-Embed1's joint video/text embedding space.
- Filter results by similarity score, time range, video name, description, source.
- Timestamped results with clip playback in the UI.
- Critique agent re-checks top retrieval results via the VLM (default-on; toggle in the UI sidebar).

## Endpoints (after deploy)

See [`base.md` — Endpoints](base.md#endpoints-after-deploy) for how `${PUBLIC}` is resolved and Brev secure-link behavior. Rows marked *(direct)* are on-host only, not browser-reachable on Brev.

| Service | URL to report (through ingress) |
|---|---|
| Agent UI | `${PUBLIC}/` |
| Agent REST API | `${PUBLIC}/api` |
| Kibana | `${PUBLIC}/kibana` |
| Phoenix | `${PUBLIC}/phoenix` |
| nvstreamer | own secure link `https://31000-<id>.brevlab.com` on Brev (see [`brev.md`](brev.md)); else `http://<HOST_IP>:31000/` |
| RT-Embed (direct) | `http://<HOST_IP>:8017/` |
| Elasticsearch (direct) | `http://<HOST_IP>:9200/` |
| RT-VLM (direct) | `http://<HOST_IP>:8018/v1/` |

## Env file location

```
deploy/docker/developer-profiles/dev-profile-search/.env
deploy/docker/developer-profiles/dev-profile-search/generated.env
```

## Perception model download (automatic)

The RT-DETR detector model, SigLIP vision encoder, and any other required assets are downloaded automatically by `ds-start.sh` phase 0 at perception container startup when `DS_MODEL_DOWNLOAD=auto` is set (the default in the compose file). No manual model staging or separate init container is needed.

Ensure `NGC_CLI_API_KEY` is exported and `${VSS_DATA_DIR}/models` exists and is writable before first deploy:

```bash
mkdir -p "$VSS_DATA_DIR/models"
chmod -R 777 "$VSS_DATA_DIR/models"
```

After download, RT-CV builds a TensorRT engine from the ONNX (3–5 min on first start). Engine caches live alongside the ONNX files under `$VSS_DATA_DIR/models/`.

## First-run note

RT-Embed downloads Cosmos-Embed1 weights from Hugging Face on first start; ds-start phase 0 downloads `siglip_v2` from NGC and stages the RT-DETR ONNX, then builds a TensorRT engine. Expect 15–25 min extra on the first deploy.

### HuggingFace token for RT-Embed

RT-Embed downloads the model named in `MODEL_PATH` (default `git:https://huggingface.co/nvidia/Cosmos-Embed1-448p-anomaly-detection`) from Hugging Face on first start. Setting `HF_TOKEN`:

- **speeds up the first-run download** of the default public Cosmos-Embed1 checkpoint, and
- **enables using private or gated HF models** when you repoint `MODEL_PATH` at, e.g., a custom fine-tune hosted in a private org.

Set `HF_TOKEN` in `deploy/docker/developer-profiles/dev-profile-search/.env` (default empty) to a token from https://huggingface.co/settings/tokens — a `read`-scope token is enough. The value wires through to the `rtvi-embed` container's `HF_TOKEN` environment variable via the search profile's `.env` (see `deploy/docker/services/rtvi/rtvi-embed/rtvi-embed-docker-compose.yml` line 64: `HF_TOKEN: "${HF_TOKEN:-}"`). Restart the container after changing it.

## Debugging

- **`docker logs vss-rtvi-embed`** — confirms model load and `Maximum concurrency for X tokens per GPU: Y x` line. If it OOMs, lower `RTVI_EMBED_NUM_VLM_PROCS` (10 → 4) or `NUM_STREAMS`.
- **`docker logs vss-rtvi-cv`** — DeepStream perception pipeline logs. If GPU 0 OOMs, lower `NUM_STREAMS`.
- **`docker logs vss-rtvi-vlm`** — RT-VLM model/proxy startup and inference logs. Confirm `curl -sf http://localhost:8018/v1/models` before testing Critique or `video_understanding`.
- **Embedding queries return zero hits** — check shared `logstash` is consuming `mdx-embed-filtered` and that the ES index `mdx-embed-filtered-2025-01-01` exists.
- **Critique returns `unverified` / "no VLM configured"** — confirm `VLM_MODEL_TYPE=rtvi`, `VLM_NAME_SLUG=none`, the resolved compose includes the `rtvi-vlm` profile and `vss-rtvi-vlm` container, and its `/v1/models` endpoint returns the configured `VLM_NAME`. For remote mode also verify `RTVI_VLM_MODEL_TO_USE=openai-compat`, `RTVI_VLM_MODEL_PATH=none`, and `RTVI_VLM_ENDPOINT=<remote>/v1`.
