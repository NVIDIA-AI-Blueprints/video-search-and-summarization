# Patch Reference: DS-SOP (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold **DS-SOP** into a generated deployment: the `component_services:` block, the Step 6.5 patch specifics, the invented-flag wiring, and the env overrides the skill applies. It is **NOT** a microservice contract.

For the underlying DS-SOP API, env vars, ports, Kafka schema, and known constraints, read the skill-neutral pair files in the SOP skill:

- `skills/vss-deploy-sop/references/integrate-ds-sop.md` — DS-SOP integration contract: interfaces, env vars, network, Kafka topic, known constraints.
- `skills/vss-deploy-sop/references/deploy-ds-sop.md` — DS-SOP deployment contract: image, GPU, storage, startup, the mandatory JSON-Logstash step, verify.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn which upstream compose service-key DS-SOP owns. Step 4 unions this block with the other selected microservices' patch files and writes the flat allow-list to `allow-list.yml`.
- **Step 6.5** reads ONLY the resulting sidecar and applies the patches in "Patch specifics" below to the ds-sop compose copy under `<BUILD_DIR>/patched/services/rtvi/ds-sop/`.

## component_services block

DS-SOP owns a single compose service (`ds-sop`); there are no variants (DDM-Net + Cosmos-Reason VLM are in-process; no sibling NIM). It occupies the **same perception slot as RT-VLM** — never select both in one profile (both target Kafka topic `mdx-vlm-captions` and want the GPU).

```yaml
component_services:
  # DS-SOP itself — required, single variant (in-process DDM-Net + vLLM; no sibling NIM).
  - key: ds-sop
    file: services/rtvi/ds-sop/ds-sop-docker-compose.yml
    role: DeepStream SOP service (DDM-Net action detection + Cosmos-Reason VLM + SOP step checker); ingests the camera/source directly, emits SOP chunk JSON on Kafka topic ${DEFAULT_TOPIC} AND an annotated RTSP output on :8554/ds-out that VIOS records (canonical camera→DS-SOP→VIOS).
```

## Patch specifics (Step 6.5)

Applied to the patched copy of `services/rtvi/ds-sop/ds-sop-docker-compose.yml` under `<BUILD_DIR>/patched/`; the upstream tree is never modified.

### Patch 1 — invented flag
The upstream ds-sop compose gates the service behind `profiles:`. Step 6.5 appends the per-generation invented flag (e.g. `bp_developer_in_sop`) to the `ds-sop` service's `profiles:` list (additive — existing upstream flags stay).

### Patch 2 — strip undefined `depends_on` peers
`ds-sop` declares `depends_on: kafka (service_started)`. `kafka` IS defined when ELK is present → **kept**. There are no sibling-NIM peers, so nothing to strip in an SOP profile. (Generalized rule: strip whatever `depends_on` is undefined in the patched include graph — robust as the graph changes.)

## Env overrides the skill applies (Step 6 `.env` generation)

The shipped ds-sop `.env` carries var-name + default footguns; the skill's `.env` generation MUST emit:

- **`DEFAULT_TOPIC=mdx-vlm-captions`** — `nvds_action_detector/messager.py`'s code default is already `mdx-vlm-captions`; keep it (must match the ELK topic). There is no `DS_SOP_KAFKA_TOPIC` in the source.
- **`SOP_MESSAGING_SCHEMA=JSON`** — flat-field JSON for the VSS-3.x ELK pipeline + Kibana dashboard.
- **`ENABLE_MESSAGING=1`** — publish chunk metadata to Kafka.
- **`ENABLE_RTSP_OUTPUT=true`** + **`RTSP_PORT=8554`** + **`SW_ENCODER=true`** — re-stream the annotated SOP output at `rtsp://<host>:8554/ds-out/<stream-name>` for VIOS to record (the DS-SOP→VIOS half of the canonical flow). Default `ENABLE_RTSP_OUTPUT` is `false`; without it there is no annotated stream (Kafka-only).
- **`VLLM_GPU_MEMORY_UTILIZATION=0.6` on ≤48 GB GPUs** — the default `0.3` is H100-tuned (24 GB) and OOMs the vLLM KV cache after the ~15.6 GB model load on an L40S. (On ≥80 GB Blackwell/H100, `0.3` is fine.)
- **Image** `${DS_SOP_IMAGE:-ds-sop:1.0.0}` — built standalone via the `vss-build-ds-sop` skill. Container workdir `/opt/nvidia/nvds_sop` (the compose mounts cache/configs there).

## Deploy-time steps the compose patch cannot cover

Step 6.5 patches **ONLY compose YAML** (`profiles:`, `depends_on:`, volume materialization) — it never wires video flow or Logstash configs. Two SOP wirings are therefore **mandatory deploy-time steps** the generated deploy skill (or operator) must perform — see `deploy-ds-sop.md § Known Deployment Issues`:

1. **ELK JSON pipeline.** DS-SOP emits **flat JSON** on `mdx-vlm-captions`; the default ELK Logstash decodes that topic as **protobuf** (RT-VLM's `nv.VisionLLM`) → `Google::Protobuf::ParseError`, **0 docs in ES**. Register `skills/vss-deploy-sop/references/sop-vlm-captions-json-logstash.conf` as its **own** Logstash `pipeline.id` (NOT merged into `mdx-lvs`) and restart Logstash.
2. **DS-SOP → VIOS sensor.** build-vision-agent composes DS-SOP + VIOS but never wires the video flow (true for RT-VLM too). Register DS-SOP's annotated output `rtsp://<host>:${RTSP_PORT:-8554}/ds-out/<stream-name>` as a VIOS sensor (`POST /vst/api/v1/sensor/add`; recording is **automatic** — `recording_status: alwayson`, no `record/start`) so VST records it. (`<stream-name>` = the source stream's name, not a VIOS sensorId; VST API base is `/vst/api/v1/...`.) DS-SOP reads its camera/source **directly** — VIOS is not in the input path.

## Emitted shape

The patched `ds-sop` block is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with `docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`. See `integrate-ds-sop.md § Example Compose Snippet` for the upstream block this is patched from. The image `ds-sop:1.0.0` is built standalone via the `vss-build-ds-sop` skill (validated with its `API_DUMMY_TEST` smoke test — no models needed).
