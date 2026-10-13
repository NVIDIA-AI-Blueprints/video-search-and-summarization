---
name: vss-benchmark-search-throughput
description: >-
  Measure throughput, latency percentiles and cluster resource use of a deployed
  VSS search profile by sweeping concurrency, top_k and the critic through the
  vss CLI, one search path at a time. Use when the user asks to "benchmark search throughput", "measure
  search latency under load", "find the concurrency limit", or "compare the cost
  of the critic" on a deployed VSS profile.
license: Apache-2.0
metadata:
  version: "1.0.0"
  author: "NVIDIA Video Search and Summarization Team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint search throughput latency load benchmarking"
---

## Instructions

Follow the routing table, then the numbered steps. Reference material is in
`references/`, the runner is in `scripts/`.

## Purpose

Answers three questions about a deployed **search profile**: how many queries
per second it sustains, where latency sits at p50/p95/p99, and which service
spends the resources. It drives the `vss` CLI from a pool of threads and
records what Prometheus and the pod logs said during the measured window.

It does **not** score accuracy. No precision, recall, mAP, MRR or HIT@k is
computed anywhere in this skill. Use `benchmark-video-search` for that — it runs
a labelled dataset through the same CLI and answers an entirely different
question.

### Why the CLI and not the agent's API

`POST /api/v1/search` has been removed from `services/agent` outright — it is
not deprecated, it has no matches left in the source. The `vss` CLI is the
search interface a caller has.

The agent does not shell out to it either: they are separate surfaces onto the
same `search_core` library. So three things the agent used to do internally now
happen inside this script, and each changes what a number here means:

- **Decomposition.** Each query is sent to the deployment's own LLM once,
  before the sweep, using the prompt read out of the agent source at run time.
  The resulting arguments are what the CLI is given. The LLM call is therefore
  *not* in the measured latency; its cost is in `query_plans.json`.
  Reasoning is turned off for it, the way the agent turns it off: the chat
  template defaults `enable_thinking` to true, and a request that leaves it
  that way comes back as pure reasoning with `content: null`. Which models
  need which mechanism is decided by calling the agent's own
  `reasoning_utils.py`, loaded from source, rather than by a copy here that
  would fall behind the next model a profile ships.
- **Path selection.** The script routes the decomposition to one of five
  paths — `event`, `attribute`, `tag`, `fusion`, `object` — and the path is
  fixed for the whole sweep. `event` is the CLI's `vss search embed`
  subcommand; every file and column here calls it `event`. Nothing has to be scraped out of a log, and a
  path is known per request at any concurrency.
- **A process per query.** Every measured request is a `vss search run`
  subprocess, so each latency includes a Python interpreter start. Measured at
  the start of every sweep and published as `cli_spawn_cost_sec`. At
  concurrency 20 this host is running twenty interpreters at once, and part of
  the throughput figure belongs to the load generator rather than the cluster.
  Compare the two before quoting a ceiling.

**Critic off has no CLI flag.** The CLI skips the critic only when its config
has no `rt_vlm` route, so a critic-off run uses a copy of the sweep's CLI
config with that one route removed (`vss_config_critic_off/`). Same services,
same routes, no verification. Critic-on runs additionally vary how many hits
are verified (`critic_eval_count`, `null` = all).

## What it does and does not touch

Two stages, the same two `benchmark-video-search` has: **ingest, then measure.**
There is no deploy stage and no flag that adds one.

Everything it changes on the cluster:

| Change | When | Guarded by |
|---|---|---|
| `DELETE` the `mdx-*` indices | Only with `--ingest`, and only when `purge_before_ingest` is on | A protected-index check, an index-template check, and a re-count 30 s later |
| `POST /api/v1/videos`, then the video's bytes to VST | Only with `--ingest` | The sweep does not start until every uploaded `sensorId` holds its own document count in both `mdx-raw-*` and `mdx-embed-filtered-*` |
| One document in `vss-search-benchmark-lock` | Every non-dry run | Deleted on exit; taken over only after 300 s of silence |

On the host it writes only into the results directory. The CLI config every
request uses is generated per sweep in `<results>/vss_config/` (`vss configure`
runs with `VSS_CONFIG_HOME` pointed there), so `~/.vss/config.json` is never
changed.

