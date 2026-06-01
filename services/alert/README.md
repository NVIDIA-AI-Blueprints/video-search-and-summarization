# Alerts Microservice

**A modular, configuration-driven Alerts microservice for the Video Search and
Summarization (VSS) blueprint — VLM-based alert verification, realtime alert
generation, and on-demand clip verification.**

## Overview

The Alerts Microservice processes alerts and incidents produced by the VSS pipeline and
uses a Vision-Language Model (VLM) to confirm, classify, and enrich them. It
supports three modes:

- **Alert verification** (primary) — alerts generated upstream by real-time CV
  detection and behavior analytics are reviewed by a VLM to reduce false
  positives. For each alert, the service resolves the corresponding video
  segment from the video service using the sensor ID and alert timestamps,
  renders an alert-type-specific prompt, and sends the clip to a VLM backend
  over an OpenAI-compatible API. It returns a structured verdict (confirmed /
  rejected / unverified) with a reasoning trace.
- **Realtime alerts** — register realtime alert rules that run continuous VLM
  processing over input streams (including "always-on" refinement); generated
  alerts are published over Kafka.
- **On-demand verification** — third-party CV applications can request VLM
  verification of a stored video snippet.

Alerts use the NvSchema `nv.Incident` / `nv.Behavior` formats (JSON or
Protobuf) and are ingested over **Kafka** or the **HTTP API**. Verified results
are persisted to **Elasticsearch** and can optionally be re-published to Kafka.
The VLM backend is pluggable — an OpenAI-compatible endpoint such as an NVIDIA
VLM NIM (e.g. Cosmos Reason), the RTVI VLM microservice, or a remote model
endpoint.

## Project Structure

| Path | Purpose |
|------|---------|
| `enhance_alert_with_vlm.py` | Alert-verification pipeline orchestrator (entrypoint) |
| `handlers/` | Alert-type config (RedisJSON), direct-media, and prompt handling |
| `vlm/` | VLM client (OpenAI-compatible) and warmup |
| `models/`, `entity_management/` | NvSchema request/response schemas and pluggable response parsers |
| `realtime/` | Realtime + always-on alert rules and the RTVI VLM client |
| `alert-agent-web/` | REST + WebSocket API and on-demand verification service |
| `persistence/` | Elasticsearch + Redis stores |
| `mdx/` | Alert ingestion sources/sinks (Kafka, Redis, Elasticsearch) |
| `blueprint_config/` | Example configs for the warehouse / public-safety / smart-city blueprints |
| `test/` | Unit, functional, and end-to-end tests (see `test/TEST_README.md`) |

## Prerequisites

- Python 3.12+
- Docker and Docker Compose
- A reachable OpenAI-compatible **VLM backend** (configured in `config.yaml`)
- **Redis** (can be started via the provided compose file)
- Depending on your source/sink choice: **Kafka** and/or **Elasticsearch**

## Installation

```bash
pip install -r requirements.txt
```

Or build/run with Docker (see Quick Start).

## Quick Start

1. **Configure** — edit `config.yaml`: set the VLM `base_url`/`model`, the
   source/sink type (`kafka`, `redisStream`, or `elasticsearch`), and the
   Redis/Kafka/Elasticsearch endpoints. Optionally override request defaults in
   `alert_request_defaults.yaml` (or point `ALERT_AGENT_DEFAULTS_FILE` at a
   custom file).

2. **Start the stack** (Redis source/sink is the default):

   ```bash
   docker compose -f deploy_docker-compose.yml up -d

   # or with a custom config file
   ALERT_BRIDGE_CONFIG_FILE=./your-config.yaml docker compose -f deploy_docker-compose.yml up -d
   ```

3. **Verify** — the service is available at:
   - Health: `http://localhost:9080/health`
   - API docs (Swagger): `http://localhost:9080/docs`
   - OpenAPI spec: `http://localhost:9080/openapi.json`
   - WebSocket: `ws://localhost:9080/ws`

To run the verification pipeline directly (without Docker):

```bash
python enhance_alert_with_vlm.py --config config.yaml
```

## Configuration

`config.yaml` controls the runtime. Key sections:

- **`vlm`** — `base_url` (OpenAI-compatible VLM endpoint), `model`, generation params.
- **source / sink** — `kafka`, `redisStream`, or `elasticsearch`.
- **persistence / elastic** — Elasticsearch host for durable storage.

Per-alert-type verification prompts and VLM parameters are seeded from
`alert_type_config.json` and stored in RedisJSON (`alert_config:{alert_type}`).
They can be managed at runtime via the Verification Config API
(`POST/PUT/GET /api/v1/verification/config[/{alert_type}]`); the pipeline reads
through to the store on each VLM call, so updates apply without a restart.

## Usage

Submit an alert over the REST API:

```bash
curl -X POST http://localhost:9080/api/v1/alerts \
  -H "Content-Type: application/json" \
  -d @test/protobuf/test_data/sample_alert.json
```

Enriched results are persisted and broadcast over the WebSocket endpoint.

## Testing

Unit tests run with `pytest`:

```bash
pip install -r requirements.txt
pytest
```

For functional and end-to-end testing against local simulators (Redis/Kafka
profiles, sending sample payloads, verifying responses), see
[`test/TEST_README.md`](test/TEST_README.md).

## Contributing

Contributions are welcome. Please see the repository root
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the contribution process, the
required SPDX license headers, and the DCO sign-off requirement.

## License

The Alerts Microservice is licensed under the Apache License, Version 2.0 — see
the repository root [`LICENSE`](../../LICENSE). Third-party dependency licenses
are listed in [`LICENSE-3rd-party.txt`](LICENSE-3rd-party.txt).
