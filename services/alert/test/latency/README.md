# Alert Verification Latency Measurement

## Prerequisites

- `curl`, `python3`, `jq` installed
- Access to Elasticsearch host
- **Alert Agent must have latency tracking enabled** (see Step 0)
- For full pipeline metrics (Step 3): `pip install requests`, access to Prometheus host, and `PROMETHEUS_METRICS_ENABLED=true` on the Alert Agent

## Step 0: Enable Latency Tracking in Alert Agent

Edit your Alert Agent `config.yaml` to enable latency instrumentation:

```yaml
alert_agent:
  include_latency_info: true
```

Restart the Alert Agent for changes to take effect.

> **Important:** Without this flag, the `info.latency.*` fields won't be present in documents, and most latency metrics will show as N/A.

## (Optional) Enable Prometheus Metrics

Alert Agent exposes a Prometheus scrape endpoint that tracks pipeline latency histograms, event counters, and verification failures. Two flags control this feature.

### Master switch — environment variable

```bash
export PROMETHEUS_METRICS_ENABLED=true
```

The Alert Agent's metrics endpoint is not scraped automatically. You need a Prometheus instance running and configured to point at it.

### Per-sensor metric variants — config flag

Edit `config.yaml` to also emit per-`sensorId` histograms and counters:

```yaml
alert_agent:
  metrics:
    per_sensor_labels: true  # default: false
```

When enabled, every aggregate metric (e.g. `alert_bridge_vst_duration_seconds`) also has a `*_by_sensor` variant labelled with `sensorId`. Each distinct sensor ID mints roughly 80 additional Prometheus series; the recorder caps distinct IDs at 128 (≈10k series total) and folds any overflow to `unknown_overflow`.

> **Note:** `per_sensor_labels` has no effect unless `PROMETHEUS_METRICS_ENABLED=true`. Only enable it for deployments with a bounded sensor fleet and a known series budget.

## Step 1: Setup ES Pipeline (one-time)

Enable `info.indexedAt` timestamp on ES:

```bash
./enable_indexed_at.sh <ES_HOST>
```

Example:
```bash
./enable_indexed_at.sh localhost
```

> **Note:** Only new documents will have `info.indexedAt`. Existing documents won't be updated.

## Step 2: Run Latency Analysis

```bash
./alert_bridge_latency.sh <duration> <ES_HOST>
```

`<duration>` uses ES time notation: `30m`, `1h`, `2h`, `1d`, etc.

Example:
```bash
./alert_bridge_latency.sh 1h localhost
```

### Output

**Console** - Summary stats (avg, p50, p90, p99, max) for each stage:

| Metric | Description |
|--------|-------------|
| Upstream (CV+Analytics) | CV pipeline + Analytics delay before Kafka publish (`kafka_published_at - end`) |
| AB Consumer Lag | Kafka publish → consume (consumer lag) |
| VST Fetch | Video URL fetch duration |
| VLM Inference | VLM model inference duration |
| Incident End → ES Indexed | Incident end time → ES indexed (E2E) |
| Incident Start → ES Indexed | Incident start time → ES indexed (E2E) |

> **Note:** The ES schema uses the `timestamp` field (incident start time), not `@timestamp`.

**CSV file** - Per-incident breakdown in table format:

```
#,Upstream (CV+Analytics),AB Consumer Lag,VST Fetch,VLM Inference,Incident End → ES Indexed,Incident Start → ES Indexed
1,312ms,101ms,1.35s,2.67s,4.50s,7.12s
2,287ms,173ms,1.35s,2.39s,4.30s,6.88s
```

> **Note:** `info.indexedAt` is optional (requires ES ingest pipeline). If not available, Incident End → ES Indexed and Incident Start → ES Indexed will show as N/A.

## Step 3: Full Pipeline Metrics via Prometheus

The bash script (`alert_bridge_latency.sh`) queries Elasticsearch and can only report on events that were fully indexed — it shows a subset of stages and no event counts or failure breakdown. `prometheus_latency.py` queries Prometheus (and optionally ES) to give a complete picture: all pipeline stages, event lifecycle counts, drop reasons, and verification failure reasons.

**Requires:** `PROMETHEUS_METRICS_ENABLED=true` on the Alert Agent (see above). Prometheus must be scraping the Alert Agent's scrape port (default `9081`).

### Basic usage

```bash
pip install requests   # one-time

python3 prometheus_latency.py <duration> <PROMETHEUS_HOST>
```

`<duration>` uses the same notation as the bash script: `30m`, `1h`, `2d`, etc.

Examples:

```bash
# Prometheus only
python3 prometheus_latency.py 1h localhost

# Prometheus + Elasticsearch side-by-side
python3 prometheus_latency.py 1h localhost --es-host localhost

# Custom ports
python3 prometheus_latency.py 2h localhost --prom-port 9090 --es-host localhost --es-port 9200
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--prom-port PORT` | `9090` | Prometheus server port |
| `--es-host HOST` | _(none)_ | Also print the ES-based latency table |
| `--es-port PORT` | `9200` | Elasticsearch port |
| `--per-sensor` | off | Print an all-sensors breakdown (one row per `sensorId`). Requires `per_sensor_labels: true` |
| `--sensor-id ID` | _(none)_ | Restrict the Prometheus report to a single sensor. Requires `per_sensor_labels: true` |

### Output

The Prometheus section prints avg / p50 / p90 / p99 for every pipeline stage, followed by event lifecycle counts and a full failure-reason breakdown:

| Section | Metrics |
|---------|---------|
| Pipeline latency | Upstream (CV+Analytics), Kafka Consumer Lag, Worker Queue Wait, VST Fetch, Video Clip Length, VLM Inference, Worker Processing, E2E |
| Event counts | Raw Kafka, Dropped (pre), After dedup, Confirmed / Rejected / Verification-fail / Unknown, Skipped (confirmed), Total published, VLM retry rate |
| Events dropped | `end_time_delta`, `dedup`, `rate_limit` with counts and percentages |
| Verification failures | Full VST + VLM failure taxonomy (16 reasons) with counts and percentages |

### Per-sensor modes

Both flags require `alert_agent.metrics.per_sensor_labels: true` in `config.yaml`.

```bash
# All-sensors table: confirmed / rejected / failed / dropped per sensorId
python3 prometheus_latency.py 1h localhost --per-sensor

# Single-sensor: full latency table + counter breakdown for one camera
python3 prometheus_latency.py 1h localhost --sensor-id cam-42
python3 prometheus_latency.py 1h localhost --sensor-id 'camera/hall-west:stream0'
```

When `--sensor-id` is combined with `--es-host`, the ES section is skipped (Elasticsearch may not contain every processed event; the per-sensor Prometheus data is the authoritative source).

### Exit codes

| Code | Meaning |
|------|---------|
| `0` | Success — at least one section produced output |
| `2` | Invalid `<duration>` format |
| `3` | One or more query endpoints unreachable or returned an error (report may be incomplete) |

## Cleanup (optional)

Remove the `info.indexedAt` pipeline:

```bash
./enable_indexed_at.sh <ES_HOST> --delete
```
