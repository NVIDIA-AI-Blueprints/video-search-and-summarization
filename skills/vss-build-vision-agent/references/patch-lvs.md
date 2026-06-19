# Patch Reference: VS (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to
fold Video Summarization (VS) into a generated deployment: the `component_services:` block,
the Step 6.5 patch specifics (Patch 1 flag insertion, Patch 2 `depends_on` strip), the invented-flag
+ patched-copy wiring, and the `.env` overrides the skill applies. It is NOT a microservice contract.

For the underlying VS API, env vars, ports, Kafka schema, and known constraints, read the
skill-neutral pair files in the VS skill:

- `skills/vss-summarize-video/references/integrate-lvs.md` — VS integration contract: API schema,
  inputs/outputs, env vars, network, Kafka topics, known constraints.
- `skills/vss-summarize-video/references/deploy-lvs-service.md` — VS deployment contract: image,
  GPU (CPU-only), storage, startup, verify, tear-down.
- `skills/vss-summarize-video/references/video-summarization-api.md` and
  `…-environment-variables.md` — OpenAPI-derived API + env reference.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the
per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is
`references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn
  which upstream compose service-key VS owns. Step 4 unions this block with the other selected
  microservices' patch files and writes the flat allow-list to `allow-list.yml` under the build
  directory.
- **Step 6.5** reads ONLY the resulting sidecar (never this file, never the catalog, never the
  integrate prose) and applies the patches in the "Patch specifics" section below to the
  `lvs-server` compose copy under the build directory's patched tree
  (`patched/services/video-summarization/`).

## component_services block

VS owns a single compose service (`lvs-server`); there are no variants. The summarization LLM and
embedding endpoints are NOT compose services VS owns — they are either a separate LLM NIM
(`services/nim/...`, with its own forthcoming patch file) or a remote NVIDIA-hosted endpoint
configured purely via `.env`. They MUST NOT appear in the allow-list as VS-owned keys.

```yaml
component_services:
  # VS itself — required, single variant. CPU-only; depends on RT-VLM (VLM) +
  # a summarization LLM endpoint (local NIM or remote) + the shared ELK stack.
  - key: lvs-server
    file: services/video-summarization/compose.yml
    role: Video Summarization — aggregates dense captions into a structured summary; reads raw events from ES, publishes structured summaries on Kafka topic ${KAFKA_STRUCTURED_SUMMARY_TOPIC} and into ES collection lvs-events.
