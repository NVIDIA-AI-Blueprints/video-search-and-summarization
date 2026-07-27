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

> **No Redis required.** Earlier releases used Redis for dedup/filter
> caching and alert-config storage. That dependency has been removed:
> deduplication, the end-time delta filter and the (optional) rate limit
> run as **in-process** state per consumer, while confirmed-verdict
> protection and alert-type configs are stored in **Elasticsearch**.
> Because `mdx-incidents` is partitioned by `sensorId`, every event for a
> dedup cohort is routed to the same consumer, so no cross-pod
> coordination — and therefore no shared cache — is needed. Multi-replica
> deployments work unchanged: each pod owns its Kafka partitions and keeps
> its own in-process state; on restart/rebalance the pod taking over
> rebuilds state from new events (verdict protection survives via ES). The
> same holds within a pod for `alert_agent.processes > 1` (see
> [Multi-core scaling](#multi-core-scaling-alert_agentprocesses)).

## Project Structure

All importable packages live under `src/` (see [`src/README.md`](src/README.md)
for a detailed layout + data-flow diagram).

| Path | Purpose |
|------|---------|
| `enhance_alert_with_vlm.py` | Alert-verification pipeline orchestrator (entrypoint, repo root) |
| `src/handlers/` | Alert-type config (Elasticsearch-backed), direct-media, and prompt handling |
| `src/vlm/` | VLM client (OpenAI-compatible) and warmup |
| `src/schemas/` | NvSchema request/response entities, VLM response model, and pluggable response parsers |
| `src/realtime/` | Realtime + always-on alert rules and the RTVI VLM client |
| `src/web/` | REST + WebSocket API and on-demand verification service |
| `src/vst/` | VST video-clip resolution (sensor ID + timestamps) |
| `src/clients/` | Elasticsearch client + in-process dedup/verdict-protection state handler |
| `src/persistence/` | Elasticsearch persistence store |
| `src/mdx/` | Alert ingestion sources/sinks (Kafka, Elasticsearch) |
| `blueprint_config/` | Example configs for the warehouse / public-safety / smart-city blueprints |
| `test/` | Unit, functional, and end-to-end tests (see `test/TEST_README.md`) |

## Prerequisites

- Python 3.12+
- Docker and Docker Compose
- A reachable OpenAI-compatible **VLM backend** (configured in `config.yaml`)
- **Elasticsearch** (durable storage for alert configs + confirmed-verdict protection)
- Depending on your source/sink choice: **Kafka** and/or **Elasticsearch**
- No **Redis** instance is required.

## Installation

```bash
pip install -r requirements.txt
```

Or build/run with Docker (see Quick Start).

## Quick Start

1. **Configure** — edit `config.yaml`: set the VLM `base_url`/`model`, the
   Kafka/Elasticsearch endpoints, and the sink type. Optionally override
   request defaults in `alert_request_defaults.yaml` (or point
   `ALERT_AGENT_DEFAULTS_FILE` at a custom file). Dedup / end-time-delta /
   verdict-protection tuning lives under `alert_agent.event_filters`.

2. **Start the stack** (Kafka source/sink is the default; no Redis):

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

## Observability

Set `PROMETHEUS_METRICS_ENABLED=true` before starting the service to expose
Prometheus metrics at `http://localhost:9081/metrics`. Kafka pipeline metrics
use the existing `alert_bridge_*` event and latency series. Requests accepted
through `POST /api/v1/verification/ondemand` use a separate
`alert_bridge_ondemand_*` family for request outcomes, completed-event verdicts,
VLM/background/request-to-publish latency, and verification failures.

The scrape endpoint is not a Prometheus query server: configure Prometheus to
scrape port 9081, then use the reporting tool documented in
[`test/latency/README.md`](test/latency/README.md). On-demand metrics are
aggregate-only; `alert_agent.metrics.per_sensor_labels` applies to Kafka
pipeline metrics.

## Configuration

`config.yaml` controls the runtime. Key sections:

- **`vlm`** — `base_url` (OpenAI-compatible VLM endpoint), `model`, generation params.
- **source / sink** — `kafka` (ingestion) and `elasticsearch`/`kafka` (output sink).
- **persistence / elastic** — Elasticsearch host for durable storage.

Per-alert-type verification prompts and VLM parameters are seeded from
`alert_type_config.json` and stored in **Elasticsearch** (index
`ab-alert_configs`). They can be managed at runtime via the Verification
Config API (`POST/PUT/GET /api/v1/verification/config[/{alert_type}]`); the
pipeline reads through to Elasticsearch on each VLM call (an in-process cache
is read-through by default), so updates apply without a restart. Set
`persistence.cache_ttl_seconds > 0` to cache config reads at the cost of
bounded cross-process staleness.

## Pipeline modes & concurrency sizing

`alert_agent.pipeline_mode` selects how per-message processing is dispatched
(invalid values fail startup; unset derives from the legacy
`async_io.enabled` flag):

| Mode | Dispatch | VLM concurrency ceiling | Use |
|---|---|---|---|
| `sync` | inline in the batch worker | `num_workers` | default / rollback |
| `thread_bridge` | dispatch thread pool, blocking wait | `async_dispatch_workers` | legacy async mode, rollback |
| `event_loop` | coroutine-per-message on one persistent loop; async clients per stage (VLM `AsyncOpenAI`, VST `httpx`, sink/verdict `AsyncElasticsearch`) | `async_io.max_vlm_concurrent` | non-blocking mode: Kafka consumption decoupled from VLM latency |

Knob meaning per mode:

- `async_dispatch_workers` — thread_bridge: dispatch-pool thread count (the
  throughput lever). event_loop: no pool is created; the value only serves as
  the default for the per-service caps.
- `async_dispatch_max_in_flight` — both async modes: global in-flight bound;
  when full, hand-off pauses and backpressure reaches the Kafka consume loop.
  It bounds memory, it does not raise the throughput ceiling.
- `async_io.max_vlm_concurrent` / `max_vst_concurrent` — event_loop only:
  per-service concurrency caps (asyncio semaphores).

Sizing rule (event_loop): size against the **survivor rate** (events that
pass dedup and reach the VLM — `rate(alert_bridge_events_after_dedup_total)`,
peak value), not raw ingest:

```
max_vlm_concurrent ≈ peak_survivor_rate × VLM_latency_p95 × 1.4 (headroom)
async_dispatch_max_in_flight ≈ 2–4 × max_vlm_concurrent
```

The sustainable rate ("knee") is `max_vlm_concurrent ÷ VLM_latency`; below it
consumer lag stays flat, above it lag grows by design (bounded backpressure).

### Multi-core scaling (`alert_agent.processes`)

The pipeline modes above all run inside **one** Python process, and the GIL
lets only one thread execute bytecode at a time. A single instance therefore
uses at most ~1 core no matter how many `num_workers` threads are configured
or how many cores the host has, and two ceilings follow from that:

- **Kafka ingest** — one scheduling loop does poll → decode → dedup → commit.
  Extra worker threads share the same GIL, so they do not raise it.
- **VLM dispatch** — in `event_loop` mode a single loop thread drives every
  VST / VLM / Elasticsearch call. Once that thread saturates, raising
  `max_vlm_concurrent` adds latency instead of throughput: the backend's
  service time is unchanged, but the coroutine cannot be resumed promptly
  after its response arrives.

`alert_agent.processes` (integer, or `"auto"` for one per available CPU;
default `1`) forks that many independent pipeline processes. Each child owns a
complete stack — its own consumers, its own event loop, its own clients — and
its own GIL, which is what actually lifts both ceilings.

```yaml
alert_agent:
  processes: 4        # or "auto"
```

- **Effective parallelism is `min(processes, partition_count)`.** Children
  beyond the partition count join the consumer group and idle. Raise the
  partition count on `mdx-incidents` alongside `processes`.
- **No shared state is needed.** `mdx-incidents` is partitioned by `sensorId`
  and every dedup cohort key is prefixed with it, so Kafka routes a whole
  cohort to one partition and therefore to exactly one child; confirmed-verdict
  protection is Elasticsearch-backed and survives restart and rebalance. This
  is the same argument that already makes multi-replica deployments safe,
  applied within a host.
- **Per-service caps are per process.** `max_vlm_concurrent`,
  `async_dispatch_max_in_flight` and `num_workers` are unchanged in meaning,
  but the instance-wide ceiling becomes `processes × cap`. Size the VLM cap
  against `peak_survivor_rate / processes` so the backend does not see
  `processes ×` its benchmarked concurrency.
- **The parent runs no pipeline.** It performs the VLM warmup once, serves the
  Prometheus scrape endpoint, owns the FastAPI child, and supervises. It never
  joins the consumer group — a member that stopped polling would stall the
  partitions assigned to it.
- **Crashed children are restarted** in place, so their partitions resume
  without waiting for a rebalance. A slot that keeps dying within 60 s is a
  config or dependency failure: after 5 such restarts the supervisor gives up
  and exits, surfacing the error instead of hiding it behind restart noise.
- **Metrics aggregate automatically.** Children inherit
  `PROMETHEUS_MULTIPROC_DIR` and the parent scrapes with
  `MultiProcessCollector`, so `:9081` stays the single endpoint. Counters and
  histograms sum, and the in-flight gauges (`dispatch_in_flight`,
  `event_loop_vlm_in_flight`, `event_loop_vst_in_flight`, `async_sink_in_flight`)
  are `livesum`, so they are instance totals. `alert_bridge_dedup_cache_occupancy`
  is the exception: it stays `livemostrecent` and reads as a per-process
  sample, because its `dedup`/`enddelta` stores are partitioned across
  processes while `alert_config` is replicated in each of them.

### Crash and replay semantics (`kafka.batch_commit`)

Offsets are committed at poll time, before dedup, VST and VLM. That makes the
pipeline **at-most-once**: a message lost to a crash after the poll is gone
permanently, with no replay. TS-014 in the capability suite asserts exactly
this.

`kafka.batch_commit` (default `false`) instead records the highest offset per
partition and commits once per poll batch, removing one commit call per
message from the GIL-bound consume thread. Committing the highest offset is
equivalent to committing each in turn — offsets are monotonic within a
partition and every intermediate message is already in the returned batch.

**It does not make the pipeline at-least-once.** The batch is flushed inside
`get_consumed_messages`, before `read_data()` returns and therefore before any
message is handed to the worker pool. A message that reached dispatch is
already committed under either setting, and dies with its process. What
batching opens is a redelivery window of exactly **one poll batch**, entered
only when the crash lands inside the poll loop itself — bounded by the time to
drain up to `max_poll_records` messages. Both settings lose in-flight
dispatched work on a crash; neither replaces an idempotent sink or an
end-to-end retry if that loss matters.

Within that window duplicates are possible, so enable it only where they are
tolerated:

- In-process dedup collapses identical cohorts inside
  `alert_agent.event_filters.dedup_ttl_seconds` (default 300 s), but only when
  the replay lands on the process that already saw the original. It will not,
  if that process is the one that died — its cache went with it.
- Confirmed-verdict protection (`protect_confirmed_verdicts`) is
  Elasticsearch-backed, so it is the only suppression that survives a process
  death or a rebalance.
- Anything downstream of the sink that is not idempotent will see duplicates.

The default keeps today's at-most-once behavior, so the two semantics can be
adopted per deployment rather than flag-day. TS-014 asserts the at-most-once
baseline; TS-033 measures the loss and replay counts across a hard kill in
both modes.

### VLM concurrency ceiling benchmark (run before raising `max_vlm_concurrent`)

`max_vlm_concurrent` must never exceed what the VLM backend actually serves
concurrently — beyond that point requests queue inside the backend and its
latency balloons instead of throughput improving. To find the ceiling:

1. Deploy the VLM backend as in production (same GPU, memory-utilization and
   batching settings).
2. Ramp offered concurrency stepwise (e.g. 2 → 4 → 8 → 16 …), ≥60 s per step,
   using representative clips.
3. At each step record wait-excluded per-call latency
   (`alert_bridge_vlm_duration_seconds`, with capacity-wait tracked
   separately in `alert_bridge_capacity_wait_seconds`).
4. The ceiling is the last step where per-call latency stays within ~120% of
   the low-concurrency baseline; the next step marks saturation.
5. Set `max_vlm_concurrent` at or below the ceiling. Watch
   `alert_bridge_event_loop_vlm_in_flight` (never exceeds the cap) and
   capacity-wait growth (backpressure building) in production.

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

For functional and end-to-end testing against local simulators (Kafka +
Elasticsearch, sending sample payloads, verifying responses), see
[`test/TEST_README.md`](test/TEST_README.md).

Sustained-load capability checks and the multi-core scaling suite (rate ramp,
child crash/restart, message-loss checks under overload) live in
[`test/functional/capability/`](test/functional/capability/README.md) and need
no GPU.

## Contributing

Contributions are welcome. Please see the repository root
[`CONTRIBUTING.md`](../../CONTRIBUTING.md) for the contribution process, the
required SPDX license headers, and the DCO sign-off requirement.

## License

This module is governed by **two separate licenses**, depending on what you use:

- **The source code in this directory and its subdirectories is licensed under the Apache License,
  Version 2.0.** The full license text is at the repository root: [`LICENSE`](../../LICENSE). If you
  clone, build, modify, or redistribute the source, Apache 2.0 terms apply.

- **The pre-built VSS Alert container images distributed by NVIDIA via NGC**
  (`nvcr.io/nvidia/blueprint/vss-alert-verification` and related tags) **are licensed under the
  NVIDIA Software License Agreement.** The full agreement is included in this directory as
  [`NVIDIA-Software-License-Agreement.pdf`](./NVIDIA-Software-License-Agreement.pdf). If you pull and
  use NVIDIA's pre-built container images, the NVIDIA Software License Agreement governs your use.

Third-party open-source components bundled in the container image are attributed in
[`LICENSE-3rd-party.txt`](./LICENSE-3rd-party.txt).

The presence of `NVIDIA-Software-License-Agreement.pdf` in this directory does **not** modify the
Apache 2.0 license that governs the source code in this repository. It is included here so that the
pre-built container images carry the license they ship under.
