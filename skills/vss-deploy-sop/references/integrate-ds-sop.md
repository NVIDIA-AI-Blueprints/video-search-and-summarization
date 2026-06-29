# Integration Reference: DS-SOP

## Overview

DS-SOP is a DeepStream-based Standard-Operating-Procedure monitoring microservice. It ingests a video stream — **canonically a Basler/Pylon industrial camera** at the work-cell (also an RTSP source or a file) — runs a **DDM-Net temporal action-detection model** to segment the stream into action chunks, then runs a **Cosmos-Reason VLM (in-process vLLM)** over each chunk to label it against a configured SOP action set, and a **SOP step-checker** that flags missing / mis-ordered / cycle-complete steps. It publishes per-chunk SOP records (JSON) to Kafka for ELK/Kibana, **and re-emits an annotated RTSP output** (`rtsp://<host>:8554/ds-out/<stream-name>`, when `ENABLE_RTSP_OUTPUT=true`) **that VIOS records for the VST UI**.

Use this service when the workflow requires **SOP compliance monitoring of a procedural task** (e.g. assembly / installation steps) on a live camera or stored video — structured, deterministic "did the operator perform step N, in order" events, as opposed to free-form dense captions (RT-VLM's job). DS-SOP occupies the same perception/inference slot as RT-VLM or RT-CV, but bundles a CV action model + a VLM in one DeepStream container **and (unlike RT-VLM) produces an annotated RTSP output VIOS records**. The image is `ds-sop:1.0.0`, built via the `vss-build-ds-sop` skill from the **internal RTSP-output-capable source** (the public GitHub mirror is Kafka-only and lacks the `:8554` output).

## Required Peer Services

**Prose — peer microservices:**

- **VIOS** (`video-storage`, `rtsp-ingestion`, `sensor-management`) — **required for the live topology**. The canonical SOP flow is **camera → DS-SOP → VIOS**: DS-SOP ingests the camera **directly** (Basler/Pylon, or an RTSP/file source — **not** proxied through VIOS), and **re-streams its annotated result to VIOS**, which records it for the VST UI. Wire it by registering **DS-SOP's output** as a VIOS sensor: `POST /vst/api/v1/sensor/add` with `sensorUrl = rtsp://<host>:${RTSP_PORT:-8554}/ds-out/<stream-name>` (`<stream-name>` = the **source stream's name** — the input video/camera id, NOT a VIOS sensorId). **Recording is automatic** (`recording_status: alwayson`) once the sensor is added — do **not** call `record/start` (it returns 405). (The VST API base is `/vst/api/v1/...`; `/api/v1/...` 404s in vst nginx mode. Optionally the raw source camera is also registered for a side-by-side raw view.) **This DS-SOP→VIOS registration is a deploy-time API step** — build-vision-agent composes the services but never wires video flow (same as RT-VLM); the generated deploy skill performs it (see `deploy-ds-sop.md`). VIOS in the live topology pulls in the full SDRC stack (see VIOS `integrate-vios-service.md` § Known Integration Constraints — `sensor-ms` calls the SDRC Envoy listener on `localhost:10000` for every sensor-add).
- **Kafka** (`kafka-ingestion`) — **required**. DS-SOP publishes SOP chunk records to the topic named by `DEFAULT_TOPIC` (see Environment Variables). Brought in by ELK's `component_services`.
- **ELK** (`caption-storage`, `kafka-ingestion`, `search`, `dashboard`) — **required for storage/search**. Logstash consumes `mdx-vlm-captions` and indexes into Elasticsearch; Kibana visualizes via the SOP dashboard. **Caveat:** build-vision-agent's default ELK decodes this topic as PROTOBUF (RT-VLM), but DS-SOP emits JSON — a dedicated JSON Logstash pipeline must be added (shipped at `references/sop-vlm-captions-json-logstash.conf`). See § Known Integration Constraints → "ELK indexing".

> **`component_services:` block lives in `references/patch-ds-sop.md`** (owned by build-vision-agent), per the decoupling convention — this integrate doc is the **neutral contract only**. DS-SOP owns one upstream compose service-key (`ds-sop`, file `services/rtvi/ds-sop/ds-sop-docker-compose.yml`); VIOS / Kafka / ELK keys come from their own patch refs. See `skills/vss-build-vision-agent/references/patch-ds-sop.md` for the block + Step 6.5 patch specifics + the skill's env overrides.

