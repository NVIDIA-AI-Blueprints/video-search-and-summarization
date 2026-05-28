# Configuration

## Default Configuration

The default bootstrap configuration is located at `configs/default-configs/config.json`. The server loads this automatically on startup.

## Custom Configuration

To override the defaults, create a copy of the config file and pass the path as a command-line argument:

```bash
cd src/app
npm start -- --config /absolute/path/to/config.json
```

## Sample Configurations

Sample configurations are available in `configs/sample-configs/`:

- `calibration.json` - Sample calibration configuration

## Configuration Sections

### Server

| Option | Description | Default |
|--------|-------------|---------|
| `server.port` | HTTP port for the API server | 8081 |
| `server.configs.postBodySizeLimit` | Maximum POST body size | 50mb |
| `server.configs.amrRetentionInSec` | AMR data retention period in seconds | 300 |
| `server.configs.inSimulationMode` | Enable simulation mode | false |

### Elasticsearch

| Option | Description | Default |
|--------|-------------|---------|
| `elasticsearch.node` | Elasticsearch connection URL | http://localhost:9200 |
| `elasticsearch.indexPrefix` | Prefix for Elasticsearch indices | mdx- |
| `elasticsearch.retries` | Maximum retries per request | 15 |

### Kafka

| Option | Description | Default |
|--------|-------------|---------|
| `kafka.brokers` | Comma-separated list of Kafka brokers | (none) |
| `kafka.retries` | Maximum retries for Kafka client | null |

Kafka is optional and only required for RTLS application and notification-related functionalities.
