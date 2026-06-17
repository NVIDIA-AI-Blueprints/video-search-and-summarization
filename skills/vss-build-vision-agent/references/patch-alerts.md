# Patch Reference: Alert Microservice (build-vision-agent)

This file is owned by `vss-build-vision-agent`. It holds the machinery the orchestrator needs to fold the **Alert Microservice** (the `alert-bridge` service; formerly "Alert Verification" / "Alert Bridge") into a generated deployment: the `component_services:` block, the `alert_source` variant, the Step 6.5 patch specifics, and the in-process-verification env overrides. It is NOT a microservice contract. (Deploy identifiers unchanged: image `vss-alert-verification`, container `vss-alert-bridge`, service-key `alert-bridge`.)

For the underlying Alert Microservice API, env vars, ports, clip-retrieval contract, sinks, and known constraints, read the skill-neutral pair files in the alerts skill:

- `skills/vss-manage-alerts/references/integrate-alerts.md` — Alert Microservice integration contract: realtime REST + Kafka/Redis event-bridge, clip retrieval from VIOS, ES/Kafka sinks, verdict semantics, known constraints.
- `skills/vss-manage-alerts/references/deploy-alerts.md` — Alert Microservice deployment contract: image, (no) GPU, config mounts, startup, verify, tear-down.

Schema for the `component_services:` block is in `references/component-services-schema.md`; the per-generation sidecar is `references/allow-list-sidecar.md`; the patch pseudocode is `references/standalone-compose-patches.md`.

## How the skill uses this file

- **Step 1** tag-matches the user's capability description ("alert verification", "verify alerts", "post verified alerts to the message broker", "clip retrieval + VLM check") against the catalog tags for this microservice.
- **Step 2 / Step 4** read the `component_services:` block below (NOT the integrate doc) to learn the upstream compose service-keys the Alert Microservice owns and the `alert_source` variant. Step 4 unions this block with the other selected microservices' patch files (VIOS, RT-VLM, ELK) and writes the flat allow-list to `allow-list.yml`.
- **Step 6.5** reads ONLY the resulting sidecar and applies the patches in the "Patch specifics" section below to the patched copies under the build directory's `patched/` tree.

## component_services block

The Alert Microservice **owns** the `alert-bridge` service. Its candidate-event source is a Step-4 decision (`alert_source` variant): the `cv-verification` case adds the static CV detector pair (RT-CV perception + Behavior Analytics) that produces incidents on Kafka; the `vlm-realtime` case adds no extra service-keys here (the VLM peer is RT-VLM, contributed by `patch-rt-vlm.md`, and the realtime REST API drives it directly).

