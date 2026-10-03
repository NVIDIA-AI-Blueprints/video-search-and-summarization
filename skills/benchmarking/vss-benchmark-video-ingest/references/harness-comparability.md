# Harness comparability

The formulas, concurrency matrix, ES readiness rules, and linear-interpolated
percentiles retain the vss_ingest_perf convention. The request path does not:

| Old baseline | Current skill |
|---|---|
| Agent PUT /api/v1/videos-for-search/{filename} | One checkout CLI process calling VIOS |
| Agent orchestrates processing | VIOS camera_streaming webhooks dispatch consumers |
| Client measures a REST call + ES wait | Client measures process launch + CLI media wait + ES wait |
| HTTP status and transfer accounting | CLI exit code; acknowledged payload bytes only |
| Agent lifecycle deletion | VIOS CLI deletion; asynchronous removal webhooks |

Metadata therefore records harness_comparable=false and
metric_definitions_match_harness=true. Identical column names do not imply identical
workloads. Establish a new CLI/webhook baseline, then compare runs with the same
corpus, CLI runtime, routing, model settings, concurrency/stagger, readiness policy,
client resources, and network path. Model initialization and CLI overhead can dominate
short clips. Failed points do not establish capacity. No internal telemetry is collected.