Everything else is read-only. `kubectl` is used for `get pods` and `logs`,
`helm` for `get values`, `nvidia-smi` for a GPU snapshot. It never runs
`helm install`/`upgrade`/`uninstall`, nor `kubectl apply`/`create`/`delete`/
`rollout`. The abort path *prints* a `kubectl rollout restart` command as advice
for a human; it does not run it.

## When not to use this skill

- To deploy or redeploy a search profile, use `vss-search-deploy-script`. This
  skill measures what is already running and will not install anything.
- To find out whether retrieval returns the *right* footage, use
  `benchmark-video-search`.
- To measure ingestion speed rather than query speed, this runner does not
  measure it; its ingest stage exists only to put a corpus in place.
- To benchmark the OpenClaw chat route, this runner does not measure it.

## Routing

| Situation | Action |
|---|---|
| No search profile deployed | Deploy one first (`vss-search-deploy-script` or `vss-build-vision-ai`), then return here. This skill never deploys |
| User did not give an agent URL | Ask. Do not guess, and do not default to localhost |
| Elasticsearch has no `mdx-*` indices | The corpus gate will fail by design. Ingest first with `--ingest`; do not report the resulting fast-and-empty latency as a result |
| Another benchmark is running on the namespace | The run lock refuses with exit 7 and names the holder. Wait; do not pass `--no-run-lock` |
| User wants per-path numbers | Already the default: `sweep.paths: [attribute, event, fusion]`. Each run cycles only the query routing to its path (q1 → attribute, q2 → event, q3 → fusion). `--path event` narrows it |
| User wants critic on or off only | `--critic off` or `--critic on`. The default runs both |
| `vss` is not on PATH | Set `cli.vss_repo_root` to a checkout containing `libs/vss/cli`, or pass `--vss-cmd`. The sweep refuses with exit 3 before touching the corpus |
| User wants the `tag` path measured | Add a query and pin it: `decomposition.pin_paths: {q4: tag}`. No decomposition routes there, so it is unreachable otherwise |
| User asks what a column means | `references/reading-results.md` |
| A sweep aborted part-way | Re-run with `--resume <dir>`. Read `references/troubleshooting.md` first — an abort means something is wrong with the service, not with the sweep |
| User wants to change the queries | Do not, without saying so in the report. The three queries are chosen to land on three different paths and verified against the live model |
| A sweep's numbers look bounded by the client | Compare `cli_spawn_cost_sec` against the median latency in `summary.csv`. The CLI starts a Python interpreter per query |

## The default run

```bash
cd skills/benchmarking/vss-benchmark-search-throughput
uv run scripts/run_throughput.py --dry-run
```

Always dry-run first. It prints every cell, the request budget per concurrency
level, and which guards are active, and it touches nothing. The default matrix
is 72 runs — critic {off, on} × top_k {5, 10, 20} × concurrency {1, 5, 10, 20}
× path {attribute, event, fusion} — and the duration floor holds each one open
for at least five minutes: at least 9 h 33 m, so a sweep is a commitment worth
checking before it starts.
The printed minimum is arithmetic on the floor and the cooldowns; it knows
nothing about latency, and the `critic_eval_count: null` half of the matrix
runs a VLM call per hit, so expect well above it.

`uv run scripts/plot_results.py <results dir>` draws the latency, throughput,
CPU and GPU charts afterwards (into `<results dir>/plots/`).
`uv run scripts/make_report.py <results dir>` writes the per-path throughput
report and its charts into `<results dir>/report/`; it can be re-run while the
sweep is still going.

`uv run` and no install step: the two dependencies (`pyyaml`, `requests`) are
declared inline at the top of `run_throughput.py` as PEP 723 metadata, and uv
builds a throwaway environment from them. Under a plain `python3` the run
stops before doing anything and names every missing package at once. It never
installs them itself — this script measures a machine, and installing into
that machine mid-run changes what is being measured.

The tests need one more package, and nothing declares it for them:

```bash
uv run --with pytest --with pyyaml --with requests python -m pytest scripts/tests -q
```

### Smoke first, on a deployment this has not run against before

A dry run exercises the planner and nothing else. Everything past it — the
decomposition call, whether the CLI accepts the flags built for each path,
parsing its NDJSON, the critic fields, Prometheus, the run lock, the artifact
writers — is first exercised by a real request. Finding a rejected flag on
request one of a 72-run sweep is an expensive way to find it.