```yaml
component_services:
  # Alert Microservice engine — always present when this microservice is selected.
  - key: alert-bridge
    file: services/alert/compose.yml
    role: VLM-as-verifier — consumes candidate incidents/alerts from Kafka, retrieves the clip from VIOS/VST, runs VLM verification, sinks verified records to Elasticsearch (mdx-vlm-incidents / mdx-vlm-alerts) and optionally Kafka.
    required: true
    # Peers the Alert Microservice needs but that are owned by OTHER component sets:
    #   - kafka, redis, elasticsearch, kafka-topic-init-container  -> ELK (integrate-elk.md)
    #   - the VST clip/storage API                                 -> VIOS (patch-vios.md)
    #   - the verification VLM                                     -> RT-VLM (patch-rt-vlm.md) or a NIM
    # Leave required_peers empty: ELK's `always:` carries kafka/redis/elasticsearch/
    # kafka-topic-init-container, so the union already keeps them.
    required_peers: []

  # Candidate-event source. Exactly one case per generation, chosen in Step 4.
  - variants:
      key: alert_source
      cases:
        # DEFAULT — "given a stream, clip retrieval, and alerts posted to the message broker":
        # a static CV detector posts candidate incidents to Kafka; alert-bridge verifies them.
        cv-verification:
          - key: perception-alerts
            file: developer-profiles/dev-profile-alerts/compose.yml
            role: RT-CV (DeepStream + Grounding DINO / RT-DETR) detector producing CV metadata to the message bus.
          - key: vss-behavior-analytics-alerts
            file: developer-profiles/dev-profile-alerts/compose.yml
            role: Behavior Analytics — turns CV metadata into candidate incidents/alerts on Kafka (mdx-incidents / mdx-alerts) with a `category` that maps to alert_type_config.json.
        # ALTERNATE — no CV detector; alert-bridge's realtime REST API drives RT-VLM directly.
        # No extra service-keys here: RT-VLM must be selected as its own microservice
        # (patch-rt-vlm.md contributes the rtvi-vlm service-key to the union).
        vlm-realtime: []

  # Agent layer — OPTIONAL. NOT part of the core verification data path (CV → broker →
  # alert-bridge → clip → VLM → ES runs fully headless; nothing in it depends on vss-agent).
  # Include ONLY when the prompt asks for natural-language incident query, report generation,
  # or the web UI (the UI consumes the agent). Owned long-term by the agent / LLM-NIM catalog
  # entries; carried here required:false so the alerts profile CAN compose the full official
  # "Alert Verification Agent" workflow in one selection when requested.
  - key: vss-agent
    file: services/agent/compose.yml
    role: VSS Agent — routes requests and orchestrates tool calls (incident queries, report generation) across the alert pipeline; calls alert-bridge via ALERT_BRIDGE_URL.
    required: false
  - key: vss-va-mcp
    file: services/agent/compose.yml
    role: Video-Analytics MCP server backing the agent's analytics tool calls.
    required: false

  # LLM NIM (Nemotron) — OPTIONAL, and only meaningful WHEN the agent layer is included
  # (the LLM serves the agent's reasoning / tool selection / report generation). Standalone
  # vs shared-GPU share container_name nvidia-nemotron-nano-9b-v2, so when included exactly
  # one is chosen from LLM_MODE (local vs local_shared). Omit entirely for a headless deploy.
  - variants:
      key: llm_placement
      required: false
      cases:
        # Dedicated GPU for the LLM (LLM_MODE=local).
        local:
          - key: nvidia-nemotron-nano-9b-v2
            file: services/nim/nvidia-nemotron-nano-9b-v2/compose.yml
            role: Nemotron Nano 9B v2 LLM NIM on its own GPU (port 30081), upstream profile llm_local_nvidia-nemotron-nano-9b-v2.
        # LLM shares a GPU with the VLM (LLM_MODE=local_shared, the alerts .env default).
        local-shared:
          - key: nvidia-nemotron-nano-9b-v2-shared-gpu
            file: services/nim/nvidia-nemotron-nano-9b-v2/compose.yml
            role: Nemotron Nano 9B v2 LLM NIM sharing a GPU with the VLM (SHARED_LLM_VLM_DEVICE_ID), upstream profile llm_local_shared_nvidia-nemotron-nano-9b-v2.

  # Optional add-ons (drop unless the user asks for the incident query API / alerts dashboard).
  - key: vss-video-analytics-api-alerts
    file: developer-profiles/dev-profile-alerts/compose.yml
    role: Video Analytics API serving incident/alert queries over the verified ES indices (a headless query surface that does NOT need vss-agent).
    required: false
  - key: kibana-init-container-alerts
    file: developer-profiles/dev-profile-alerts/compose.yml
    role: One-shot import of the alerts Kibana dashboard.
    required: false
```

> **`alert_source` case vocabulary = the two official VLM-alerting approaches.** The two cases map to the two deploy-time modes the `vss-manage-alerts` skill documents and the VSS docs call out:
> - `cv-verification` ↔ **Alert Verification** ↔ `MODE=2d_cv` / `bp_developer_alerts_2d_cv`. CV detector + Behavior Analytics generate candidate alerts upstream; the VLM is invoked **sporadically** to verify clips → **lower GPU**. The default; matches the canonical "stream → clip retrieval → alerts to broker → verify" description.
> - `vlm-realtime` ↔ **Real-Time Alerts** ↔ `MODE=2d_vlm` / `bp_developer_alerts_2d_vlm`. No CV detector; the VLM **continuously** processes chunks (alert-bridge realtime API drives RT-VLM) → **higher GPU**.
>
> Step 4 presents the choice with `cv-verification` as the default. When `vlm-realtime` is chosen, the skill MUST ensure RT-VLM is in the candidate set (Step 1) — otherwise the realtime API has nothing to drive; surface this as a gap rather than silently composing a non-functional verifier.

