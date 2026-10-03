# Metrics and Artifacts

Two levels of metric live in a run. Mixing them is the easiest way to misread it:
per-upload rows in `ingest_requests.csv`, per-sweep-point rows in
`ingest_summary.csv`.

## Artifact layout

```text
benchmark-results/vss/ingest/
├── raw/
│   └── upload_details.jsonl
├── csv/
│   ├── ingest_corpus.csv
│   ├── ingest_requests.csv
│   ├── ingest_summary.csv
│   └── ingest_errors.csv
├── visualizations/
│   ├── summary.md
│   ├── summary.json
│   ├── ingest-throughput-vs-concurrency.png
│   ├── ingest-latency-p95-by-concurrency.png
│   └── ingest-outcome-by-concurrency.png
└── run-metadata.json
```

Every CSV is UTF-8 with a header row, uses stable file names, and carries no
credentials or tokens. Charts and summaries are generated from the CSVs, never from ad
hoc parsing of raw logs, so a finished run can be re-summarized and re-plotted without
re-uploading anything.

## `ingest_corpus.csv` — one row per measured video

| Column | Meaning |
|---|---|
| `video_id` | Source file stem |
| `video_class` | `50MB`, `500MB`, `2GB`, or `10GB` |
| `source_path` | Local path the bytes were read from |
| `bytes` | Source video size |
| `duration_sec` | Source video length, from ffprobe |
| `fps` | Average frame rate, from ffprobe |
| `width`, `height`, `resolution` | Frame geometry |
| `codec` | Video codec name |
| `container` | ffprobe `format_name` |
| `content_type` | `video/mp4` or `video/x-matroska` |
| `probe_status` | `ok` — a file that cannot be probed fails validation instead of appearing here |

A file that cannot be read fails validation rather than being skipped, because
skipping changes the denominator of every throughput number.

## `ingest_requests.csv` — one row per upload

| Column | Meaning |
|---|---|
| `worker_index` | Which client worker sent it |
| `upload_sequence` | Order within the sweep point |
| `video_id` | Source video, from `ingest_corpus.csv` |
| `video_class` | Class of the sweep point |
| `concurrency` | Concurrency of the sweep point |
| `upload_filename` | Generated name it was sent under, `<point_uuid>-<seq>.<ext>` |
| `bytes` | Source video size |
| `duration_sec` | Source video length, as measured by ffprobe |
| `request_sent_at` | Client clock before CLI process launch |
| `ingest_confirmed_at` | `max(ingested_at)` across matched documents; blank unless `confirmed` |
| `latency_sec` | Harness `upload_duration_sec` — client monotonic time from CLI process launch to readiness, poll loop included |
| `es_indexed_latency_sec` | `ingest_confirmed_at` minus `request_sent_at`. Diagnostic only; no summary percentile is taken over it |
| `http_status` | Blank for CLI uploads (HTTP status is not exposed) |
| `cli_exit_code` | Actual process exit; blank if launch/protocol failed |
| `cli_duration_sec` | CLI startup, upload and VIOS timeline wait |
| `outcome` | `confirmed`, `unconfirmed`, `failed`, or `timed_out` |
| `expected_frames` | `max(1, ceil(duration*fps) - 15)` |
| `es_frame_count` | RT-CV documents observed in `mdx-raw-*` |
| `expected_chunks` | `reported_chunks` or `ceil(duration/chunk_duration)` |
| `es_chunk_count` | RT-Embed documents observed in `mdx-embed-*` |
| `raw_completion_ratio` | `es_frame_count / expected_frames` |
| `rt_cv_dropped` | `True` when raw settled below the exact count (live-source drops) |

Two clocks are recorded per upload, and they answer different questions.

`latency_sec` is the harness `upload_duration_sec` — the client's own monotonic
measurement, which includes the readiness poll interval. The harness computes
`ingest_latency_p50/p95_sec` and `max_upload_duration_sec` from exactly this value, so
the summary percentiles use it and nothing else. Substituting a smaller quantity under
harness column names would report a different measurement as if it were the same one.

`es_indexed_latency_sec` is `ingest_confirmed_at - request_sent_at`. Under
`es_readiness` that ends at `max(ingested_at)`, the instant the last document was
indexed, so it carries no poll-interval error. It is the better number for reasoning
about the pipeline itself, and it is the clock the *throughput* window closes on — but
it is not harness-comparable as a latency, so it stays out of the percentiles.

The gap can include polling delay and the CLI media wait if indexing finishes before
the CLI returns. It is not an internal stage duration.

