# Standalone RT-CV-3D MV3DT Troubleshooting

Load this reference when setup, staging, launch, RTSP registration, Kafka flow, OSD, saved video, or BEV visualization fails.

## Wrong Deployment Path

Symptom: commands mention `MODE=mv3dt`, `BP_PROFILE`, warehouse `generated.env`, VST, ELK, Kibana, Logstash, or `deploy/docker/industry-profiles/warehouse-operations`.

Fix: return to `services/rtvi/rt-cv-3d/rt-cv-mv3dt`, use `docker/compose.yml`, and launch with the standalone broker mode selected in `deploy-rtvi-cv-3d-stack.md`. Use the warehouse/profile skill only when the user explicitly asked for warehouse MV3DT or a combined warehouse deployment.

## Runtime Image Confusion

Resolved runtime images come only from Compose:

```bash
cd "${RTCV3D_APP}/docker"
docker compose config --images | sort -u
```

Do not infer image tags from this skill's version or hardcode release tags in troubleshooting steps.

## Missing Models

Symptom: compose fails with `MODELS_DIR` errors, perception cannot load models, or image starts then exits during model init.

```bash
cd "${RTCV3D_APP}"
MODELS_DIR="${MODELS_DIR:?set MODELS_DIR from docker/.env or user input}"
ls "${MODELS_DIR}/mtmc"
ls "${MODELS_DIR}/mv3dt/BodyPose3DNet"
```

Fix: download/extract app-data, set `MODELS_DIR` to its `models` directory in standalone `docker/.env`, then restage/redeploy.

## No `camInfo` Or Wrong Camera Count

Symptom: `stage-configs.sh` warns no camInfo, file input fails, BEV Fusion waits, or perception logs camera config errors.

```bash
cd "${RTCV3D_APP}"
find generated/camInfo -maxdepth 1 -type f -name '*.yml' | sort
```

Fix: validate `calibration.json` with `configure-cameras.md`; `NUM_CAMS` must count only sensors where `type == "camera"`, and generated camInfo count must match that filtered camera count.

## Unsafe Or Mismatched Camera IDs

Symptom: file-mode input starts with missing source files, stream registration does not match calibration, or camInfo generation fails.

Fix: camera ids must be non-empty, unique, safe filename tokens containing only letters, digits, dot, underscore, or dash, with no path separators, traversal components, or control characters. Do not mutate source videos. Point `VIDEO_DIR` at files already named `<sensor_id>.mp4` or create `generated/video-input/<sensor_id>.mp4` symlinks when the mapping is explicit or unambiguous.

## RTSP Streams Do Not Start

Symptom: `ds-ready: YES` appears but FPS stays 0 after stream registration.

```bash
cd "${RTCV3D_APP}"
./scripts/add-streams.sh --list
docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | grep -iE 'error|rtsp|source|fps' | tail -50
```

Fixes:

- Ensure each `add-streams.sh` key exactly matches a generated camInfo basename.
- Removal also requires the original `NAME=rtsp://...` mapping: `./scripts/add-streams.sh --remove 'Camera_01=rtsp://host/cam1'`.
- Verify each RTSP URL is reachable from the deployment host.
- Confirm streams are synchronized and close to 30 FPS.
- After `add-streams.sh`, validate exact stream count and camera IDs with `configure-cameras.md`.

## Bundled Or External Broker Problems

Symptom: perception fails at MQTT init, Kafka dump cannot connect, BEV Fusion remains unhealthy, or `mdx-bev` does not grow.

```bash
docker ps --format '{{.Names}}	{{.Status}}'   | awk '$1 ~ /^(vss-mosquitto-mv3dt|kafka|vss-rtvi-cv-bev-fusion)$/ {print}'
docker logs --tail 100 vss-mosquitto-mv3dt 2>&1 | tail -30 || true
docker logs --tail 100 kafka 2>&1 | tail -30 || true
docker logs --tail 100 vss-rtvi-cv-bev-fusion 2>&1 | tail -30
```

For external brokers, confirm the basic endpoints and regenerated MQTT config:

```bash
cd "${RTCV3D_APP}"
MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}" ./scripts/generate-configs.sh "${CALIBRATION_JSON}"
# Use the Kafka CLI offset helpers from verify-and-view.md with KAFKA_BOOTSTRAP.
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}" --topic "${RAW_TOPIC:-mdx-raw}" --count 5
```

If advanced Kafka/MQTT TLS/auth is required, use the standalone README custom-broker section.

## `mdx-raw` Grows But `mdx-bev` Does Not

Cause: BEV Fusion is not receiving enough synchronized per-camera measurements, `MAX_EXPECTED_SENSORS` does not match actual camera count, or time skew is too large.

```bash
docker inspect --format '{{.State.Health.Status}}' vss-rtvi-cv-bev-fusion
cd "${RTCV3D_APP}"
# Use the Kafka CLI offset helpers from verify-and-view.md to compare mdx-raw and mdx-bev high-watermark offsets.
```