> **Agent layer + LLM NIM are OPTIONAL — the core verify path is headless.** Nothing in the verification data path (`alert-bridge`, `perception-alerts`, `vss-behavior-analytics-alerts`) `depends_on` `vss-agent`; verified incidents land in Elasticsearch (`mdx-vlm-incidents`) and are queryable directly (ES `_search`, or the optional `vss-video-analytics-api`). `vss-agent` + `vss-va-mcp` + an LLM NIM add only **natural-language incident query, report generation, and the backing for the web UI**. Step 4 decision rule:
> - Prompt is pure verification ("verify alerts / reduce false positives / store to ES + broker") → **headless**: omit the agent layer and the `llm_placement` variant entirely.
> - Prompt mentions NL query / report generation / UI / dashboard (the full official "Alert Verification Agent" workflow) → **include** `vss-agent` + `vss-va-mcp` + one LLM NIM. Default LLM `LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2` at `LLM_BASE_URL=http://${HOST_IP}:30081`; `llm_placement` maps to `LLM_MODE` (`local` dedicated GPU vs `local-shared` co-resident with the VLM on `SHARED_LLM_VLM_DEVICE_ID`). A different LLM model (gpt-oss-20b, llama-3.3-nemotron-super-49b, …) is a Step-4 choice owned by the LLM-NIM catalog entry (`skills/vss-deploy-profile/`, pending `patch-llm-nim.md`) — swap the `llm_placement` keys to that model's `(key, file)` pair.
>
> The web **UI** (`vss-ui` + HAProxy) implies the agent layer (the UI calls the agent); the agent layer does NOT imply the UI. Phoenix (agent observability) is already in ELK's `component_services` (`phoenix` key) and needs no separate entry.

> **LLM NIM uses `llm_*` profile gating, not `bp_developer_*`.** The Nemotron compose gates on `profiles: [llm_local_nvidia-nemotron-nano-9b-v2]` / `[llm_local_shared_...]` (upstream composes a `COMPOSE_PROFILES` list that includes the matching `llm_*` flag). For a single-flag standalone deploy, Patch 1 appends the invented `bp_developer_at_1` flag to the chosen LLM NIM's `profiles:` list (additive) so `--profile bp_developer_at_1` brings it up alongside everything else.

## Patch specifics (Step 6.5)

Applied to patched copies under `<BUILD_DIR>/patched/`; the upstream tree is never modified.

### Patch 1 — invented flag

The upstream `alert-bridge` block (`services/alert/compose.yml`) gates on `profiles: ["bp_wh_2d", "bp_developer_alerts_2d_cv", "bp_developer_alerts_2d_vlm"]`, so `docker compose up` without `--profile` starts nothing. Step 6.5 appends the per-generation invented flag (e.g. `bp_developer_at_1`) to the `alert-bridge` service's `profiles:` list in the patched copy (additive — existing upstream flags stay). For the `cv-verification` case, the same flag is also appended to `perception-alerts` and `vss-behavior-analytics-alerts` in the patched copy of `developer-profiles/dev-profile-alerts/compose.yml` (and to `vss-video-analytics-api-alerts` / `kibana-init-container-alerts` if they survived the allow-list). When the agent layer is included, append the flag to `vss-agent` + `vss-va-mcp` (`services/agent/compose.yml`) and to the selected LLM NIM service-key (`services/nim/compose.yml`) as well — every one of these is profile-gated upstream.

### Patch 2 — strip undefined `depends_on` peers

