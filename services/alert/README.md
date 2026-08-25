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
| `src/web/` | REST API and on-demand verification service |
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
   - Health: `http://localhost:9080/health` (also served as `/ready`)
   - API docs (Swagger): `http://localhost:9080/docs`
   - OpenAPI spec: `http://localhost:9080/openapi.json`

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

### The knobs

Five keys control scaling. Each mode has exactly one throughput lever, so at
any moment only three of the five are in play:

| Key | Applies to | Meaning |
|---|---|---|
| `alert_agent.processes` | all modes | independent pipeline processes; the only way past ~1 CPU core |
| `alert_agent.pipeline_mode` | — | `sync` \| `thread_bridge` \| `event_loop` |
| `alert_agent.num_workers` | `sync` | batch worker threads — the throughput lever |
| `alert_agent.async_dispatch_workers` | `thread_bridge` | dispatch-pool threads — the throughput lever |
| `alert_agent.async_io.max_vlm_concurrent` | `event_loop` | VLM concurrency cap — the throughput lever |

Everything else is derived. The formulas below are the defaults; each key is
still honoured when set explicitly, so an existing tuned deployment keeps its
values:

```
async_dispatch_workers        = num_workers
async_dispatch_max_in_flight  = 2 × async_dispatch_workers
async_io.max_vst_concurrent   = async_dispatch_workers
async_io.sink_warn_in_flight  = async_dispatch_max_in_flight
```

`async_dispatch_max_in_flight` is the global in-flight bound for both async
modes: when full, hand-off pauses and backpressure reaches the Kafka consume
loop. It bounds memory; it does not raise the throughput ceiling.

Retired keys are ignored with a startup warning rather than failing the boot:
`alert_agent.chunk_size` (dispatch is per message in every mode) and the
`alert_agent.async_io` per-service switches `vst_enabled`, `elastic_enabled`,
`dedup_enabled`, `redis_enabled` (external I/O now follows the pipeline mode).
`alert_agent.async_io.enabled` is deprecated but still consulted when
`pipeline_mode` is unset — set `pipeline_mode` instead.

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

- **Kafka ingest ≈ 2,400 msg/s per instance** — one scheduling loop does
  poll → decode → dedup → commit. Extra worker threads share the same GIL, so
  they do not raise it.
- **VLM dispatch ≈ 30 req/s per instance** — in `event_loop` mode a single
  loop thread drives every VST / VLM / Elasticsearch call. Once that thread
  saturates, raising `max_vlm_concurrent` adds latency instead of throughput:
  the backend's service time is unchanged, but the coroutine cannot be resumed
  promptly after its response arrives.

**Check that you are actually near a ceiling before raising `processes`.**
Below them the GIL is not the binding constraint and extra processes buy
nothing but memory and consumer-group churn — a deployment sitting at 0.2 core
is limited by offered load or by `max_vlm_concurrent / VLM_latency`, not by
the GIL. The signal to look for is a *single* process pinned near 100% CPU
(`~1 core`) with `alert_bridge_vlm_duration_seconds` inflating above the VLM
backend's true service time while its concurrency cap is nowhere near
saturated. Both figures above are per-instance and workload-dependent;
re-measure against your own dependencies rather than treating them as
constants.

`alert_agent.processes` (positive integer, default `1`) forks that many
independent pipeline processes. Each child owns a complete stack — its own
consumers, its own event loop, its own clients — and its own GIL, which is
what actually lifts both ceilings.

```yaml
alert_agent:
  processes: 4
  pipeline_mode: event_loop     # required above 1
```

- **The count is validated, not adjusted.** Above `1`, startup fails unless
  the mode is `event_loop` and *every* source topic carries at least that many
  partitions. Per topic, not in total: each process runs one consumer per
  topic in the same group, so Kafka assigns each topic independently. Eight
  partitions on one topic and one on another total nine, which would pass any
  check on the sum, yet only one process can ever hold the second topic. Nothing is clamped or derived: a count the
  deployment cannot honour is a configuration error, and silently running
  fewer processes than asked hides it. There is no `"auto"` — deriving from
  the CPU count read well but hid the constraint that actually binds, and on
  a 256-core host with 8 partitions it produced 248 children that could never
  receive a partition and still cost ~140 MiB each.
