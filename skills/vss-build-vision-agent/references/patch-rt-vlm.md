# Patch Reference: RT-VLM (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold RT-VLM into a generated deployment: the `component_services:` block, the Step 6.5 patch specifics (Patch 2 sibling-NIM `depends_on` strip), the invented-flag + patched-copy wiring, and the in-process-backend override the skill applies. It is NOT a microservice contract.

For the underlying RT-VLM API, env vars, ports, Kafka schema, and known constraints, read the skill-neutral pair files in the RT-VLM skill:

- `skills/vss-deploy-dense-captioning/references/integrate-rt-vlm.md` — RT-VLM integration contract: API schema, inputs/outputs, env vars, network, Kafka topics, known constraints.
- `skills/vss-deploy-dense-captioning/references/deploy-rt-vlm-service.md` — RT-VLM deployment contract: image, GPU, storage, startup, verify, tear-down.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn which upstream compose service-key RT-VLM owns. Step 4 unions this block with the other selected microservices' patch files and writes the flat allow-list to `allow-list.yml` under the build directory.
- **Step 6.5** reads ONLY the resulting sidecar (never this file, never the catalog, never the integrate prose) and applies the patches in the "Patch specifics" section below to the rtvi-vlm compose copy under the build directory's patched tree (`patched/services/rtvi/rtvi-vlm/`).

## component_services block

RT-VLM owns a single compose service (`rtvi-vlm`); there are no variants. The sibling NIM backends (`cosmos-reason1-7b`, `cosmos-reason2-8b`, `cosmos3-reasoner`, `qwen3-vl-8b-instruct`, each ± `-shared-gpu`) live in a separate `services/nim/compose.yml` with their own (forthcoming) `integrate-vlm-nim.md`; for the in-process backend (the IN-1 default) those NIM service-keys MUST NOT appear in the allow-list (see Patch 2 below).

```yaml
component_services:
  # RT-VLM itself — required, single variant (the in-process vLLM backend; no sibling NIM).
  - key: rtvi-vlm
    file: services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml
    role: VLM inference service emitting captions on Kafka topic ${KAFKA_TOPIC}.
```

## Patch specifics (Step 6.5)

Applied to the patched copy of `services/rtvi/rtvi-vlm/rtvi-vlm-docker-compose.yml` under `<BUILD_DIR>/patched/`; the upstream tree is never modified.

### Patch 1 — invented flag

The upstream rtvi-vlm compose gates every service behind `profiles:` (6 upstream flags: `bp_wh_2d`, `bp_developer_alerts_2d_vlm`, `bp_developer_alerts_2d_cv`, `bp_developer_base_2d_IGX-THOR`, `bp_developer_base_2d_AGX-THOR`, `bp_developer_lvs_2d`), so `docker compose up` without `--profile` starts nothing. Step 6.5 appends the per-generation invented flag (e.g. `bp_developer_in_1`) to the `rtvi-vlm` service's `profiles:` list in the patched copy (additive — existing upstream flags stay).

### Patch 2 — strip undefined sibling-NIM `depends_on` peers

The live rtvi-vlm compose declares **8** sibling-NIM `depends_on` peers, all `required: false`: `cosmos-reason1-7b`, `cosmos-reason1-7b-shared-gpu`, `cosmos-reason2-8b`, `cosmos-reason2-8b-shared-gpu`, `cosmos3-reasoner`, `cosmos3-reasoner-shared-gpu`, `qwen3-vl-8b-instruct`, `qwen3-vl-8b-instruct-shared-gpu` (the peer set has grown over time — earlier revisions omitted `cosmos3-reasoner` ± `-shared-gpu`). Recent Docker Compose still validates `required: false` refs at project-load time and rejects a standalone project with `invalid compose project`. The **generalized** Patch 2 rule strips whichever `depends_on` peers are **undefined** in the patched include graph — for an in-process IN-1 that is all 8 NIM peers. `broker-health-check` IS defined when ELK is present and is **kept**. Because the rule is "strip whatever is undefined," it is robust to the NIM peer set changing; this file need not be updated when the set grows. (Source: `deploy-rt-vlm-service.md` §20 + §4.)

## In-process-backend override the skill applies

The raw compose default is `VLM_MODEL_TO_USE=openai-compat`, which makes rtvi-vlm a proxy to an external NIM at `${VIA_VLM_ENDPOINT}` — for an in-process IN-1 with no sibling NIM, that silently passes warmup but fails every caption request (only an ES doc-count check catches it). When the user selects the in-process backend, the skill's Step 6 `.env` generation MUST set:

- `RTVI_VLM_MODEL_TO_USE=cosmos-reason2` (in-process vLLM), and
- `RTVI_VLM_ENDPOINT=` (empty),

regardless of what `dev-profile-base/.env` ships. Resolve `RTVI_VLM_IMAGE_TAG` and `RTVI_VLM_MODEL_PATH` from `dev-profile-base/.env` (do not hardcode — the tag stream moves). The caption topic must be `RTVI_VLM_KAFKA_TOPIC=mdx-vlm-captions` (the raw compose default `vision-llm-messages` is unsubscribed by both Logstash pipelines). See `integrate-rt-vlm.md § Environment Variables` for the neutral env contract behind these overrides.

## Emitted shape

The patched `rtvi-vlm` block is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with `docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`. See the `## Example Compose Snippet` in `integrate-rt-vlm.md` for the full upstream block this is patched from.
