# VSS Alerts Profile — Reference

Profile: `alerts` | Blueprint: `bp_developer_alerts` | Modes: `2d_cv` (verification) and `2d_vlm` (real-time)

Real-time alert generation and verification on RTSP / live video. VLM is **always served by RT-VLM** (port 8018), same as LVS — there is no standalone Cosmos NIM in the alerts compose graph. The `COMPOSE_PROFILES` for alerts is `${BP_PROFILE}_${MODE},${BP_PROFILE}_${MODE}_${HARDWARE_PROFILE},llm_${LLM_MODE}_${LLM_NAME_SLUG}` — note the absence of `vlm_*_<slug>`. Setting `VLM_NAME_SLUG=none` for alerts is intentional and required.

## Two modes

| Mode | CLI | `MODE` env | How it works | VLM load |
|---|---|---|---|---|
| **verification** | `--mode verification` | `2d_cv` | DeepStream perception (RT-CV with Grounding DINO) generates alerts upstream; behavior analytics filters them; alert-bridge invokes VLM **only** to verify alert clips. | Lower — VLM runs per alert |
| **real-time** | `--mode real-time` | `2d_vlm` | VLM continuously inspects live video at periodic intervals; broad coverage without upstream CV dependency. RT-CV not deployed. | Higher — VLM runs continuously |

Switch modes by editing `MODE` in `dev-profile-alerts/.env` (`MODE=2d_cv` or `MODE=2d_vlm`) and re-resolving the compose. `dev-profile.sh` also rewrites the UI subtitle (`NEXT_PUBLIC_APP_SUBTITLE`) for the matching mode label.

## What's different from `base` and `lvs`

- **VLM is RT-VLM with the integrated Cosmos Reason 2 checkpoint.** Default `RTVI_VLM_MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208`, `RTVI_VLM_MODEL_TO_USE=cosmos-reason2`. No standalone `cosmos-reason2-8b` NIM service is started.
- **`VLM_NAME` must match RT-VLM's `/v1/models` basename.** Set `VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208` for the default Cosmos2 path; alert-bridge / agent get HTTP 400 "No such model" otherwise (see `vllm_compatible_model.py::get_model_info` for the lookup logic).
- **`VLM_NAME_SLUG=none`** for alerts — `COMPOSE_PROFILES` does not include a `vlm_local_*_<slug>` segment. The only LLM/VLM compose profile that matters for alerts is `llm_${LLM_MODE}_${LLM_NAME_SLUG}`.
- **`VLM_PORT=8018`** by default (RT-VLM). Set to `30082` when `VLM_MODE=remote` (RT-VLM not started; agent points at the remote endpoint).
- **Alert-bridge** (port 9080) is the bridge between RT-VLM events / behavior analytics and the agent's realtime alerting API. Verification mode reads from RT-CV → behavior analytics → alert-bridge → VLM verification. Real-time mode reads from RT-VLM → alert-bridge directly.

