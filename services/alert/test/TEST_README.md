# Testing the Alert Agent with VSS Integration

## Directory Layout

Tests are grouped by type so unit tests stay isolated from the rest:

| Path | Type | How to run |
|------|------|------------|
| `test/unit/` | Fast, mocked **unit tests** (pytest) | `pytest test/unit` |
| `test/functional/` | Functional P1 suite (simulators) | `cd test/functional/p1 && ./run_p1.sh` |
| `test/e2e/` | End-to-end pipeline tests | `pytest test/e2e` |
| `test/latency/` | Performance / latency tooling + its unit test | `pytest test/latency` |
| `test/sanity/` | Deployment sanity checks (shell) | `ES_HOST=... test/sanity/run_sanity.sh` |
| `test/protobuf/`, `test/sim_scripts/` | Shared tooling (producers, simulators) — used by functional/e2e, not pytest tests | — |
| `test/test_lite/` | Manual local integration helpers (Kafka/MinIO) | see below |

## Prerequisites

1. Make sure you have test video files available
2. Make sure your VSS agent is running and configured in `config.yaml`
3. Ensure Python dependencies are installed

For Kafka and on-demand Prometheus latency/counter reports, see
[`latency/README.md`](latency/README.md). The report includes a separate
On-Demand API section for `alert_bridge_ondemand_*` metrics.

## Quick Start

### Using Docker Compose

```bash
# Default: Kafka source/sink, no Redis required
docker compose -f deploy_docker-compose.yml up -d

# config.yaml defaults: sourceType: "kafka", sinkType: "kafka"
```

> Redis has been removed entirely. Dedup / filter state is in-process and
> durable state (verdict protection, alert configs) lives in Elasticsearch.
> Ingestion is over Kafka (or the HTTP API); there is no Redis Streams
> transport.

### Testing with Kafka

```bash
# 1. Start Kafka services
docker compose -f test/test_lite/kafka/docker-compose.yml up -d

# 2. Create topics (first time only)
cd test/test_lite/kafka
python3 create_topics.py

# 3. Start the alert agent (in a new terminal)
python3 enhance_alert_with_vlm.py --config config.yaml

# 4. Send test messages (in another terminal)
cd test/test_lite/kafka
python3 send_payload.py

# 5. Verify responses
python3 verify_responses.py
```

---

## Testing Direct Media URL Feature 

Mode 3 allows processing media from URLs directly, bypassing VST entirely.

### Prerequisites

1. Set `vst_pass_through_mode: true` in config.yaml
2. Ensure VLM service is available
3. **VST is NOT required** for Mode 3

### Config for Mode 3 Testing

```yaml
alert_agent:
  vst_pass_through_mode: true    # REQUIRED for Mode 3
  media_download:
    enabled: true
    timeout_seconds: 30
    max_size_mb: 50
```

### Running Integration Tests

```bash
# 1. Start Kafka services
docker compose -f test/test_lite/kafka/docker-compose.yml up -d

# 2. Start alert agent with pass-through mode
python3 enhance_alert_with_vlm.py --config config.yaml

# 3. Send Direct Media URL payloads from a folder
cd test/test_lite/kafka
python3 send_direct_media_payload.py /path/to/media_folder

# The script will:
# - Scan folder for all video/image files
# - Start HTTP server to serve files
# - Send Kafka message for each file

# Example with custom keep-alive time:
python3 send_direct_media_payload.py /path/to/media_folder 
```

### Running Unit Tests

```bash
# Run all Direct Media URL tests
pytest test/unit/test_direct_media_url.py -v
```

### Supported Media Formats

| Type | Extensions |
|------|------------|
| Image | .jpg, .jpeg, .png, .gif, .webp, .bmp |
| Video | .mp4, .avi, .mov, .mkv, .webm, .flv, .wmv |

### Expected Output

```json
{
  "info": {
    "media_url": "http://localhost:12345/video.mp4",
    "media_type": "video",
    "reasoning": "Analysis result from VLM...",
    "verdict": "",
    "videoSource": "http://localhost:12345/video.mp4",
    "verificationResponseCode": 200,
    "verificationResponseStatus": "OK"
  }
}
```