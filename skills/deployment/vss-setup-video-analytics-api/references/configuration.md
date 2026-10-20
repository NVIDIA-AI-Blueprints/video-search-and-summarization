# Configuration Guide

## Overview

The video-analytics-api server loads a JSON config file at startup via the `--config <path>` CLI flag. The config controls server port, Elasticsearch connection, Kafka connection, and application-level tuning.

## Structure

```json
{
  "server": {
    "port": 8081,
    "configs": [...]
  },
  "elasticsearch": {
    "node": "http://localhost:9200",
    "indexPrefix": "mdx-",
    "rawIndex": "mdx-raw-*",
    "retries": 15
  },
  "kafka": {
    "brokers": ["localhost:9092"],
    "retries": null
  }
}
```

## Sections

### `server`

| Field | Type | Default | What it controls |
|---|---|---|---|
| `port` | number | `8081` | HTTP port the API listens on. |
| `configs[]` | array of `{name, value}` | see below | Application-level tuning knobs. |

#### `server.configs[]` keys

| Key | Type | Default (service-shipped) | Default (image-baked) | What it controls |
|---|---|---|---|---|
| `postBodySizeLimit` | string | `"50mb"` | `"50mb"` | Maximum POST body size accepted by Express. |
| `amrRetentionInSec` | string | `"3"` | `"3"` | How long AMR data is retained in memory (seconds). |
| `inSimulationMode` | string | `"false"` | `"false"` | Whether the server runs in simulation mode. |
| `configStatusTimeoutMs` | string | `"30000"` | `"30000"` | How long to wait for an ACK from behavior-analytics after publishing a config update (milliseconds). |
| `configStatusTimeoutCheckFrequencyMs` | string | `"900000"` | `"900000"` | How often the server checks for timed-out config update ACKs (milliseconds). |

### `elasticsearch`

| Field | Type | Default | What it controls |
|---|---|---|---|
| `node` | string | `"http://localhost:9200"` | Elasticsearch URL. The server pings this on startup; if unreachable, the server exits. |
| `indexPrefix` | string | `"mdx-"` | Prefix for all Elasticsearch index names. |
| `rawIndex` | string | `"mdx-raw-*"` | Raw data index pattern. |
| `retries` | number | `15` | Number of Elasticsearch connection retries before giving up. |

### `STREAM_TYPE` environment variable

| Variable | Allowed values | Default | What it controls |
|---|---|---|---|
| `STREAM_TYPE` | `kafka`, `redis` | `kafka` | Selects startup behavior. `kafka` enables configured Kafka readiness/workers; `redis` skips Kafka startup work. Invalid values make the server exit. |

### `kafka`

| Field | Type | Default | What it controls |
|---|---|---|---|
| `brokers` | array of strings | `["localhost:9092"]` (service-shipped) / `[]` (image-baked) | Kafka broker addresses. With `STREAM_TYPE=kafka`, an empty array or `null` skips Kafka startup work. `STREAM_TYPE=redis` also skips Kafka startup work, regardless of this setting. |
| `retries` | number or null | `null` | KafkaJS retry count. `null` uses KafkaJS defaults. |

## Config sources

Three viable sources, in order of increasing customization:

### Image-baked default

Path inside container: `/configs/default-configs/config.json`

Assumes Elasticsearch at `http://localhost:9200`, index prefix `mdx-`, Kafka **disabled** (empty brokers list), server port **8081**.

### Service-shipped config (default in compose)

Path on host: `services/analytics/video-analytics-api/configs/vss-video-analytics-api-config.json`

Identical to the image-baked default except Kafka is **enabled** (`brokers: ["localhost:9092"]`). This is the right choice when you have a local Kafka broker running.

### Custom config

Any absolute host path. Copy one of the above as a starting point and edit. Bind-mount it into the container via the compose `volumes:` section.

## Minimal example

```json
{
  "server": {
    "port": 8081,
    "configs": [
      { "name": "postBodySizeLimit", "value": "50mb" },
      { "name": "inSimulationMode", "value": "false" }
    ]
  },
  "elasticsearch": {
    "node": "http://localhost:9200",
    "indexPrefix": "mdx-",
    "rawIndex": "mdx-raw-*",
    "retries": 15
  },
  "kafka": {
    "brokers": ["localhost:9092"],
    "retries": null
  }
}
```

## Tips

- Keep `server.configs[].value` as strings — the server parses types internally.
- When running with `network_mode: "host"`, Elasticsearch and Kafka must also be on the host network.
- `STREAM_TYPE` must be `kafka` or `redis`; when unset, it defaults to `kafka`. Select `redis` to skip Kafka startup work. For `kafka`, set `kafka.brokers` to `[]` to skip Kafka startup work. Kafka-dependent endpoints (dynamic config, dynamic calibration, RTLS/AMR) require `STREAM_TYPE=kafka` and configured brokers.
- The `amrRetentionInSec` default is `"3"` in both the service-shipped config and the image-baked default.
