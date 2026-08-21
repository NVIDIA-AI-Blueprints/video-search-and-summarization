# Integration Reference: Alert Microservice

> **Naming.** This component is the **Alert Microservice** (formerly referred to as "Alert Verification" / "Alert Bridge"). The deploy identifiers are unchanged: image `vss-alert-verification`, container `vss-alert-bridge`, compose service-key `alert-bridge`. "Alert Verification" now refers ONLY to one of the two VLM-alerting **approaches** the Alert Microservice implements (the other is "Real-Time Alerts").

## Overview

The Alert Microservice (`alert-bridge`, image `vss-alert-verification`, container `vss-alert-bridge`) is the VLM-as-verifier stage of the VSS alert pipeline. It exposes a FastAPI REST surface on `:9080` (verification ingress `/api/v1/alerts`, `/api/v1/incidents`, `/api/v1/verification/ondemand`; verifier-prompt config CRUD `/api/v1/verification/config`; realtime rules `/api/v1/realtime`; `/health`, `/metrics`) and runs a Kafka/Redis event-bridge worker pool. Its core loop is: **(1)** receive a candidate event (a detector incident/alert on the message broker, an HTTP submission, or a realtime REST rule), **(2)** resolve the event's sensor/stream and **retrieve the relevant video clip from VIOS/VST**, **(3)** send the clip plus a per-`alert_type` prompt to a Vision Language Model, **(4)** publish the VLM-verified alert/incident (with a `verdict` of `confirmed` / `rejected` / `unverified`) to the message broker and/or Elasticsearch. **This step-(4) direct-to-ES write applies to the `cv-verification` approach only.** In the **`vlm-realtime`** approach the Alert Microservice does **NOT** write incidents to Elasticsearch itself — the realtime rule (`POST /api/v1/realtime`) drives **RT-VLM**, and **RT-VLM** produces the incident to Kafka `mdx-vlm-incidents` (`RTVI_VLM_KAFKA_INCIDENT_TOPIC`), which **Logstash** indexes into ES; `alert-bridge` only reads them back via `GET /api/v1/realtime/incidents`. See the § Outputs scoping note.

### The two VLM-alerting approaches

VSS showcases **two approaches** for using a VLM to generate alerts in the agent workflow. This microservice + its `alert_source` variant (in `references/patch-alerts.md`) covers both:

- **Alert Verification** (`alert_source: cv-verification`, the default; deploy mode `MODE=2d_cv` / flag `bp_developer_alerts_2d_cv`). The VLM analyzes only the video snippets corresponding to alerts **generated upstream** — original "candidate" alerts come from object detection/tracking (RTVI CV / Grounding DINO) + Behavior Analytics processing the streams in real time. The VLM is invoked **sporadically** (only to verify candidates), so **GPU requirements are lower**, but it depends on the upstream detector to produce candidates. This is the workflow these reference files target.
- **Real-Time Alerts** (`alert_source: vlm-realtime`; deploy mode `MODE=2d_vlm` / flag `bp_developer_alerts_2d_vlm`). The VLM **continuously** processes segments from the source at periodic chunk intervals, leveraging VLM generalizability to trigger alerts for a broad set of cases (may need prompt/fine-tuning). No CV detector — `alert-bridge`'s realtime REST API (`POST /api/v1/realtime`) drives RT-VLM directly. Higher GPU requirements due to more frequent VLM usage.

### Alert Verification — official scope

- **Use cases:** PPE-compliance verification (hard hats, safety vests), restricted-area monitoring, asset presence/absence detection, custom object-detection scenarios.
- **Key features:** RTVI CV real-time open-vocabulary object detection (Grounding DINO); Behavior Analytics rule-based/configurable alert generation; VLM-based clip review to **reduce false positives**; alert storage for query/reporting; report generation (via VSS Agent).
- **Estimated deployment time:** 15–20 minutes.

Source-of-truth definitions: `deploy/docker/services/alert/compose.yml` (the `alert-bridge` service block), `deploy/docker/developer-profiles/dev-profile-alerts/compose.yml` (the CV-side `perception-alerts` + `vss-behavior-analytics-alerts` + `nvstreamer-alerts` + agent/analytics services), `services/alert/config.yaml` (runtime config: VST/Kafka/Redis/VLM/sinks), and `services/alert/openapi.json` (the full 14-endpoint REST schema).

## Required Peer Services

