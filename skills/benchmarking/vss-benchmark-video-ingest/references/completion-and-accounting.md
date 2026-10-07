# Completion and accounting

CLI success confirms VIOS media availability. The benchmark also requires outputs
from both CV and Embed to be visible in public Elasticsearch:

| Pipeline | Identity filter | Expected documents |
|---|---|---|
| Raw CV | `sensorId.keyword` = generated camera name | `max(1, ceil(duration_sec × fps) - 15)` |
| Embed | `sensor.id.keyword` = returned VIOS sensor UUID | `ceil(duration_sec / embed_chunk_duration_sec)` |

Both observed counts must be at least the expected count. Extra documents do not
prevent completion. The 15-frame subtraction is an explicit tolerance, so success
does not mean every source frame was indexed. VLM output is outside this criterion.

A partial plateau is not success, including at 95% raw coverage. The
`raw_completion_ratio` output reports observed raw count / expected raw count; it
is not a configurable success threshold. `raw_drop_grace_sec` is a failure-only
stall guard when neither index advances and work remains incomplete. Its effective
budget scales with concurrency. Absence/stall guards and the overall timeout can
end the wait with an unconfirmed result; they cannot distinguish permanent drops
from documents that may arrive later.

The default readiness timeout is derived from measured frame/chunk counts and
concurrency. `--readiness-timeout` can cap that budget. A transient network failure,
HTTP error or timeout of an ES read keeps polling within the existing budget; it
never retries the CLI upload. Cleanup verification separately fails on an ES read
error, so an uncertain cleanup does not permit the next point to start.
Cleanup requires both configured index targets to exist and be searchable;
missing indices or zero-shard responses cannot confirm removal. Readiness may
still wait for indices created by the first ingestion write.

## Timing

`latency_sec` measures client monotonic time from CLI launch to the readiness
observation. `request_sent_at` and `ingest_confirmed_at` are client UTC timestamps;
the latter is recorded on a successful readiness response. The skill does not
read a server indexing timestamp.

Polling and ES response time delay observation of completion. With healthy periodic
polling, this can include approximately a poll interval plus request latency;
transient read failures can add more. Consequently measured latency and the
throughput window include observation delay, and throughput may understate the
rate at which the backend actually finished. Compare runs using the same polling
settings and client conditions.

## Outcomes and validity

| Outcome | Meaning |
|---|---|
| `confirmed` | CLI succeeded and both expected ES counts were observed |
| `unconfirmed` | CLI succeeded but readiness or required identity could not be established |
| `failed` | CLI upload, launch or protocol failed |
| `timed_out` | CLI returned timeout exit code 7 |

Only confirmed uploads contribute video-minutes and latency percentiles.
Acknowledged payload bytes count CLI-successful uploads, including those that
remain unconfirmed in ES. Partial failed transfers and wire overhead are unknown.

A point is invalid if any upload is non-confirmed, none confirms, confirmed raw
coverage is insufficient, or a confirmed upload lacks a positive, timezone-aware
request-to-confirmation timestamp window. Invalid timing leaves throughput and
its window blank. Invalid points remain in artifacts but cannot be quoted as
valid capacity measurements.
