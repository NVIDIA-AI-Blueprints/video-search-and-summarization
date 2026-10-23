# Reading the results

## Where to look first

`summary.csv` for throughput and latency, `resources_summary.csv` for CPU and
GPU per service, `query_plans.json` for the route check. Group by the cell's
`path` and quote only rows with `cell_valid=True`.

Every measured request is end to end: decompose with the LLM (t0), `route()`
and spawn the CLI (t1), CLI exits (t2). The headline latency is
`e2e_latency_sec` (t2−t0); `decompose_sec` (t1−t0) and the CLI's
`latency_sec` (t2−t1) sit beside it, so either half can be read on its own.

For charts: `uv run scripts/plot_results.py <results dir>` writes into `plots/`:

| File | Shows |
|---|---|
| `throughput.png`, `throughput_<path>.png`, `latency.png` | end-to-end queries/sec, and e2e p50/p95 with the CLI-only p50 dotted, vs concurrency. Hollow markers: broken window or `cell_valid=False` |
| `resources_{cpu,memory,gpu,gpu_memory}.png` | per-service mean over each run's window; rows = path, columns = critic, line style = top_k |
| `resources_timeline.png` | the same four resources across the whole sweep, each run's window shaded by path (darker = critic on) |
| `latency_breakdown_<path>.png` | mean `decompose (LLM)` plus self time per CLI stage, stacked, with the e2e wall clock as a tick |

## Latency per stage — `stage_summary.csv`

One row per (run, stage), over the run's successful on-target measured
requests. `step` maps each stage to a step of the request:

| step | stage(s) | from |
|---|---|---|
| `decompose` | LLM call + `route()` (`decompose_sec`) | benchmark |
| `cli overhead` | `latency_sec − search_total_sec`: interpreter start, config load, client setup | benchmark |
| `retrieval (wall)` | `search: retrieval` | CLI |
| `encode` | `embed_search: generate query embedding`, `attribute_search: generate text embedding`, `fusion: generate attribute embeddings (once)` | CLI |
| `knn` | `embed_search: ES search execution`, `attribute_search: search behavior embeddings`, `search: object_ids behavior KNN` | CLI |
| `frame lookups` | `attribute_search: frame lookups` (attribute/fusion second ES step) | CLI |
| `verify (wall)` | `critic: verify` — the whole critic phase of one request | CLI |
| `resolve clip url` | `vlm: resolve VST clip url`, `critic: resolve VST timeline` | CLI |
| `verify clip (VLM)` | `vlm: inference` — one CLI→RT-VLM call per verified hit, **including** RT-VLM's download and decode | CLI |
| `download clip (client)` | `vlm: prepare media` — always ~0: the CLI sends `video_url`, it never downloads | CLI |
| `download clip (RT-VLM)`, `decode clip (RT-VLM)`, `vlm inference (RT-VLM)`, `clip total (RT-VLM)` | RT-VLM's own histograms | `rt-vlm /v1/metrics` |
| `e2e` | `e2e_latency_sec` | benchmark |

Columns:

- `calls_per_request` — how often the stage ran per request (per-hit stages:
  about the number of verified hits).
- `per_call_mean_sec` — seconds per call. **Use this for per-hit stages.**
- `p50_sec` .. `mean_sec` — the per-request sum of the stage. For a stage
  that runs concurrently within a request (`vlm: inference`, fusion's
  `frame lookups`) the sum is work, not elapsed time: 10 VLM calls summing to
  128 s inside a 28 s request. Read the `(wall)` steps for elapsed time.
- `per_call_p50_sec` / `per_call_p95_sec` — RT-VLM rows only, from its
  histogram buckets over the measured window.
- `concurrent` — the stage's children overlapped in some request.

The `(wall)` steps need a CLI whose `search_core` wraps retrieval and the
critic in `search: retrieval` / `critic: verify`; older CLIs have neither,
and the critic's elapsed time then cannot be read per request.

The RT-VLM rows difference RT-VLM's counters between the moment the measured
window opens and the end of the run, so they are server-wide: keep other
RT-VLM clients off during a sweep. They are per clip, not per request, and
include clips verified for failed or off-target requests. Collected for
critic-on runs when `services.rt_vlm_metrics_url` is set.

`latency_breakdown_<path>.png` stacks the same steps: decompose, the CLI's
overhead, retrieval (split into stages unless they overlapped), and
`critic: verify (wall)`, so a bar sums to the black e2e tick. Sweeps from
CLIs without the wall phases fall back to every stage's self time, which can
stack above the tick.

## `summary.csv` — one row per run

