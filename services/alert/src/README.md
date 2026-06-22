<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Alert Verification — Source Layout

This directory (`src/`) holds all importable Python packages for the **Alerts
microservice**. The service ingests alerts/incidents produced by the VSS
pipeline and uses a Vision-Language Model (VLM) to **confirm, classify, and
enrich** them.

> The launcher lives one level up at `services/alert/enhance_alert_with_vlm.py`.
> It puts `src/` on `sys.path` and wires the packages below into the running
> pipeline. Tests rely on `services/alert/conftest.py`, which also adds `src/`
> to the path, so imports are written top-level (e.g. `from vlm.vlm_client import VLMClient`).

## High-level data flow

```mermaid
flowchart LR
    subgraph Ingest["Ingest (mdx)"]
        SRC["mdx/source<br/>Kafka · Redis · ES"]
    end

    subgraph Core["Verification pipeline"]
        ORC["enhance_alert_with_vlm.py<br/>(orchestrator, repo root)"]
        H["handlers/<br/>config · prompt · enrichment · direct_media"]
        VID["vst/ + vss/<br/>resolve video clip"]
        VLM["vlm/<br/>VLM client (OpenAI-compatible)"]
    end

    subgraph Out["Output"]
        SNK["mdx/sink<br/>(+ vlm_enhanced_sink)"]
        PER["persistence/<br/>Elasticsearch"]
        WEB["web/<br/>REST + WebSocket"]
    end

    SRC --> ORC
    ORC --> H
    H --> VID
    VID --> VLM
    VLM --> ORC
    ORC -->|"VLMResponse → AlertResponseEntity (schemas/)"| SNK
    ORC --> PER
    ORC --> WEB

    H -. "alert_type config" .-> CL["clients/<br/>Redis · ES"]
    PER -. uses .-> CL
    ORC -. metrics .-> MET["metrics/<br/>Prometheus"]
    WEB -. realtime rules .-> RT["realtime/<br/>always-on + RTVI"]
    ORC -. "shared models / utils" .-> UT["schemas/ · utils/"]
```

Two secondary entry paths share the same packages:
- **`web/`** — FastAPI app (REST `/api/v1/...` + WebSocket) for alert submission,
  on-demand verification, config management, and realtime broadcasting.
- **`realtime/`** — always-on / realtime alert rules driven by the RTVI VLM client.

## Top-level packages

| Package | Responsibility |
|---|---|
| `mdx/` | **Alert ingestion transport** (NvSchema). Sources/sinks over Kafka, Redis Streams, Elasticsearch; protobuf schemas; dedup fingerprints. |
| `handlers/` | **Core alert handlers** — alert-type config store, prompt rendering, enrichment, direct-media mode, exception handling, async mixins. |
| `vlm/` | **VLM client** (OpenAI-compatible NIM): sync/async clients, async runtime, NIM warmup. |
| `vss/` | **VSS integration** — orchestrates the clip-verification request to the VSS/VLM backend (media upload/delete, sessions, workflow, retries). |
| `vst/` | **VST video-storage client** — resolves the video segment for an alert from `sensorId` + timestamps (timelines, clip extraction). |
| `schemas/` | **Data models / NvSchema entities** — request/response entities, VLM response model + pluggable parser registry, shared enums, config defaults. |
| `custom_parsers/` | **Sample/pluggable VLM-response parsers**, loaded dynamically via config. |
| `clients/` | **Low-level external-service clients** — Elasticsearch (`ElasticClient`) and Redis (`RedisHandler` / `RedisClient`). |
| `persistence/` | **Durable storage abstraction** (`PersistenceStore` ABC + Elasticsearch implementation, factory, config). |
| `utils/` | **Shared utilities** — config loader, logging, time/ISO helpers, event/schema helpers, URL transform. |
| `metrics/` | **Prometheus metrics** — definitions, recorder helpers, multiprocess setup. |
| `web/` | **FastAPI app** — REST routers, API schemas, services, WebSocket broadcasting. |
| `realtime/` | **Realtime & always-on alert rules** — services, rule store, RTVI VLM client, schemas, config. |
| `webhook/` | **Outbound webhook notifications** (e.g. OpenClaw notifier). |
| `tools/` | **Operational CLI** (e.g. migrate alert-config Redis → Elasticsearch). |