```bash
uv run scripts/run_throughput.py --smoke
```

Six runs — critic off/on × the three paths — of six requests each at
concurrency 2 and top_k 5, no duration floor: a few minutes. It deliberately does *not* shrink the corpus gate, the health gate,
the run lock or the metrics capture, which are among the least-exercised parts
of a sweep and cost nothing. Results land in `smoke_*` rather than
`search_throughput_*`, and the run says at both ends that it is not a
measurement — six requests per run have the shape of results without being
results.

`--smoke --dry-run` prints the shrunken plan, because `--smoke` is a config
override rather than a separate code path.

Then, against an already-ingested corpus:

```bash
uv run scripts/run_throughput.py
```

## Step 1 — Point it at the deployment

Edit `config.yaml` or override on the command line.

The URLs:

- `services.vss_agent_url` — the agent's **own** origin. Used for ingest only,
  never for search.
- `cli.origin_url` — the unified path-prefix origin the CLI is pointed at. The
  agent's own port does not route `/vst`, `/elasticsearch` or `/rtvi-embed`:
  a CLI sent there finds one service of seven and every search exits 4.
- `services.llm_url` — where queries are decomposed.
- `services.elasticsearch_url` — read outside the measured window, so a
  `kubectl port-forward` is acceptable here.

### This skill targets Helm on Kubernetes, not Docker Compose

`benchmark-video-search` next door targets a Compose deployment, where every
service sits on one host and a sibling endpoint is just another port on it:
HAProxy on 7777, the LLM NIM on 30081. That is where those numbers come from,
and under Helm they are both wrong.

A Kubernetes install puts the unified prefixes behind the profile's **HAProxy
Ingress** at `global.externalHost`, and gives the LLM its own ClusterIP
Service created by the NIM Operator and named after the deployed model. So
neither endpoint is a port on the agent's Service, and the model-dependent
name is not guessable from here. **Both must be configured, and the runner
never infers either one** rather than hand the sweep an address nothing
listens on and discover that only after ingest.

A second reason the shipped values are raw addresses: the benchmark runs on
the cluster node, and the host's resolver is not CoreDNS, so
`*.svc.cluster.local` does not resolve. ClusterIP addresses do work, because
kube-proxy's rules apply to locally-originated traffic. Re-read them after
anything recreates a Service — a stale one fails loudly at stage 1.

```bash
kubectl get svc -n vss-search
```

Also worth knowing: the LLM Service name has no dots —
`nemotron-35-lightning-30b-a3b`, not `nemotron-3.5-...` as the model is
written everywhere else.

Confirm the origin before committing to a sweep — both must return 200:

```bash
curl -sf <origin>/elasticsearch/_cat/indices
curl -sf <origin>/vst/api/v1/sensor/version
```

Two Kubernetes-specific traps this configuration already avoids:

- **Elasticsearch must be the ClusterIP, not the ingress route.** HAProxy
  guards `/elasticsearch` against `PUT` and `DELETE`, and `purge_before_ingest`
  deletes indices. Through the ingress the purge fails and the sweep then
  measures the old corpus plus the new one, which no later count can separate.
- **`cli.service_urls` lets measured requests skip the ingress.** Discovery
  has to go through the one origin, but the CLI reads each service's URL
  verbatim from its config rather than re-deriving it, so the runner repoints
  them in the sweep's own `<results>/vss_config/config.json` afterwards.
  Worth doing: this benchmark looks for a saturation point, and a ceiling
  imposed by a shared proxy would read as a ceiling of the services — and
  from inside the cluster the ingress host hairpins out to the node and back.
  **ClusterIPs change on every clean redeploy.** Stage 1 opens a TCP
  connection to each one and exits 3 if any does not answer; refresh them
  from `kubectl get svc -n vss-search`.
- `services.prometheus_url` — optional. Leave it empty and the sweep still
  produces every latency and throughput number, and says in the log that it has
  nothing to explain them with.

From inside the cluster prefer the ClusterIP names, so neither an ingress hop
nor a port-forward (a userspace proxy) sits inside the measured path.

