# Flags

Every flag that changes what a run *means*, and what turning it off costs.
Flags that only change where files land are in `--help`.

## Choosing what to measure

### `--dataset <dir>`

A directory holding `queries.yaml` (the questions) and `videos.yaml` (the
file to upload, `copies`, `upload_timestamp`), overriding the config's
`dataset.dir`. Default: the skill's own `dataset/`.
Changing it changes what every number means, so compare only sweeps that ran
the same dataset -- the results' `config.yaml` records it under `dataset`.

### `--path attribute event fusion`

Which search paths to measure, one per run. Default: the config's
`sweep.paths`, which is all three — q1 → `attribute`, q2 → `event`,
q3 → `fusion`.

The five paths are `event`, `attribute`, `tag`, `fusion` and `object`.
`event` is the CLI's `vss search embed` subcommand. Only four are reachable
from a decomposition — nothing in the contract asks for keyword retrieval over
VLM tag documents, so `tag` needs `decomposition.pin_paths`.

Each run cycles only the queries routing to its path, so its latency,
throughput and per-service CPU attribute to that path. The run id ends in the
path: `critic_off_topk_5_c1_event`. A path no query routes to after
decomposition exits 5.

Setting `sweep.paths: []` returns to the old round-robin over all queries, where
a run's p95 is the p95 of a tri-modal mixture. The paths are 20–70× apart in
latency, so that mixture describes no real workload.

### `--path-order grouped | interleaved`

Where the path loop sits in the matrix. It cannot avoid both distortions:

- **`grouped`** (default) runs a whole path before starting the next, so an
  `event` run follows another `event` run and there is nothing to average in.
  Only the first cell of each path inherits anything — one cell instead of
  twelve. The price is drift: two paths at the same cell are a whole matrix
  apart in wall-clock time.
- **`interleaved`** runs the paths of a cell back to back, so comparing paths
  at one cell is clean of cluster drift. But every `event` run then starts
  seconds after an `attribute` run that left `vss-rtvi-cv` at ~130 cores, and
  `cpu_cores` is a rate over 30 s while short cells last 21–28 s. A measured
  sweep reported 12–31 CV cores on `event` cells whose pod log shows **zero**
  calls to that service.

Default is `grouped` because per-service CPU is the harder question, and the
paths are far enough apart in latency that an hour of drift cannot change which
is faster.

### `--critic on off`

Run with the critic on, off, or both (default: `sweep.critic`, both). The
critic setting is the outermost loop, so an abort still leaves one complete
setting.

The CLI has no flag to disable the critic; it skips it only when its config
has no `rt_vlm` route. So critic-off runs use `vss_config_critic_off/`, a copy
of the sweep's CLI config without that route, and pass no
`--critic-eval-count`. Critic-on runs against a deployment that exposes no
`rt_vlm` exit 3 at stage 1 — they would silently measure critic off.

### `--top-k`, `--concurrency`, `--critic-eval-count`

Override the config's sweep axes. Duplicates are collapsed, so
`--critic-eval-count all all` is one run, not two colliding on the same
`run_id`. Every value must be ≥ 1.

`--critic-eval-count` applies to critic-on runs only. It takes integers ≥ 1, or
`all` (equivalently `null` in the config) to verify every hit. To measure the
critic as a cost rather than a constant, sweep `[1, 5, null]`: the same
retrieval with increasing verification work.

## Finding the CLI

### `--vss-cmd`, `--vss-repo-root`

How to invoke `vss`, most explicit first: `--vss-cmd`, then
`--vss-repo-root` (which becomes `uv run --project <root>/libs/vss vss`), then
`vss` on `PATH`. Exit 3 names all three when none works.

A checkout is needed regardless of how the CLI is found: the decomposition
prompt is read from `services/agent/.../tools/search.py` at run time rather
than copied into this skill, so the benchmark cannot route queries the
deployment would not.

### `--llm-url`

Where queries are decomposed.

Required, and never inferred: blank exits 5. The sibling Compose skill reads
it as port 30081 on the agent's host, which is the NIM's NodePort there. Under
Helm the LLM has its own ClusterIP Service named after the deployed model, so
there is no port to derive it from and no name to guess. Set
`services.llm_url`, and find yours with
`kubectl get svc -n <ns> | grep -iE 'nemotron|nim|llm'` — note the Service
name drops the dots, `nemotron-35-lightning-30b-a3b`.

Decomposition runs **once per query before the sweep**, not per request. That
keeps the LLM call out of the measured latency, and keeps each query on one
path for the whole sweep — at temperature 0 the model is still not
contractually deterministic, and a query that routed to `fusion` on one
request and `attribute` on the next would merge two pipelines into one latency
distribution with nothing saying so.

### `--skip-vss-configure`

Do not run `vss configure`. Normally the runner discovers the deployment from
`cli.origin_url` into the sweep's own `<results>/vss_config/` (with
`VSS_CONFIG_HOME` set there), so `~/.vss` is never written. With this flag it
copies `~/.vss/config.json` there as it stands instead, then applies
`cli.service_urls` to the copy. Use it only when that config is known to point
at this deployment — a CLI pointed at the wrong origin exits 4 on every search.