| Column | Meaning |
|---|---|
| `run_id` | `critic_{off\|N\|all}_topk_{N}_c{C}[_{path}]` — also the subdirectory name |
| `critic` | `on` or `off`. Off runs use a CLI config without the `rt_vlm` route |
| `critic_eval_count` | How many hits the critic verified. Empty means every hit (the CLI default), or that the critic was off — read `critic` |
| `path` | Which path this run measured alone (`attribute`, `event`, `fusion`, ...); empty means the round-robin mix |
| `query_count` | How many queries the run cycled through. The sample budget is per query, so this turns the per-query target into the run total |
| `resolved_paths` | `q2=event`: the target path of each cycled query, from the route check |
| `wall_clock_sec` | Whole run, warmup included |
| `measurement_wall_clock_sec` | From the post-warmup barrier to the last response. **This is the throughput denominator** |
| `measurement_window_clean` | `False` means the barrier broke, so the run never reached full concurrency and its throughput includes warmup. Exclude it from comparisons |
| `warmup_requests` / `measured_requests` | Warmup is discarded from every statistic |
| `success_count` / `failure_count` | Success is exit 0 **and** readable output. An exit 0 whose NDJSON does not parse is not a measurement of a search. A search that found nothing **is** one |
| `queries_per_sec` | End-to-end: `success_count / measurement_wall_clock_sec`. Off-target requests count — the product answered them |
| `e2e_p50_sec` / `e2e_p95_sec` / `e2e_p99_sec` / `e2e_mean_sec` | **The headline latency**: `e2e_latency_sec` over the successful, on-target measured requests |
| `decompose_p50_sec` / ... / `decompose_mean_sec` | The LLM call alone, same requests |
| `p50_sec` / `p95_sec` / `p99_sec` / `mean_sec` | The CLI alone (`latency_sec`), same requests. Comparable with sweeps from before end-to-end measurement |
| `llm_share` | `mean(decompose_sec) / mean(e2e_latency_sec)` |
| `path_stability` | Share of measured requests with a resolved path that ran the cell's path |
| `off_target_count` | Measured requests whose decomposition routed elsewhere. In throughput, not in latency |
| `decompose_error_count` | Measured requests whose decomposition failed (`decompose_error`): no CLI ran, counted as failures |
| `cell_valid` | `False` when `path_stability < execution.min_path_stability` (0.95). The cell mixes pipelines; leave it out of comparisons |
| `cli_spawn_cost_sec` | Median time for a bare `vss --version`. The floor under every latency in this file — read the next section before quoting throughput |
| `critic_ran_count` | Measured requests that carried verification evidence. **Zero while `critic_eval_count` is set means the critic never ran**, and that run's latency is not comparable with one where it did |
| `critic_confirmed` / `critic_rejected` | Hits the critic accepted and removed, summed over the run |
| `critic_unverified` | Hits returned without being verified — what `critic_eval_count` caps. At `top_k` 20 and a cap of 1, nineteen per request |
| `pod_log_error_count` | Matches of the configured error patterns in the run's `pod_logs/`. Non-zero means read `<run_id>/pod_log_errors.json` before quoting anything |
| `health_gate_sec` | Latency of the one pre-run gate request; empty when the gate was skipped. A sudden jump is an early sign of a wedging service |
| `cooldown_reason` / `cooldown_waited_sec` | How the pre-run drain ended (`drained`, `timeout`, `first run`, ...). A `timeout` means the run opened on a busy cluster |

### `critic_eval_count: null` makes `top_k` two variables

Verifying every hit means the critic's work grows with `top_k`, so a row at
`top_k` 20 differs from one at 5 in both how deep retrieval went and how many
VLM calls followed it. The two cannot be separated afterwards, and the `top_k`
axis then measures their sum rather than retrieval depth.

That is the honest end-to-end number, and it is what a caller experiences. It
is just not an answer to "what does raising `top_k` cost retrieval". For that,
pin the critic to a constant across the sweep — `--critic-eval-count 1` —
and compare `critic_unverified` to confirm the cap held.

### Read `cli_spawn_cost_sec` before quoting a ceiling

Each measured request is a `vss search run` subprocess, so each latency
includes a Python interpreter start. At concurrency 20 the load host runs
twenty of them at once.

Divide `cli_spawn_cost_sec` by the median CLI latency (`p50_sec`) in `per_query_summary.csv`.
Under about 10% the figure is the cluster's. Above roughly 20% — which the
runner warns about in the log — the throughput is partly the rate at which
this machine can fork processes, and a "saturation point" found that way may
be the client's. The fix is not a flag: measuring the service's own ceiling
needs an in-process caller, which is a different benchmark from this one.

`measured_requests` is frequently far above the configured budget. That is the
duration floor doing its job — see *Why n is bigger than you asked for* below.

## `per_query_summary.csv` — one row per (run, query)

