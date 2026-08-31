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
| `src/mdx/` | Alert ingestion sources/sinks (Kafka, Redis Streams, Elasticsearch, console) |
| `blueprint_config/` | Example configs for the warehouse / public-safety / smart-city blueprints |
| `test/` | Unit, functional, and end-to-end tests (see `test/TEST_README.md`) |

## Prerequisites

- Python 3.12+
- Docker and Docker Compose
- A reachable OpenAI-compatible **VLM backend** (configured in `config.yaml`)
- **Elasticsearch** (durable storage for alert configs + confirmed-verdict protection)
- Depending on your source/sink choice: **Kafka** and/or **Elasticsearch**
- **Redis** only if you opt into the Redis Streams transports (see
  [Event bridge transports](#event-bridge-transports)). Alert MS keeps no state
  in Redis and never deploys it.

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
  a perfectly good configuration. A source that has no partitions to wait for
  skips the wait rather than exhausting it: with a `redisStream` source there is
  no topic to appear, so the retry loop only delayed startup by its full deadline
  and ate the window the VLM warm-up and the consumer-group join needed.
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

## Event bridge transports

Three transport selections are made independently, so a deployment can move one
of them off Kafka without touching the others. Kafka is the default source and
sink, and Elasticsearch the default for VLM-enhanced results, so a config that
sets none of these behaves exactly as before.

| Setting | Default | Alternatives | Carries |
|---------|---------|--------------|---------|
| `event_bridge.sourceType` | `kafka` | `redisStream` | Incoming Alert and Incident payloads |
| `event_bridge.sinkType` | `kafka` | `redisStream`, `console` | Validation-error responses |
| `vlm_enhanced_sink.type` | `elastic` | `kafka`, `redisStream`, `console` | VLM-verified Alert and Incident results |

Transport names are matched case-insensitively and ignore `_` and `-`, so
`redisStream`, `redis_stream` and `redis` all select the same implementation. The
resolved name is logged next to the configured one at startup, which is the
quickest way to confirm a value was understood as intended.

One VLM-enhanced sink serves both incidents and alerts, so `vlm_enhanced_sink.type`
is read only at that top level. A `type` nested under `incident:` or `alert:` has
never been read; older configs carry one, and the service now warns when it finds
one that disagrees with the transport actually in use. The per-kind blocks carry
routing — index, stream, topic — not transport selection.

Selecting a `redisStream` transport requires an existing Redis instance —
Alert MS does not deploy one, and none of the service's own state lives there
(dedup state is in-process, durable state is in Elasticsearch). The connection
comes from the top-level `redis` block, the analogue of
`kafka.bootstrap_servers`; the per-component blocks (`event_bridge.redis_source`,
`event_bridge.redis_sink`, `vlm_enhanced_sink.redisStream`) hold the stream names
and may override any connection field. `config.yaml` carries a commented example
of each.

Stream names are required, not defaulted, in the two places where guessing one
produces a service that runs and delivers nothing:

* `event_bridge.redis_source.streams` must name **both** `incident` and `alert`
  (`anomaly` is read as `alert`). Both kinds are produced upstream and verified
  by the same pipeline, so a map naming one is a config that lost a line — and
  the service it produced consumed half its traffic while reporting healthy.
* each `vlm_enhanced_sink.<kind>.redisStream.stream` must be set when that sink
  is selected. There is no default: substituting one publishes verdicts to a
  name the deployment never gave, which nobody is reading.

Both are rejected before anything starts, and a blank value counts as absent —
that is what a rendered config produces for an unset variable.

A key that is *not* one of the names a section reads is rejected there too,
rather than ignored. Ignored is indistinguishable from absent, and absent on the
sink side means "do not publish that kind" — so one misspelt key disabled a whole
route while the sink reported healthy. The error names the keys the section does
accept.

Payloads use the MDX stream envelope — `XADD <stream> * key <sensorId> value
<payload> headers <json>` — which is what vss-behavior-analytics publishes and
what the Logstash `redis_stream` input consumes (its `data_field` defaults to
`value`). The Redis source reads both encodings the envelope carries (protobuf
and JSON text); the Redis sink writes the same protobuf messages the Kafka sink
does, so downstream consumers decode either transport identically.

**On the read side the body field is not required to be `value`.** A producer
using the JSON envelope — `data` as the body with `metadata` alongside it, which
is what RT-VLM publishes — is read without configuration: the source looks for
`value`, then `data`, then `payload`, then `metadata`, in that order. The order
matters rather than first-match-wins, because an entry carrying both `data` and
`metadata` has a body *and* a sidecar, and reading the sidecar as the event
yields something that decodes and describes nothing. Publishing is always
`value`, since that is the field every reader shipped in this repository looks
in.

The `console` sink renders results to the log instead of a datastore. It needs no
broker, which makes it the quickest way to inspect verdicts while developing, but
output is not durable and nothing downstream can consume it.

**It redacts by default.** The document carries the VLM's reasoning about the
people and vehicles in the footage, the VST video URL and the GPS fix, and
selecting this sink is a quick debugging decision while the log collector it
writes to is someone else's long-lived system — so those fields are masked unless
you say otherwise. The verdict, id, sensorId, category and timestamps are left
readable, which is what the sink is selected to show. A JSON array is masked
element by element, since a batch published as one carries the same fields the
paths name. A payload that is not JSON cannot be field-masked at all, so while
redaction is on it is logged as a size and digest rather than printed.

`max_chars` truncates the rendered text. Like every display setting here it
falls back to its default on a value it cannot use, with a warning naming the
key — a rendered config substitutes an unset variable as `""`, and no log-format
option is worth failing a container over.

`event_bridge.console_sink.redact` and `vlm_enhanced_sink.console.redact` control
it. A list of dotted paths (or a comma-separated string) replaces the default
set; the word `none` turns masking off and logs the document in full. An unset or
empty value means the default — deliberately, because a rendered deployment
config substitutes an unset variable as `""` and that must not read as consent.

### Delivery semantics

Both sources are **at-most-once**, and deliberately so: the Kafka source commits
offsets inside its poll loop and the Redis source `XACK`s once a batch is decoded
— in both cases before the VLM has verified anything. A crash mid-verification
therefore drops that batch rather than replaying it. The alternative costs more
than it returns here: verification is the expensive step, dedup state is
in-process and does not survive a restart, so replaying a batch would re-run the
VLM and can publish a second verdict for an event already in Elasticsearch.
Choosing Redis Streams does not change this contract in either direction, which
is the point — the transport is swappable without the pipeline's guarantees
moving underneath it.

Within that contract the ack still follows a decision about each entry rather
than the read: an entry is acked once it has been accepted into a batch or
explicitly rejected. So a non-empty `XPENDING` means a consumer died between
those two points, not that work is queued.

That window is small but not empty, and an entry stranded in a dead consumer's
pending list is never redelivered by `XREADGROUP >` — so the source sweeps for
them with `XAUTOCLAIM`, on a timer rather than only when a poll came back empty.
Idle-only was the wrong trigger for the case that needs it: a replica dies while
the group is busy, which is exactly when polls stop being empty, so the sweep
would not fire until the backlog cleared. Two knobs, in different places because
they mean different things:

| Setting | Where | Default | What it does |
|---|---|---|---|
| `reclaim_interval` | `event_bridge.redis_source.consumer_config` | 30s | How often to sweep. `0` disables it. Checked on every poll, so it is a period under load and a floor when idle. |
| `pending_min_idle_ms` | top-level `redis` | 60000ms | How long an entry must have been idle before another consumer may claim it. |

On Compose both are set in the mounted config file itself; in Helm they are
`redis.reclaimIntervalSeconds` and `redis.pendingMinIdleMs`, which render into
the same two keys. The earlier spellings `reclaim_min_idle_ms` and
`reclaim_min_idle_time` are still read, with a warning naming the current one.
`XAUTOCLAIM` needs Redis 6.2+; on an older server the sweep is disabled after
the first rejection and says so once.

**The idle threshold does not have to clear a VLM verification.** Every read path
acks an entry before returning it — commit-on-consume, matching the Kafka source
— so an entry is pending only between `XREADGROUP` and `XACK`, and is still
pending after that only because the consumer died in between. The threshold is
therefore how long a *stranded* entry waits before anyone picks it up, and the
cost of setting it high is recovery time after a replica is lost, not correctness.

**The sweep also collects the dead consumers themselves.** A consumer name is
per-process (`alert-bridge-<host>-<pid>`), so every restart and every pipeline
child leaves a record behind in the group, and Redis keeps them until something
calls `XGROUP DELCONSUMER`. Left alone, `XINFO CONSUMERS` on a long-lived
deployment grows without bound. So the same timer that reclaims entries drops any
consumer record that holds nothing pending and has been idle past the reclaim
window, and a source removes its own record on a clean shutdown. Entries are
reclaimed before records are dropped, so a record is only ever removed after its
work has been moved. On a server without `XINFO CONSUMERS` — or an ACL that
withholds it — the pruning is disabled after the first rejection and the reclaim
sweep carries on.

The shutdown half of that is housekeeping, and is treated as such: releasing its
own record runs after SIGTERM, inside the deployment's grace period, and costs two
commands per stream. So it is skipped outright when Redis is already known to be
unreachable, and abandoned once it has spent five seconds — in both cases the idle
sweep removes the record later, which is what it is for. Overrunning the grace
period would instead earn a SIGKILL and lose the shutdown this was part of.

Read-path drops are counted, not just logged. An entry that cannot be used is
acked and discarded — the right call for a poison pill, since leaving it un-acked
replays it forever — and shows up under
`alert_bridge_source_dropped_total{transport="redis_stream",reason=...}`:

| `reason` | Meaning |
|---|---|
| `no_payload` | No payload field in the envelope, or an empty one. |
| `undecodable` | A payload the protobuf decoder rejected, or one the JSON parser did not finish. The second is why the reason is broad: a few hundred kilobytes of nested brackets raises `RecursionError`, which is not a parse error, so it escaped the read loop with the batch unacked and the reclaim sweep handed the same entry to the next process — one `XADD`, a permanent restart. Such an entry is dropped rather than retried as protobuf, since a payload the JSON parser choked on is JSON text and the protobuf decoder is lenient enough to make an event out of it. |
| `unmapped_kind` | An entry from a stream this consumer has no configured kind for. |
| `schema_invalid` | JSON that parsed but carries no `sensorId` and no `sensor.id`. Any client can `XADD` to these streams; without this check an arbitrary object reached the VLM, which then paid to verify it. |
| `payload_encoding` | JSON in an encoding other than UTF-8. `json.loads` accepts UTF-16 and UTF-32 by sniffing the BOM, but everything downstream of the source is UTF-8 — so such an entry parsed here and then raised in the batch builder, where the entry had already been acked and the failure crashed the consumer instead of dropping one message. One `XADD` was enough to do it. |
| `kind_mismatch` | JSON that set `notification_type` to a kind other than its stream's. The stream decides the kind and the pipeline stamps it over the payload's, so such an entry was not mis-decoded but relabelled — verified as the stream's kind and published as one. Only a contradiction counts: a payload that omits the field, or sets a value naming no kind, is accepted and the stream decides. |

A rising count means a producer is emitting entries this consumer cannot use —
except for `kind_mismatch`, which says a producer is publishing usable events to
the wrong stream.

### Scaling the Redis source: dedup needs consumer affinity

**Run one Alert MS replica per Redis consumer group, or shard by sensorId.**

Dedup, the end-time delta filter and the VLM rate limit are all kept
**in-process**, and that is only sound because a given `sensorId` is always seen
by the same instance. On Kafka that holds structurally: `mdx-incidents` is
partitioned by `sensorId`, every dedup cohort key is prefixed with `sensorId`,
and a consumer owns whole partitions — so a cohort never splits across pods
(`test_multi_consumer_dedup` pins this).

A Redis Streams consumer group gives no such guarantee. `XREADGROUP` hands each
entry to whichever consumer asks first, and each replica registers under its own
consumer name (`alert-bridge-<host>-<pid>`). Two replicas on one group therefore
interleave the same sensor's events, each sees only part of the cohort, and
duplicates that in-process dedup would have suppressed reach the VLM instead —
extra verification cost and duplicate verdicts, quietly. Nothing errors.

How bad the duplicate gets depends on the **sink**, and the all-Redis
configuration is the worst case:

| Sink | What a cross-replica duplicate costs |
|---|---|
| `elastic` | The VLM verifies twice (wasted GPU), but the sink indexes by fingerprint (`document["Id"]`), so Elasticsearch still holds **one** document. |
| `redisStream` / `kafka` | The VLM verifies twice **and** both verdicts are appended — `XADD` has no doc-id equivalent, so a genuine duplicate reaches downstream consumers. |

`test_redis_multi_consumer_dedup` demonstrates both: with two replicas on one
group, twelve events for a single sensorId split 6/6 across them, and a repeated
fingerprint produced two publishes that only Elasticsearch's doc id collapsed.

If you must run more than one replica against Redis, either give each replica
its own consumer group over a disjoint set of streams (shard by sensor), or
enable `alert_agent.event_filters.protect_confirmed_verdicts` so the
Elasticsearch-backed verdict marker catches the duplicates that in-process state
no longer can. Note that it is **off by default**, so an unsharded scale-up has
no backstop as shipped.

The write path is where at-most-once bites hardest, so it does not simply give
up. A `redisStream` sink is the payload's only destination — the source has
already acked and nothing upstream will offer the verdict again — so a failed
`XADD` is retried (`publish_retries`, default 2, with a short linear backoff),
rebuilding the connection first when the failure was a dropped one. Retries are
few on purpose: the caller is on the consume path, so blocking there stalls the
batch behind it. When they are exhausted the payload *is* dropped, and that is
visible rather than silent: the sink logs an error naming the stream, and
`alert_bridge_redis_publish_failures_total{outcome="dropped"}` counts it. The
`outcome="recovered"` series counts blips a retry absorbed — a rising
`recovered` with a flat `dropped` means Redis is unstable but nothing was lost.
Alert on `dropped`.

A third series, `outcome="replayed"`, is the honest accounting for the case where
a retry can cost something. A write whose reply never arrived — a connection lost
or timed out *after* the command reached the socket — does not say whether Redis
appended the entry, so the retry may append a second copy. A pipelined batch
widens the same window rather than introducing a new one: `execute` sends every
append before reading any reply, so a break in between leaves entries the server
already applied with nothing to say so, and the fallback re-publishes all of them.

Retrying is still the right default — the source has acked, so not retrying drops
verdicts outright — and Redis offers nothing to make the append idempotent: the
entry ID is the server's to assign, and an ID chosen here so a second attempt
would be refused is also refused whenever another writer got in first, which turns
a rare duplicate into a silent loss. So the count is how you find out. `replayed`
is the upper bound on duplicates a downstream reader may have seen; failures that
cannot have applied anything — the server's own refusal, a connection never
opened, an authentication rejection — are retried without adding to it. It is
separate from `recovered` because the two answer different questions: `recovered`
says the payload was not lost, `replayed` says it may have landed twice, and one
publish can be counted under both.

Collapsing a duplicate is the consumer's job, and what it has to work with depends
on `payload_format`: `json` carries the whole document including its `Id`
fingerprint, and a protobuf `alert` (Behavior) carries `id`, but the protobuf
`Incident` schema has no identifier field at all — on that route the counter is
the only signal there was one.

**One publish is bounded by a clock as well as by a retry count.** A retry count
alone does not bound time, because every attempt can spend a whole socket
timeout, and that time is time the consume path is not reading. One publish
against a host that accepts packets and answers nothing measured **126.7s** —
which neither retry setting predicted, because the timeouts were 30s and the
client was retrying each connect four times underneath. `publish_budget` (top-level
`redis`, default 15s) is the ceiling on one publish including its retries,
checked between attempts — never mid-attempt, since interrupting an append in
flight would leave one that may have landed indistinguishable from one that did
not. A batch falling back to individual publishes shares one budget rather than
taking one each. Set it to `0` for no ceiling.

Because the budget is read *between* attempts, it only bounds a publish while one
attempt fits inside it — and an attempt costs `redis.socket_timeout`, which is
**5s** by default and in every shipped config for that reason. It was 30s, which
put a single attempt past the 15s budget; raise it there again and startup logs a
warning naming both values rather than leaving you to measure it. `socket_timeout`
is the unit of every worst case on this transport, not just this one: shutdown
spends it per stream it tidies up, and the readiness probe below spends it inside
the consumer group's session window. It must stay above
`consumer_config.block_time` (100ms) so an idle blocking read is not read as a
timeout; startup warns if it does not.

**The client does no retrying of its own.** redis-py 6 otherwise retries a
connection that times out four times, sleeping up to ten seconds between the
attempts, underneath all of the above — which does not compose with it. Measured
on one command against a host that times out on connect: four connect attempts and
ten seconds of sleeping that `publish_retries`, `publish_budget` and the counters
knew nothing about, so at the timeouts this shipped with, one publish cost minutes
while both retry layers reported doing what they were configured to do. The client
is configured for a single attempt per command so that retrying happens in one
place and is counted there.

**Acks are retried too, and that retry is the safe one.** `XACK` is idempotent,
so unlike a publish it cannot duplicate anything, while a lost ack is expensive:
the entry stays pending until the reclaim sweep gives it to another consumer,
which verifies it a second time and publishes a second verdict. It gets the same
retry count, backoff and budget as a publish, and its own two series —
`outcome="ack_recovered"` and `outcome="ack_dropped"`. They are separate from the
write path's because they mean the opposite thing: a dropped *publish* is a
verdict nobody received, a dropped *ack* is a verdict that will be produced
again. A rising `ack_dropped` is the explanation for duplicates that `replayed`
does not account for.

A sink that lost its connection also recovers its own readiness. The health flag
used to clear only on a successful publish, so a sink between verdicts stayed
unready indefinitely after a blip that had already healed; the readiness check now
pings when the flag is down — at most once a second, because the readiness timer is
not its only caller. Every consumer-group assignment and revocation reads it too,
on the rebalance callback, where a ping against a host that is not answering costs
a socket timeout inside the window the group allows a member before evicting it.
Between probes the answer is the flag.

**Publishing is batched where there is a batch to publish.** The event-bridge
sink's `write_*` methods are each handed a list, and each used to issue one
`XADD` per entry — so a ten-event batch spent ten round trips of latency on the
consume path, which the source cannot read past. They now go out in one
pipelined exchange (not a `MULTI`: these are independent appends, and a
transaction would only add a mode where one bad entry discards the rest). Order,
envelope and per-entry drop accounting are unchanged, because anything the
pipeline cannot place falls back to the individual retrying path.

The VLM-enhanced sink is not batched, and cannot be without changing what it
promises: it is called once per verdict as each one is produced, so batching
there would mean holding finished verdicts back to accumulate a batch — trading
the latency this saves for latency on the result nobody asked to delay.

Startup behaviour differs between the two directions, also deliberately. The
Redis sink pings on construction and refuses to start when Redis is unreachable,
because a sink that cannot reach its destination has nowhere to put results and
would discard them silently. The Redis source instead logs and retries with
backoff, because a consumer outliving a broker restart is normal operation. The
Kafka sink does not ping at all, so moving a sink to Redis makes startup stricter
than it was.

On Docker Compose the selections and the connection live in the config file the
profile mounts — `event_bridge.sourceType`, `event_bridge.sinkType`,
`vlm_enhanced_sink.type` and the top-level `redis` block, all shipped on their
Kafka defaults. In Helm they are the `eventSourceType`, `eventSinkType`,
`vlmSinkType`, and `redis.*` values, which render into those same keys. The
compose files pass no `REDIS_*` variables and are not modified by this feature,
so an existing deployment takes the new image with no change at all.

**Neither has a default endpoint.** `redis.host` is empty as shipped on both
paths, and Helm refuses to render when a `redisStream` transport is selected
without one. Both used to fall back to the bundled development Redis, which meant
a forgotten host produced a working pipeline attached to the wrong instance —
including publishing verdicts into streams the deployment does not own. For the
bundled instance, ask for it explicitly with `redis.useInClusterRedis: true`.

Everything that decides *where* a component connects, or whether it will be let
in, is judged at startup — before the API is up and before the pipeline processes
fork — so a one-line config mistake fails the container with the key named rather
than crash-looping a child with a stack trace:

| Setting | Unset means | Judged how |
|---|---|---|
| `redis.host` | nothing; a `redisStream` transport is refused | No default endpoint, per above |
| `redis.port` | 6379 | Anything outside 1-65535 fails, instead of reaching the client and reading as "Redis is down" |
| `redis.db` | 0 | `db: "one"` fails rather than coercing to 0, which would connect to a database that exists, accept every command and consume an empty stream in the wrong place |
| `redis.password_file` / `_env` | no credential, which is fine | A source that was named and yields nothing fails, rather than surfacing as `NOAUTH` on the first command |
| `vlm_enhanced_sink.type` | `elastic` | A name nobody implements fails here; validation used to resolve any non-Redis value to "not Redis" and pass it, so `type: mongo` was rejected later, by the sink factory inside a forked child |

Every row is a Redis setting or a selection this feature added. Kafka's own
endpoint is deliberately not on the list: that path is unchanged by this feature
and validates nothing new.

Knobs that only tune *how* a component behaves are treated the opposite way, on
purpose: an unusable value warns, names the full config path, and falls back to
the default rather than refusing to start. A typo in a backoff is not worth a
failed deployment — but it is worth a line, which is what the copies of that
coercion did not do before. Where the fallback would disable the knob's job
(`error_backoff: 0` paces nothing; `count: 0` reads nothing) the value is raised
to a floor instead.

Authentication and TLS are configured out-of-band from the rest of the config,
because that config is a ConfigMap:

| | Config key (Compose) | Helm value |
|---|---|---|
| ACL username | `redis.username` | `redis.username` |
| Password, inline (dev) | `redis.password` | `redis.password` |
| Password, from a file | `redis.password_file` | `redis.passwordSecret` |
| TLS on | `redis.ssl` | `redis.tls.enabled` |
| Private CA | `redis.ssl_ca_certs` | `redis.tls.caCertSecret` |
| Client cert / key (mTLS) | `redis.ssl_certfile` / `redis.ssl_keyfile` | `redis.tls.clientCertSecret` |

The Helm secrets are mounted into the pod for you. On Compose the file keys name
paths *inside the container*, and the Alerts compose mounts nothing for them, so
mount the host directory holding them yourself — a compose override file is the
place for it, since the shipped compose is left untouched. A password file wins
over an inline password when both are set.

Naming a password file or environment variable that yields nothing falls back to
the inline password if there is one, and **fails startup if there is not** —
naming the path it could not read. The Helm shape has no inline value, so a
`passwordSecret` whose mount never appeared would otherwise connect
unauthenticated and surface as `NOAUTH` on the first command, several layers away
from the mount that caused it. Leaving all three unset is still fine: an instance
with no `requirepass` is the ordinary local case.

`redis.ssl` and `redis.tls` are two spellings of one switch, and writing the
mapping form is enough: `tls: {}` means TLS on with the defaults, because an empty
block is a deployment asking for TLS and having nothing to add. That distinction
exists because an empty mapping is falsy in Python — read as a boolean, such a
config ran unencrypted and said nothing about it. The certificate settings are the
flat keys beside it (`ssl_cert_reqs`, `ssl_ca_certs`, `ssl_certfile`,
`ssl_keyfile`), so keys nested *under* `tls` are not read, and are named in a
warning rather than ignored. The scalar spelling keeps value semantics — `ssl: ""`
is an unresolved variable, not a request — and a value that is neither true-like
nor false-like leaves TLS off and says so.

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