## Stages

### `--ingest` / `--skip-ingest`

`--ingest` turns on the upload stage (same as `corpus.ingest.enabled: true`).
`--skip-ingest` turns it off at the point of use, so the config written into the
results directory still records what was *configured* rather than what was
skipped.

A sweep against an already-loaded corpus needs neither: the default config has
ingest off. `--resume` skips ingest unless `--ingest` is given, because
re-ingesting would purge the corpus the earlier runs measured.

### `--no-purge-es`

Do not clear the indices before ingesting.

The purge exists because every upload gets a fresh filename and therefore a
fresh `sensor_id`. Ingesting the same file twice without clearing leaves ten
sensors instead of five: every per-file target is met, every floor is cleared,
and each search scores against a corpus holding each video twice. **Counts
cannot detect this afterwards**, which is why the clearing happens first.

With `--no-purge-es`, a non-empty index aborts the run instead
(`require_empty_before_ingest`). Set that to `false` only to measure a mixed
corpus deliberately.

### `--skip-prometheus`, `--skip-pod-logs`, `--skip-deployment-capture`

Turn off the resource collectors. Latency and throughput are unaffected. Pod
logs need `kubectl` with read access to the namespace; without it the streamer
logs a warning and the sweep continues.

## Guards

Each of these exists because its absence produced a plausible-looking wrong
number at least once. Turning one off is a deliberate act that belongs in the
report.

### `--skip-health-gate`

Before each run, one request at concurrency 1 carrying that run's own critic
setting and `top_k`.

Readiness is not something a benchmark can gate on. Through a whole outage
`vss-agent` returned 200 on `/v1/health/ready` while every search hung to the
full 300 s client timeout, and five consecutive runs then collected nothing
across 4.1 hours — a timed-out request spends the sample budget just as well as
a successful one.

The gate carries the run's own critic setting because the critic is what
reaches the VLM: a gate that skipped it cannot observe a wedged VLM at all,
which is the failure this exists to catch. Costs about 8 s per run when
healthy.

### `--no-abort-on-empty-run`

Keep sweeping after a run finishes with zero successful requests.

A run with no successful request is not a slow data point, it is the absence of
one: every percentile is undefined and throughput is zero. Continuing spends the
same hours again collecting nothing. The run is recorded either way, so
`--resume` does not retry it blindly.

### `--skip-cooldown`

Do not wait for the cluster to drain between runs.

A run ends when its own requests have been answered, which is not when the
cluster goes quiet: work the agent already dispatched keeps burning CPU. Without
the wait, the next run opens its measurement window on a busy cluster and
charges that CPU to itself.

Measured: the embed path makes **zero** calls to `vss-rtvi-cv`, verified in the
pod log, yet its short cells reported 68–111 CV cores. Those cells run 20–30 s
and follow an attribute-path cell whose CV work takes 20–25 s to drain. The c1
cells are 67–80 s, long enough to show what the others hide: CV falls to exactly
0.0 cores by 25 s and stays there.

The wait polls the busiest pod in the namespace rather than a named one, because
what contaminates a run is whatever is still busy.

### `--no-run-lock`

Run without the cluster-wide single-run lock.

A host-local lock reaches one user on one machine. A different user, a different
host sharing the cluster, or a direct script invocation all slip past it. A
second sweep once emptied Elasticsearch nine hours into a running one, and
neither sweep's artifacts recorded the collision — it is only visible by
noticing that one sweep's `doc_counts_before` is all zeros at a timestamp inside
the other's run window.

The lock is a document in Elasticsearch, so an in-cluster Job and any host on
the network both see it. It heartbeats every 30 s against a 300 s TTL, and a
lock whose heartbeat has gone stale can be taken over — otherwise a run killed
with `SIGKILL` would block the namespace forever.

Exit code 7 means the lock is held, and the message names the holder. Exit 2
with "cannot reach the single-run lock store" means Elasticsearch did not
answer the lock query — fix `services.elasticsearch_url` rather than reaching
for this flag. A sweep that loses the lock mid-way aborts before its next run
(exit 6).

## Resuming

### `--resume <dir>`

Continue into an existing results directory. A run counts as complete when its
`run_summary.json` exists — that file is written last, so a run interrupted
mid-way is retried rather than silently skipped.

It reuses the directory's `query_plans.json`, so the remaining runs search with
exactly the arguments the finished ones did; a query whose text changed in the
config exits 5. Ingest is skipped unless `--ingest` is given. The config of the
resuming invocation is written to `config.resume_<stamp>.yaml` beside the
original `config.yaml`.

`--resume` starts a fresh process against what should be an idle cluster, so the
first run of an invocation skips the cooldown.

## `--dry-run`

Prints the plan and touches nothing: no lock is taken, no request is sent. Run
it first. A full matrix is 72 runs with a five-minute floor each — at least
9 h 33 m — so the total is worth seeing before it starts.

## `--smoke`

Six runs — critic off/on × the three paths — of six requests each, at
concurrency 2 and top_k 5, with no duration floor and no cooldown. Every
stage runs, including the gates, the lock and the collectors. Results land in
`smoke_*`. Not a measurement.
