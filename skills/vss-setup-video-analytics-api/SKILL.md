---
name: vss-setup-video-analytics-api
description: >
  Deploy the `vss-video-analytics-api` service standalone — no perception, no behavior-analytics, no UI.
  Use when the user says "deploy video analytics api", "run video-analytics-api standalone",
  "set up the REST API service", "change the API config", "swap the video-analytics-api config",
  "run the API against my own Elasticsearch", "point the API at a different broker", or wants to
  bring up the REST API layer without redeploying the full warehouse blueprint. Walks the user
  through config selection, data-log volume, infrastructure dependencies (Elasticsearch / Kafka),
  and deploy + verify.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational deployment video-analytics-api rest-api"
---

# VSS Setup Video Analytics API — Standalone

Deploy **just** the `vss-video-analytics-api` container (the Node.js REST API from the upstream `video-analytics-api` repo), not as part of the full warehouse blueprint stack.

The full operational walkthrough — config options, infrastructure dependencies, REST API endpoints, deploy + verify, troubleshooting — is [`references/deploy-video-analytics-api-service.md`](references/deploy-video-analytics-api-service.md). This SKILL.md only handles routing and prerequisites.

## When to use

- "Deploy video analytics api" / "run video-analytics-api standalone"
- "I just want to run the REST API, not the full stack"
- "Use my own video-analytics-api config"
- "Point the API at a different Elasticsearch / Kafka"
- "Start the API without Kafka" / "run the API broker-less"
- "Check what REST endpoints are available"

## Prerequisites

1. **Repo checkout** with `$VSS_APPS_DIR` pointing at `<repo>/deploy/docker/`. Required by the service compose's volume binds.
2. **NGC credentials** — `$NGC_CLI_API_KEY` set so docker can pull the image. See [`../vss-deploy-profile/references/ngc.md`](../vss-deploy-profile/references/ngc.md).
3. **Docker runtime** — Docker Engine **28.3.3** with Docker Compose plugin **v2.39.1+**. Verify with `docker --version` and `docker compose version`.
4. **Elasticsearch** — must be reachable at the URL configured in `elasticsearch.node`. The server pings ES on startup; if unreachable, it exits (and `restart: always` brings it back). If you need to bring up ES too, use the infra compose: `docker compose -f services/infra/compose.yml up -d elasticsearch`.
5. **Optional Kafka broker**. The API starts fine without Kafka — Kafka-dependent features (dynamic config, dynamic calibration, RTLS/AMR) are simply unavailable.
6. **Optional `$VSS_DATA_DIR`** for file upload endpoints (sensor images, calibration files uploaded via REST).

If any required prerequisite fails, surface the gap before going further.

## Workflow

Hand the user [`references/deploy-video-analytics-api-service.md`](references/deploy-video-analytics-api-service.md) and walk them through its steps in order:

1. Choose a config — image-baked default, service-shipped, or custom.
2. Decide whether a data-log volume is needed for file uploads.
3. Confirm infrastructure dependencies — Elasticsearch (required), Kafka (optional).
4. Deploy + verify with `docker compose up` and health check.

The compose-file edits, config options, deploy + verify commands, REST API endpoint table, and troubleshooting table all live in that reference — don't duplicate them here.

## REST API capabilities

Once the container is up and Elasticsearch is reachable, the API serves these endpoint groups:

| Endpoint | What it does |
|---|---|
| `/livez` | Health check — returns 200 when routes are registered and ES ping succeeded. |
| `/sensor` | CRUD for sensor metadata (GET / POST / DELETE), supports file uploads. |
| `/config` | Dynamic config management — GET retrieves current config; POST publishes config updates to Kafka. |
| `/behavior` | Query behavior data from Elasticsearch. |
| `/alerts` | Query alert data with time-range and sensor filters. |
| `/events` | Query event data from Elasticsearch. |
| `/incidents` | Query incident data from Elasticsearch. |
| `/frames` | Query frame-level data from Elasticsearch. |
| `/metrics` | Aggregation / computation metrics (occupancy, behavior metrics). |
| `/tracker` | Tracker data queries. |
| `/clustering` | Clustering analysis queries. |

All endpoints except `/livez` require Elasticsearch. Endpoints that publish notifications (config, calibration) also require Kafka.

## Kafka-dependent features (runtime, requires broker)

Once the container is up **and a Kafka broker is reachable**, three additional capabilities are available:

### Dynamic config

The API acts as the **producer** for dynamic config updates. When an operator POSTs to `/config`, the API publishes an `upsert` message to the `mdx-notification` topic with Kafka key `behavior-analytics-config`. The downstream `behavior-analytics` container consumes this and ACKs back. The API also handles the bootstrap flow — when `behavior-analytics` starts, it publishes a `request-config` message, and the API replies with `upsert-all` containing the latest verified config from Elasticsearch.

### Dynamic calibration

The API produces calibration update notifications on `mdx-notification` with Kafka key `calibration`. Supports `upsert-all` (full snapshot), `upsert` (per-sensor merge), and `delete` (per-sensor removal). The downstream `behavior-analytics` container consumes these and applies them to the live calibration.

### RTLS / AMR

The API consumes real-time location (`mdx-rtls`) and AMR (`mdx-amr`) messages from Kafka and exposes them via REST endpoints.

## Routing rules

- If the user wants "the full stack" (UI / agent / perception): hand off to [`vss-deploy-profile`](../vss-deploy-profile/SKILL.md) with profile `warehouse` (or `alerts`). Don't run this skill in parallel.
- If the user wants to deploy the analytics pipeline (behavior creation, incident detection): hand off to [`vss-setup-behavior-analytics`](../vss-setup-behavior-analytics/SKILL.md).
- If the user wants to understand the dynamic config / dynamic calibration wire contract from the **consumer** (behavior-analytics) side: point them at [`../vss-setup-behavior-analytics/references/dynamic-config.md`](../vss-setup-behavior-analytics/references/dynamic-config.md) and [`../vss-setup-behavior-analytics/references/dynamic-calibration.md`](../vss-setup-behavior-analytics/references/dynamic-calibration.md).
- If the user wants to query or interact with the REST API endpoints: the endpoint table above and the deploy reference cover what's available. For the full OpenAPI spec, see `src/app/specification/openapi.json` in the `video-analytics-api` repo.