`raw/upload_details.jsonl` carries the same rows plus `sensor_id`, `error_detail`,
`transmitted_bytes` (acknowledged payload only), `cli_response`, `cleanup_detail`, and `readiness_polls` — useful for triage, kept out of the CSV
so the normalized schema stays stable.

## `ingest_summary.csv` — one row per sweep point

Harness column names are reused so a Phase 1 curve and a `vss_ingest_perf` curve line
up without renaming.

| Column | Meaning |
|---|---|
| `video_class` | `50MB`, `500MB`, `2GB`, or `10GB` |
| `concurrency` | Simultaneous client workers |
| `rt_set` | **Always blank** — Phase 1 does not control the deployment shape |
| `ba_enabled` | **Always blank** — same reason |
| `upload_count` | Concurrency times the file count, not concurrency |
| `success_count` | Confirmed uploads |
| `failure_count` | Rejected, errored, unconfirmed, or timed-out uploads |
| `success_rate_pct` | Confirmed over attempted |
| `result_valid` | `False` for failed/unconfirmed uploads, insufficient raw coverage, or invalid completion timing |
| `result_invalid_reason` | Why the point is invalid, in words |
| `min_raw_completion_ratio` | Slowest stream's frame coverage across the point |
| `total_video_duration_min` | Confirmed video-minutes; already includes the concurrency multiplier |
| `total_video_size_gb` | Confirmed source bytes |
| `wall_clock_min` | Duration of the sweep point |
| `video_min_per_sec` | `total_video_duration_min` divided by a valid positive `success_window_sec`; blank for invalid completion timing |
| `success_window_sec` | `max(ingest_confirmed_at) - min(request_sent_at)` over confirmed uploads; blank if any confirmed upload has invalid timing |
| `aggregate_mb_per_sec` | Acknowledged CLI payload bytes / point wall time; partial failures and overhead unknown |
| `p50_latency_sec` | Median upload latency in the point |
| `p95_latency_sec` | 95th percentile upload latency |
| `max_latency_sec` | Slowest upload |
| `cli_exit_statuses` | Full CLI exit histogram, e.g. `0:5; 7:1` |
| `api_failure_statuses` | Legacy field; HTTP status is unavailable for CLI uploads |

Throughput compares client request timestamps with indexed completion timestamps.
Clock skew or malformed timing must not create a zero/negative window that is
clamped to an arbitrarily small positive value. Such points are invalid, retain
a reason in `result_invalid_reason` identifying the affected upload, and leave both
`video_min_per_sec` and `success_window_sec` blank. Every confirmed upload needs
parseable, timezone-aware request and completion timestamps, with completion
strictly after its request. Client monotonic latency remains separate from this
timestamp window. Points with no confirmed uploads retain zero throughput, use
point wall time for `success_window_sec`, and remain invalid.

`api_failure_statuses` is a histogram over **every** upload in the point, including
successes, so it sums to `upload_count`. Transport-level give-ups have no status and
are counted under `none`.

### Columns the harness has and this does not

Byte counters, GPU utilization, chunk latency, and frame rate all need Prometheus, so
they are omitted rather than estimated. `p99` is not added: `p50`, `p95`, and `max`
match the harness.

`result_valid`, `result_invalid_reason`, and `min_raw_completion_ratio` **are**
carried, because they are what stop a degraded point from being read as clean
scaling. Every run checks Elasticsearch readiness for both pipelines and reports
raw coverage along with upload outcomes and completion timing.

**Never include a point whose `result_valid` is `False` in the valid headline curve.**

## `ingest_errors.csv` — error taxonomy

`cli_exit_code` is included and grouped independently of the legacy HTTP field.

| Column | Meaning |
|---|---|
| `video_class`, `concurrency` | Which sweep point |
| `phase` | `ingest_request` or `readiness_check` — where the client was |
| `http_status` | Blank/none for CLI uploads |
| `outcome` | `failed`, `timed_out`, or `unconfirmed` |
| `count` | Uploads in this group |
| `example_detail` | First observed detail string, truncated to 300 characters |

`phase` names where the client was, never an internal pipeline stage. Phase 1 collected
nothing that could identify one.

## Required charts

Exactly three, all produced by `scripts/charts.py` from `csv/`. No other chart is
generated.