The `alert-bridge` block declares these `required: false` `depends_on` peers: `nvstreamer-alerts`, `cosmos-reason1-7b`, `cosmos-reason1-7b-shared-gpu`, `cosmos-reason2-8b`, `cosmos-reason2-8b-shared-gpu`, `cosmos3-reasoner`, `cosmos3-reasoner-shared-gpu`, `qwen3-vl-8b-instruct`, `qwen3-vl-8b-instruct-shared-gpu`, `rtvi-vlm`. The generalized Patch 2 rule strips whichever are **undefined** in the patched include graph:

- `kafka`, `redis`, `elasticsearch`, `kafka-topic-init-container` are **defined** (ELK component set) and are **kept** (these have hard, non-`required:false` conditions and must resolve).
- `rtvi-vlm` is **kept** when RT-VLM is in the allow-list (required for `vlm-realtime`; commonly present as the verification VLM); **stripped** otherwise.
- `nvstreamer-alerts` is **kept** only if the NvStreamer validation harness was emitted (Step 6 sidecar `validation_harness:` key) into the patched tree; **stripped** otherwise.
- The 8 sibling NIM peers are **stripped** for an in-process / RT-VLM-backed verification (the IN-/AT- default). They are kept only if a sibling NIM service-key is in the allow-list (a NIM-backed verification VLM).

Because the rule is "strip whatever is undefined," it is robust to the NIM peer set changing; this file need not be updated when the set grows. (Mirror of the RT-VLM Patch 2 in `patch-rt-vlm.md`.)

When the agent layer is included, the same generalized strip applies to `vss-agent`, which declares **~20** `required: false` `depends_on` peers (all LLM/VLM NIMs ± `-shared-gpu`, plus `rtvi-vlm`, `rtvi-embed`, `vss-va-mcp`, `lvs-server`): keep the selected LLM NIM key, `rtvi-vlm`, and `vss-va-mcp` (defined in the allow-list); strip the rest.

### Patch 3 — materialize verifier config bind-mounts

`alert-bridge` bind-mounts four host files and uses an `env-substitute.py` entrypoint that requires them at boot. Step 6.5 Patch 3 must copy these into the patched tree and point the `VLM_AS_VERIFIER_*` env at the copied paths:

- `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml` → mounted at `/app/configs/config.yml` (`VLM_AS_VERIFIER_CONFIG_FILE`)
- `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/realtime-config.yml` (or the sibling sample) → `/app/configs/realtime-config.yml` (`VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`)
- `developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/alert_type_config.json` → `/app/alert_type_config.json` (`VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE`)
- `services/alert/scripts/env-substitute.py` → `/app/env-substitute.py`

For `cv-verification`, also materialize the CV-side configs the `extends`-based services need: `developer-profiles/dev-profile-alerts/vss-behavior-analytics/configs/vss-behavior-analytics-config.json` and `developer-profiles/dev-profile-alerts/deepstream/configs/`. (The `perception-alerts` / `vss-behavior-analytics-alerts` keys use compose `extends:` — copy the extended base files `services/rtvi/rtvi-cv/compose.yaml` + `services/rtvi/rtvi-cv/ds-start.sh` and `services/analytics/behavior-analytics/compose.yml` into the patched tree so `extends:` resolves.)

### Patch 4 — neutralize nested `include:`

The `developer-profiles/dev-profile-alerts/compose.yml` and the `extends:` chains pull in sibling files via relative paths. As with the IN-1 SDRC/infra case, strip/neutralize any nested `include:` in copied composes and let the build's top-level `compose.yml` be the single include orchestrator. Record dropped includes in `PATCHES.md`.

### cv-verification host-prep — engine dirs + model staging (Step 6.5 Patch 0)

The `perception-alerts` (RT-CV) service needs **host directories prepared with the right permissions and the detector models staged** before bring-up, or it crash-loops. This is the work `deploy/docker/scripts/dev-profile.sh` does in its alerts branch (lines 1428–1503); the skill's Patch 0 host-prep MUST replicate it whenever the `cv-verification` case is chosen. The bind mounts on `perception-alerts` are `${VSS_DATA_DIR}/models/ → /opt/storage/` and `${VSS_APPS_DIR}/engines/ → /opt/engines/`.