`count`, `e2e_p50_sec`, `e2e_p95_sec`, `e2e_p99_sec`, `e2e_mean_sec`,
`decompose_mean_sec`, then the CLI's `p50_sec`, `p95_sec`, `p99_sec`,
`mean_sec`, `min_sec`, `max_sec`, `std_sec`, plus `resolved_path` (the
query's target path).

Percentiles use linear interpolation between the two neighbouring samples,
matching `numpy.percentile`'s default, so these are comparable with figures
produced by a numpy-based harness.

Warmup, failed and off-target requests are excluded from all of them. A timed-out
request's latency is the latency of the timeout, and counting it would let a
broken deployment fill the budget and quietly lower every percentile.

A search that returned **zero hits** is kept. It is a real answer with a real
latency, and dropping it would bias the distribution towards whichever queries
happen to match the corpus.

**A p95 over 10 samples is just their maximum.** Check `count` before quoting a
tail percentile.

## `query_plans.json`

The route check, written before the sweep. Each query is decomposed
`decomposition.route_check_calls` times; one call off the target path stops
the sweep (exit 5). Measured requests decompose again on their own, so this
is a reference, not what they send.

| Field | Meaning |
|---|---|
| `path` | The query's target path: `expected_path` when set, else what the first call chose |
| `route_check_paths` | The path every check call resolved to |
| `routed` | `true` when decomposition chose the path, `false` when `pin_paths` did |
| `plan` / `decomposition` / `decompose_sec` | The first check call: arguments, LLM answer, cost |
| `expected_path` | What the config expected |
| `status` | `ok`, `pinned`, or `failed` (with `error`) |

The top level adds `decomposer` (LLM URL, model, reasoning controls) and
`route_check_calls`. `distribution` counts queries per path and **keeps the
zeroes**: a path showing 0 had no measurement at all in this sweep.

### Why the path is known rather than observed

It used to be scraped from the agent's INFO log, which carries no request id.
Driving the CLI means this script routes every request itself and records
`resolved_path` on it, so the path is known for every request at any
concurrency, and an off-target request is visible as `on_target=False` in
`<run_id>/per_query_latency.csv`.

## `resources_summary.csv` — one row per (run, metric, series)

The run's columns (`run_id`, `critic`, `top_k`, `concurrency`, `path`, ...),
then `metric`, `service`, `series`, `avg`, `peak`. `series` is the pod, plus
`/gpuN` for GPU metrics; `service` is the pod name with its Deployment hash or
StatefulSet ordinal stripped, so replicas and restarted pods group together.

Gauges (`gpu_utilization`, `gpu_power_watts`, `cpu_cores`, ...) carry the
window's average and peak from the `summary` block below. Counters appear as
`<metric>:mean_rate` from `window_mean`, with no peak —
**`cpu_core_seconds_total:mean_rate` is the per-run CPU figure to quote**, for
the reason given under `window_mean`.

What is covered, per service (pod) unless noted:

| Resource | metric | notes |
|---|---|---|
| CPU | `cpu_core_seconds_total:mean_rate` (quote), `cpu_cores`, `cpu_limit_cores`, `cpu_throttled_ratio` | cAdvisor |
| Memory | `memory_working_set_bytes`, `memory_limit_bytes`, `oom_kills_total` | cAdvisor / kube-state-metrics |
| GPU | `gpu_utilization`, `gpu_engine_active`, `gpu_power_watts`, `gpu_memory_used` | DCGM, per GPU (`/gpuN`); LLM, rtvi-cv, rtvi-embed, rtvi-vlm, streamprocessing |
| Network | `network_{receive,transmit}_bytes_total:mean_rate`, `..._bytes_per_sec` | RT-VLM rx tracks critic load: it downloads every verified clip |
| Disk | `fs_{read,write}_bytes_total:mean_rate`, `..._bytes_per_sec` | mostly Elasticsearch |
| LLM engine | `llm_requests_running/waiting`, `llm_queue_time_sec`, `llm_ttft_sec`, `llm_preemptions_total` | vLLM metrics of the decomposition LLM |
| Load generator | `cpu_core_seconds_total:mean_rate` under service `load-generator (vss CLI)`, `cli_peak_rss_bytes` | `getrusage` of the CLI processes this script ran in the window — they live outside any pod, so cAdvisor never sees them |
| Host | `host_cpu_cores`, `host_memory_used_bytes` under `host (whole node)` | `/proc` on the machine running the benchmark, sampled every 2 s. On a single-node cluster this includes every pod |

The load generator's CPU shares a metric with the pods' so it plots beside
them: when it rivals the busiest service, part of the throughput is the
client. `cli_peak_rss_bytes` is the largest single CLI process reaped so far
in the sweep (a high-water mark), not a per-run sum.

## `<run_id>/prometheus_metrics.json`

Three blocks, and they answer different questions.

### `summary` — use for timelines and peaks

Per-query `avg`, `peak`, `samples` and a per-series breakdown. The series key is
the pod (and GPU index), so a metric can be attributed to the service that
caused it. The pooled `avg` dilutes busy devices with idle ones; read `series`.

`samples` is the difference between a figure and a coin flip. A run shorter than
the scrape interval yields one sample, and an average over one sample says
nothing.

### `window_mean` — use for a run's mean CPU

Counters differenced over the measured window: last sample minus first, divided
by the time between them.

This exists because `rate(counter[30s])` answers "how fast did this grow over
the last 30 seconds". For a 25-second cell that window still holds the previous
run's work. A measured sweep had 24 of 36 runs too short for the rate window,
and every one reported a CPU figure anyway, entirely residue.

| Field | Meaning |
|---|---|
| `mean_rate` | CPU-cores over this run's own window. `None` means the run was too short to difference |
| `coverage_ratio` | How much of the measured window the samples actually span. Below 0.75, the scrape interval is coarse against this run |
| `resets` | Non-zero means a container restarted mid-run |

### `window`

`start_epoch`, `end_epoch`, and `excludes_warmup`. Everything above is scoped to
the measured window, so the resource figures describe the same window as the
latency beside them.

Pod logs are the deliberate exception: they start at run start, so a service
that crashed during warmup has its crash in the file. Anything counted out of
them spans a longer window, so cut on `measurement_started_at`.

## `corpus_state.json`

What was ingested and what Elasticsearch held.

- `doc_counts_before` / `doc_counts_after` — around the ingest
- `uploads` — per upload: `sensor_id`, `phases` (`request_url_sec`,
  `upload_sec`), `duration_sec`, `retry_count`
- `video_targets` — per uploaded video: `sensor_id`, `expected` and `observed`
  keyed by index pattern, and `met`. This is the gate that decides when ingest
  is finished
- `readiness.expected_source` — `config` or `converged`
- `foreign_docs` — embed documents belonging to no file this sweep uploaded
- `doc_counts_at_end` / `corpus_drift` — re-counted **after** the sweep

Read `readiness.expected_source` before trusting a comparison. `config` means
the bar existed before a document arrived. `converged` means the copies set it
between themselves, which cannot distinguish all-finished from all-stalled —
there, the 300 s quiet window is the only thing separating the two.

The copies are the same file, so `video_targets[].observed` should be nearly
identical across them. A high outlier means that stream was registered twice;
the console warns about it, and no later count can separate the two passes.

`corpus_drift.drifted: true` means the runs did not all query the same index.
Treat per-run comparisons as unsafe. The gate's counts are taken seconds before
the first run, so only this second sample can show the index moved underneath
the sweep.

Counts come from `_cat/count`, which counts root documents. `_cat/indices`'
`docs.count` also counts Lucene's nested subdocuments and reads about 2× higher
on these mappings, 16× on `mdx-raw`. Do not mix the two.

## Why n is bigger than you asked for

Three floors size a run, and the largest wins:

1. **A flat request floor** (`min_measured_requests_per_run`, 200). A
   10-request run puts the 95% interval on the mean past ±400%.
2. **A rounds-per-worker floor** (`min_rounds_per_worker`, 10). The measured
   window opens at full concurrency on the barrier but closes by draining: each
   worker stops once it has spent its share, so the tail runs at falling
   concurrency. Measured, time-weighted concurrency reaches 67% of nominal at
   2.5 rounds per worker, 82% at 4, 92% at 7 and 100% at 31.
3. **A duration floor** (`min_run_duration_sec`, 300). A request budget is the
   right unit for statistics and the wrong one for anything read off a time
   series: one cell spent its whole 200-request budget in 21 s, which is 1.4
   Prometheus scrapes.

A floor never shortens a run, so slow cells pay nothing, and the time is not
spent idling — requests past the budget are recorded like any other. Read
`measured_requests`, never assume the budget. The log says so explicitly:

```
Duration floor held the window open past the request budget:
1774 samples against 200 asked for (8.9x)
```

## What not to quote

**`vss-rtvi-embed` and `vss-rtvi-cv` resource figures, unless the embedding
cache was disabled.** Both search paths embed the query text on one of those
services first, and both clients keep the vector in an LRU keyed on that text,
in a cache that lives as long as the agent pod. Three fixed queries cost three
embedding calls, once — a measured sweep of 2,160 requests made exactly three
POSTs to `vss-rtvi-embed`, all within four seconds, then nothing for two hours.

**Recall, or anything resembling it.** The corpus is one video five times: 1,800
vectors at 360 distinct points. This skill computes no accuracy metric, and the
corpus could not support one.

**Any run with `measurement_window_clean: false`.** Its throughput includes
warmup and it never ran at the concurrency on its label.

**Any run from a sweep whose `corpus_drift.drifted` is true**, for comparisons
across runs.
