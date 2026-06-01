# VSS Video Analytics API

This module contains a Node.js REST API service for VSS Video Analytics applications.

## Introduction

`vss-video-analytics-api` exposes Express endpoints for querying analytics data, managing sensors and runtime configuration, and serving warehouse / search / alerts workflows. It reads analytics results from Elasticsearch and optionally uses Kafka for notification-driven features such as dynamic configuration, dynamic calibration, RTLS, and AMR data.

Frame, behavior, event, alert, incident, tracker, clustering, and metrics data is produced by upstream services and stored in Elasticsearch for this API to query.

![Block Diagram - Architecture](./readmes/images/Architecture.png)

### Available Endpoints

The REST routes are registered from `src/app/controllers/rest-apis/`. The API provides endpoints for the following functionalities:

- **Metrics** - Computation of key performance indicators
- **Tracker** - Tracker-related data retrieval
- **Frames** - Frame metadata
- **Behavior** - Behavior metadata
- **Clustering** - Clustering results
- **Events** - Event data
- **Sensor** - Sensor-related operations
- **Config** - Configuration management
- **Alerts** - Alert notifications
- **Incidents** - Incident tracking

The OpenAPI specification lives in [`src/app/specification/openapi.json`](./src/app/specification/openapi.json).

### Configuration Schema

Configuration is JSON, loaded at startup by `src/app/index.js` via the `--config <path>` CLI flag and consumed by the app initializers under `src/app/initializers/`. The default configuration lives at [`configs/default-configs/config.json`](./configs/default-configs/config.json).

## Getting Started

### Dependencies

1. [Node.js](https://nodejs.org/) version 22.22.3
2. Elasticsearch version 9.3.4
3. Kafka (optional) - required for notification, dynamic-config, dynamic-calibration, RTLS, and AMR workflows

### Installation

```bash
cd src/web-api-core
npm install

cd ../app
npm install --save ../web-api-core
npm install
```

### Running the Server

The video-analytics-api server can be started with default configs using the following command:

```bash
cd src/app
npm start
```

The default configs can be overridden by passing command line argument `config`. The following command can be used to start the server with user provided configs:

```bash
cd src/app
npm start -- --config /absolute/path/to/config.json
```

The server listens on port **8081** by default. Once Elasticsearch is reachable and routes are registered, verify the service with:

```bash
curl -sf http://localhost:8081/livez
```

See [`readmes/docker.md`](./readmes/docker.md) for Docker build and run examples.

## Configuration

The default configuration can be found at [config.json](./configs/default-configs/config.json).

### Configuration Options

| Section | Option | Description |
|---------|--------|-------------|
| `server.port` | HTTP port | Default: `8081` |
| `server.configs[].postBodySizeLimit` | Request body limit | Maximum POST body size. Default: `50mb` |
| `server.configs[].amrRetentionInSec` | AMR retention | AMR data retention period in seconds. Default: `3` |
| `server.configs[].inSimulationMode` | Simulation mode | Enables simulation behavior. Default: `false` |
| `server.configs[].configStatusTimeoutMs` | Config ACK timeout | Time to wait for config-update acknowledgement. Default: `30000` |
| `server.configs[].configStatusTimeoutCheckFrequencyMs` | Config timeout scan interval | Interval for checking timed-out config updates. Default: `900000` |
| `elasticsearch.node` | Elasticsearch URL | Default: `http://localhost:9200` |
| `elasticsearch.indexPrefix` | Index prefix | Default: `mdx-` |
| `elasticsearch.rawIndex` | Raw-data index pattern | Default: `mdx-raw-*` |
| `elasticsearch.retries` | Elasticsearch retries | Client retry count. Default: `15` |
| `kafka.brokers` | Kafka broker list | Empty or missing disables Kafka-dependent features |
| `kafka.retries` | KafkaJS retries | Client retry count. Default: `null` |

**Note**: If any change needs to be made, it is recommended to create a copy of the config file and make changes so that the default-config is preserved.

For the full configuration guide, see [`readmes/configuration.md`](./readmes/configuration.md).

## Documentation

- [Configuration Guide](./readmes/configuration.md)
- [Docker and Deployment](./readmes/docker.md)
- [Modules Overview](./readmes/modules-overview.md)
- [Testing Guide](./readmes/testing.md)