Fixes:

- Confirm `NUM_CAMS` equals the filtered camera count and generated camInfo count.
- Confirm all file/RTSP inputs are active.
- Check camera clock synchronization; at 30 FPS, frame timestamps should agree within about 33 ms.
- Tune BEV Fusion timing env values only after validating camera count and stream activity.

## OSD Window Missing

```bash
echo "DISPLAY=${DISPLAY:-}"
ls /tmp/.X11-unix 2>/dev/null || true
command -v xdpyinfo >/dev/null 2>&1 && xdpyinfo >/dev/null 2>&1 && echo 'display ok'
docker logs --tail 100 vss-rtvi-cv-mv3dt 2>&1 | grep -iE 'display|egl|x11|sink0|error'
```

Fixes:

- Restage with `OSD=1` only after a working display is detected.
- Ask before modifying X11 access.
- Do not use broad `xhost +`.
- If no display is available, ask before switching to `SAVE_VIDEO=1` and saved BEV output.

## File-Input Completion Versus Crash

For `INPUT_MODE=file`, `vss-rtvi-cv-mv3dt` exits after EOS by design. `Exited (0)` with `App run successful` in logs is success, not a failed deployment.

```bash
status="$(docker inspect --format '{{.State.Status}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
exit_code="$(docker inspect --format '{{.State.ExitCode}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
oom="$(docker inspect --format '{{.State.OOMKilled}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
echo "status=${status} exit=${exit_code} oom=${oom}"
docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | tail -100
```

Classify:

- `status=exited exit=0` plus `App run successful`: completed finite file-input run; verify artifacts and Kafka offsets against pre-run baselines.
- `exit` non-zero, `oom=true`, missing success log, or fatal/error logs before outputs are written: crash/failure; inspect logs before cleanup.
- RTSP input should remain running until stopped; unexpected exit is a failure.

## Kafka Verification Hangs

Do not run an unbounded live-tail after finite MP4 input has completed. For file mode, use offset baselines or bounded beginning reads only when the topic is known fresh:

```bash
cd "${RTCV3D_APP}"
# Use the Kafka CLI offset helpers from verify-and-view.md for baseline comparison.
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}" --topic "${RAW_TOPIC:-mdx-raw}" --from-beginning --count 20
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}" --topic "${FUSED_TOPIC:-mdx-bev}" --from-beginning --count 20
```

For active RTSP, live-tail sampling is acceptable only with `--count` and an outer `timeout`.

## Saved Video Missing Or Stale

```bash
cd "${RTCV3D_APP}"
RUN_START_EPOCH="${RUN_START_EPOCH:-$(cat generated/run-state/run-start-epoch 2>/dev/null || echo 0)}"
GRID="video-output/grid-view.mkv"
test -s "${GRID}" || echo "missing or empty ${GRID}"
[ "$(stat -c %Y "${GRID}" 2>/dev/null || echo 0)" -ge "${RUN_START_EPOCH}" ] || echo "grid video predates current run"
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "${GRID}" || true
docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | grep -iE 'sink2|encoder|nvenc|video-output|error' | tail -50
```

Fixes:

- Restage with `SAVE_VIDEO=1`.
- For file input, wait for EOS.
- For live RTSP, stop/remux when done if seekability is needed.
- On GPUs without NVENC, apply the software encoder instructions from the standalone README.

## BEV Visualizer Fails Or Saves Old Output

```bash
cd "${RTCV3D_APP}"
test -f "${BEV_DATASET_PATH}/map.png" || echo 'missing map.png'
test -f "${BEV_DATASET_PATH}/transforms.yml" || echo 'missing transforms.yml'
test -s generated/run-state/bev-visualizer.group || echo 'BEV Kafka consumer group missing'
test -s generated/run-state/bev-consumer-group-"$(cat generated/run-state/run-id 2>/dev/null)".txt || echo 'BEV Kafka assignment evidence missing'
test -f generated/run-state/bev-visualizer.pid && ps -p "$(cat generated/run-state/bev-visualizer.pid)" || true
BEV_LOG="$(cat generated/run-state/bev-visualizer.log 2>/dev/null || true)"
[ -n "${BEV_LOG}" ] && tail -80 "${BEV_LOG}"
```

Fixes:

- Resolve `BEV_DATASET_PATH` to one directory containing both `map.png` and `transforms.yml`.
- Generate transforms only when the correct calibration map image is available.
- Use `BEV_SOURCE=fused` by default for saved output.
- Use `BEV_SAVE_VIDEO=1` for saved output/headless systems.
- Start the BEV recorder and wait for Kafka consumer group assignment evidence before file-mode perception or before RTSP stream registration.
- Select the saved artifact from the current recorder log's `Video saved: ... (N frames)` line; do not glob old `fused_trajectory_video_*.mp4` files.
- Verify the selected artifact is non-empty, newer than the run start, and parseable by `ffprobe`.