- **Shared alert-config storage is required above 1.** With
  `persistence.enabled: false` the store is private to each process, so no
  supervisor can initialise one the workers will read. Startup refuses the
  combination rather than running N stores that drift apart.
- **`event_loop` is required above 1** because the other modes hold their
  concurrency in threads. Several processes then multiply the load offered to
  the VLM and VST backends by the process count, without the per-process caps
  that bound it.
- **Startup waits for the topics before validating.** The partition count is
  read from the broker, retried while the topics do not yet exist, and only
  then compared. Compose gates the container on the topic-init container and
  never spends that wait; on Kubernetes the topics come from a Job with no
  ordering against the Deployment, so a first install would otherwise fail
  a perfectly good configuration.
- **Across replicas the constraint is `replicas × processes ≤ partitions`.**
  Consumer-group members are pods *and* processes: 2 replicas × 4 processes
  needs 8 partitions, and 3 × 4 on 8 partitions leaves 4 members idle. A pod
  cannot see the replica count, so only the per-pod rule is enforced; the
  idle members still report themselves ready, because a member that has been
  told it owns nothing has been told, and reporting otherwise would leave a
  correctly-running rollout permanently unhealthy. `alert_bridge_assigned_partitions`
  is where that shows up. Scale partitions before scaling either dimension.
- **`/health` carries the fleet.** It is 503 until every pipeline process
  holds a decided assignment, and again for the length of each rebalance.
  `/ready` answers the same, for deployments that prefer the conventional
  name. A multi-process instance reports no ready pipelines for the whole of
  its startup, so give a startup probe a failure threshold that covers
  `alert_agent.startup_timeout_seconds` rather than the default. **Do not
  point a liveness probe at it.** It reports whether this instance is serving
  its partitions, not whether the process is alive, so a liveness probe
  restarts the container for every rebalance -- at any process count,
  including one.
