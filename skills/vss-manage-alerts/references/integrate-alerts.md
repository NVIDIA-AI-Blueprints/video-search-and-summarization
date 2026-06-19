# Integration Reference: Alert Verification (Alert Bridge)

## Overview

Use this service when a workflow needs **VLM-verified alerting** layered on top of a
captioning / perception baseline. The Alert Verification service (image
`nvcr.io/nvidia/vss-core/vss-alert-verification`, container `vss-alert-bridge`, also
referred to as "Alert Bridge" / "AB") watches for trigger conditions and uses a VLM to
confirm or reject them, turning raw detections or rule-driven prompts into *verified*
alert/incident events. It operates in two complementary modes:

- **Stream-driven verification** — AB consumes raw events on the Kafka input topics
  (`mdx-incidents`, `mdx-alerts`), pulls the relevant video clip from VIOS, sends it to a
  VLM for a yes/no verdict against the configured prompt, and writes the enhanced
  (verified) event back out.
- **Real-time always-on alerting** — AB exposes a REST API (`/api/v1/realtime`,
  `/api/v1/realtime/always-on`) that registers durable alert *rules* (e.g. "person on a
  ladder without a hardhat"). Each incoming camera event triggers one RTVI VLM call per
  rule; AB drives `rtvi-vlm`'s `/v1/generate_captions` on the live stream and persists
  the rules + verified results.

AB does **not** run a VLM itself — it calls an existing RT-VLM (`rtvi-vlm`) over HTTP. It
is therefore the natural extension of an IN-1-style streaming/VOD dense-captioning
deployment: the same `rtvi-vlm` GPU container serves both the caption stream and the
alert-verification VLM calls.

Source of truth for this contract: the upstream compose `deploy/docker/services/alert/compose.yml`,
the verifier config `deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml`,
and the realtime rule sample `deploy/docker/industry-profiles/warehouse-operations/vlm-as-verifier/realtime-config.yml`.

## Required Peer Services

Prose — peer microservices (cross-skill dependencies):

- **Kafka** — required. AB's stream-driven path consumes `mdx-incidents` / `mdx-alerts`
  and (optionally) publishes enhanced events. `depends_on: kafka (service_healthy)`.
- **Elasticsearch** — required. AB writes verified incidents/alerts directly to ES
  (`vlm_enhanced_sink`, see Outputs) and persists realtime rules + prompt configs
  (`persistence.backend: elasticsearch`). `depends_on: elasticsearch (service_healthy)`.
- **Redis** — required. Used for the event-bridge dedup / heartbeat streams and
  in-flight protection. `depends_on: redis (service_started)`.
- **kafka-topic-init-container** — required init gate; creates `mdx-incidents`,
  `mdx-alerts`, `mdx-vlm-incidents`, `mdx-vlm-alerts` before AB starts.
- **RT-VLM (`rtvi-vlm`)** — required for verification. AB calls it over HTTP at
  `${VLM_BASE_URL}` (defaults to `http://${HOST_IP}:${VLM_PORT}`, port 8018) for the
  stream-driven verdict path, and at `${RTVI_VLM_BASE_URL}/v1` for the realtime
  always-on path. Declared `required: false` in upstream `depends_on` (alongside the
  sibling NIM keys) so that remote-VLM deployments validate; in an IN-1-layered build
  `rtvi-vlm` IS present and is the verification backend. **No second GPU is allocated for
  AB.**
- **VIOS** — required for the stream-driven clip-pull path (AB reads
  `vst_config.base_url: http://localhost:30888` to resolve a sensor's recorded segment
  before sending it to the VLM). Present in the IN-1 baseline.
- **Logstash** — optional. AB's *default* `vlm_enhanced_sink` writes directly to ES
  (`type: elastic`), so Logstash is NOT on the critical path for the verified output.
  Logstash's `mdx-logstash.conf` does separately subscribe to `mdx-vlm-alerts` /
  `mdx-vlm-incidents` (and `mdx-alerts` / `mdx-incidents`) when a producer publishes to
  those topics, routing them to date-stamped ES indices.

The `component_services:` block for this service is owned by `vss-build-vision-agent` and
lives in `references/patch-alerts.md` (decoupling, 2026-06-08) — NOT in this file.

## Inputs

**Kafka input topics** (stream-driven path; `event_bridge.kafka_source.topics`):

| Topic | Purpose |
|---|---|
| `mdx-incidents` | raw incident events (`message_type: "Incident"`) to be VLM-verified |
| `mdx-alerts` | raw alert events |

**REST inputs** (real-time always-on path; `${ALERT_BRIDGE_PORT}` = 9080):

| Method + path | Purpose |
|---|---|
| `POST /api/v1/realtime` | register a realtime alert rule (durable; persisted to ES) |
| `DELETE /api/v1/realtime/{rule_id}` | stop / delete a realtime rule |
| `GET /api/v1/realtime` | list active realtime rules |
| `POST /api/v1/realtime/always-on` | deliver a camera event; fires one VLM call per rule in the always-on ruleset |
| `POST /api/v1/realtime/replay` | replay persisted rules (501 if `enable_realtime_persistence: false`) |

**Alert rule schema** (real-time rules; from `realtime-config.yml § always_on_rules[*]`):

```yaml
- rule_id: ppe                      # required, free-form label
  description: "PPE"                 # optional
  alert_type: "PPE Violation"        # required; passed to RTVI as alert_category
  always_on_params:
    system_prompt: "<VLM system prompt>"   # required
    prompt: "<detection prompt>"           # required
    model: "nim_nvidia_cosmos-reason2-8b_hf-1208"  # runtime-generated RT-VLM model id (NOT the friendly name)
    chunk_duration: 6                       # seconds of video per VLM chunk
    chunk_overlap_duration: 0
    num_frames_per_second_or_fixed_frames_chunk: 1
    use_fps_for_chunking: true
    vlm_input_width: 854
    vlm_input_height: 480
    enable_reasoning: true
    max_tokens: 4096
    temperature: 0.0
```

`live_stream_url` is populated automatically from the incoming event's `camera_url`
field — do NOT set it in the rule.

**Alert-type config** (stream-driven prompts; `alert_type_config.json`):

```json
{ "version": "1.0",
  "alerts": [ { "alert_type": "<type>", "output_category": "<category>",
                "prompts": { "system": "...", "user": "Is <condition>? Answer yes or no." } } ] }
```

## Outputs

**Verified events to Elasticsearch** (default sink — `config.yml § vlm_enhanced_sink`,
`type: elastic`, written directly by AB, no Logstash needed):

| Sink | ES index | Content |
|---|---|---|
| `vlm_enhanced_sink.incident` | `mdx-vlm-incidents` | VLM-verified incidents (verdict + reasoning) |
| `vlm_enhanced_sink.alert` | `mdx-vlm-alerts` | VLM-verified alerts |

**Realtime rule persistence** — `persistence.backend: elasticsearch`,
`index_prefix: "ab-"` + `rtvi_vlm.rules_collection: "alert-realtime-rules"` → ES index
**`ab-alert-realtime-rules`**.

**Optional Kafka sink** — the commented `vlm_enhanced_sink` alt form publishes to Kafka
`mdx-vlm-incidents` / `mdx-vlm-alerts` instead of ES; Logstash then routes those topics to
`mdx-vlm-incidents-YYYY-MM-DD` / `mdx-vlm-alerts-YYYY-MM-DD` date indices.

**REST query** — `GET /api/v1/realtime` returns the active rules with their last verdicts.

## Environment Variables

From `deploy/docker/services/alert/compose.yml` (service `alert-bridge`) and
`deploy/docker/developer-profiles/dev-profile-alerts/.env`:

| Variable | Default / value | Meaning |
|---|---|---|
| `VLM_BASE_URL` | `http://${HOST_IP}:${VLM_PORT}` | VLM endpoint AB calls for verification (stream-driven path). `VLM_PORT=8018` → the existing `rtvi-vlm`. |
| `VLM_NAME` | `nim_nvidia_cosmos-reason2-8b_hf-1208` | model id AB passes to the VLM; MUST match `/v1/models` (the runtime-generated id, not `cosmos-reason2-8b`). |
| `VLM_PORT` | `8018` | local rtvi-vlm port (overridden to 30082 only when `VLM_MODE=remote`). |
| `EXTERNAL_IP` / `INTERNAL_IP` | `${EXTERNAL_IP}` / `${HOST_IP}` | URL-rewriting for VLM/UI media access (`url_transform`). |
| `LLM_MODE` / `VLM_MODE` | `local_shared` | `remote` / `local` / `local_shared`. |
| `RTVI_VLM_MODEL_TO_USE` | `cosmos-reason2` | passed to AB realtime layer to drive `rtvi-vlm`. |
| `RTVI_VLM_BASE_URL` | `http://${HOST_IP}:8018` | base URL for the realtime always-on RTVI path (`rtvi_vlm.base_url: ${RTVI_VLM_BASE_URL}/v1`). |
| `ALERT_BRIDGE_PORT` | `9080` | AB REST API port (host networking). |
| `ALERT_BRIDGE_URL` | `http://${HOST_IP}:9080` | external URL of the AB API. |
| `CONFIG_PATH` | `/app/runtime/config.yml` | rendered config path (env-substituted from `config.yml`). |
| `ALWAYS_ON_RULES_CONFIG` | `/app/configs/realtime-config.yml` | mounted realtime ruleset (see Known Constraints — must be supplied). |
| `VLM_AS_VERIFIER_CONFIG_FILE` | `.../vlm-as-verifier/configs/config.yml` | host path bind-mounted to `/app/configs/config.yml`. |
| `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` | (unset in dev-profile-alerts) | host path bind-mounted to `/app/configs/realtime-config.yml`; defaults to `/path/to/realtime-config.yml` (a non-existent placeholder) if not set. |
| `VLM_AS_VERIFIER_ALERT_TYPE_CONFIG_FILE` | `.../vlm-as-verifier/configs/alert_type_config.json` | host path bind-mounted to `/app/alert_type_config.json`. |

Config-file (non-env) settings of note (`config.yml`): `kafka.bootstrap_servers:
localhost:9092`; `event_bridge.kafka_source.topics.{incident,alert}`;
`vst_config.base_url: http://localhost:30888`; `elastic.hosts: http://localhost:9200`;
`rtvi_vlm.base_url: ${RTVI_VLM_BASE_URL}/v1`. These resolve to host-networked localhost
because AB runs `network_mode: host`. AB env-substitutes `${VAR}` tokens in `config.yml`
at start via `/app/env-substitute.py` (entrypoint), writing the rendered file to a tmpfs
at `/app/runtime/config.yml`.

## Network

- `network_mode: host` (no port mapping; binds directly to host ports).
- REST API: **port 9080** (`ALERT_BRIDGE_PORT`), reachable at `http://${HOST_IP}:9080`
  and via HAProxy at `/alert-bridge/` (`bk_alert_bridge_strip` backend).
- Talks to Kafka `localhost:9092`, ES `localhost:9200`, Redis `localhost:6379`, VIOS
  `localhost:30888`, RT-VLM `localhost:8018` — all over host networking.
- **GPU: none.** AB is CPU-only; all VLM inference is delegated to `rtvi-vlm` over HTTP.
- `tmpfs: /app/runtime` (10 MB) for the rendered config.

## Integration Interfaces

```
                              ┌─────────────────────────────────────┐
                              │  rtvi-vlm  (RT-VLM, GPU)             │
                              │  :8018  /v1/generate_captions        │
                              └──────────────▲──────────────────────┘
                                             │ HTTP (verify / always-on)
 raw events                                  │
 mdx-incidents ──┐                  ┌────────┴─────────┐      verified events
 mdx-alerts ─────┼──Kafka:9092────► │  alert-bridge    │ ──► ES mdx-vlm-incidents
                 │                  │  (vss-alert-     │ ──► ES mdx-vlm-alerts
 camera events ──┼──REST:9080─────► │   bridge)        │
 /api/v1/realtime/always-on        │  CPU only        │ ──► ES ab-alert-realtime-rules
                                    └────────┬─────────┘      (rule persistence)
 clip pull  ◄──────VIOS:30888───────────────┘
```

Flow (stream-driven): producer → `mdx-incidents`/`mdx-alerts` (Kafka) → alert-bridge →
clip pull from VIOS → VLM verdict from rtvi-vlm → verified doc to ES (`mdx-vlm-*`).

Flow (real-time always-on): rule registered via `POST /api/v1/realtime` (persisted to
`ab-alert-realtime-rules`) → camera event via `POST /api/v1/realtime/always-on` →
alert-bridge drives rtvi-vlm `/v1/generate_captions` on the live stream → verified verdict
to ES.

## Known Constraints

1. **Realtime config file is not shipped in dev-profile-alerts.** The upstream
   `dev-profile-alerts/.env` does NOT define `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME`, and
   there is no `realtime-config.yml` under `dev-profile-alerts/`. The compose default for
   the `ALWAYS_ON_RULES_CONFIG` mount is the non-existent placeholder
   `/path/to/realtime-config.yml`, which Docker would silently materialize as an empty
   directory. A standalone deployment that uses the always-on REST path MUST supply a real
   `realtime-config.yml` (the canonical sample lives at
   `industry-profiles/warehouse-operations/vlm-as-verifier/realtime-config.yml`) and point
   `VLM_AS_VERIFIER_CONFIG_FILE_REALTIME` at it. (Handled by `patch-alerts.md`.)
2. **Model id is runtime-generated.** `VLM_NAME` / `model` must be the RT-VLM
   runtime-registered id `nim_nvidia_cosmos-reason2-8b_hf-1208`, not the friendly
   `cosmos-reason2-8b` — otherwise rtvi-vlm returns HTTP 400 "No such model". Resolve from
   `GET http://${HOST_IP}:8018/v1/models` at runtime.
3. **`VLM_MODEL_TYPE=rtvi`, not `nim`.** Routing through rtvi-vlm requires the openai-typed
   `rtvi` profile; the `nim` type leaks `verify_ssl` into the request body and the FastAPI
   rtvi-vlm rejects it with 422.
4. **Stream-driven verification needs a producer.** The `mdx-incidents` / `mdx-alerts`
   topics are only populated by an upstream perception/analytics producer (RT-CV +
   Behavior Analytics in the `2d_cv` mode). In a pure IN-1 + VLM-alerting (`2d_vlm`) build
   with no CV producer, the **real-time always-on REST path** is the working alert source;
   the Kafka-consumer path stays idle until a producer is added (e.g. an AN-2 build).
5. **Many `required: false` `depends_on` peers.** AB's upstream `depends_on` lists all 8
   sibling NIM keys (cosmos-reason1-7b ± shared, cosmos-reason2-8b ± shared,
   cosmos3-reasoner ± shared, qwen3-vl-8b-instruct ± shared) plus `nvstreamer-alerts`, all
   `required: false`. A standalone build must strip the ones not present in its include
   graph (see `patch-alerts.md` Patch 2).
6. **CPU/host-net co-tenancy.** AB binds host port 9080; ensure it is free. It shares the
   host network with all other VSS services, so `localhost`-addressed peers resolve
   correctly only when those peers are also host-networked (they are in the VSS stack).

## Example Compose Snippet

```yaml
services:
  alert-bridge:
    image: nvcr.io/nvidia/vss-core/vss-alert-verification:3.2.0
    container_name: vss-alert-bridge
    profiles: ["bp_developer_alerts_2d_vlm", ...]
    network_mode: host
    environment:
      VLM_BASE_URL: ${VLM_BASE_URL:-http://${HOST_IP}:${VLM_PORT}}
      VLM_NAME: ${VLM_NAME}
      RTVI_VLM_BASE_URL: ${RTVI_VLM_BASE_URL:-http://${HOST_IP}:8018}
      ALERT_BRIDGE_PORT: ${ALERT_BRIDGE_PORT}
      CONFIG_PATH: /app/runtime/config.yml
      ALWAYS_ON_RULES_CONFIG: /app/configs/realtime-config.yml
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
      rtvi-vlm: { condition: service_healthy, required: false }
```

---

*Footnotes (source tracing):*
- *Service def, env, depends_on, entrypoint: `deploy/docker/services/alert/compose.yml`.*
- *Input topics, VLM config, ES sink, realtime rules persistence: `deploy/docker/developer-profiles/dev-profile-alerts/vlm-as-verifier/configs/config.yml`.*
- *Realtime rule schema: `deploy/docker/industry-profiles/warehouse-operations/vlm-as-verifier/realtime-config.yml § always_on_rules`.*
- *Topic creation: `deploy/docker/services/infra/compose.yml § kafka-topic-init-container`.*
- *ES index routing for mdx-vlm-alerts/incidents: `deploy/docker/services/infra/elk/logstash/pipelines/kafka/mdx-logstash.conf`.*
- *Env var defaults: `deploy/docker/developer-profiles/dev-profile-alerts/.env`.*
- *REST API ports + HAProxy backend: `deploy/docker/services/infra/haproxy/haproxy.cfg.template § bk_alert_bridge_strip`.*
- *Local met-blueprint-docs (`alert-verification-service.rst`, `alert-verification-api.rst`) were NOT readable this session (local Read denied); the contract above is sourced entirely from the remote compose + config ground truth, which is authoritative for deploy-time concerns. Re-trace against those .rst files when next accessible.*
