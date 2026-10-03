# Elasticsearch Readiness and Accounting

The benchmark has one definition of finished: RT-CV raw frames and RT-Embed
chunks are indexed in Elasticsearch. Both pipelines are mandatory and there are no
switches to disable either check. This is the `vss_ingest_perf` definition.

## Public ES endpoint (defaults to CLI discovery)

```bash
--elasticsearch-url "http://<es-host>:9200"
```

The readiness check uses:

- `mdx-raw-*`, keyed by camera name in `sensorId.keyword`
- `mdx-embed-filtered-*`, keyed by VST sensor UUID in `sensor.id.keyword`

It reads only document counts and `max(ingested_at)`. It does not collect
Elasticsearch performance metrics.

## Expected work

```text
expected_chunks = ceil(duration_sec / chunk_duration)
expected_frames = max(1, ceil(duration_sec * fps) - 15)
```

RT-CV uses live-source behavior and may drop late frames. Raw processing is
ready when either the exact expectation is reached, or coverage is at least
`raw_completion_ratio` and remains unchanged for `raw_drop_grace_sec`.

The default tuning is:

```yaml
es_readiness:
  poll_interval_sec: 5
  timeout_sec: null
  embed_chunk_duration_sec: 5
  frame_processing_time_ms: 33
  raw_drop_grace_sec: 120
  raw_completion_ratio: 0.95
```

`timeout_sec: null` keeps the harness-derived per-upload budget. An explicit
`--readiness-timeout` is a hard ceiling.

## Timing

Client latency starts before CLI process launch and ends when readiness
is established. `ingest_confirmed_at` is `max(ingested_at)` from the matched
documents, so the success-window calculation does not inherit poll-interval
quantization.

## Outcomes

| Outcome | Meaning |
|---|---|
| `confirmed` | Upload succeeded and Elasticsearch readiness was reached |
| `unconfirmed` | Upload succeeded but readiness was not reached in time |
| `failed` | Upload or service request failed |
| `timed_out` | CLI returned timeout exit code 7 |

Only confirmed uploads contribute video-minutes and latency percentiles.
Acknowledged payload bytes count CLI-successful uploads, including those still
unconfirmed in ES. Partial failed transfers and wire overhead are unknown.

## Validity

A sweep point is invalid when:

- any upload fails or remains unconfirmed;
- no upload confirms;
- the slowest confirmed stream indexes less than 95% of expected raw frames; or
- any confirmed upload lacks parseable, timezone-aware request and completion
  timestamps, or its completion is not strictly after its request.

Invalid timing leaves throughput and the success window blank and records a reason;
client latency measurements remain available for diagnosis.

Invalid points remain in the artifacts but are excluded from headline curves.