- **Shutdown needs 60s of container grace, and the budget differs by process
  count.** The shipped profiles set it (`stop_grace_period` on Compose,
  `terminationGracePeriodSeconds` from `values.yaml` on Kubernetes). Docker's
  own default is 10s, which SIGKILLs the parent mid-teardown and cuts
  in-flight work rather than finishing it.
  - At `processes: 1` and `pipeline_mode: event_loop`, which is what every
    shipped profile sets, **there is no supervisor**, and this is the longer
    of the two paths: up to 10s to terminate and join the
    API child, then up to 15s of drain, then up to 5s to close the event-loop
    HTTP and Elasticsearch clients, then up to 15s to stop the runtime. 45s
    bounded. Leaving the consumer group delivers one more revoke, but it
    inherits the drain budget above rather than opening a second one.
  - Above one process the supervisor owns the timeline: drain at T+15,
    terminate at T+18, kill at T+19, finished by T+20. The API child is reaped
    inside that timeline rather than after it, so this path is the shorter of
    the two. Cutting it short means the children die by `PR_SET_PDEATHSIG`
    with none of the supervisor's exit accounting run.

  In the other modes the arithmetic does not hold at all: `thread_bridge`
  and `sync` shut their executors down with an unbounded wait that runs
  before the drain window is even opened. Nothing shipped selects them, and
  more than one process requires `event_loop`.

  What is still unbounded, and all of it predates this work: the consumer's
  own close, which commits and leaves the group against a coordinator that
  may be gone; the webhook forwarder's Kafka close; the notifier's thread
  pool; the sink's close; and
  -- the one that lands last -- the async runtime's `ab-vlm-io` executor.
  Its workers are not daemons, so the interpreter joins them on the way out,
  after `main()` has returned and after the line that says shutdown is
  complete; and when `stop()` has to force the loop down, the runtime's own
  attempt to shut that executor raises and the pool is never closed at all.
  A blocking call that will not return -- a VST download on a half-open
  socket, say -- holds the process there for as long as it takes. 60s covers
  everything that is bounded, with margin, but no grace period is a promise
  while those five can wait indefinitely.
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
  `processes ×` its benchmarked concurrency. This is the most common way to
  break a working deployment by enabling `processes`: leaving
  `max_vlm_concurrent: 8` and setting `processes: 4` offers the VLM backend 32
  concurrent requests, well past the ceiling the
  [benchmark below](#vlm-concurrency-ceiling-benchmark-run-before-raising-max_vlm_concurrent)
  establishes. Startup logs the resulting instance totals at WARNING for
  exactly this reason — every other concurrency log line is per-process.
- **The parent runs no pipeline.** It performs the VLM warmup once, serves the
  Prometheus scrape endpoint, owns the FastAPI child, and supervises. It never
  joins the consumer group — a member that stopped polling would stall the
  partitions assigned to it.
- **A crashed child takes the instance down.** The supervisor terminates and
  reaps the others and exits non-zero, leaving the restart to the
  orchestrator. Replacing the dead child in place kept the container alive
  around a partially rebuilt instance — the replacement rejoins the group and
  forces a rebalance, the survivors keep whatever work they had, and whatever
  caused the exit is still there. It also reported success on the way out, so
  a crash-looping deployment read as a clean finish.
- **A rebalance drains before it hands a partition over.** Dedup state is held
  in the process that made it, so a member still finishing a cohort while
  another starts one for the same sensor can publish twice. The outgoing
  member waits for what it owes on the partitions being taken away, bounded at
  15 s so it cannot overrun the poll interval and lose its place in the group.
  This closes an overlap, not a durability gap: offsets are committed when
  records are read, so a process that dies loses what it held regardless.
  Watch `alert_bridge_rebalance_drains_total{outcome="timed_out"}` — the bound
  has been exercised against a stubbed backend, where a drain finished in
  under half a second, and a real VLM is slower by orders of magnitude.
  One expected source of that counter is shutdown itself: the revoke that
  leaving the group delivers shares the shutdown drain's window rather than
  opening its own, so a restart with work still running records a timeout
  here where it used to record a clean drain. Alert on the rate during
  steady state, not on restarts.
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

Size the expected win honestly: `Consumer.commit()` defaults to
`asynchronous=True` and the call site does not override it, so the per-message
commit was never a blocking broker round-trip. What batching removes is the
per-message Python call and the offset-commit request volume to the broker,
not a stall. Treat it as a per-core efficiency change to be measured on the
deployment, not an assumed throughput multiplier.

**It does not make the pipeline at-least-once.** The batch is flushed inside
`get_consumed_messages`, before `read_data()` returns and therefore before any
message is handed to the worker pool. A message that reached dispatch is
already committed under either setting, and dies with its process. What
batching opens is a redelivery window of exactly **one poll batch**, entered
only when the crash lands inside the poll loop itself — bounded by the time to
drain up to `max_poll_records` messages. Both settings lose in-flight
dispatched work on a crash; neither replaces an idempotent sink or an
end-to-end retry if that loss matters.

Measured on the simulator harness, enabling it *increased* the number of
in-flight messages lost when a pipeline child was killed mid-batch — 5 against
10, then 5 against 15, across two runs. The cause is unlikely to be the commit
boundary, since the batch is flushed before any message is dispatched either
way; the plausible mechanism is throughput, in that dropping a commit call per
message lets the consume loop admit work faster, so more of it is in flight at
any instant. Two samples with uncontrolled kill timing cannot size that effect,
but the direction held in both. Worth knowing before enabling it in the belief
that batching makes a crash cheaper: on this evidence it makes it dearer.

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

Enriched results are persisted to Elasticsearch and published to the Kafka
sink (`event_bridge.sinkType: kafka`). Consumers receive alerts by subscribing
to the configured sink topic, and can also query stored alerts/incidents over
the REST API (e.g. `GET /api/v1/realtime`, `GET /api/v1/realtime/incidents`).

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
  NVIDIA Software License Agreement.** If you pull and use NVIDIA's pre-built container
  images, the NVIDIA Software License Agreement governs your use; the agreement is conveyed by the
  distribution channel those images ship through.

Third-party open-source components bundled in the container image are attributed in
[`LICENSE-3rd-party.txt`](./LICENSE-3rd-party.txt).

The container image carries `LICENSE-3rd-party.txt` and `NVIDIA-Software-License-Agreement.pdf`
under `/app`. The agreement is **not** vendored in this source tree — the Dockerfile's `ADD` instruction
fetches it from `nvidia.com` at build time with a pinned SHA-256, which keeps the repository free
of a proprietary EULA and needs no HTTP client in any build stage.
