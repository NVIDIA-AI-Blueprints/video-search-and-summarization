# Integration Reference: RTVI-SOP

## Overview

RTVI-SOP (DS-SOP) is a DeepStream-based Standard-Operating-Procedure monitoring microservice. It ingests a video stream (RTSP, file, or Basler camera), runs a **DDM-Net temporal action-detection model** to segment the stream into action chunks, then runs a **Cosmos-Reason VLM (in-process vLLM)** over each chunk to label it against a configured SOP action set, and a **SOP step-checker** that flags missing / mis-ordered / cycle-complete steps. It publishes per-chunk SOP records (JSON) to Kafka — exactly like RT-VLM publishes captions — for ELK/Kibana. (The public `sop-inference-bp` build is **Kafka-output-only**; it does not re-stream an annotated RTSP output, matching how the perception slot connects to VIOS — see below.)

Use this service when the workflow requires **SOP compliance monitoring of a procedural task** (e.g. assembly / installation steps) on a live RTSP stream or stored video — i.e. structured, deterministic "did the operator perform step N, in order" events, as opposed to free-form dense captions (which is RT-VLM's job). RTVI-SOP occupies the same perception/inference slot as RT-VLM or RT-CV, but bundles both a CV action model and a VLM inside one DeepStream container. The docker image is `nvds-sop:1.0.0` (built via the `vss-build-ds-sop` skill).

## Required Peer Services

**Prose — peer microservices:**

- **VIOS** (`video-storage`, `rtsp-ingestion`, `sensor-management`) — **required for the live-RTSP topology**. RTVI-SOP does not own a camera manager — it connects to VIOS **input-only, exactly like RT-VLM** (VIOS is the producer/proxy; the perception service is the reader; cf. `integrate-rt-vlm.md` § Required Peer Services → Video source). The operator registers the **source camera** with VIOS (`POST /api/v1/sensor/add`, `POST /api/v1/record/<id>/start`); RTVI-SOP then **consumes** that stream via its own API — either the **VIOS-proxy RTSP URL** passed as the `video_url` to `/v1/chat/completions`, or a shared file mount. **The live-pool port is assigned dynamically** — read the actual URL from VIOS `GET :30888/api/v1/live/streams` (e.g. `rtsp://<host>:30561/live/<sensorId>`); the documented `:30554` base commonly 404s, so do **not** hardcode it (verified live 2026-06-26). RTVI-SOP does **not** emit its own annotated RTSP stream back to VST — SOP results go to Kafka → ELK → **Kibana** (the SOP dashboard); VST shows the source camera. VIOS in the live-RTSP topology pulls in the full SDRC stack (see VIOS `integrate-vios-service.md` § Known Integration Constraints — `sensor-ms` calls the SDRC Envoy listener on `localhost:10000` for every sensor-add).
- **Kafka** (`kafka-ingestion`) — **required**. RTVI-SOP publishes SOP chunk records to the topic named by `DEFAULT_TOPIC` (see Environment Variables). Brought in by ELK's `component_services`.
- **ELK** (`caption-storage`, `kafka-ingestion`, `search`, `dashboard`) — **required for storage/search**. Logstash consumes `mdx-vlm-captions` and indexes into Elasticsearch; Kibana visualizes via the SOP dashboard. **Caveat:** build-vision-agent's default ELK decodes this topic as PROTOBUF (RT-VLM), but RTVI-SOP emits JSON — a dedicated JSON Logstash pipeline must be added (shipped at `references/sop-vlm-captions-json-logstash.conf`). See § Known Integration Constraints → "ELK indexing".

> **`component_services:` block lives in `references/patch-rtvi-sop.md`** (owned by build-vision-agent), per the decoupling convention (2026-06-08) — this integrate doc is the **neutral contract only**. RTVI-SOP owns one upstream compose service-key (`rtvi-sop`, file `services/rtvi/rtvi-sop/rtvi-sop-docker-compose.yml`); VIOS / Kafka / ELK keys come from their own patch refs. See `skills/vss-build-vision-agent/references/patch-rtvi-sop.md` for the block + Step 6.5 patch specifics + the skill's env overrides.

## Integration Interfaces

**Inputs:**

- **Video source** — RTSP URL or file (passed to the inference pipeline via the `/v1/chat/completions` request with a `video_url` of `rtsp://...` or a file path, or via the API server's RTSP/Basler input path). In a VIOS deployment the stable input is the **source camera RTSP**, consumed via the **VIOS-proxy RTSP URL** — the live-pool port is **pool-assigned** (discover it from `GET :30888/api/v1/live/streams`, e.g. `:30561`; the fixed `:30554` often 404s). RTVI-SOP runs `network_mode: host`, so `localhost` reaches VIOS — unlike a bridge-network consumer, which would need `${HOST_IP}`.
- **REST** — OpenAI-compatible API server on `:${API_SERVER_PORT:-8300}` (`GET /v1/ready` → `200` readiness; `GET /v1/models` → `ds_sop_model`; `GET /v1/metadata` → version + model info; `POST /v1/chat/completions` with a `video_url`). This is what `VLM_BASE_URL` points at when a VSS Agent is layered on top.
- **Action config** — `${ACTION_CONFIG_PATH}` (JSON, the ordered SOP action set) and `${VLM_PROMPT_PATH}` (VLM prompt template), bind-mounted from the host.

**Outputs:**

- **Kafka** — per-chunk SOP records published to the topic named by **`DEFAULT_TOPIC`** (read by `nvds_action_detector/messager.py`; code default is already `mdx-vlm-captions`). Payload (when `SOP_MESSAGING_SCHEMA=JSON`, the code default) is flat JSON: `{chunk_idx, start_time, end_time, first_timestamp, response, sensor_id, req_id, cv_execute_time, vlm_execute_time, cv_boundary_score, checker_result:{missing_detected, misordered_detected, cycle_completed,...}, ...}`. Kafka key is `request_id` (UUID fallback). **`req_id`** is the unique per-chunk id (e.g. `0001-<uuid>`) — the JSON Logstash pipeline uses it as the ES `document_id` (idempotent upsert; without it all chunks collapse to one ES doc). **`first_timestamp`** is the epoch-seconds base used to build `@timestamp` (`first_timestamp + end_time`). Both fields ARE present in the live payload (verified) — the consuming pipeline `references/sop-vlm-captions-json-logstash.conf` relies on them.
- *(No annotated RTSP output.)* The public `sop-inference-bp` build does not re-stream an overlaid video — the perception slot is Kafka-output-only (like RT-VLM). Visualization is via Kibana on the indexed SOP records, and via VST on the **source** camera.

## Environment Variables

| Variable | Required | Default (code/compose) | Notes |
|---|---|---|---|
| `RTVI_SOP_IMAGE` | yes | `nvds-sop:1.0.0` | Locally built (see `deploy-rtvi-sop.md` / the `vss-build-ds-sop` skill). |
| `API_SERVER_PORT` | yes | `8300` | REST API server (host network). |
| `DEFAULT_TOPIC` | yes | `mdx-vlm-captions` | Code default is already `mdx-vlm-captions` — keep it; it must match the ELK topic. (No `RTVI_SOP_KAFKA_TOPIC` exists in the source — `messager.py` reads `DEFAULT_TOPIC`.) |
| `SOP_MESSAGING_SCHEMA` | yes | `JSON` | Code default is already `JSON` (flat-field for the VSS-3.x ELK pipeline + Kibana dashboard). |
| `ENABLE_MESSAGING` | **yes** | `false` | **Must set `1`** to publish to Kafka at all (compose default is `false`). |
| `KAFKA_BROKER` | yes | `localhost:9092` | Host-network Kafka. |
| `MODEL_ROOT_DIR` | yes | `/opt/models` | Host model root, bind-mounted 1:1. |
| `VLLM_MODEL_PATH` | yes | staged VLM path | Point at where the VLM is staged. `download_assets.sh` verifies `/opt/models/vlm/checkpoint`; NGC `sop-data:1.0` lays it at `/opt/models/cosmos-reason1.1-7b/checkpoint`. (Compose default is HF id `nvidia/cosmos-reason1-7b`.) |
| `DDM_MODEL_PATH` | yes | `/opt/models/gbed_models/ddm/checkpoint.pth.tar` | DDM-Net weights. |
| `VLLM_GPU_MEMORY_UTILIZATION` | conditional | `0.3` | **Set `0.6` on ≤48 GB GPUs (L40S)** — `0.3` is H100-80GB-tuned and OOMs the KV cache after the ~15.6 GB model load. (On ≥80 GB Blackwell/H100, `0.3` is fine.) |
| `ACTION_CONFIG_PATH` | yes | `/opt/sop/configs/actions.json` | Host SOP action set (staging path; bind-mounted 1:1). |
| `VLM_PROMPT_PATH` | yes | `/opt/sop/configs/vlm_prompts.txt` | Host VLM prompt (staging path; bind-mounted 1:1). |
| `NVIDIA_VISIBLE_DEVICES` | yes | `0` | GPU id. |

## Network Requirements

- `network_mode: host` (reaches Kafka/VIOS on `localhost`; binds API `:8300`).
- `privileged: true`, `ipc: host`, `shm_size: 16gb` (DeepStream + vLLM).
- Ports used on the host: `8300` (REST API). RTVI-SOP **replaces** RT-VLM in the perception slot, so do not co-deploy them in one profile.

## Known Integration Constraints

- **Publish env.** The messager (`nvds_action_detector/messager.py`) reads `DEFAULT_TOPIC` (code default `mdx-vlm-captions`) and `SOP_MESSAGING_SCHEMA` (code default `JSON`) — both already correct for ELK, so keep them. The one required override is **`ENABLE_MESSAGING=1`** (compose default `false`); without it nothing is published and ES stays empty.
- **ELK indexing — the #1 deployment gotcha (schema mismatch).** build-vision-agent composes ELK from `integrate-elk.md`, whose VSS-3.x Logstash pipeline (`mdx-lvs-logstash.conf`) decodes `mdx-vlm-captions` as **NvSchema `nv.VisionLLM` PROTOBUF only** — tuned for RT-VLM. RTVI-SOP publishes **flat JSON**; that JSON is NOT decodable by the protobuf codec, so Logstash logs `Google::Protobuf::ParseError` and **0 docs reach Elasticsearch** (verified). Flipping `SOP_MESSAGING_SCHEMA=NvProtoSchema` does NOT help (different wire shape from `nv.VisionLLM`). **Remediation (REQUIRED):** add a dedicated JSON Logstash pipeline that consumes `mdx-vlm-captions` with `codec => json` and indexes to `mdx-vlm-captions-*` — ships at `references/sop-vlm-captions-json-logstash.conf`; register it as its **own pipeline-id** (do NOT merge into the protobuf `mdx-lvs` pipeline). build-vision-agent's compose patching does NOT touch Logstash configs, so this is a deploy-time step the generated deploy skill (or operator) performs — see `deploy-rtvi-sop.md` § Known Deployment Issues.
- **Single perception per profile.** RTVI-SOP and RT-VLM both target `mdx-vlm-captions` and both want the GPU — select exactly one per profile.
- **VIOS live-RTSP topology required** when ingesting cameras — pulls the full SDRC stack (`sensor-ms` → SDRC Envoy `:10000`).
- **Models + configs are host-staged, not in the image** — `/opt/models/...` (DDM + cosmos-reason) and `/opt/sop/configs/...` must exist before bring-up (see `deploy-rtvi-sop.md`).

## Example Compose Snippet

The upstream `rtvi-sop` service block the skill patches (Step 6.5) and `include:`s. Derived from the source's `deploy/compose.yaml` (`nvds-action-sop` service), renamed to the `rtvi-sop` service-key and parameterized for build-vision-agent. If the compose file is not in the repo, the skill authors `deploy/docker/services/rtvi/rtvi-sop/rtvi-sop-docker-compose.yml` from this block:

```yaml
services:
  rtvi-sop:
    image: ${RTVI_SOP_IMAGE:-nvds-sop:1.0.0}
    runtime: nvidia
    network_mode: host
    privileged: true
    ipc: host
    shm_size: '16gb'
    ulimits:
      memlock: -1
      stack: 67108864
    profiles:
      - bp_developer_sop             # stable upstream gate (keeps the service off by default); Step 6.5 Patch 1 APPENDS the per-generation flag (e.g. bp_developer_in_sop) to the patched copy
    devices:
      - "/dev/snd:/dev/snd"
    depends_on:
      kafka:
        condition: service_started
    working_dir: ${WORK_DIR_PATH:-/opt/nvidia/nvds_sop}
    entrypoint: ${ENTRYPOINT:-./start_server.sh}
    volumes:
      - "${MODEL_ROOT_DIR:-/opt/models}:${MODEL_ROOT_DIR:-/opt/models}"
      - "${HOST_CACHE:-$HOME/.cache/ds_sop}:/opt/nvidia/nvds_sop/.cache"
      - "${ACTION_CONFIG_PATH:-/opt/sop/configs/actions.json}:${ACTION_CONFIG_PATH:-/opt/sop/configs/actions.json}"
      - "${VLM_PROMPT_PATH:-/opt/sop/configs/vlm_prompts.txt}:${VLM_PROMPT_PATH:-/opt/sop/configs/vlm_prompts.txt}"
    environment:
      NVIDIA_VISIBLE_DEVICES: "${NVIDIA_VISIBLE_DEVICES:-0}"
      API_SERVER_PORT: ${API_SERVER_PORT:-8300}
      DEFAULT_TOPIC: ${DEFAULT_TOPIC:-mdx-vlm-captions}
      SOP_MESSAGING_SCHEMA: ${SOP_MESSAGING_SCHEMA:-JSON}
      ENABLE_MESSAGING: "${ENABLE_MESSAGING:-1}"
      KAFKA_BROKER: "${KAFKA_BROKER:-localhost:9092}"
      MODEL_ROOT_DIR: "${MODEL_ROOT_DIR:-/opt/models}"
      VLLM_MODEL_PATH: "${VLLM_MODEL_PATH:-/opt/models/cosmos-reason1.1-7b/checkpoint}"
      DDM_MODEL_PATH: "${DDM_MODEL_PATH:-/opt/models/gbed_models/ddm/checkpoint.pth.tar}"
      VLLM_GPU_MEMORY_UTILIZATION: "${VLLM_GPU_MEMORY_UTILIZATION:-0.3}"
      ACTION_CONFIG_PATH: "${ACTION_CONFIG_PATH:-/opt/sop/configs/actions.json}"
      VLM_PROMPT_PATH: "${VLM_PROMPT_PATH:-/opt/sop/configs/vlm_prompts.txt}"
```
> Step 6.5 Patch 1 appends the invented flag to `profiles:`; Patch 2 keeps `depends_on: kafka` (defined when ELK is present). The `.env` generation sets `ENABLE_MESSAGING=1` and, on ≤48 GB GPUs, `VLLM_GPU_MEMORY_UTILIZATION=0.6`. See `patch-rtvi-sop.md`.