The CLI itself: leave `cli.vss_cmd` and `cli.vss_repo_root` blank and the
runner walks up from the skill looking for a VSS checkout, falling back to
`vss` on `PATH`. A checkout is needed either way — the decomposition prompt is
read from `services/agent/.../tools/search.py` at run time rather than
vendored here, so that this benchmark cannot drift from what the product does.

Stage 1 runs `vss --version`, runs `vss configure` into the sweep's own config
directory, applies and probes `cli.service_urls`, checks that the deployment
has an RT-VLM when critic-on runs are planned, and times the process startup,
all before ingest. A stale virtualenv or a stale ClusterIP then costs one
subprocess instead of a corpus.

## Step 2 — Put a corpus in place

The corpus is **one video uploaded five times**. Set
`corpus.ingest.video` to the file and run with `--ingest`.

This is deliberate. Every upload is renamed `searchbench-<timestamp>-<n>-<stem>.mp4`,
so VST mints a fresh `sensorId` per request and the embed service — which keys
on that id, not on content — has nothing to collapse. Five uploads of one file
are five independent sensors, and the per-sensor verification proves it.

What it costs: the copies put their vectors on the same points in embedding
space, so absolute top-K recall is not comparable with a corpus of five distinct
videos. This skill does not measure recall, and the path-to-path ratios it does
measure are unaffected. Point `video` at a directory and set `copies: 1` if a
recall-comparable corpus is ever wanted.

Each upload is two hops — `POST /api/v1/videos` for a URL, then the bytes
straight to VST. There is no `/complete` call: VIOS raises `camera_streaming`
on the upload and its webhooks drive RT-CV, RT-Embed and RT-VLM by themselves,
so calling `/complete` as well is a second registration. All five go up at
once by default (`upload_concurrency`).

### How the sweep knows ingest is finished

**Document counts per video, not HTTP statuses.** The upload returns long
before any indexing has happened, and the perception fan-out is a
fire-and-forget webhook that reports its outcome to nobody. So the gate is:
every uploaded `sensorId` must hold its own document count in **both**
`mdx-raw-*` and `mdx-embed-filtered-*`, and only then must the counts stop
changing for 300 s.

Per video rather than per index, because an index total is satisfied by five
videos' worth of documents belonging to four sensors — and nothing afterwards
can detect that.

The two indices do not agree on where the sensor id lives: `mdx-raw-*`
documents carry `sensorId`, the embed index carries `sensor.id`. The field is
configured per pattern in `gate_indices`; using one name for both counts zero
forever on the other, and the gate reports `sensor_field_mismatch` rather than
waiting out the timeout.

Where the bar comes from: `expected_docs_per_video` when the config sets it.
Otherwise **the copies check each other** — they are the same file, so
whichever copy gets furthest sets the bar for the rest, and a copy that stalls
stands out against its siblings. That is the weaker of the two: it cannot tell
five finished videos from five that stalled in step, which is what the 300 s
quiet window is covering. Measure the yields once and write them down.

`mdx-behavior-*` is counted but not gated on — identical input has produced 492
and 486 documents, so a target for it would fail runs over noise.

Uploading in parallel shortens the indexing, not just the transfer. The five
streams are batched into one DeepStream pipeline rather than queued:
`dev-profile-search` configures `vss-rtvi-cv` with `max-batch-size=16` and a
`streammux`/`primary-gie` batch size of 16, and the hardware profiles claim 20
to 61 concurrent 2D streams. Five fit well inside that, so five copies of a
30-minute video cost roughly the wall clock of one, not of five.

Lower `upload_concurrency` if the deployment is smaller than the one this was
measured on. Above the configured batch size the surplus streams queue and the
parallelism stops paying.

## Step 3 — Dry-run, then sweep

```bash
uv run scripts/run_throughput.py --dry-run
```

Read the four things it prints:

1. **The cell list.** `critic_all_topk_5_c5_event` is one run. Each lists the
   query it will cycle, chosen by `expected_path` (marked `(expected)`); the
   real routing is checked after decomposition. The critic
   setting is the outermost loop on purpose — an abort then still leaves one
   complete critic setting rather than fragments of several.
2. **The request budget per concurrency.** The per-query sample target grows
   with concurrency so each worker keeps at least 10 rounds; below that the run
   spends too much of its window draining at falling concurrency.