```

## Patch specifics (Step 6.5)

Applied to the patched copy of `services/video-summarization/compose.yml` under `<BUILD_DIR>/patched/`;
the upstream tree is never modified.

### Patch 1 — invented flag

The upstream `lvs-server` compose gates the service behind `profiles: ["bp_developer_lvs_2d"]`, so
`docker compose up` without `--profile` starts nothing. Step 6.5 appends the per-generation invented
flag (e.g. `bp_developer_in_1_lvs`) to the `lvs-server` service's `profiles:` list in the patched
copy (additive — the existing upstream `bp_developer_lvs_2d` flag stays).

### Patch 2 — strip undefined `depends_on` peers

The live `lvs-server` compose declares a large `depends_on:` block, all `required: false`:

- **18 sibling LLM/VLM NIM keys** (the candidate summarization-LLM and VLM NIM backends):
  `nvidia-nemotron-nano-9b-v2`, `nvidia-nemotron-nano-9b-v2-fp8`, `nemotron-3-nano`,
  `llama-3.3-nemotron-super-49b-v1.5`, `gpt-oss-20b`, each ± `-shared-gpu`; `cosmos-reason1-7b`,
  `cosmos-reason2-8b`, `cosmos3-reasoner`, `qwen3-vl-8b-instruct`, each ± `-shared-gpu`.
- **`rtvi-vlm`** — the VLM passthrough peer.

The **generalized** Patch 2 rule (identical to RT-VLM's) strips whichever `depends_on` peers are
**undefined** in the patched include graph and marked `required: false`. For an IN-1+VS build where
the summarization LLM is **remote** and no LLM NIM compose is included, ALL of the 18 NIM peers are
undefined and stripped. `rtvi-vlm` IS defined (it is in the RT-VLM allow-list / patched tree when VS
layers on a dense-captioning baseline) and is **kept**. If a local LLM NIM compose is included, the
selected NIM service-key becomes defined and is likewise kept.

Because the rule is "strip whatever is undefined," it is robust to the NIM peer set changing; this
file need not be updated when the set grows. (Same mechanism as `patch-rt-vlm.md` Patch 2.)

### Patch 4 — nested include strip (N/A)

The `lvs-server` compose (`services/video-summarization/compose.yml`) has no top-level `include:`
block, so Patch 4 (nested-include neutralization) is a no-op for this file.

## `.env` overrides the skill applies

VS's CA-RAG `summarization_llm` tool needs a reachable OpenAI-compatible LLM endpoint, and the
streaming summary path needs Kafka under host networking. The skill's Step 6 `.env` generation MUST
set the following when VS is in the build:

```
# Enable the structured-summary path (compose default is false)
KAFKA_ENABLED=true
# Host networking: the compose default kafka:9092 is unreachable; use the host port.
KAFKA_BOOTSTRAP_SERVERS=${HOST_IP}:9092
KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary
LVS_ENABLE_LLM_MERGING=true
# Default CA-RAG backend — no graph DB, no embedding endpoint needed.
LVS_DATABASE_BACKEND=elasticsearch_db
LVS_EMB_ENABLE=false
# REST API + opt-out MCP (avoids :38112 host-port collision under host networking).
BACKEND_PORT=38111
LVS_ENABLE_MCP=false
# VLM passthrough to the existing RT-VLM endpoint.
RTVI_VLM_URL=http://${HOST_IP}:${RTVI_VLM_PORT}
# Elasticsearch (shared with ELK).
ES_HOST=${HOST_IP}
ES_PORT=9200
```

**Summarization LLM — the key architectural decision (surface in Step 4).** VS requires an LLM
endpoint that the dense-captioning baseline does NOT provide. The upstream `dev-profile-lvs` default
is a **local LLM NIM** (`local_shared` mode, `nvidia-nemotron-nano-9b-v2`). At Step 4 the skill
MUST check how many GPUs the host has (`nvidia-smi --query-gpu=name --format=csv,noheader | wc -l`)
and how many the current baseline profile already occupies (e.g. IN-1 uses 1 GPU for RT-VLM / NIM).
If `(host_gpu_count - profile_gpu_count) >= 1`, default to local NIM on the spare GPU. Only fall
back to the remote NVIDIA-hosted endpoint when no spare GPU is available OR the user explicitly opts
out of local NIM deployment.

- **Local LLM NIM (upstream default — use when a spare GPU is available):**
  Include the LLM NIM compose (`services/nim/...`), set `LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2`,
  `LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2`, `LLM_MODE=local_shared`, leave `LLM_BASE_URL=''` so
  `LVS_LLM_BASE_URL` resolves to `http://${HOST_IP}:${LLM_PORT}/v1`. Add
  `nvidia-nemotron-nano-9b-v2` (or the `-shared-gpu` variant) to the allow-list so Patch 2 keeps it
  in `depends_on`. Requires `NGC_CLI_API_KEY` for image pull + a free GPU.
  ```
  LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2
  LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2
  LLM_MODE=local_shared
  LLM_BASE_URL=          # leave empty — resolves to http://${HOST_IP}:${LLM_PORT}/v1
  LLM_PORT=8000          # or whichever port the NIM compose exposes
  ```

- **Remote NVIDIA-hosted endpoint (fallback — use when no spare GPU or user opts out):**
  ```
  LLM_BASE_URL=https://integrate.api.nvidia.com/v1
  LLM_PORT=         # unused when LLM_BASE_URL is set
  LVS_LLM_MODEL_NAME=nvidia/llama-3.3-nemotron-super-49b-v1.5
  NVIDIA_API_KEY=<cloud-inference-entitled-key>    # NOT the NGC registry key (nvapi-*)
  ```
  **Important:** `integrate.api.nvidia.com` requires a **cloud-inference-entitled** key — this is
  a different entitlement scope from the NGC registry key (`nvapi-*`) used for image pulls. An NGC
  registry key returns HTTP 403 on `/v1/chat/completions`. Surface this distinction explicitly in
  Step 4 if the user chooses remote.

Do not hardcode the VS image tag — resolve `LVS_TAG` (and thus `CONTAINER_IMAGE`) from
`dev-profile-lvs/.env` / `dev-profile-base/.env`. See `integrate-lvs.md § Environment Variables` for
the neutral env contract behind these overrides.

## No new Kafka/Logstash wiring required

The structured-summary topic `mdx-structured-events-summary` is already created by ELK's
`kafka-topic-init-container` (`services/infra/compose.yml`), and the Logstash `mdx-lvs` pipeline
(`elk/logstash/pipelines/kafka/mdx-lvs-logstash.conf`) already subscribes to it (alongside
`mdx-vlm-captions`) and indexes the docs by `collection_name`. So when VS layers on a baseline that
already has ELK + RT-VLM (e.g. IN-1), no patch to ELK, Kafka, or Logstash is needed — VS is a pure
additive producer / ES reader.

