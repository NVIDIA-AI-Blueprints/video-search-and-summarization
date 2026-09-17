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
- Collapses the several views of one globally consistent object ID into a single measurement according to `FUSION_METHOD`, averaging timestamps and confidence scores across the views it keeps.
- Resolves object type disagreements by majority vote among those views, using total confidence as the tie-breaker.
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
| `FUSION_METHOD`              | `rays`       | How views of one object are combined: `first`, `closest`, `mean`, `median` or `rays`. See below. |
| `VISIBILITY_MIN`             | `0.3`            | Minimum reported visibility for a view to be fused, under `FUSION_METHOD=rays`.                |
| `CALIBRATION_PATH`           | `/calibration/calibration.json` | Camera geometry. Required by `closest`; `rays` fuses worse without it.       |
| `MAX_DIST`                   | `25`             | Drop a view reported further than this, in metres, from its own camera. `0` disables. |
| `FOOT_OFFSET`                | `auto`           | Ground-contact offset correction. `auto` measures it from overlapping views; `off`, or a distance in metres, to pin it. |
| `CONFLICT_RADIUS`            | `2.0`            | Views of one id further apart than this are treated as a mis-association. `0` disables. |
| `SMOOTH_LAG`                 | `0`              | Publish each frame this many buckets late so a backward pass can use later frames. `0` is causal. |
| `TEMPORAL_FILTER`            | `1`              | Filter each fused track over time with a constant-velocity Kalman filter.           |
| `PIXEL_SIGMA`                | `3.0`            | Box bottom-edge jitter in pixels, scaled into the filter's measurement covariance.   |
| `ACCEL_SIGMA`                | `3.0`            | How hard a tracked object may accelerate, m/s². 3 covers a person or forklift.       |
| `SENSOR_TIMEOUT_MS`          | `100`            | Maximum bucket wait before publishing with the sensors received so far.                      |
| `BUCKET_MS`                  | `17`             | Timestamp bucket width in milliseconds. The default is roughly half a 30 FPS frame.          |
| `SWEEP_INTERVAL_S`           | `0.02`           | Background sweep cadence for timeout and stale-bucket handling.                              |
| `BUFFER_DURATION_S`          | `1.0`            | Hard upper bound on bucket age before dropping stale buffered data.                          |
| `CLOSED_BUCKET_RETENTION_MS` | `1000`           | How long closed bucket keys are retained to reject late duplicate frames.                    |
| `LOG_LEVEL`                  | `INFO`           | Python logging level. Use `DEBUG` for per-frame tracing.                                     |

## Fusion Methods

`FUSION_METHOD` selects how the several views of one object become one position. An unrecognized value fails at startup rather than defaulting, since a typo would otherwise quietly change every published position.

Four baselines, each one idea taking every view, and the combination that beat them all:

| Method | Behavior |
| ------ | -------- |
| `first` | The first view by sensor id. |
| `closest` | The view of the camera nearest the reported position. Needs calibration. |
| `mean` | Unweighted mean over every view. |
| `median` | Coordinate-wise median over every view. A view placing the object elsewhere has to outnumber the rest, not merely outweigh them. |
| `rays` (default) | Views reporting visibility below `VISIBILITY_MIN` are refused, and the survivors are solved by weighted least squares over each view's anisotropic uncertainty. An object no view sees well enough is not published for that bucket. Needs calibration. |

Against ground truth on the 8-camera warehouse capture, at `MAX_DIST=25` with the temporal filter on:

| Method | HOTA | MOTA | LocA | CLR_TP | CLR_FP | Precision |
| ------ | ---- | ---- | ---- | ------ | ------ | --------- |
| `rays` | **13.615** | **20.295** | **87.630** | 131014 | **10665** | **92.47%** |
| `median` | 13.632 | 19.669 | 87.239 | 134722 | 18076 | 88.17% |
| `mean` | 13.592 | 19.626 | 87.213 | 134594 | 18204 | 88.09% |
| `closest` | 13.493 | 19.518 | 86.754 | 134271 | 18527 | 87.87% |
| `first` | 13.364 | 19.421 | 86.594 | 133985 | 18813 | 87.69% |

