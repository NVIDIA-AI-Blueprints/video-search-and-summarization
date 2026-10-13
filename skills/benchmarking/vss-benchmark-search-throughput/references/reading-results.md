# Reading the results

## Where to look first

`summary.csv` for throughput and latency, `resources_summary.csv` for CPU and
GPU per service, `query_plans.json` to find out which path each query took.
Group everything by **`resolved_path`**.

For charts: `uv run scripts/plot_results.py <results dir>` writes
`latency.png`, `throughput.png`, `cpu.png` and `gpu.png` into `plots/`.

## `summary.csv` — one row per run

| Column | Meaning |
|---|---|
| `run_id` | `critic_{off\|N\|all}_topk_{N}_c{C}[_{path}]` — also the subdirectory name |
| `critic` | `on` or `off`. Off runs use a CLI config without the `rt_vlm` route |
| `critic_eval_count` | How many hits the critic verified. Empty means every hit (the CLI default), or that the critic was off — read `critic` |
| `path` | Which path this run measured alone (`attribute`, `event`, `fusion`, ...); empty means the round-robin mix |
| `query_count` | How many queries the run cycled through. The sample budget is per query, so this turns the per-query target into the run total |
| `resolved_paths` | `q2=event`: the path each cycled query actually resolved to |
| `wall_clock_sec` | Whole run, warmup included |
| `measurement_wall_clock_sec` | From the post-warmup barrier to the last response. **This is the throughput denominator** |
| `measurement_window_clean` | `False` means the barrier broke, so the run never reached full concurrency and its throughput includes warmup. Exclude it from comparisons |
| `warmup_requests` / `measured_requests` | Warmup is discarded from every statistic |
| `success_count` / `failure_count` | Success is exit 0 **and** readable output. An exit 0 whose NDJSON does not parse is not a measurement of a search. A search that found nothing **is** one |
| `queries_per_sec` | `success_count / measurement_wall_clock_sec` |
| `p50_sec` / `p95_sec` / `p99_sec` / `mean_sec` | Over every successful measured request of the run. For a path run this is that path's one query; `per_query_summary.csv` splits a mix |
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

Divide `cli_spawn_cost_sec` by the median latency in `per_query_summary.csv`.
Under about 10% the figure is the cluster's. Above roughly 20% — which the
runner warns about in the log — the throughput is partly the rate at which
this machine can fork processes, and a "saturation point" found that way may
be the client's. The fix is not a flag: measuring the service's own ceiling
needs an in-process caller, which is a different benchmark from this one.

`measured_requests` is frequently far above the configured budget. That is the
duration floor doing its job — see *Why n is bigger than you asked for* below.

## `per_query_summary.csv` — one row per (run, query)

`count`, `p50_sec`, `p95_sec`, `p99_sec`, `mean_sec`, `min_sec`, `max_sec`,
`std_sec`, plus `resolved_path`.

Percentiles use linear interpolation between the two neighbouring samples,
matching `numpy.percentile`'s default, so these are comparable with figures
produced by a numpy-based harness.

Warmup and failed requests are excluded from all of them. A timed-out
request's latency is the latency of the timeout, and counting it would let a
broken deployment fill the budget and quietly lower every percentile.

A search that returned **zero hits** is kept. It is a real answer with a real
latency, and dropping it would bias the distribution towards whichever queries
happen to match the corpus.

**A p95 over 10 samples is just their maximum.** Check `count` before quoting a
tail percentile.

## `query_plans.json`

One entry per query, resolved once before the sweep and fixed for all of it.

| Field | Meaning |
|---|---|
| `path` | The CLI path every request for this query ran. Group by this |
| `routed` | `true` when decomposition chose the path, `false` when `pin_paths` did |
| `decomposition` | What the LLM returned: `attributes`, `has_action`, `object_ids`, the rewritten `query` |
| `decompose_sec` | What the LLM call cost. **Not** in any measured latency — it happened before the window |
| `expected_path` | What the config expected. An expectation, nothing more |
| `status` | `ok`, `pinned`, or `failed` |

The top-level `distribution` counts queries per path and **keeps the zeroes**.
A path showing 0 had no measurement at all in this sweep; a key that were
simply absent would read as if the question was never asked.

### Why the path is known rather than observed

It used to be scraped from the agent's INFO log, which carries no request id —
so attribution needed a serialized probe at concurrency 1 and still came out
`ambiguous` whenever two replicas disagreed. Driving the CLI means this script
chooses the path and puts it on the command line, so it is simply known, for
every request, at any concurrency.

The cost of that: `decompose_sec` is excluded from the latencies here. If the
question is end-to-end cost as a user experiences it, add it back — it is one
LLM call per query, not per request.

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
