# mv3dt-config-init

Init container that generates the MV3DT-specific configs the DeepStream
pipeline needs. It waits for `calibration.json` on the shared calibration
volume, validates it, then writes:

- `${CAM_INFO_OUTPUT_DIR}/<sensor_id>.yml` — one camInfo file per camera
- `${PUB_SUB_OUTPUT_DIR}/pub_sub_info_config.yml` — pub/sub topology

The image is a distroless Python 3.13 runtime (no shell), built with pinned
dependencies from `Pipfile.lock`.

## Build

This source tree lives in the broader VSS project as the `rt-cv-config-init`
service directory:

```bash
services/rtvi/rt-cv-3d/rt-cv-config-init
```

Build the image:

```bash
cd services/rtvi/rt-cv-3d/rt-cv-config-init

docker build \
  -f Dockerfiles/mv3dt-config-init.Dockerfile \
  -t vss-rt-cv-mv3dt-config-init:local \
  .
```


## Run

Mount a calibration volume (where `calibration.json` will appear) and output
directories, then start the container:

```bash
docker run --rm \
  -v /path/to/calibration:/calibration \
  -v /path/to/camInfo:/tmp/camInfo \
  -v /path/to/generated:/tmp/generated \
  -e MQTT_HOST=localhost \
  -e MQTT_PORT=1883 \
  vss-rt-cv-mv3dt-config-init:latest
```

### Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `CALIBRATION_API_URL` | `http://vss-video-analytics-api:8081/config/calibration?emptyIfNotFound=false` | VSS video-analytics endpoint to fetch calibration from. Set empty to poll the shared volume instead |
| `CALIBRATION_JSON_PATH` | `/calibration/calibration.json` | Shared-volume calibration file, watched when `CALIBRATION_API_URL` is empty. Not written in API mode; see `CALIBRATION_FETCH_PATH` |
| `CAM_INFO_OUTPUT_DIR` | `/tmp/camInfo` | Output dir for per-camera YAMLs |
| `PUB_SUB_OUTPUT_DIR` | `/tmp/generated` | Output dir for `pub_sub_info_config.yml` |
| `CALIBRATION_WAIT_TIMEOUT` | `3600` | Seconds to wait for calibration (fetch or file) |
| `CALIBRATION_FETCH_PATH` | `/tmp/calibration/calibration.json` | Where API-fetched calibration is written. Must be writable — the shared calibration volume is mounted read-only |
| `CALIBRATION_POLL_INTERVAL` | `10` | Seconds between retries |
| `MQTT_HOST` / `MQTT_PORT` | `localhost` / `1883` | MQTT broker hostname and port |
| `CLASS_SPECS` | `0,1.60,0.3;`<br>`1,1.60,0.3;`<br>`2,1.60,0.3;`<br>`3,0.48,0.3;`<br>`4,0.2,0.52;`<br>`5,2.2,0.9` | Object model dims as `;`-separated `"classID,height,radius"` entries (metres); spaces around tokens are ignored |
| `RANGE_OF_INTEREST` | `""` (empty) | World-plane ROI `x1,y1,x2,y2` (metres). When empty, computed from camera positions with 20 m padding |
| `NEIGHBOR_CRITERIA` | `overlap_threshold:1e-6` | FOV-overlap rule: `top_N:<N>` or `overlap_threshold:<float>` in `[0, 1]` |
| `MINIMUM_OBJECT_SIZE` | `50` | Min object height (px) to consider an object visible when rendering FOV |

## Testing

The `tests/` directory contains unit tests for the config generators and the
numpy projection math, plus integration tests that run the built image in both
shared-volume and calibration-API modes.

```bash
cd services/rtvi/rt-cv-3d/rt-cv-config-init

# Create and activate a virtual environment or use your existing one
# python3 -m venv .venv
# source .venv/bin/activate
python3 -m pip install -r tests/requirements.txt

# Unit tests (no docker required)
pytest -c tests/pytest.ini tests -m unit -v

# Integration tests against a built image
pytest -c tests/pytest.ini tests -m integration -v \
  --image-ref=vss-rt-cv-mv3dt-config-init:local
```

The integration tests move files with `docker cp` rather than bind mounts, so
they work both against a local daemon and against a docker-in-docker daemon in
CI.