## Emitted shape

The patched `lvs-server` block is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with
`docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`.
See the `## Example Compose Snippet` in `integrate-lvs.md` for the full upstream block this is
patched from.


## Validated: local LLM NIM wiring (IN-1-1, 2026-06-18)

IN-1-1 (IN-1 dense captioning + VS) was validated end-to-end on 2xRTXPro-ubuntu with the
**local LLM NIM** summarization path (the upstream-default `local_shared` option), after the prior
cloud-endpoint run hit HTTP 403 on `integrate.api.nvidia.com` (NGC registry key is not
cloud-inference-entitled). Wiring that worked:

- **NIM compose:** `services/nim/nvidia-nemotron-nano-9b-v2/compose.yml`. It defines TWO service
  keys: `nvidia-nemotron-nano-9b-v2` (non-shared, no `depends_on`) and
  `nvidia-nemotron-nano-9b-v2-shared-gpu` (has a `depends_on` block of cosmos/rtvi peers). For a
  **dedicated spare GPU** (IN-1: RT-VLM on GPU0, LLM on GPU1), prefer the **non-shared key** — it has
  no `depends_on`, so Patch 2 is trivial (nothing to strip). Use `-shared-gpu` only when the LLM must
  co-locate on the VLM's GPU.
- **Patch 1 must flag BOTH services.** The NIM service is gated behind its own model profile
  (`profiles: [llm_local_nvidia-nemotron-nano-9b-v2]`), NOT the blueprint flag. Step 6.5 Patch 1 must
  append the invented flag (e.g. `bp_developer_in_1_lvs`) to the NIM service's `profiles:` list too,
  exactly as it does for `lvs-server` — otherwise `--profile <flag>` will not start the NIM.
- **Patch 2 keeps the selected NIM key** in `lvs-server` `depends_on` (it is now defined) and strips
  the other 17 undefined sibling LLM/VLM NIM peers. Confirmed: build resolved with
  `nvidia-nemotron-nano-9b-v2` retained + `rtvi-vlm` retained.
- **`.env` overrides (local-NIM branch), all confirmed working:**
  ```
  LLM_BASE_URL=                 # EMPTY (set, not unset) -> LVS_LLM_BASE_URL resolves to http://${HOST_IP}:${LLM_PORT}/v1
  LLM_PORT=30081
  LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2     # -> LVS_LLM_MODEL_NAME via services/video-summarization/.env (do NOT override LVS_LLM_MODEL_NAME)
  LLM_NAME_SLUG=nvidia-nemotron-nano-9b-v2
  LLM_MODE=local_shared
  LLM_DEVICE_ID=1               # the spare GPU; RT-VLM stays on RT_VLM_DEVICE_ID=0
  HARDWARE_PROFILE=RTXPRO6000BW # REQUIRED — NIM compose env_file is hw-${HARDWARE_PROFILE}.env (non-shared key) / hw-${HARDWARE_PROFILE}-shared.env (shared key). dev-profile defaults to H100; the skill MUST set this to match the host or the NIM include errors on a missing env file.
  NVIDIA_API_KEY=${NGC_CLI_API_KEY}   # local NIM ignores the value, but LVS_LLM_API_KEY=${OPENAI_API_KEY:-${NVIDIA_API_KEY}} must be non-empty for the OpenAI client
  ```
  Remove the remote-branch overrides (`LLM_BASE_URL=https://integrate.api.nvidia.com`,
  `LVS_LLM_MODEL_NAME=…llama…`, the cloud `NVIDIA_API_KEY`) when switching to local.
- **GPU-slot check (Step 4) confirmed correct:** host had 2 GPUs, IN-1 baseline occupied 1 (RT-VLM
  GPU0), so `2-1 >= 1` -> default to local NIM on GPU1. nvidia-smi after deploy showed RT-VLM
  VLLM::EngineCore on GPU0 (uuid c1028a9b) and `/opt/nim/llm/.venv/bin/python3` on GPU1 (uuid 0fc77e47).
- **Result:** `POST /v1/summarize` returned `choices[0].message.content` with a non-empty
  `video_summary` + `events` from the local NIM; the structured summary was published to
  `mdx-structured-events-summary` AND indexed into ES under `default_<file_id>` (see
  `integrate-lvs.md § Outputs` — NOT `lvs-events`).