## Integration Interfaces

**Inputs:**

- **Video source** — DS-SOP ingests the source **directly** (the API accepts a Basler camera, an RTSP URL, or a file). It does **not** consume video via a VIOS proxy — VIOS is downstream (it records DS-SOP's annotated output, see Outputs):
  1. **Basler/Pylon industrial camera (primary / canonical)** — the SOP work-cell setup; DS-SOP reads the camera directly. For testing without hardware, **camera emulation** replays a sample video as a fake Basler camera: `PYLON_CAMEMU=1` + the in-image `configs/Emulation_0815-0000.pfs` + `CAMERA_EMULATION_DIR` (the owner's standard eval path in `vss-sop-skills`).
  2. **RTSP URL** — any `rtsp://...` source as `video_url` on `/v1/chat/completions` (a real IP camera, or a local `rtsp_server.py` relay of a sample video — the owner's standard non-camera live path).
  3. **File / on-demand** — a file path or base64 `video_url` for offline evaluation (deterministic; not realtime-bound).
- **REST** — OpenAI-compatible API server on `:${API_SERVER_PORT:-8300}` (`GET /v1/ready` → `200`; `GET /v1/models` → `ds_sop_model`; `GET /v1/metadata` → version + model info; `POST /v1/chat/completions` with a `video_url`). This is what `VLM_BASE_URL` points at when a VSS Agent is layered on top.
- **Action config** — `${ACTION_CONFIG_PATH}` (JSON, the ordered SOP action set) and `${VLM_PROMPT_PATH}` (VLM prompt template), bind-mounted from the host.

**Outputs:**

- **Kafka** — per-chunk SOP records published to the topic named by **`DEFAULT_TOPIC`** (read by `nvds_action_detector/messager.py`; code default is already `mdx-vlm-captions`). Payload (when `SOP_MESSAGING_SCHEMA=JSON`, the code default) is flat JSON: `{chunk_idx, start_time, end_time, first_timestamp, response, sensor_id, req_id, cv_execute_time, vlm_execute_time, cv_boundary_score, checker_result:{missing_detected, misordered_detected, cycle_completed,...}, ...}`. Kafka key is `request_id` (UUID fallback). **`req_id`** is the unique per-chunk id (e.g. `0001-<uuid>`) — the JSON Logstash pipeline uses it as the ES `document_id` (idempotent upsert; without it all chunks collapse to one ES doc). **`first_timestamp`** is the epoch-seconds base used to build `@timestamp` (`first_timestamp + end_time`). Both fields ARE present in the live payload — the consuming pipeline `references/sop-vlm-captions-json-logstash.conf` relies on them.
- **Annotated RTSP output** — with **`ENABLE_RTSP_OUTPUT=true`** (set it for the canonical flow), DS-SOP re-streams the source with SOP overlays at `rtsp://<host>:${RTSP_PORT:-8554}/ds-out/<stream-name>` (H.264; `SW_ENCODER=true` for the SW fallback). **VIOS records this stream** (registered via `POST /vst/api/v1/sensor/add`) so the VST UI shows the annotated video — this is the **DS-SOP → VIOS** half of the canonical flow. (Implemented in the source's `api_server.py` / `ds_sop_process.py` / `ds_3d_action_pipeline.py` via `RTSPStreamingServer`; present only in the internal source, not the public Kafka-only mirror.)

## Environment Variables

| Variable | Required | Default (code/compose) | Notes |
|---|---|---|---|
| `DS_SOP_IMAGE` | yes | `ds-sop:1.0.0` | Locally built (see `deploy-ds-sop.md` / the `vss-build-ds-sop` skill). |
| `API_SERVER_PORT` | yes | `8300` | REST API server (host network). |
| `DEFAULT_TOPIC` | yes | `mdx-vlm-captions` | Code default is already `mdx-vlm-captions` — keep it; it must match the ELK topic. (No `DS_SOP_KAFKA_TOPIC` exists in the source — `messager.py` reads `DEFAULT_TOPIC`.) |
| `SOP_MESSAGING_SCHEMA` | yes | `JSON` | Code default is already `JSON` (flat-field for the VSS-3.x ELK pipeline + Kibana dashboard). |
| `ENABLE_MESSAGING` | **yes** | `false` | **Must set `1`** to publish to Kafka at all (compose default is `false`). |
| `ENABLE_RTSP_OUTPUT` | **yes (for DS-SOP→VIOS)** | `false` | **Set `true`** so DS-SOP re-streams the annotated output VIOS records. Off → no `:8554` stream (Kafka-only). |
| `RTSP_PORT` | conditional | `8554` | Port of the annotated `/ds-out/<stream-name>` output (used when `ENABLE_RTSP_OUTPUT=true`). |
| `SW_ENCODER` | conditional | `true` | Software H.264 encode fallback for the RTSP output (set `true` if no NVENC available). |
| `KAFKA_BROKER` | yes | `localhost:9092` | Host-network Kafka. |
| `MODEL_ROOT_DIR` | yes | `/opt/models` | Host model root, bind-mounted 1:1. |
| `VLLM_MODEL_PATH` | yes | staged VLM path | Point at where the VLM is staged. `download_assets.sh` verifies `/opt/models/vlm/checkpoint`; NGC `sop-data:1.0` lays it at `/opt/models/cosmos-reason1.1-7b/checkpoint`. (Compose default is HF id `nvidia/cosmos-reason1-7b`.) |
| `DDM_MODEL_PATH` | yes | `/opt/models/gbed_models/ddm/checkpoint.pth.tar` | DDM-Net weights. |
| `VLLM_GPU_MEMORY_UTILIZATION` | conditional | `0.3` | **Set `0.6` on ≤48 GB GPUs** — `0.3` is H100-80GB-tuned and OOMs the KV cache after the ~15.6 GB model load. (On ≥80 GB Blackwell/H100, `0.3` is fine.) |
| `ACTION_CONFIG_PATH` | yes | `/opt/sop/configs/actions.json` | Host SOP action set (staging path; bind-mounted 1:1). |
| `VLM_PROMPT_PATH` | yes | `/opt/sop/configs/vlm_prompts.txt` | Host VLM prompt (staging path; bind-mounted 1:1). |
| `NVIDIA_VISIBLE_DEVICES` | yes | `0` | GPU id. |

## Network Requirements

- `network_mode: host` (reaches Kafka/VIOS on `localhost`; binds API `:8300` and the annotated RTSP out `:8554`).
- `privileged: true`, `ipc: host`, `shm_size: 16gb` (DeepStream + vLLM).
- Ports used on the host: `8300` (REST API), `8554` (annotated RTSP out → VIOS records, when enabled), random UDP (internal udpsink→RTSP loop). DS-SOP **replaces** RT-VLM in the perception slot, so do not co-deploy them in one profile.

## Known Integration Constraints

- **Publish env.** The messager (`nvds_action_detector/messager.py`) reads `DEFAULT_TOPIC` (code default `mdx-vlm-captions`) and `SOP_MESSAGING_SCHEMA` (code default `JSON`) — both already correct for ELK, so keep them. The one required override is **`ENABLE_MESSAGING=1`** (compose default `false`); without it nothing is published and ES stays empty.
- **ELK indexing — the #1 deployment gotcha (schema mismatch).** build-vision-agent composes ELK from `integrate-elk.md`, whose VSS-3.x Logstash pipeline (`mdx-lvs-logstash.conf`) decodes `mdx-vlm-captions` as **NvSchema `nv.VisionLLM` PROTOBUF only** — tuned for RT-VLM. DS-SOP publishes **flat JSON**; that JSON is NOT decodable by the protobuf codec, so Logstash logs `Google::Protobuf::ParseError` and **0 docs reach Elasticsearch**. **Remediation (REQUIRED):** add a dedicated JSON Logstash pipeline that consumes `mdx-vlm-captions` with `codec => json` and indexes to `mdx-vlm-captions-*` — ships at `references/sop-vlm-captions-json-logstash.conf`; register it as its **own pipeline-id** (do NOT merge into `mdx-lvs`). build-vision-agent's compose patching does NOT touch Logstash configs, so this is a deploy-time step — see `deploy-ds-sop.md` § Known Deployment Issues.
- **DS-SOP → VIOS wiring is deploy-time, not composed.** build-vision-agent composes DS-SOP + VIOS into one profile but does **not** wire the video flow (it never does — even RT-VLM's stream registration is a post-boot API call). So registering DS-SOP's `:8554/ds-out` output as a VIOS sensor (`POST /vst/api/v1/sensor/add`) is a **mandatory deploy-time step** the generated deploy skill performs — exactly like the JSON Logstash pipeline. See `deploy-ds-sop.md`.
- **Single perception per profile.** DS-SOP and RT-VLM both target `mdx-vlm-captions` and both want the GPU — select exactly one per profile.
- **Models + configs are host-staged, not in the image** — `/opt/models/...` (DDM + cosmos-reason) and `/opt/sop/configs/...` must exist before bring-up (see `deploy-ds-sop.md`).

## Scope notes

- **Source:** built from the **internal RTSP-output-capable** `sop-inference-bp` (`sop-training-bp`); the public GitHub mirror is **Kafka-only** (no `:8554`) and cannot feed the DS-SOP→VIOS flow. See the `vss-build-ds-sop` skill.
- **Report generation — NOT included (out of scope).** The owner's SOP blueprint adds a **VSS Agent + VA-MCP + LLM NIM (Nemotron)** that generates SOP compliance/incident reports. build-vision-agent's catalog marks **Agent (Ask Video), LLM NIM, and Video Report as PENDING (Phase 1c)** — those `integrate-*.md` reference files are not yet authored, so the orchestrator cannot compose an agent/report-gen layer. This integration delivers SOP **detection → Kafka → ELK/Kibana + annotated stream → VIOS/VST**. The report layer can be added separately (deploy the owner's agent stack pointed at DS-SOP `:8300` + ES) or by authoring the pending catalog entries.

## Example Compose Snippet

The upstream `ds-sop` service block the skill patches (Step 6.5) and `include:`s. Derived from the source's `deploy/compose.yaml` (`nvds-action-sop` service), renamed to the `ds-sop` service-key and parameterized for build-vision-agent. If the compose file is not in the repo, the skill authors `deploy/docker/services/rtvi/ds-sop/ds-sop-docker-compose.yml` from this block:

```yaml
services:
  ds-sop:
    image: ${DS_SOP_IMAGE:-ds-sop:1.0.0}
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
      ENABLE_RTSP_OUTPUT: "${ENABLE_RTSP_OUTPUT:-true}"     # DS-SOP→VIOS: re-stream annotated output
      RTSP_PORT: "${RTSP_PORT:-8554}"
      SW_ENCODER: "${SW_ENCODER:-true}"
      KAFKA_BROKER: "${KAFKA_BROKER:-localhost:9092}"
      MODEL_ROOT_DIR: "${MODEL_ROOT_DIR:-/opt/models}"
      VLLM_MODEL_PATH: "${VLLM_MODEL_PATH:-/opt/models/cosmos-reason1.1-7b/checkpoint}"
      DDM_MODEL_PATH: "${DDM_MODEL_PATH:-/opt/models/gbed_models/ddm/checkpoint.pth.tar}"
      VLLM_GPU_MEMORY_UTILIZATION: "${VLLM_GPU_MEMORY_UTILIZATION:-0.3}"
      ACTION_CONFIG_PATH: "${ACTION_CONFIG_PATH:-/opt/sop/configs/actions.json}"
      VLM_PROMPT_PATH: "${VLM_PROMPT_PATH:-/opt/sop/configs/vlm_prompts.txt}"
```
> Step 6.5 Patch 1 appends the invented flag to `profiles:`; Patch 2 keeps `depends_on: kafka` (defined when ELK is present). The `.env` generation sets `ENABLE_MESSAGING=1`, `ENABLE_RTSP_OUTPUT=true`, and on ≤48 GB GPUs `VLLM_GPU_MEMORY_UTILIZATION=0.6`. **Registering DS-SOP's `:8554/ds-out` output as a VIOS sensor is a deploy-time step** (the compose only exposes it; see `patch-ds-sop.md` + `deploy-ds-sop.md`).