The official Alert Verification workflow's "what's being deployed" set, mapped to peer roles. Items the `alert-bridge` microservice itself owns vs. those owned by other component sets are noted; the structured `component_services:` union lives in `references/patch-alerts.md`.

**Core verification data path (required):**

- **Kafka (message broker)** — required (default `event_bridge.sourceType: kafka`, `sinkType: kafka`). `alert-bridge` consumes candidate events from `event_bridge.kafka_source.topics` (`incident: mdx-incidents`, `alert: mdx-alerts`) under group `alert-bridge-vlm-group`, and publishes verified records. Broker must be reachable at `${HOST_IP}:9092` (host-network Kafka). Source: `services/alert/config.yaml § kafka` + `§ event_bridge`. `depends_on: kafka { condition: service_healthy }`. Owned by ELK/infra.
- **kafka-topic-init-container** — required. The candidate-event and verified-output topics (`mdx-incidents`, `mdx-alerts`, `mdx-vlm-incidents`, `mdx-vlm-alerts`) must exist before the worker pool starts, otherwise the consumer idles. `depends_on: kafka-topic-init-container { condition: service_completed_successfully }`. Owned by ELK/infra.
- **Elasticsearch + Logstash + Kibana (ELK)** — required. In `cv-verification` the default `vlm_enhanced_sink` writes verified records to ES indices `mdx-vlm-incidents` / `mdx-vlm-alerts` **directly from `alert-bridge`**; in `vlm-realtime` those same ES indices are populated instead by **RT-VLM → Kafka `mdx-vlm-incidents` → Logstash** (AB does not write — see § Outputs scoping note). Either way ELK is required. Additionally the `persistence` layer stores alert configs, verifier prompts, and durable realtime rules (index prefix `ab-`, rules collection `alert-realtime-rules`); Kibana provides the alerts dashboard. Reachable at `http://${HOST_IP}:9200`. Source: `config.yaml § elastic` + `§ persistence` + `§ vlm_enhanced_sink`. `depends_on: elasticsearch { condition: service_healthy }`. Owned by ELK (`integrate-elk.md`).
- **Redis** — required. Used for incident deduplication (`event_bridge.redis_source.dedup_ttl_seconds`), confirmed-verdict protection, and (in `vlm-realtime`) rule-state caching. Reachable at `${HOST_IP}:6379`. Source: `config.yaml § event_bridge.redis_source` + `§ redis_sink`. `depends_on: redis { condition: service_started }`. Owned by ELK/infra.
- **VIOS (Video I/O & Storage / VST)** — required for **clip retrieval** and ingestion of the camera/NVStreamer streams (live streaming, recording, playback). `alert-bridge` calls the VST API (`vst_config.base_url: http://localhost:30888`, `sensor_list_endpoint: /vst/api/v1/sensor/streams`) to resolve the sensor's stream, then fetches an end-anchored clip (`segment_anchor: end`, `segment_duration_seconds: 10`) via the storage service (`storage.media_file_path_by_id_endpoint: /api/v1/storage/file/path`). Without VST, the verifier has no media to send to the VLM. Source: `config.yaml § vst_config`. Owned by VIOS (`integrate-vios-service.md` / `patch-vios.md`).
- **RT-VLM (RTVI VLM microservice)** — required. The VLM used by the Alert Microservice for clip review. Reachable at `RTVI_VLM_BASE_URL=http://${HOST_IP}:8018`; mandatory for `vlm-realtime` (the realtime API issues `/v1/streams/add` + `/v1/generate_captions` against it) and the default verification backend for a standalone alerts deploy. Owned by RT-VLM (`patch-rt-vlm.md`). Alternative backend: a sibling VLM NIM (e.g. `cosmos-reason2-8b`) at `VLM_BASE_URL=http://${HOST_IP}:${VLM_PORT}` (`config.yaml § vlm.base_url: http://localhost:30082/v1`); each sibling NIM `depends_on` is `required: false`.

**Candidate-alert source (required for `cv-verification`):**