`rays` derives its weights from geometry, with nothing fitted: for a camera of focal length `f` at height `h` viewing a ground point at slant range `d`, a pixel of error on the box's bottom edge gives `sigma_along = d²/(f·h)` and `sigma_across = d/f`, so the anisotropy is `d/h` and the common pixel term cancels. A ground-plane projection is precise across the camera's line of sight and vague along it, so views from a wide baseline pin a position none of them could fix alone. Measured on a live 8-camera run, 94.6% of physically impossible frame-to-frame steps pointed along the camera ray against 26% expected by chance, confirming the error is radial.

Fewer than two calibrated views leaves the position at an area-weighted mean of the gated views, since one view alone would simply return its own position. That same mean is the whole no-calibration path: `rays` warns at startup and keeps running, rather than refusing, so an older deployment without the calibration mount still starts.

### Foot-point offset

A ground-contact point sitting `d` off the assumed `z=0` plane back-projects to the wrong range by `d/h` of that range, for a camera at height `h` — a fraction, not a fixed distance. Measured against ground truth, single-view positions landed ~1% of their range too near their own camera, consistently across distance. The correction is a radial rescale about each camera, applied per view before anything else reads it.

`FOOT_OFFSET=auto` ships no constant. The offset may come from the detector's bottom edge or from the calibrated floor — indistinguishable from one site, and one is a detector property while the other is a deployment property — so the service measures the value that makes overlapping cameras agree, which needs no ground truth. A site whose true offset is zero measures zero; one with the opposite sign is corrected the other way. The estimate is clamped, needs `FOOT_OFFSET_MIN_PAIRS` before it applies at all, and is re-fitted on a rolling window.

Pairs are sampled every `FOOT_OFFSET_STRIDE` buckets: consecutive buckets hold the same people on the same cameras, so 5000 contiguous pairs measured 0.0125 m where 5000 spread over time measured 0.0188 m. A deployment with no overlapping views measures nothing and is left uncorrected.

### False associations

Two views sharing an id but sitting metres apart are two different people, mis-associated upstream; averaged, they place the object between the two. Correctly associated pairs measured 0.27 m apart at the median against 2.27 m for mis-associated ones, so `CONFLICT_RADIUS` separates them better than a covariance test would — two people are metres apart whatever the sensor is doing. On a conflict the service keeps the views agreeing with the track prediction, and publishes nothing for that object in that bucket when there is no history to judge by.

### Smoothing

`SMOOTH_LAG` holds each frame back that many buckets so an RTS backward pass can use later ones. Off by default, since it is latency the consumer pays: 5 buckets buys most of the available accuracy for ~85 ms. A track that ends keeps whatever the causal filter produced.

`MAX_DIST` and `TEMPORAL_FILTER` are separate from the method and apply to all five. `MAX_DIST` drops a view reported further than that from its own camera before anything is combined; at 25 m the discarded boxes had a median height of 101 px against 205 px for those kept. `TEMPORAL_FILTER` runs each fused track through a constant-velocity Kalman filter whose measurement covariance is the same ray geometry, so a position the geometry says is vague moves less.

`CALIBRATION_PATH` (default `/calibration/calibration.json`) supplies the camera geometry. `closest` refuses to start without it and `rays` fuses worse; the rest ignore it. The compose deployments mount the same `calibration.json` the perception container is given. On helm, where mv3dt fetches camera config at runtime rather than shipping a file, the chart resolves it in order: `fusion.calibrationConfigMap`, then its own ConfigMap if `files/warehouse-standalone-mv3dt/calibration/calibration.json` is supplied, then an init container that fetches from `dynamicCameraConfig.calibrationApiUrl` into the pod.

Visibility is read from `Object.info["visibility"]` on `RAW_TOPIC` and requires `TargetManagement.outputVisibility: 1` in the upstream tracker config. Without it the field is still present but pinned at `1.0`, so the gate silently admits everything.

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

