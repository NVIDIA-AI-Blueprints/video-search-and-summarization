# MV3DT BEV Measurement Fusion

The `measurement-fusion` service (image `vss-rt-cv-mv3dt-bev-fusion`) is the
companion Birds-Eye-View (BEV) fusion service for the MV3DT RTVI-CV pipeline. It
consumes raw per-sensor 3D measurements emitted by the `perception` service,
groups measurements into wall-clock timestamp buckets, merges same-object
measurements across camera views, and republishes fused measurements for
downstream microservices and applications.

## What It Does

- Reads per-sensor 3D measurements from `RAW_TOPIC` (default: `mdx-raw`).
- Buckets protobuf `Frame` messages by timestamp instead of `frame_id`, because
  separate DeepStream containers can have independent frame counters.
- Flushes a bucket as soon as all expected sensors arrive, or after the
  configured timeout.
- Merges measurements with the same globally consistent object ID by averaging
  timestamps, 3D bounding-box coordinates, and confidence scores.
- Resolves object type disagreements by majority vote, using total confidence as
  the tie-breaker.
- Publishes fused BEV tracks to `FUSED_TOPIC` (default: `mdx-bev`) with
  `sensorId=bev-sensor-1`.

## Source Files

The source files that implement the service are:

| Path                                       | Purpose                                                                 |
| ------------------------------------------ | ----------------------------------------------------------------------- |
| `src/measurement_fusion.py` | Service entry point and fusion logic.                                   |
| `src/schema_pb2.py`         | Generated protobuf schema used to parse and serialize `Frame` messages. |

## Build

This source tree is intended to live in the broader VSS project as the
`rt-cv-bev-fusion` service directory:

```bash
services/rtvi/rt-cv-3d/rt-cv-bev-fusion
```

Build the image:

```bash
cd services/rtvi/rt-cv-3d/rt-cv-bev-fusion

docker build \
  -f Dockerfiles/measurement-fusion.Dockerfile \
  -t vss-rt-cv-mv3dt-bev-fusion:local \
  .
```

## Runtime Configuration

All runtime configuration is supplied through environment variables.

| Variable                     | Default          | Description                                                                                  |
| ---------------------------- | ---------------- | -------------------------------------------------------------------------------------------- |
| `BROKER_TYPE`                | `kafka`          | Broker backend. Supported values are `kafka` and `redis`.                                    |
| `KAFKA_BOOTSTRAP`            | `localhost:9092` | Kafka bootstrap servers when `BROKER_TYPE=kafka`.                                            |
| `REDIS_HOST`                 | `localhost`      | Redis host when `BROKER_TYPE=redis`.                                                         |
| `REDIS_PORT`                 | `6379`           | Redis port when `BROKER_TYPE=redis`.                                                         |
| `RAW_TOPIC`                  | `mdx-raw`        | Input Kafka topic or Redis stream containing raw per-sensor 3D measurement `Frame` messages. |
| `FUSED_TOPIC`                | `mdx-bev`        | Output Kafka topic or Redis stream for fused BEV tracks.                                     |
| `CONSUMER_POLL_MS`           | `10`             | Broker poll/block timeout in milliseconds.                                                   |
| `MAX_EXPECTED_SENSORS`       | `4`              | Number of distinct sensor IDs expected per timestamp bucket.                                 |
| `SENSOR_TIMEOUT_MS`          | `100`            | Maximum bucket wait before publishing with the sensors received so far.                      |
| `BUCKET_MS`                  | `17`             | Timestamp bucket width in milliseconds. The default is roughly half a 30 FPS frame.          |
| `SWEEP_INTERVAL_S`           | `0.02`           | Background sweep cadence for timeout and stale-bucket handling.                              |
| `BUFFER_DURATION_S`          | `1.0`            | Hard upper bound on bucket age before dropping stale buffered data.                          |
| `CLOSED_BUCKET_RETENTION_MS` | `1000`           | How long closed bucket keys are retained to reject late duplicate frames.                    |
| `LOG_LEVEL`                  | `INFO`           | Python logging level. Use `DEBUG` for per-frame tracing.                                     |

## Running Locally

Kafka example:

```bash
docker run --rm --network host \
  -e BROKER_TYPE=kafka \
  -e KAFKA_BOOTSTRAP=localhost:9092 \
  -e RAW_TOPIC=mdx-raw \
  -e FUSED_TOPIC=mdx-bev \
  -e MAX_EXPECTED_SENSORS=4 \
  vss-rt-cv-mv3dt-bev-fusion:local
```

Redis Streams example:

```bash
docker run --rm --network host \
  -e BROKER_TYPE=redis \
  -e REDIS_HOST=localhost \
  -e REDIS_PORT=6379 \
  -e RAW_TOPIC=mdx-raw \
  -e FUSED_TOPIC=mdx-bev \
  -e MAX_EXPECTED_SENSORS=4 \
  vss-rt-cv-mv3dt-bev-fusion:local
```

## Testing

The `tests/` directory contains unit tests for the fusion logic and integration
tests that run the built image with Kafka.

```bash
# Create and activate a virtual environment or use your existing one
# python3 -m venv .venv
# source .venv/bin/activate
python3 -m pip install -r tests/requirements.txt

# Unit tests
pytest -c tests/pytest.ini tests -m unit -v

# Integration tests against a built image
pytest -c tests/pytest.ini tests -m integration -v \
  --image-ref=vss-rt-cv-mv3dt-bev-fusion:local
```

See `tests/README.md` and `tests/TESTING.md` for the full test matrix.

## Health Check

After the service successfully subscribes to Kafka or Redis, it writes the
following readiness file:

```text
/tmp/fusion_ready
```

The compose service uses this file as its health check.