- **RTVI CV (object detection)** — `vss-rt-cv` (DeepStream + Grounding DINO open-vocabulary detection, `perception-alerts`). Processes VIOS live streams and outputs detection metadata to Kafka. Owned by RT-CV (`skills/vss-deploy-detection-tracking-2d/`); for the alerts profile its config-bearing service-key `perception-alerts` is contributed by the `cv-verification` variant in `patch-alerts.md`.
- **Behavior Analytics** — `vss-behavior-analytics` (`vss-behavior-analytics-alerts`). Processes RTVI CV metadata into rule-based candidate alerts/incidents on Kafka. Its emitted `category` must match an `alert_type` key in `alert_type_config.json` (or a `/api/v1/verification/config` entry). Owned by Behavior Analytics (`skills/vss-setup-behavior-analytics/`); the alerts-profile service-key is contributed by the `cv-verification` variant.

**Agent layer (optional) + synthetic source:**

- **VSS Agent + Video-Analytics MCP** — `vss-agent` (`:8000`) + `vss-va-mcp` (`:9901`). Routes requests and orchestrates tool calls (NL incident queries, **report generation**) across the alert pipeline; calls `alert-bridge` via `ALERT_BRIDGE_URL`. **Optional — NOT part of the core verify data path** (nothing in `alert-bridge` / `perception-alerts` / `vss-behavior-analytics` `depends_on` it; verified incidents are queryable directly from ES or `vss-video-analytics-api`). Include only for the full agentic workflow (NL query / report gen) or to back the web UI. Owned by the agent layer (`skills/vss-ask-video/` etc.); `required: false` `component_services` in `patch-alerts.md`.
- **Nemotron LLM (NIM)** — **optional**, only meaningful when the agent layer is included (the LLM serves the agent's reasoning / tool selection / report generation). Alerts default `LLM_NAME=nvidia/nvidia-nemotron-nano-9b-v2` at `LLM_BASE_URL=http://${HOST_IP}:${LLM_PORT}` (`:30081`); `LLM_MODE` picks dedicated (`local`) vs shared-GPU (`local_shared`) placement. Owned by the LLM-NIM catalog entry (`skills/vss-deploy-profile/`); the `required: false` `llm_placement` variant in `patch-alerts.md`. Omit for a headless verify deployment.
- **Phoenix** — observability for the agent layer (`PHOENIX_ENDPOINT=http://${HOST_IP}:6006`). Already part of ELK's `component_services` (`phoenix` key). Owned by ELK/infra.
- **NVStreamer** — video streaming service that plays back dataset videos to replicate live cameras (`vss-vios-nvstreamer`, `ADAPTOR=streamer`). In `build-vision-agent` this is the **validation-harness** component emitted by Step 6 (recorded under the sidecar `validation_harness:` key) when the deployment needs a live/streaming source but no real camera is supplied — it is NOT a `component_services:` entry. Source: `references/validation-harness.md`.
- **Video Analytics API** — `vss-video-analytics-api-alerts` (`:9901` query surface) serving incident/alert queries over the verified ES indices — a **headless** query surface that does NOT require `vss-agent`. Optional. Owned by the alerts profile (optional `component_services`).
- **VSS Agent UI** — `vss-ui` (`vss-agent-ui`, `:3000`) + HAProxy ingress. Optional web UI (Alerts tab, Video Management, Kibana dashboard). Requires the agent layer (it calls the agent) and HAProxy routing; consumes the `NEXT_PUBLIC_*` env block. Owned by the UI/ingress layer. Omit for a headless deployment.
- **MQTT / mosquitto** — optional. Only when republishing verified alerts over MQTT. Declared `required: false`.

> **Where the `component_services:` block lives (decoupling).** The Alert Microservice is owned by the `vss-manage-alerts` skill, so — per the 2026-06-08 decoupling convention used by VIOS and RT-VLM — its structured `component_services:` block (the upstream compose service-keys + the `alert_source` variant) is **not** carried here. It lives in `vss-build-vision-agent`'s own patch reference, `references/patch-alerts.md`, so this skill never depends back on the orchestrator. This file is the neutral integration contract only.

## Integration Interfaces

### Inputs

- **Method:** Kafka topic (consume) — candidate detector events
  **Topic:** `mdx-incidents` (protobuf `Incident`) and `mdx-alerts` (per `event_bridge.kafka_source.topics`)
  **Schema:** NvSchema `nv.Incident` / `nv.Behavior` protobuf (`config.yaml § kafka.message_type: "Incident"`). Each record carries the sensor/stream id and a `category`.
  **Auth:** none (in-cluster broker).

- **Method:** REST — HTTP verification ingress (alternative to the Kafka source)
  **Endpoint:** `POST http://${HOST_IP}:9080/api/v1/alerts`, `POST /api/v1/incidents`, `POST /api/v1/verification/ondemand`
  **Schema:** alert/incident JSON per `services/alert/schemas/request_schema.yaml`. `ondemand` accepts a clip/media reference + `alert_type` and returns the verdict synchronously; `/alerts` and `/incidents` enqueue for the same verify→sink path the Kafka consumer uses.
  **Auth:** optional Bearer token.

- **Method:** REST — realtime alert rule management (`vlm-realtime` shape)
  **Endpoint:** `POST` / `GET` / `DELETE http://${HOST_IP}:9080/api/v1/realtime`, plus `POST /api/v1/realtime/always-on`, `GET /api/v1/realtime/incidents`, `POST /api/v1/realtime/replay`
  **Schema:** JSON `{live_stream_url, sensor_id, sensor_name, alert_type, prompt, system_prompt, chunk_duration, chunk_overlap_duration}` (see `references/alert-subscriptions.md § Step 4`). `POST` returns `201` with the rule `id`.
  **Auth:** optional Bearer token.

- **Method:** VST clip retrieval (outbound call treated as an input dependency)
  **Endpoint:** `GET http://${HOST_IP}:30888/vst/api/v1/sensor/streams` → `GET /vst/api/v1/sensor/<sensorId>/streams`, then the storage file-path / clip endpoints.
  **Schema:** VST sensor + storage JSON (see `integrate-vios-service.md § API Schema`). The clip window is end-anchored, `segment_duration_seconds` long.

### Outputs

> **Sink-by-approach scoping (READ FIRST — verified live 2026-07-14).** Who writes the ES `mdx-vlm-incidents` doc depends on the `alert_source` approach:
> - **`cv-verification`** — `alert-bridge` itself writes the verified record to ES via its `vlm_enhanced_sink` (the ES-sink block below). The doc carries AB verdict fields (`info.verdict` / `info.reasoning` / `verification_response_code`).
> - **`vlm-realtime`** — `alert-bridge` does **NOT** write to ES. The realtime rule drives **RT-VLM**; **RT-VLM** produces the incident to Kafka `mdx-vlm-incidents` (`RTVI_VLM_KAFKA_INCIDENT_TOPIC`) and **Logstash** indexes it into ES `mdx-vlm-incidents-<date>`. The doc carries the RT-VLM/via-ctx-rag shape (`llm.queries[]`, `analyticsModule.info.streamType=live`, `embeddings`, `frameIds`); `info.verdict:confirmed` is set at source (realtime triggers are confirmed-at-source). `alert-bridge` only reads these back via `GET /api/v1/realtime/incidents`.
>
> The ES-sink block below therefore describes the **cv-verification** path. Do NOT diagram or describe an `alert-bridge → ES` incident write for a `vlm-realtime` deployment.

- **Method:** Elasticsearch (default sink, **`cv-verification` approach**) — verified incident/alert records
  **Index:** `mdx-vlm-incidents` and `mdx-vlm-alerts` (`config.yaml § vlm_enhanced_sink`). Realtime rules persist under `ab-alert-realtime-rules` (config/rule storage — distinct from the incident docs, which in `vlm-realtime` come from RT-VLM via Kafka+Logstash per the note above).
  **Schema:** the original record enriched with `info.verdict` (`confirmed` / `rejected` / `unverified`), `info.reasoning` (VLM explanation), and `verification_response_code`.
  **Trigger:** per verified event.

- **Method:** Kafka topic (optional sink) — verified records
  **Topic:** `mdx-vlm-incidents` / `mdx-vlm-alerts` (commented Kafka-sink stanza in `config.yaml § vlm_enhanced_sink`; switch `sinkType: kafka` + the sink type to `kafka` to enable). Used when downstream consumers (Logstash, notification relays) read verified alerts off the bus.
  **Schema:** NvSchema `nv.Incident` / alert protobuf with the verdict fields above.
  **Trigger:** per verified event.

- **Method:** Webhook (optional, fire-and-forget) — verified incidents
  **Endpoint:** `webhook.openclaw.url` (default `http://localhost:9090/webhook/alert-notify-slack`), consuming Kafka topic `mdx-vlm-incidents` under group `openclaw-webhook-group`. Disabled by default. Forwards to the `alert-notify` relay (`references/alert-notify.md`).
  **Trigger:** per verified incident when `webhook.openclaw.enabled: true`.

## API Schema

The full REST surface (`alert-bridge` on `:9080`) is specified in `services/alert/openapi.json` (14 endpoints), with request/response models in `services/alert/schemas/{request_schema.yaml,response_schema.yaml}`. The event-bridge (Kafka/Redis) consume→verify→sink loop has no REST surface — it is configured through the mounted `config.yml` — but the same verification engine is also reachable over HTTP via the ingress endpoints below.

| Category | Endpoint(s) | Purpose |
|---|---|---|
| **Verification ingress** | `POST /api/v1/alerts` (Submit Alert for Processing); `POST /api/v1/incidents` (Submit Incident for Processing); `POST /api/v1/verification/ondemand` (Verify alert on demand) | Push a candidate alert/incident — or a one-shot clip — to the verifier over HTTP instead of via Kafka. `ondemand` returns the verdict synchronously. `GET /api/v1/alerts/health` is the submission-path health probe. |
| **Verifier config (prompt) CRUD** | `GET`/`POST /api/v1/verification/config`; `GET`/`PUT`/`DELETE /api/v1/verification/config/{alert_type}` | List/create/read/update/delete per-`alert_type` verifier prompt configs (the dynamic, ES-persisted equivalent of `alert_type_config.json`). The `category` emitted by Behavior Analytics keys into these. |
| **Realtime rules** | `POST`/`GET /api/v1/realtime`; `GET`/`DELETE /api/v1/realtime/{alert_rule_id}` | Create / list / get / delete VLM-realtime rules (the `vlm-realtime` approach; see `references/alert-subscriptions.md`). |
| **Realtime ops** | `POST /api/v1/realtime/always-on` (start/stop always-on rules for an incoming camera event); `GET /api/v1/realtime/incidents` (list incidents from ES); `POST /api/v1/realtime/replay` (replay persisted active/failed rules onto RTVI VLM) | Always-on rule bulk control (`503 ALWAYS_ON_DISABLED` unless `alert_agent.always_on: true`), incident listing, and rule replay/recovery onto RT-VLM. |
| **Health & metrics** | `GET /health` (readiness — NOT `/api/v1/health`, which 404s); `GET /metrics` (Prometheus); `GET /ws/health` (WebSocket health) | Liveness/readiness, Prometheus metrics (gated by `PROMETHEUS_METRICS_ENABLED`), and the optional WebSocket broadcast health. |

## Environment Variables

The compose interpolates host-side names; the `env-substitute.py` entrypoint folds `${...}` into the mounted `config.yml` at container start. Subset relevant to composing a deployment (full runtime knobs live in `services/alert/config.yaml`):

| Variable | Purpose | Default | Required? |
|---|---|---|---|
| `ALERT_BRIDGE_PORT` / `FASTAPI_PORT` | Host REST API port for `/api/v1/realtime` + `/health` | `9080` | **Yes (effective)** |
| `HOST_IP` (→ `INTERNAL_IP`) | Interpolated into Kafka/Redis/ES/VST/VLM URLs; no fallback | — | **Yes** |
| `EXTERNAL_IP` | URL-rewrite target so clip/media URLs handed to the VLM/UI are externally reachable (`alert_agent.url_transform`) | `${HOST_IP}` | **Yes (effective)** |
| `VLM_BASE_URL` | Verification VLM endpoint (NIM backend) | `http://${HOST_IP}:${VLM_PORT}` | conditional (NIM backend) |
| `VLM_NAME` | VLM model id; must match what the backend advertises (`/v1/models`) or requests 400 | `nim_nvidia_cosmos-reason2-8b_hf-1208` | **Yes (effective)** |
| `VLM_MODE` / `LLM_MODE` | Inference placement: `remote` / `local` / `local_shared` | `local_shared` | **Yes** |
| `RTVI_VLM_BASE_URL` | RT-VLM realtime endpoint (drives `/v1/streams/add` for `vlm-realtime`) | `http://${HOST_IP}:8018` | conditional (realtime) |
| `RTVI_VLM_MODEL_TO_USE` | RT-VLM backend selector the realtime path requests | `cosmos-reason2` | conditional (realtime) |
| `VLM_AS_VERIFIER_CONFIG_FILE` | Host path to the mounted verifier `config.yml` (`→ /app/configs/config.yml`) | `.../vlm-as-verifier/configs/config.yml` | **Yes** |
| `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` | Host path to `realtime-config.yml` (`→ /app/configs/realtime-config.yml`) | `.../realtime-config.yml` | **Yes** |
| `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | Host path to `alert_type_config.json` (CV `category` → verifier prompts) | `.../alert_type_config.json` | **Yes (CV mode)** |
| `CONFIG_PATH` | In-container resolved config path the command reads | `/app/runtime/config.yml` | **Yes (compose-set)** |
| `ALWAYS_ON_RULES_CONFIG` | In-container rendered always-on rules YAML | `/app/runtime/realtime-config.yml` | optional |

## Network Requirements

- **Ports exposed:** `9080` (REST realtime API + `/health`), bound on the host interface (`network_mode: host`). No other host-bound ports.
- **Inbound traffic:** REST clients (the `vss-manage-alerts` skill / VSS Agent) on `:9080`.
- **Outbound traffic:**
  - Kafka broker at `${HOST_IP}:9092` (consume candidate events, optionally produce verified)
  - Redis at `${HOST_IP}:6379` (dedup, rule state)
  - Elasticsearch at `${HOST_IP}:9200` (verified sink + persistence)
  - VST/VIOS at `${HOST_IP}:30888` (clip retrieval)
  - Verification VLM at `${HOST_IP}:8018` (RT-VLM) or `${HOST_IP}:30082` (NIM)
  - NGC registry `nvcr.io` for the image pull on first boot
- **DNS / hostname assumptions:** runs on `network_mode: host`, so all peer URLs resolve via `${HOST_IP}` (the routable host IP), not bridge service names. The `url_transform` rewrites internal IPs to `${EXTERNAL_IP}` so media URLs handed to a remote VLM or the UI stay reachable.
- **`network_mode`:** host.

## Known Integration Constraints

- **Single-instance.** `container_name: vss-alert-bridge` is hardcoded; a second instance on the same host fails with a name conflict. The `:9080` host port is likewise singleton under `network_mode: host`.
- **Config mounts + env-substitute entrypoint are mandatory.** A single `env-substitute.py` invocation renders the required main config and, when present, the optional realtime config under `/app/runtime`, then launches `enhance_alert_with_vlm.py`. This is required for always-on rules using `model: "${VLM_NAME}"`. `config.yml`, `alert_type_config.json`, and `env-substitute.py` must be bind-mounted; `realtime-config.yml` is optional only while `alert_agent.always_on` is false. If always-on is enabled without a rendered rules file, startup validation fails. `/app/runtime` must be a writable tmpfs.
- **Topics must be pre-created.** `alert-bridge` consumes `mdx-incidents` / `mdx-alerts` and (optionally) produces `mdx-vlm-incidents` / `mdx-vlm-alerts`. It `depends_on: kafka-topic-init-container (service_completed_successfully)`; a standalone deploy must keep that init container in the allow-list (it is part of ELK's component set).
- **CV `category` must match `alert_type_config.json`.** In `cv-verification`, an incident whose Behavior-Analytics `category` has no entry in `alert_type_config.json` is not verified (no prompt to send the VLM). Editing the JSON requires an `alert-bridge` restart.
- **Verdict semantics.** Verified records carry `info.verdict` ∈ {`confirmed`, `rejected`, `unverified`} and `verification_response_code` (200 = success). `vlm-realtime` incidents are confirmed at source (the trigger itself is a Yes/No VLM answer) and carry no separate verdict. The verifier matches verdict tokens against the VLM response per `vlm.response_format` (`auto` detects Cosmos-Reason vs. generic).
- **Clip window is end-anchored.** `vst_config.segment_anchor: end` + `segment_duration_seconds: 10` means the verifier pulls the last ~10 s ending at the incident time. A sensor with no retrievable stream yields an `unverified` verdict.
- **`always_on` gate.** `POST /api/v1/realtime/always-on` short-circuits with `503 ALWAYS_ON_DISABLED` unless `alert_agent.always_on: true`; when enabled, a malformed `ALWAYS_ON_RULES_CONFIG` fails app boot rather than failing on first event.
- **Sibling-NIM `depends_on` are `required: false`.** The upstream `alert-bridge` block declares `depends_on` on `nvstreamer-alerts`, the 8 sibling NIMs (`cosmos-reason1-7b`, `cosmos-reason2-8b`, `cosmos3-reasoner`, `qwen3-vl-8b-instruct`, each ± `-shared-gpu`), and `rtvi-vlm`, all `required: false`. Recent Docker Compose rejects undefined peers at project-load; a standalone deploy must strip whichever are undefined in its include graph (see `references/patch-alerts.md` + `references/standalone-compose-patches.md`).

## Example Compose Snippet

Minimal block, abbreviated from `deploy/docker/services/alert/compose.yml`. A standalone deploy patches a copy (never the upstream tree); the `profiles:` placeholder is where Step 6.5 inserts the invented flag.

```yaml
services:
  alert-bridge:
    image: nvcr.io/nvidia/vss-core/vss-alert-verification:3.2.0
    container_name: vss-alert-bridge
    profiles:
      - <your-profile-flag>            # Step 6.5 Patch 1 inserts the invented flag (additive)
      # - bp_developer_alerts_2d_cv    # existing upstream flags preserved
      # - bp_developer_alerts_2d_vlm
    network_mode: host
    restart: unless-stopped
    environment:
      VLM_BASE_URL: ${VLM_BASE_URL:-http://${HOST_IP}:${VLM_PORT}}
      VLM_NAME: ${VLM_NAME}
      EXTERNAL_IP: ${EXTERNAL_IP}
      INTERNAL_IP: ${HOST_IP}
      LLM_MODE: ${LLM_MODE}
      VLM_MODE: ${VLM_MODE}
      RTVI_VLM_MODEL_TO_USE: ${RTVI_VLM_MODEL_TO_USE}
      RTVI_VLM_BASE_URL: ${RTVI_VLM_BASE_URL:-http://${HOST_IP}:8018}
      CONFIG_PATH: /app/runtime/config.yml
      ALWAYS_ON_RULES_CONFIG: /app/runtime/realtime-config.yml
    volumes:
      - ${VLM_AS_VERIFIER_CONFIG_FILE_REALTIME}:/app/configs/realtime-config.yml:ro
      - ${VLM_AS_VERIFIER_CONFIG_FILE}:/app/configs/config.yml:ro
      - ${VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE}:/app/alert_type_config.json:ro
      - $VSS_APPS_DIR/services/alert/scripts/env-substitute.py:/app/env-substitute.py:ro
    tmpfs:
      - /app/runtime:mode=1777,size=10M
    depends_on:
      kafka: { condition: service_healthy }
      redis: { condition: service_started }
      elasticsearch: { condition: service_healthy }
      kafka-topic-init-container: { condition: service_completed_successfully }
      # rtvi-vlm / sibling-NIM / nvstreamer-alerts peers are required:false and
      # are STRIPPED if undefined in the standalone include graph (Patch 2).
    entrypoint:
      - /usr/local/bin/python
      - /app/env-substitute.py
      - --source
      - /app/configs/config.yml
      - --output
      - /app/runtime/config.yml
      - --optional-source
      - /app/configs/realtime-config.yml
      - --optional-output
      - /app/runtime/realtime-config.yml
      - --
    command: ["/usr/local/bin/python", "enhance_alert_with_vlm.py", "--config", "/app/runtime/config.yml"]
```

## Schema Compatibility

The candidate-event protobuf (`nv.Incident` / `nv.Behavior` on `mdx-incidents` / `mdx-alerts`) and the verified-output protobuf must align with the NvSchema descriptors at `deploy/docker/services/infra/elk/pb_definitions/descriptors/{schema.desc, ext.desc}` shared with Logstash and the CV producers. The CV `category` field is the join key into `alert_type_config.json`. Drift between the Behavior-Analytics producer schema and the `alert-bridge` consumer schema causes silently dropped or unverified events.

## Test / Smoke Hooks

- **Health:** `curl -sf --connect-timeout 5 http://${HOST_IP}:9080/health` — expect HTTP 200 (NOT `/api/v1/health`).
- **Realtime rules list (vlm-realtime):** `curl -s http://${HOST_IP}:9080/api/v1/realtime | jq .` — empty array is a valid (success) response.
- **Verified records in ES:** after a detection, confirm verified docs land in `mdx-vlm-incidents`:

```bash
curl -sf "http://${HOST_IP}:9200/mdx-vlm-incidents/_count" | jq '.count'
curl -sf "http://${HOST_IP}:9200/mdx-vlm-incidents/_search?size=1" \
  | jq '.hits.hits[0]._source.info | {verdict, reasoning, verification_response_code}'
```

- **Verdict distribution (CV mode):** a populated `verdict` field of `confirmed` / `rejected` proves the VST clip → VLM verification round-trip is wired end to end.