> **`VLM_MODE` is required in `.env` but is a runtime signal, not a deployment-shape selector.** The vss-agent container reads `VLM_MODE` at startup (`services/agent/compose.yml:79` → `dev-profile-alerts/vss-agent/configs/config.yml:76,91`) to configure its VLM tool. It does **not** flip a compose profile (alerts has no `vlm_*_<slug>`). Set it to match physical placement: `local_shared` (RT-VLM on the LLM's GPU), `local` (RT-VLM on its own GPU), or `remote` (no local RT-VLM).

> **An LLM endpoint is required.** The alerts agent uses the LLM for incident-report generation and tool routing. `dev-profile-alerts/vss-agent/configs/config.yml:152,193,259` reference `nim_llm` / `prompt_gen_llm`, and `COMPOSE_PROFILES` always pulls in `llm_${LLM_MODE}_${LLM_NAME_SLUG}`. To skip the local LLM container, point at a remote endpoint (`LLM_MODE=remote`, set `LLM_BASE_URL`). The detection pipeline alone (RT-CV → behavior-analytics → RT-VLM, alerts in Kafka/ES) doesn't need the LLM, but the agent does — and without the agent the user has no UI / report flow.

## What gets deployed

| Service | Container | Port | Purpose | Mode |
|---|---|---|---|---|
| RT-CV (DeepStream perception) | vss-rtvi-cv | — (host net) | Object detection (Grounding DINO via `MODEL_NAME_2D=GDINO`) | **2d_cv only** |
| Behavior analytics | vss-behavior-analytics | — | Rule-based alerts from RT-CV metadata | **2d_cv only** |
| RT-VLM | vss-rtvi-vlm | 8018 | VLM runner (Cosmos Reason 2 by default) | both |
| Alert-bridge | (alert-bridge) | 9080 | Realtime alerting API; drives `POST/DELETE /api/v1/realtime` on the agent | both |
| LLM NIM | mdx-nim-llm-1 | 30081 | Same options as `base` (Nano 9B v2 default) | both |
| nvstreamer-alerts | vss-vios-nvstreamer | 31000 | Plays back dataset video to simulate live cameras | both |
| VST | mdx-vst-1 | 30888 | Video storage + ingest | both |
| VSS Agent | mdx-vss-agent-1 | 8000 | Orchestrates alert verification and incident reports | both |
| VSS UI | mdx-vss-ui-1 | 3000 | Alerts tab | both |
| Video-Analytics MCP | vss-va-mcp | 9901 | Analytics API for the agent | both |
| Elasticsearch + Kibana | mdx-elasticsearch-1, kibana | 9200, 5601 | Alert/event storage | both |
| Kafka | mdx-kafka-1 | 9092 | Message bus | both |
| Phoenix | mdx-phoenix-1 | 6006 | Observability | both |

## Default models

| Role | Model | `VLM_NAME` / slug | Served by |
|---|---|---|---|
| LLM | `nvidia/nvidia-nemotron-nano-9b-v2` | `nvidia-nemotron-nano-9b-v2` | NIM (port 30081) |
| VLM | Cosmos Reason 2 8B (integrated) | **`nim_nvidia_cosmos-reason2-8b_hf-1208`** / slug **`none`** | RT-VLM (port 8018), `MODEL_PATH=ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` |
| Perception (2d_cv only) | Grounding DINO | (`MODEL_NAME_2D=GDINO`, `MODEL_TYPE=cnn`) | RT-CV (DeepStream) |

LLM alternates: same as `base` — `NVIDIA-Nemotron-Nano-9B-v2-FP8`, `nemotron-3-nano`, `llama-3.3-nemotron-super-49b-v1.5`, `gpt-oss-20b`.

VLM alternates: see [VLM serving paths](#vlm-serving-paths) below.

## Default GPU layout

Reference defaults from `dev-profile-alerts/.env`:

```bash
RT_CV_DEVICE_ID=0           # perception (2d_cv only)
RT_VLM_DEVICE_ID=1          # RT-VLM container
LLM_DEVICE_ID=1             # LLM NIM
VLM_DEVICE_ID=1
LLM_MODE=local_shared       # LLM compose service: shared-GPU variant
VLM_MODE=local_shared       # signals the agent that RT-VLM lives on the LLM's GPU
RESERVED_DEVICE_IDS='0'     # GPU 0 reserved for RT-CV; not picked by shared-LLM compose
```

The default is a **2-GPU layout**: RT-CV alone on GPU 0, LLM + RT-VLM shared on GPU 1. `VLM_MODE=local_shared` here just tells the agent's runtime config that the VLM endpoint is co-located — it doesn't gate any compose service on its own. To move RT-VLM to its own GPU 2, change `RT_VLM_DEVICE_ID` and `VLM_DEVICE_ID` to `2`, set `VLM_MODE=local`, and bump `RTVI_VLLM_GPU_MEMORY_UTILIZATION` per [Sizing](#sizing). To use a remote VLM, see [Path B](#path-b--remote-vlm-user-supplied).

Real-time mode (`MODE=2d_vlm`) doesn't deploy RT-CV, so GPU 0 is free in that mode — often given to RT-VLM for more KV-cache headroom.

## VLM serving paths

Pick the path that matches the user's VLM choice. Default is **integrated**.

### Path A — Integrated (RT-VLM loads Cosmos Reason)

Default. RT-VLM serves the VLM locally. Supported integrated checkpoints (same set as LVS):

| VLM | `RTVI_VLM_MODEL_PATH` | `RTVI_VLM_MODEL_TO_USE` |
|---|---|---|
| Cosmos Reason 2 8B (default) | `ngc:nim/nvidia/cosmos-reason2-8b:hf-1208` | `cosmos-reason2` |
| Cosmos Reason 1 7B | `ngc:nim/nvidia/cosmos-reason1-7b:hf-<tag>` | `cosmos-reason` |
| Nemotron Nano V3 Omni 30B | `git:https://huggingface.co/nvidia/Nemotron-Nano-V3-Omni-GA0420-FP8` | `vllm-compatible` (see [`lvs.md` § Nemotron Omni](lvs.md#path-a--integrated-rt-vlm-loads-the-checkpoint-itself)) |

Switching VLMs is a `dev-profile-alerts/.env` edit; **`VLM_NAME_SLUG` stays `none`** and `VLM_NAME` must match the model basename returned by RT-VLM's `/v1/models` (otherwise alert-bridge fails with HTTP 400). For Cosmos Reason 1 the `VLM_NAME` becomes `nim_nvidia_cosmos-reason1-7b_hf-<tag>`.

### Path B — Remote VLM (user supplied)

Use when:

1. **The user supplied a remote VLM endpoint URL**, or
2. **The local GPU(s) can't fit the requested VLM alongside the LLM + RT-CV** per the sizing math (and the user agreed to go remote — same two-trigger rule as [`base.md` § When to use remote LLM/VLM](base.md#when-to-use-remote-llmvlm)).

The full set of env vars the skill writes to `dev-profile-alerts/.env`:

```bash
VLM_MODE=remote                                          # agent reads this; switches its VLM tool to remote-endpoint mode
VLM_PORT=30082                                           # was 8018; rtvi-vlm is not started in remote mode
VLM_BASE_URL=<remote-endpoint>                           # no trailing /v1
VLM_NAME=<model-name-served-there>                       # not nim_…; use the model id the remote actually advertises
RTVI_VLM_ENDPOINT=<remote-endpoint>/v1                   # WITH /v1
RTVI_VLM_MODEL_TO_USE=openai-compat
RTVI_VLM_MODEL_PATH=none
NVIDIA_API_KEY=<key if required>
```

> **`/v1` quirk** (same as LVS): `VLM_BASE_URL` no `/v1`; `RTVI_VLM_ENDPOINT` yes `/v1`. Always write both consistently.

### Path C — BYO local VLM (model not in the integrated set)

For VLMs RT-VLM can't load directly (e.g. Qwen3-VL or a third-party HF model): stand it up as a separate NIM/vLLM service per [`base.md` § Swapping a different LLM/VLM](base.md#swapping-a-different-llmvlm), then point RT-VLM at the local URL via Path B's env vars (`VLM_BASE_URL=http://${HOST_IP}:30082`, etc.). Keep `VLM_MODE=remote` so RT-VLM doesn't try to load the model itself.

## Sizing

For LLM weight cost, VLM weight cost, and the general formula, see [`base.md` § Sizing math](base.md#sizing-math) — it applies unchanged. The alerts profile adds **RT-VLM** and **RT-CV** as co-residents that the LLM has to share with.

### Values the skill writes directly

The skill writes these env vars to `dev-profile-alerts/.env` itself; there's no auto-tuning, so the agent has to apply the right values per the chosen layout. The numbers below are the upstream `dev-profile.sh:1200-1234` defaults — used as a precedent / starting point, not because the script ever runs.

**`RTVI_VLLM_GPU_MEMORY_UTILIZATION`:**

| Layout | Hardware | Value |
|---|---|---|
| RT-VLM shares GPU with LLM (`VLM_MODE=local_shared`) | any | **0.35** |
| RT-VLM on its own GPU (`VLM_MODE=local`) | DGX-SPARK or L40S | **0.8** |
| RT-VLM on its own GPU (`VLM_MODE=local`) | H100 / RTXPRO6000BW | leave blank → RT-VLM's hardcoded 0.7 fallback applies |
| RT-VLM on its own GPU on edge | OTHER / IGX-THOR / AGX-THOR | leave blank |

**`RT_VLM_DEVICE_ID`:**

| Layout | Value |
|---|---|
| RT-VLM shares GPU with LLM | same as `LLM_DEVICE_ID` (GPU 1 default) |
| RT-VLM on its own GPU | the dedicated GPU index (e.g. 2) |
| `VLM_MODE=remote` | irrelevant; RT-VLM not started |
| Edge (`IGX-THOR` / `AGX-THOR`) | `0` (unified memory) |

Edge platforms also need `VLM_AS_VERIFIER_CONFIG_FILE_PREFIX=EDGE-LOCAL-VLM-` so `vlm-as-verifier` picks up the matching config under `dev-profile-alerts/vlm-as-verifier/configs/EDGE-LOCAL-VLM-config.yml`.

### Default shared-mode budget (H100 80 GB, MODE=2d_cv)

```text
GPU 0 (RT-CV alone):
  Grounding DINO + DeepStream pipeline ≤ NUM_SENSORS streams (default 1).
  Plenty of headroom on a dedicated H100.

GPU 1 (LLM + RT-VLM shared):
  RT-VLM at 0.35 × 80 GB = 28 GB                           # set by dev-profile.sh
  LLM    at NIM_KVCACHE_PERCENT × 80 GB                    # tune in nim/<llm-slug>/hw-H100-shared.env
  framework/CUDA buffer ~ 15% of GPU = 12 GB
  → leaves 80 - 28 - 12 = 40 GB for the LLM
  → NIM_KVCACHE_PERCENT ≈ 0.50 (40 / 80)
```

So on H100 with the default Cosmos2 + Nano 9B pair:
- `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.35` (already set by the script for shared mode).
- `NIM_KVCACHE_PERCENT=0.50` in `nim/nvidia-nemotron-nano-9b-v2/hw-H100-shared.env`.

For real-time mode (`MODE=2d_vlm`), GPU 0 is free (no RT-CV). Two layouts make sense:

1. **Keep shared on GPU 1** — simpler. Same numbers as above; GPU 0 is unused.
2. **Move RT-VLM to GPU 0** for more KV-cache headroom (`local` mode, `VLM_DEVICE_ID=0`, `RTVI_VLLM_GPU_MEMORY_UTILIZATION=0.7` → ~56 GB for VLM). LLM gets all of GPU 1. Useful when VLM throughput dominates.

### Per-GPU `NIM_KVCACHE_PERCENT` quick-reference (shared mode, alerts)

With RT-VLM at 0.35 and a 15% framework reservation, the LLM gets:

| GPU | VRAM | RT-VLM (0.35) | Framework (0.15) | LLM gets | `NIM_KVCACHE_PERCENT` |
|---|---|---|---|---|---|
| H100 / A100-80 | 80 GB | 28 GB | 12 GB | 40 GB | **0.50** |
| H200 | 141 GB | 49 GB | 21 GB | 71 GB | **0.50** |
| RTX PRO 6000 (Blackwell) | 96 GB | 34 GB | 14 GB | 48 GB | **0.50** |
| L40S | 48 GB | 17 GB | 7 GB | 24 GB | **0.50** (tight — Nano 9B at 23.4 GB barely fits) |

Formula: `NIM_KVCACHE_PERCENT = 1 - 0.35 - 0.15 = 0.50`. Same fraction across GPUs because the script's RT-VLM util is fixed at 0.35 in shared mode.

### Hard rules

- **L40S is the floor for shared mode.** 24 GB for the LLM barely fits Nano 9B FP16 (23.4 GB total). Switch to FP8 (`nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8`, 11.7 GB total) for any breathing room, or move to dedicated mode (LLM on its own GPU, RT-VLM on its own GPU at 0.8 util).
- **DGX-Spark / IGX-Thor / AGX-Thor — Cosmos Reason 2 still serves via RT-VLM** but the agent script uses `VLM_AS_VERIFIER_CONFIG_FILE_PREFIX=EDGE-LOCAL-VLM-` and pins `RT_VLM_DEVICE_ID=0` (unified memory). For the LLM side, follow [`edge.md`](edge.md) (Edge 4B mandatory for shared mode on edge).
- **Don't co-deploy a standalone Cosmos NIM with alerts.** `COMPOSE_PROFILES` for alerts has no `vlm_*_<slug>` segment by design. Verify by checking `resolved.yml` doesn't have `cosmos-reason2-8b` / `cosmos-reason2-8b-shared-gpu` services alongside `rtvi-vlm`.
- **`VLM_NAME` mismatch ⇒ HTTP 400.** dev-profile.sh sets `VLM_NAME=nim_nvidia_cosmos-reason2-8b_hf-1208` for the default Cosmos2 path. If you change `RTVI_VLM_MODEL_PATH` you must update `VLM_NAME` to match the new model basename — otherwise alert-bridge / agent get "No such model" from `/v1/models`.
- **`VLM_NAME_SLUG=none` is required.** The alerts compose graph has no `vlm_local_*_<slug>` profiles. Setting a real slug doesn't bring up a VLM service — it just makes the COMPOSE_PROFILES reference dead.
- **`/v1` suffix mismatch.** `VLM_BASE_URL` no `/v1`; `RTVI_VLM_ENDPOINT` yes `/v1`. dev-profile.sh writes them consistently in remote mode; if you edit by hand, mirror that.

## Use cases

- PPE compliance verification (hard hats, safety vests)
- Restricted area / asset presence detection
- Custom object detection scenarios (driven by behavior analytics rules in 2d_cv mode)
- Continuous live-stream incident detection (in 2d_vlm mode)

## Endpoints (after deploy)

| Service | URL |
|---|---|
| Agent UI | `http://<HOST_IP>:3000/` (Alerts tab) |
| Agent REST API | `http://<HOST_IP>:8000/` |
| Alert-bridge realtime API | `http://<HOST_IP>:9080/api/v1/realtime` |
| RT-VLM | `http://<HOST_IP>:8018/v1/` (or remote if `VLM_MODE=remote`) |
| Video-Analytics MCP | `http://<HOST_IP>:9901/` |
| Kibana | `http://<HOST_IP>:5601/` |
| nvstreamer | `http://<HOST_IP>:31000/` |
| Phoenix | `http://<HOST_IP>:6006/` |

## Env file location

```
deploy/docker/developer-profiles/dev-profile-alerts/.env
```

## First-run note

RT-VLM downloads `cosmos-reason2-8b:hf-1208` from NGC on first start (~10–20 min depending on bandwidth). RT-CV's perception model (`GDINO`) is downloaded by the `perception-2d-init` container into `$DATA/models/gdino`. Real-time mode skips RT-CV entirely.

## Debugging

- **`docker logs vss-rtvi-vlm`** — confirms model load and `Maximum concurrency for X tokens per GPU: Y x` line. OOM → lower `RTVI_VLLM_GPU_MEMORY_UTILIZATION` by 0.05 or drop `RTVI_VLM_MAX_MODEL_LEN` / `RTVI_VLLM_MAX_NUM_SEQS`.
- **`docker logs alert-bridge`** — if it logs HTTP 400 "No such model: …", check `VLM_NAME` matches RT-VLM's `/v1/models` basename. `curl http://${HOST_IP}:8018/v1/models | jq` confirms what's actually advertised.
- **2d_cv: alerts never fire** — check `vss-behavior-analytics` is consuming RT-CV metadata: `docker logs vss-behavior-analytics`. RT-CV side: `curl http://${HOST_IP}:9000/v1/health`.
- **2d_vlm: VLM not running over live streams** — confirm `MODE=2d_vlm` (not `2d_cv`) in `resolved.yml` and that nvstreamer-alerts is publishing streams.
- **OOM on shared GPU 1** — drop `NIM_KVCACHE_PERCENT` for the LLM by 0.05; if RT-VLM is the OOM, raise its `RTVI_VLLM_GPU_MEMORY_UTILIZATION` ceiling and re-tune the LLM down (the 0.35/0.50 split assumes Nano 9B FP16; larger LLMs need different ratios).
- **Edge: `vlm-as-verifier` config not loaded** — verify `VLM_AS_VERIFIER_CONFIG_FILE_PREFIX=EDGE-LOCAL-VLM-` is set in `generated.env` and the matching `EDGE-LOCAL-VLM-config.yml` exists under `dev-profile-alerts/vlm-as-verifier/configs/`.