## Key subpackages

### `mdx/` — ingestion transport
| Path | Purpose |
|---|---|
| `mdx/source/` | Input sources: `source_kafka`, `source_redis_stream`, `source_elasticsearch`, `source_base`. |
| `mdx/sink/` | Output sinks: `sink_kafka`, `sink_redis_stream`, `sink_base`. |
| `mdx/sink/vlm_enhanced_sink/` | Writes the **enriched** result to Kafka/Elasticsearch. |
| `mdx/protobuf/` | Generated NvSchema protobuf (`Behavior`, `Incident`). *Auto-generated — do not edit.* |
| `mdx/utils/elastic_ready.py` | Alert/incident fingerprints for dedup. |
| `mdx/event_bridge_factory.py` | Builds the configured source/sink. |

### `handlers/` — core handlers
| Path | Purpose |
|---|---|
| `handlers/alert_config/` | Per-alert-type config store (Redis + ES backends, cache, hydration, factory, service, `normalize`). |
| `handlers/prompt_handler/` | Prompt rendering + `alert_type_config` loading. |
| `handlers/enrichment/` | Enrichment processor. |
| `handlers/direct_media/` | Direct-media mode: downloader, analyzer, handler. |
| `handlers/exception_handler/` | Error handler + VSS exception types. |
| `handlers/async_*_mixin.py` | Async dispatch / external-IO / VLM-mode mixins for the orchestrator. |

### `schemas/` — data models
| Path | Purpose |
|---|---|
| `schemas/vlm_responses.py` | `VLMResponse` + model-type detection + parser registry. |
| `schemas/pluggable_parser_runtime.py`, `base_response_parser.py` | Pluggable response-parser machinery. |
| `schemas/request_entity/` | `AlertRequestEntity`, builder, validator. |
| `schemas/response_entity/` | `AlertResponseEntity`, `ResponseBuilder`. |
| `schemas/shared/` | Shared enums + exceptions. |
| `schemas/config/` | Request defaults loader. |

### `web/` — FastAPI app
| Path | Purpose |
|---|---|
| `web/main.py` | App assembly (routers, middleware, lifecycle). |
| `web/api/` | Routers: alerts, incidents, realtime, verification, alert-config, heartbeat. |
| `web/schemas/` | Pydantic request/response models for the API. |
| `web/core/` | `AlertSubmissionService` + dependencies (config / Redis). |
| `web/service/` | On-demand verification service. |
| `web/websocket/` | Connection manager, Redis consumer, WS routes/service. |

### `realtime/` — realtime rules
| Path | Purpose |
|---|---|
| `realtime/services/` | `realtime_service`, `always_on_service`, `incident_service`, `rule_store`, `rtvi_client` (RTVI VLM client). |
| `realtime/schemas/` | Alert-config + always-on-config schemas. |
| `realtime/config/` | Service config + constants. |

### `vss/` — VSS integration
| Path | Purpose |
|---|---|
| `vss/vss_handler.py`, `component_factory.py`, `retry_manager.py` | Verification orchestration + retries. |
| `vss/media_handler/` | Media upload / delete. |
| `vss/session_handler/` | Thread-safe session manager. |
| `vss/vss_request_handler/` | Alert-verification client. |
| `vss/workflow/` | Workflow executor. |

## Conventions

- **Imports are top-level** (`from <package>... import ...`); `src/` is placed on
  `sys.path` by the launcher (runtime) and `conftest.py` (tests).
- **`clients/`** holds connection wrappers; higher layers (`persistence/`,
  `handlers/alert_config/`, `web/`) compose on top of them.
- **`mdx/`** keeps its legacy name (NvSchema "anomaly" transport); `vst` = video
  storage client, `vss` = the VSS verification workflow — they are distinct.