1. **Engine output dirs (world-writable).** RT-CV builds TensorRT engines into `/opt/engines/{gdino,rtdetr-its}` at first run. The host dir is bind-mounted; if it is root-owned / non-writable the container dies with `mkdir: cannot create directory '/opt/engines/gdino': Permission denied`. Pre-create and `chmod 777`:

```bash
mkdir -p "${VSS_APPS_DIR}/engines/gdino" "${VSS_APPS_DIR}/engines/rtdetr-its"
chmod -R 777 "${VSS_APPS_DIR}/engines"
```

2. **Detector models (host-staged, NGC).** Download the two TAO ONNX artifacts into `${VSS_DATA_DIR}/models/{gdino,rtdetr-its}` (clean the dir first), then `chmod 777`. Requires `NGC_CLI_API_KEY`:

```bash
rm -rf "${VSS_DATA_DIR}/models"
mkdir -p "${VSS_DATA_DIR}/models/rtdetr-its" "${VSS_DATA_DIR}/models/gdino"

# Grounding DINO (open-vocabulary detection)
ngc registry model download-version \
  nvidia/tao/mask_grounding_dino:mask_grounding_dino_swin_tiny_commercial_deployable_v2.1_wo_mask_arm
mv mask_grounding_dino_v*/mgdino_mask_head_pruned_dynamic_batch.onnx \
  "${VSS_DATA_DIR}/models/gdino/mgdino_mask_head_pruned_dynamic_batch.onnx"

# TrafficCamNet RT-DETR
ngc registry model download-version \
  nvidia/tao/trafficcamnet_transformer_lite:deployable_resnet50_v2.0
mv trafficcamnet_transformer_lite_v*/resnet50_trafficcamnet_rtdetr.fp16.onnx \
  "${VSS_DATA_DIR}/models/rtdetr-its/model_epoch_035.fp16.onnx"

chmod -R 777 "${VSS_DATA_DIR}/models"
```

3. **CV runtime/log dirs (world-writable).** Also create `${VSS_DATA_DIR}/data_log/vss_video_analytics_api`, `${VSS_DATA_DIR}/videos/dev-profile-alerts` (the NvStreamer/recording dir), and `chmod -R 777 ${VSS_DATA_DIR}/data_log` — same blanket `data_log` permission `dev-profile.sh` applies (line 1549).

4. **SDRC cluster config must include the RT-CV workload.** `cv-verification` adds RT-CV (`vss-rtvi-cv`) as a stream-consuming workload that the SDR controller's WDM must route streams to. The SDRC template set is MODE-dependent: use the **`2d_cv`** model (`developer-profiles/dev-profile-alerts/sdrc/2d_cv/configs/`), which adds **`docker_cluster_config-rtvi-cv.json.tmpl`** on top of the streamprocessing template — NOT the `2d_vlm` single-workload set. Set `SDR_CONTROLLER_CONFIG_PATH=...sdrc/2d_cv` (the `${MODE}` in the path = `2d_cv`) and materialize that full template set in Patch 3. Omitting the rtvi-cv cluster template leaves RT-CV with **`Active sources: 0`** (no streams routed) even though every container is healthy. Full rule + the per-workload table is in `references/patch-vios.md § Patch 3` (the SDRC stack is owned by VIOS's patch reference).

Long-term these belong in RT-CV's own `deploy-*`/`patch-*` files (catalog entry `skills/vss-deploy-detection-tracking-2d/`); they live here because `cv-verification` pulls `perception-alerts` into the alerts allow-list. The model versions and target filenames are pinned to match the upstream `dev-profile.sh` — re-verify against that script if RT-CV bumps a model. Emit all three steps into the generated `deploy-<flag>` skill's Patch-0 pre-flight (and as `PATCHES.md` rows) so the operator never hits the permission/missing-model failure modes in `deploy-alerts.md § Known Deployment Issues`.