3. **The search interface.** Which CLI will be invoked and at which origin.
   A `--path` given here is checked against the config's `expected_path`,
   which is a guess; it is re-checked against the resolved path after
   decomposition, and a path nothing routes to fails then.
4. **The guards.** Health gate, abort-on-empty, cooldown, single-run lock,
   corpus gate. Every one of them exists because its absence produced a
   plausible-looking wrong number at least once. Do not disable one to make a
   sweep finish faster.

Then run it under `nohup`, because a full sweep outlives a terminal. The
results directory gets its own `benchmark.log`; the redirect only catches
what is printed before it exists:

```bash
mkdir -p results
nohup uv run scripts/run_throughput.py > results/nohup-$(date -u +%Y%m%dT%H%M%S).out 2>&1 &
tail -f "$(ls -td results/search_throughput_* | head -1)/benchmark.log"
```

An interrupted sweep continues with `--resume <dir>`: finished runs are
skipped, `query_plans.json` is reused (so every run keeps the same
decomposition), and ingest is skipped unless `--ingest` is given.

## Step 4 — Read the results

Everything lands in `results/search_throughput_<timestamp>/`:

| File | What it holds |
|---|---|
| `summary.csv` | One row per run: critic, path, throughput, p50/p95/p99, request counts, critic counts, health gate, cooldown, window cleanliness |
| `per_query_summary.csv` | One row per (run, query): p50/p95/p99, mean, min, max, std |
| `resources_summary.csv` | One row per (run, metric, pod/GPU) with its `service`: CPU, GPU, memory, avg and peak |
| `plots/` | Written by `scripts/plot_results.py`, not by the sweep |
| `vss_config/`, `vss_config_critic_off/` | The CLI config every request ran with |
| `summary.jsonl` | The same runs as JSON, including the Prometheus summaries |
| `query_plans.json` | Each query's decomposition, the path it resolved to, and what the LLM call cost |
| `corpus_state.json` | What was ingested, what ES held at the gate and at the end |
| `benchmark.log` | Everything the console said |
| `<run_id>/request_records.jsonl` | Every request, warmup included |
| `<run_id>/per_query_latency.csv` | Every measured latency, one row each |
| `<run_id>/prometheus_metrics.json` | Raw and summarised resource series |
| `<run_id>/pod_logs/` | Logs followed live during the run |

Group by **`resolved_path`**, never by the config's `expected_path`. The config
labels are expectations; decomposition decides, and the two disagree often
enough that it matters.

See `references/reading-results.md` for what each number means and which ones
are safe to quote.

## Four things that will mislead you

**1. The query-embedding cache makes the text embedding free.** Both search
paths embed the query text on a separate service first, and both clients keep
the vector in an LRU keyed on that text, in a cache that outlives the sweep.
Three fixed queries cost three embedding calls, once: a measured sweep of 2,160
requests made exactly three POSTs to `vss-rtvi-embed`, all within four seconds,
then nothing for two hours. Quote resource figures for `vss-rtvi-embed` and
`vss-rtvi-cv` only from a deployment with the cache disabled.

**2. `rate()` over a short run reports the previous run.** `rate(counter[30s])`
answers "how fast did this grow over the last 30 seconds", which for a 25-second
cell is mostly its predecessor's work. Read `window_mean` in
`prometheus_metrics.json`, which differences the counter over this run's own
window. The `summary` block is for timelines and peaks, not for a run's mean.

**3. Part of the throughput is this host.** Every query is a Python
interpreter start, measured once per sweep as `cli_spawn_cost_sec`. A run
whose median latency is within a few multiples of it is reporting how fast
this machine forks processes at least as much as how fast the cluster
searches. The runner says so per run when startup exceeds 20% of the median,
but the comparison is yours to make before quoting a ceiling. If the question
is the service's limit rather than the CLI's, that needs an in-process caller,
which is a different benchmark.

**4. Five identical videos are not five distinct ones.** They are five sensors
with 1,800 vectors at 360 distinct points. Elasticsearch handles the search in
4–8 ms out of a ~489 ms query, so this barely moves latency — but it does mean
recall numbers from this corpus mean nothing, which is one more reason this
skill does not compute them.

## References

- `references/flags.md` — every flag, and what turning it off costs
- `references/reading-results.md` — the columns, and which to trust
- `references/troubleshooting.md` — gates that fail, and what each one means