| Chart | Contents |
|---|---|
| `ingest-throughput-vs-concurrency.png` | `video_min_per_sec` against concurrency, one series per class. Mirrors the harness chart of the same name. Invalid points with numeric throughput are hollow markers, excluded from the valid headline curve. Points with blank throughput have no throughput marker. |
| `ingest-latency-p95-by-concurrency.png` | p95 upload latency against concurrency, one line per class |
| `ingest-outcome-by-concurrency.png` | Confirmed, unconfirmed, failed, and timed-out counts, one stacked bar per sweep point |

**The x-axis carries exactly the concurrencies that were swept.** A run of
`[1, 5, 10]` produces ticks at 1, 5, and 10 and nothing between them. An automatic
integer locator would fill in 2, 3, 4 and so on, which reads as sweep points that
were never measured.

**p95 is the only latency series plotted.** `p50` and `max` stay in
`ingest_summary.csv` and in the `summary.md` results table. Three near-parallel
lines on one axis made the chart harder to read without changing any decision made
from it; p95 is the number a capacity decision is taken on.

Figure size, dpi, marker, and axis labels match
`vss_ingest_perf/scripts/chart_throughput_vs_concurrency.py`, so a Phase 1 chart can
be laid beside a harness chart.

`--fixed-concurrency` and `--uplink-mbps` are still accepted by `charts.py` so an old
command line does not fail, but neither affects any chart. Pass `--uplink-mbps` to
`summarize.py`, which is where the uplink verdict is now computed.

## `summary.json` and `summary.md`

Written by `scripts/summarize.py` into `visualizations/`. Both state:

- **the inputs**, read back from `run-metadata.json` rather than restated from
  intent: profile, video classes, video source (corpus root or the `--video` paths),
  the measured corpus size in files/GB/video-minutes, concurrencies, VSS service URL,
  upload route, Elasticsearch readiness, timeouts, warmup, ramp, cleanup policy,
  projected transfer, whether a token was provided, and which config file was used
- **the steps the runner took**, in order, from validation through to charting
- **every output file**, with what it holds and why it matters
- that all metrics are client-observed and no cluster telemetry was collected
- the Elasticsearch readiness settings and what stops the clock
- whether the run is harness-comparable, with the reason
- whether the transfer rate approached the client uplink, when `--uplink-mbps` is given
- both mandatory warnings, verbatim

`summary.json` is the machine-readable form and carries `sweep_points_detail`, the full
summary CSV as JSON, for downstream automation. Peak throughput is reported as
unavailable (`null` in JSON) when no valid point exists.

## `run-metadata.json`

Describes the test itself: run id, profile, classes, concurrencies, corpus root and
limit, any `--video` paths and class name, upload flow and route, Elasticsearch
readiness settings, ES request and readiness timeouts, CLI command/config,
warmup, ramp, cleanup, projected bytes, redacted VSS service and Elasticsearch URLs,
auth-token presence, sweep overrides, and `telemetry_collected: "none"`.
`version_compatibility` records the successful deployed-version API check: skill
version, VSS version, compatibility requirement, endpoint, comparison convention,
and check time. These fields describe the completed check, not a guessed local
CLI or chart version.

Anything that would change a number between two runs belongs here. If a comparison
between two runs is ever questioned, this file is the first thing to diff.

## Artifact validation

The runner generates reports and invokes `scripts/validate_artifacts.py` after a
completed sweep. Repeat its read-only checks without re-uploading:

```bash
python scripts/validate_artifacts.py --results-dir /path/to/results
```

It verifies the four current CSV schemas, nonempty raw upload records, valid metadata
and summary JSON, matching run IDs and declared measurement scope, and exactly the
three required nonempty PNGs plus `summary.md`. Metadata must declare
`telemetry_collected: "none"`. When `VSS_AUTH_TOKEN` is present, every result file is
scanned for its value without echoing that value in diagnostics. Exit status is
`0` for success, `2` for failure.

Before reporting a run, confirm:

- the four CSVs exist and have headers
- `summary.json` parses and carries `run_id`, `readiness_stops_clock_at`, `harness_comparable`
- all three charts were created
- no token value appears anywhere under the results directory
- manually review summary interpretations: no text claims internal telemetry, an
  internal bottleneck, or a server-side stage timing; structural validation cannot
  establish whether a narrative conclusion is justified

### Interrupted-run ownership ledger

`raw/upload_ledger.jsonl` persists upload intents before CLI submission and
returned upload identities before ES polling, including warmups. A CLI timeout
without a returned handle retains the exact UUID name for inventory-based recovery.
Resolved handles are saved before deletion; recovery never changes ingest metrics. It supports
[interrupted-run cleanup](security-guidelines.md#interrupted-run-recovery); it does
not establish successful ingestion or replace the final metrics/artifacts.