## Verification-backend env overrides the skill applies

`alert-bridge` reads its VLM endpoint from the mounted `config.yml` (`vlm.base_url`, `rtvi_vlm.base_url`) folded from env. The skill's Step 6 `.env` generation MUST make the verifier point at the chosen, actually-deployed VLM:

- **RT-VLM-backed verification (default for a standalone AT-1):** set `RTVI_VLM_BASE_URL=http://${HOST_IP}:8018`, `RTVI_VLM_MODEL_TO_USE=cosmos-reason2`, `VLM_MODE=local` (or `local_shared`), and `VLM_NAME` to the id RT-VLM advertises at `GET /v1/models` (e.g. `nim_nvidia_cosmos-reason2-8b_hf-1208`) — a mismatch yields HTTP 400 "No such model" and every verdict becomes `unverified`.
- **NIM-backed verification:** set `VLM_BASE_URL=http://${HOST_IP}:${VLM_PORT}` (`VLM_PORT=30082` for a co-located NIM) and `VLM_NAME=nvidia/cosmos-reason2-8b`, and add the chosen NIM service-key to the allow-list (so Patch 2 keeps its `depends_on`).
- **Remote verification:** set `VLM_MODE=remote`, `VLM_BASE_URL` to the build.nvidia.com / OpenAI-compatible endpoint, and provide `NVIDIA_API_KEY`.

Resolve `EXTERNAL_IP` / `INTERNAL_IP` to the host's routable IP so `alert_agent.url_transform` rewrites clip/media URLs to a reachable address for the VLM. Pre-resolve all `${VAR}` chains during env-folding so dry-run (Step 7) has zero unexpanded tokens.

> **Drop `NEXT_PUBLIC_*` when folding `dev-profile-alerts/.env`.** That file carries the UI-only `NEXT_PUBLIC_*` block, including the ~319-char `NEXT_PUBLIC_SIDEBAR_CHAT_CHAT_API_CUSTOM_AGENT_PARAMS_JSON={...}` value. `vss-ui` is **not** part of an Alert Microservice allow-list, so drop the whole `NEXT_PUBLIC_*` set during env-folding — besides being dead weight, the long unquoted JSON breaks line-based `--env-file` parsers and silently truncates every var after it (including `HOST_IP` / `VSS_APPS_DIR` / `VSS_DATA_DIR`). See `references/env-file-enumeration.md § Variable folding rule` for the general rule + the quote-complex-values defense.

> **Keep agent / va-mcp config paths CONTAINER-relative — do NOT absolutize them .** `vss-agent` and `vss-va-mcp` mount the host repo at `${VSS_APPS_DIR}:/vss-agent/deploy/docker:ro` and run with workdir `/vss-agent`, so their config/template vars are **relative paths that resolve inside the container**, not on the host: `VSS_AGENT_CONFIG_FILE=./deploy/docker/developer-profiles/dev-profile-alerts/vss-agent/configs/config.yml`, `VSS_VA_MCP_CONFIG_FILE=./deploy/docker/.../va_mcp_server_config.yml`, `VSS_AGENT_TEMPLATE_PATH=./deploy/docker/.../templates`. During env-folding the skill MUST pass these through **verbatim** (keep the leading `./deploy/docker/...`). Rewriting them to a host-absolute path (e.g. prefixing `${VSS_APPS_DIR}`) makes the agent look for a path that does not exist inside the container → config-not-found boot failure. (Same class of bug as a flat-resolver over-expanding a container-relative value — the path is intentionally container-scoped.)

## Emitted shape

The patched `alert-bridge` block (plus the `alert_source` services) is `include:`d from `<BUILD_DIR>/compose.yml`; deploy with `docker compose --env-file <BUILD_DIR>/.env -f <BUILD_DIR>/compose.yml --profile <invented-flag> up -d`. See the `## Example Compose Snippet` in `integrate-alerts.md` for the full upstream block this is patched from, and `references/standalone-compose-patches.md` for the generalized Patch 0–4 pseudocode.
