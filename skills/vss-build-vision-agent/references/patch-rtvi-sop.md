# Patch Reference: RTVI-SOP (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold **RTVI-SOP (DS-SOP)** into a generated deployment: the `component_services:` block, the Step 6.5 patch specifics, the invented-flag wiring, and the env overrides the skill applies. It is **NOT** a microservice contract.

For the underlying RTVI-SOP API, env vars, ports, Kafka schema, and known constraints, read the skill-neutral pair files in the SOP skill:

- `skills/vss-deploy-sop/references/integrate-rtvi-sop.md` — RTVI-SOP integration contract: interfaces, env vars, network, Kafka topic, known constraints.
- `skills/vss-deploy-sop/references/deploy-rtvi-sop.md` — RTVI-SOP deployment contract: image, GPU, storage, startup, the mandatory JSON-Logstash step, verify.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn which upstream compose service-key RTVI-SOP owns. Step 4 unions this block with the other selected microservices' patch files and writes the flat allow-list to `allow-list.yml`.
- **Step 6.5** reads ONLY the resulting sidecar and applies the patches in "Patch specifics" below to the rtvi-sop compose copy under `<BUILD_DIR>/patched/services/rtvi/rtvi-sop/`.

## component_services block

RTVI-SOP owns a single compose service (`rtvi-sop`); there are no variants (DDM-Net + Cosmos-Reason VLM are in-process; no sibling NIM). It occupies the **same perception slot as RT-VLM** — never select both in one profile (both target Kafka topic `mdx-vlm-captions` and want the GPU).

```yaml
component_services:
  # RTVI-SOP itself — required, single variant (in-process DDM-Net + vLLM; no sibling NIM).
  - key: rtvi-sop
    file: services/rtvi/rtvi-sop/rtvi-sop-docker-compose.yml
    role: DeepStream SOP service (DDM-Net action detection + Cosmos-Reason VLM + SOP step checker); emits SOP chunk JSON on Kafka topic ${DEFAULT_TOPIC} (Kafka-output-only, like RT-VLM — the public build has no annotated RTSP output; VIOS supplies the source-camera input).
```

## Patch specifics (Step 6.5)

Applied to the patched copy of `services/rtvi/rtvi-sop/rtvi-sop-docker-compose.yml` under `<BUILD_DIR>/patched/`; the upstream tree is never modified.

### Patch 1 — invented flag
The upstream rtvi-sop compose gates the service behind `profiles:`. Step 6.5 appends the per-generation invented flag (e.g. `bp_developer_in_sop`) to the `rtvi-sop` service's `profiles:` list (additive — existing upstream flags stay).

### Patch 2 — strip undefined `depends_on` peers
`rtvi-sop` declares `depends_on: kafka (service_started)`. `kafka` IS defined when ELK is present → **kept**. There are no sibling-NIM peers, so nothing to strip in an SOP profile. (Generalized rule: strip whatever `depends_on` is undefined in the patched include graph — robust as the graph changes.)

## Env overrides the skill applies (Step 6 `.env` generation)

The shipped rtvi-sop `.env` carries var-name + default footguns; the skill's `.env` generation MUST emit:

- **`DEFAULT_TOPIC=mdx-vlm-captions`** — `nvds_action_detector/messager.py`'s code default is already `mdx-vlm-captions`; keep it (must match the ELK topic). There is no `RTVI_SOP_KAFKA_TOPIC` in the source.
- **`SOP_MESSAGING_SCHEMA=JSON`** — flat-field JSON for the VSS-3.x ELK pipeline + Kibana dashboard.
- **`ENABLE_MESSAGING=1`** — publish chunk metadata to Kafka.
- **`VLLM_GPU_MEMORY_UTILIZATION=0.6` on ≤48 GB GPUs** — the default `0.3` is H100-tuned (24 GB) and OOMs the vLLM KV cache after the ~15.6 GB model load on an L40S. (On ≥80 GB Blackwell/H100, `0.3` is fine.)
- **Image** `${RTVI_SOP_IMAGE:-nvds-sop:1.0.0}` — built standalone via the `vss-build-ds-sop` skill. Container workdir `/opt/nvidia/nvds_sop` (the compose mounts cache/configs there).

## ELK JSON pipeline — NOT a compose patch (mandatory deploy-time step)

RTVI-SOP emits **flat JSON** on `mdx-vlm-captions`; build-vision-agent's default ELK Logstash decodes that topic as **protobuf** (RT-VLM's `nv.VisionLLM`) → `Google::Protobuf::ParseError`, **0 docs in ES**. Step 6.5 patches **ONLY compose YAML** — it cannot edit Logstash pipeline configs. So the generated deploy skill MUST register `skills/vss-deploy-sop/references/sop-vlm-captions-json-logstash.conf` as its **own** Logstash `pipeline.id` (NOT merged into `mdx-lvs`) and restart Logstash — see `deploy-rtvi-sop.md § Known Deployment Issues`. This is the one mandatory deploy-time step the compose patch cannot cover.

## Emitted shape

The patched `rtvi-sop` block is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with `docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`. See `integrate-rtvi-sop.md § Example Compose Snippet` for the upstream block this is patched from. The image `nvds-sop:1.0.0` is built standalone via the `vss-build-ds-sop` skill (validated with its `API_DUMMY_TEST` smoke test — no models needed).
