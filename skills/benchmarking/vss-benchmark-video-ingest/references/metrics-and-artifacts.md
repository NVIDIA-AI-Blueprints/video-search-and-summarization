# Metrics and artifacts

A completed sweep produces four UTF-8 CSVs, raw JSONL, run metadata and exactly
three charts. A failed warmup retains metadata, corpus, ledger and warmup details,
without sweep CSVs or charts. Reports and charts are regenerated from CSVs and
metadata without contacting the deployment.

```text
results/
├── csv/
│   ├── ingest_corpus.csv
│   ├── ingest_requests.csv
│   ├── ingest_summary.csv
│   └── ingest_errors.csv
├── raw/
│   ├── upload_details.jsonl
│   ├── upload_ledger.jsonl
│   └── warmup_details.jsonl       # when warmup ran
├── run-metadata.json
└── visualizations/
    ├── summary.md
    ├── summary.json
    ├── ingest-throughput-vs-concurrency.png
    ├── ingest-latency-p95-by-concurrency.png
    └── ingest-outcome-by-concurrency.png
```

## Corpus and upload records

`ingest_corpus.csv` records each source's `video_id`, class, path, bytes, duration,
frame rate, frame geometry, codec, container, content type and probe status.
Durations and frame rates come from ffprobe; an unreadable video fails validation.

`ingest_requests.csv` has one row per attempted upload:

| Fields | Meaning |
|---|---|
| `worker_index`, `upload_sequence` | Worker and sequence within the point |
| `video_id`, `video_class`, `concurrency` | Source and workload |
| `upload_filename` | Unique generated name, `<point_uuid>-<sequence>.<ext>` |
| `bytes`, `duration_sec` | Measured source size and duration |
| `request_sent_at` | Client UTC immediately before CLI launch |
| `ingest_confirmed_at` | Client UTC on readiness observation; blank unless confirmed |
| `latency_sec` | Client monotonic time from CLI launch through readiness observation |
| `cli_exit_code`, `cli_duration_sec` | Actual CLI process outcome and elapsed time |
| `outcome` | `confirmed`, `unconfirmed`, `failed` or `timed_out` |
| `expected_frames`, `es_frame_count` | Adjusted expected and observed raw frame counts |
| `expected_chunks`, `es_chunk_count` | Expected and observed Embed chunk counts |
| `raw_completion_ratio` | Observed raw count / expected raw count |
| `rt_cv_dropped` | Failure flag for stalled incomplete indexing, not proof of its cause |

`expected_frames = max(1, ceil(duration_sec × fps) - 15)`;
`expected_chunks = ceil(duration_sec / embed_chunk_duration_sec)`.
Both counts must reach their expectations. Coverage is diagnostic, not permission
to count a partial video as complete.

These are client observations. Poll intervals, ES response time and temporary read
failures can delay confirmation beyond actual backend completion. The CLI's own
media wait can also outlast indexing. The skill does not measure server indexing
instants or internal pipeline durations.

`raw/upload_details.jsonl` adds returned `sensor_id`, CLI response, diagnostics,
acknowledged payload bytes, readiness poll count and cleanup detail. Warmups have
a separate raw file and are excluded from measured metrics. Ownership evidence
and interrupted-run recovery are described in [security](security-guidelines.md).

## Sweep-point metrics

`ingest_summary.csv` has one row per `(video_class, concurrency)`:

| Fields | Meaning |
|---|---|
| `upload_count` | Attempts: workers × selected files |
| `success_count`, `failure_count`, `success_rate_pct` | Confirmed and non-confirmed uploads |
| `result_valid`, `result_invalid_reason` | Whether the point supports a capacity result, and why |
| `min_raw_completion_ratio` | Lowest raw coverage among confirmed uploads |
| `total_video_duration_min`, `total_video_size_gb` | Confirmed source volume, including worker repetitions |
| `wall_clock_min` | Point elapsed time before cleanup |
| `success_window_sec` | Last client confirmation minus first client request among confirmed uploads |
| `video_min_per_sec` | Confirmed video-minutes / success window |
| `aggregate_mb_per_sec` | CLI-successful source MB / point wall time |
| `p50_latency_sec`, `p95_latency_sec`, `max_latency_sec` | Confirmed-upload latency statistics within this point |
| `cli_exit_statuses` | Actual CLI exit-code histogram over all attempts |

Percentiles use linear interpolation over sorted confirmed-upload latencies.
Partial failed transfers and wire overhead are unknown; payload MB/s is not a
wire-bandwidth measurement and cannot establish or exclude an uplink bottleneck.

Every confirmed timestamp pair must parse with a timezone and have completion
strictly after request. Invalid timing leaves throughput and its window blank,
rather than manufacturing a tiny denominator. Points with no confirmed uploads
retain zero throughput and remain invalid. All invalid points are excluded from
the throughput headline.

`ingest_errors.csv` groups non-confirmed uploads by class, concurrency, observed
client phase, CLI exit and outcome, with counts and a bounded example diagnostic.
Its phase names identify the client operation, not a failed internal service.

## Reports and charts

`summary.md` and `summary.json` describe actual inputs from metadata, steps, outcomes,
excluded points, artifact paths and client-observation limitations. JSON includes
the point table for automation. Peak throughput is unavailable when no valid
point exists.

The throughput chart shows one series per class, with invalid numeric points as
unjoined hollow markers. Missing throughput has no marker. The latency chart shows
p95; p50 and maximum remain in the CSV and report. The outcome chart stacks
confirmed, unconfirmed, failed and timed-out counts per point. X-axis ticks show
only tested concurrencies.

## Metadata and validation

`run-metadata.json` records the selected corpus/matrix, endpoint configuration,
installed CLI, version-check evidence, timeouts, polling, warmup, ramp, cleanup,
transfer projection and config provenance. It is written before warmup, so setup
and cleanup evidence also survive a warmup failure. Authentication is recorded only as
present or absent. Compare this file first when two runs disagree.

`es_readiness.completion_rule` records the count criterion; `clock_stops_at`
identifies client-observed completion. `cleanup_verification` records timeout,
settle period and scope. `cleanup_totals.deleted` requires both confirmed CLI
recording removal and successful ES settling; `failed` includes verification
failures. `cleanup_checks` records each warmup/point verification result, including
its outcome, detail, poll count and elapsed wait; per-upload cleanup details retain
the associated diagnostic. Cleanup stays outside upload and point metrics.

```bash
python scripts/validate_artifacts.py --results-dir /path/to/results
```

Validation checks current CSV schemas, raw records, metadata and summary JSON,
consistent run IDs and measurement scope, and the three nonempty PNGs plus report.
When `VSS_AUTH_TOKEN` is present, it scans every result file for that value and its
JSON-escaped form without echoing it. Exit 0 means these checks passed; exit 2
means failure. Review report interpretations separately: structural validation
cannot establish that a bottleneck claim is justified.
