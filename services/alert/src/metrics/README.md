# Alert Agent Prometheus Metrics

Enable metrics before starting Alert Agent:

```bash
export PROMETHEUS_METRICS_ENABLED=true
```

The scrape endpoint is `http://<alert-agent-host>:9081/metrics` by default.
Override the port with `PROMETHEUS_PORT`. This endpoint exposes Prometheus text
format; it is not the Prometheus query API. Configure a Prometheus server to
scrape it before using PromQL or `test/latency/prometheus_latency.py`.

## Process model

The Kafka pipeline runs in the parent process and the HTTP API runs in a child
process. Both write to `prometheus_client` multiprocess shards under
`PROMETHEUS_MULTIPROC_DIR`; the parent scrape server aggregates those shards.
Do not scrape the FastAPI `/metrics` route on port 9080—it only points clients
to the scrape endpoint on port 9081.

## Kafka pipeline metrics

Kafka accounting and latency use the established aggregate series:

- `alert_bridge_upstream_duration_seconds`
- `alert_bridge_kafka_lag_duration_seconds`
- `alert_bridge_worker_queue_wait_duration_seconds`
- `alert_bridge_vst_duration_seconds`
- `alert_bridge_video_length_seconds`
- `alert_bridge_vlm_duration_seconds`
- `alert_bridge_worker_processing_seconds`
- `alert_bridge_e2e_duration_seconds`
- `alert_bridge_events_dropped_total{reason}`
- `alert_bridge_events_after_dedup_total`
- `alert_bridge_events_skipped_confirmed_total`
- `alert_bridge_events_total{verdict}`
- `alert_bridge_verification_failures_total{reason}`

These counters preserve the reconciliation rules documented in
`prometheus_metrics.py`. Do not add HTTP on-demand traffic to them.

Set `alert_agent.metrics.per_sensor_labels: true` to emit the corresponding
Kafka `*_by_sensor*` variants. This is opt-in because histogram buckets
multiplied by sensor IDs create substantial series cardinality. On-demand
metrics are not affected by this setting.

## On-demand API metrics

`POST /api/v1/verification/ondemand` uses a separate aggregate namespace:

- `alert_bridge_ondemand_requests_total{outcome}` records synchronous request
  outcomes: `accepted`, `unknown_category`, `invalid_request`, or `unknown`.
- `alert_bridge_ondemand_events_total{verdict}` records accepted requests that
  completed background processing. Verdicts are `confirmed`, `rejected`,
  `verification-failed`, or `unknown`.
- `alert_bridge_ondemand_verification_failures_total{reason}` records
  `media_download`, `vlm_api`, `vlm_schema`, `pluggable_parser`,
  `background_exception`, or `unknown`.
- `alert_bridge_ondemand_vlm_duration_seconds` measures each VLM attempt.
- `alert_bridge_ondemand_processing_seconds` measures background evaluation
  and publishing.
- `alert_bridge_ondemand_e2e_duration_seconds` measures HTTP request entry
  through background publish completion.

On-demand series deliberately have no `sensorId` label because sensor IDs are
arbitrary HTTP input. A successful evaluation may have `verdict="unknown"`
when verdict parsing is disabled; this is not a verification failure.

## Reporting

After Prometheus is scraping Alert Agent, run:

```bash
python3 test/latency/prometheus_latency.py 1h <prometheus-host>
```

The default Prometheus query port is 9090. The report prints Kafka pipeline
metrics and a separate On-Demand API section. See
[`../test/latency/README.md`](../test/latency/README.md) for options and
examples.
