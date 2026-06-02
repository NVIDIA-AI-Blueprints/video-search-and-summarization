# Alert Bridge Functional Tests

Standalone shell scripts for running functional tests in discrete, independently runnable steps.

## Quick Start

```bash
# Run all steps sequentially
./run_all.sh

# Run individual steps
./step1_start_simulators.sh   # Start Kafka + simulators
./step2_start_alert_bridge.sh # Start Alert Bridge
./step3_trigger_incident.sh   # Send test incident
./step4_check_results.sh      # Verify results

# Cleanup when done
./cleanup.sh
```

## Steps Overview

| Step | Script | What It Does |
|------|--------|--------------|
| 1 | `step1_start_simulators.sh` | Starts Kafka container and all 4 simulators (Elastic, NIM, VST, VSS) |
| 2 | `step2_start_alert_bridge.sh` | Runs Alert Bridge with simulator config |
| 3 | `step3_trigger_incident.sh` | Produces a test incident to Kafka |
| 4 | `step4_check_results.sh` | Polls Elasticsearch for processed results |

## Environment Variables

### Step 1 (Simulators)
- `PID_DIR` - Directory for PID files (default: `/tmp/alert_agent_functional`)

### Step 2 (Alert Bridge)
- `USE_DOCKER` - Set to `true` to run Alert Bridge in Docker (default: `false`)
- `DOCKER_IMAGE` - Docker image name (default: `alert-bridge:local`)
- `CONFIG_FILE` - Config file path (default: `test/e2e/kafka_incident/config_sim.yaml`)

### Step 3 (Trigger Incident)
- `PAYLOAD` - Path to incident JSON (default: `test/protobuf/test_data/sample_incident.json`)
- `BOOTSTRAP` - Kafka bootstrap servers (default: `127.0.0.1:9092`)
- `TOPIC` - Kafka topic (default: `mdx-incidents`)

### Step 4 (Check Results)
- `ES_HOST` - Elasticsearch host (default: `http://127.0.0.1:9200`)
- `TIMEOUT` - Max seconds to poll (default: `60`)
- `INTERVAL` - Seconds between polls (default: `5`)

## Examples

### Run with Docker
```bash
USE_DOCKER=true ./step2_start_alert_bridge.sh
```

### Custom incident payload
```bash
PAYLOAD=test/protobuf/test_data/custom_incident.json ./step3_trigger_incident.sh
```

### Extended timeout for slow systems
```bash
TIMEOUT=120 INTERVAL=10 ./step4_check_results.sh
```

### Run a single step
```bash
./run_all.sh --step 3  # Only trigger incident
```

## Services and Ports

| Service | Port | Health Check |
|---------|------|--------------|
| Kafka | 9092 | TCP connect |
| Elasticsearch (sim) | 9200 | `GET /health` |
| NIM (sim) | 18081 | TCP connect |
| VST (sim) | 30888 | `GET /status` |
| VSS (sim) | 8080 | `GET /models` |

## Troubleshooting

### Simulators not starting
```bash
# Check if ports are in use
netstat -tlnp | grep -E '9200|18081|30888|8080'

# Check simulator logs
cat /tmp/alert_agent_functional/alert_bridge.log
```

### Kafka not available
```bash
# Check Docker container
docker ps | grep kafka
docker logs alert-agent-kafka-test
```

### No results in Step 4
```bash
# Check Alert Bridge is running
ps aux | grep enhance_alert

# Check Alert Bridge logs
cat /tmp/alert_agent_functional/alert_bridge.log

# Manually query Elasticsearch
curl http://127.0.0.1:9200/mdx-vlm-incidents-$(date +%Y-%m-%d)/_all
```

## Adding New Test Scenarios

Step1–4 are shared building blocks: setup → start AB → trigger → verify (ES). For self-contained tests with custom configs and assertions, see the `p1/` subdirectory.

- Each test in `p1/` is a directory with a `run.sh` and optionally a `config.yaml`
- Tests without a `config.yaml` automatically use the shared base config
- Tests only need a custom config when they require non-default AB settings (e.g., verdict protection, enrichment)
- For tests with different output sinks (Kafka, Redis), write your own verification in `run.sh`
- The framework handles timestamps, dedup isolation, and state reset between tests automatically

## File Structure

```
test/functional/
├── step1_start_simulators.sh   # Start Kafka + simulators
├── step2_start_alert_bridge.sh # Start Alert Bridge
├── step3_trigger_incident.sh   # Send incident to Kafka
├── step4_check_results.sh      # Verify in Elasticsearch
├── run_all.sh                  # Orchestrator
├── cleanup.sh                  # Stop everything
└── README.md                   # This file
```

## Dependencies

- Docker (for Kafka container)
- Python 3.x with project dependencies
- `curl`, `nc` (netcat) for health checks
