# Standalone RT-CV-3D MV3DT Troubleshooting

Load this reference when setup, staging, launch, RTSP registration, Kafka flow, OSD, saved video, or BEV visualization fails.

## Contents

- [Wrong Deployment Path](#wrong-deployment-path)
- [Runtime Image Confusion](#runtime-image-confusion)
- [Missing Models](#missing-models)
- [Sample Dataset Assets Missing](#sample-dataset-assets-missing)
- [No `camInfo` Or Wrong Camera Count](#no-caminfo-or-wrong-camera-count)
- [Unsafe Or Mismatched Camera IDs](#unsafe-or-mismatched-camera-ids)
- [RTSP Streams Do Not Start](#rtsp-streams-do-not-start)
- [Bundled Resource Conflict](#bundled-resource-conflict)
- [Staged Broker Mismatch](#staged-broker-mismatch)
- [Bundled Or External Broker Problems](#bundled-or-external-broker-problems)
- [TensorRT Engine Build Or Cache Permission](#tensorrt-engine-build-or-cache-permission)
- [Host Tool Or Python Prerequisite Missing](#host-tool-or-python-prerequisite-missing)
- [`mdx-raw` Grows But `mdx-bev` Does Not](#mdx-raw-grows-but-mdx-bev-does-not)
- [OSD Window Missing](#osd-window-missing)
- [File OSD Blank Or No Active Sources](#file-osd-blank-or-no-active-sources)
- [File-Input Completion Versus Crash](#file-input-completion-versus-crash)
- [Kafka Verification Hangs](#kafka-verification-hangs)
- [Saved Video Missing Or Stale](#saved-video-missing-or-stale)
- [BEV Visualizer Fails Or Saves Old Output](#bev-visualizer-fails-or-saves-old-output)

## Wrong Deployment Path

Symptom: commands mention `MODE=mv3dt`, `BP_PROFILE`, warehouse `generated.env`, VST, ELK, Kibana, Logstash, or `deploy/docker/industry-profiles/warehouse-operations`.

Fix: return to `services/rtvi/rt-cv-3d/rt-cv-mv3dt`, use `docker/compose.yml`, and launch with the standalone broker mode selected in `references/deploy-rtvi-cv-3d-stack.md`. Use the warehouse/profile skill only when the user explicitly asked for warehouse MV3DT or a combined warehouse deployment.

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

## Sample Dataset Assets Missing

Symptom: a sample-dataset run cannot find `warehouse-4cams-20mx20m-synthetic`, the four sample MP4s, `rtdetr_warehouse_v1.0.2.fp16.onnx`, `bodypose3dnet_accuracy.onnx`, sample `calibration.json`, or `Top.png`.

Fix: load `sample-dataset.md`. Use an existing extracted `WAREHOUSE_APP_DATA_DIR` if available; otherwise obtain the release-compatible NGC warehouse app-data resource from the user/environment/public docs and download it with the NGC CLI without printing credentials. Validate the exact sample model files before launch. The sample calibration and `Top.png` come from the repo sample-data path, then `transforms.yml` is generated into `generated/bev-dataset/`.

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
- Verify each RTSP URL is reachable from the deployment host. If host probing requires TCP, set `RTSP_RTP_PROTOCOL=4` before restaging or using the static fallback.
- Confirm streams are synchronized and close to 30 FPS.
- After stream registration, validate exact stream count and camera IDs with `configure-cameras.md`.
- `STREAM_ADD_SUCCESS` plus a matching `stream-count` is not enough. If `Active sources : 0`, FPS remains zero, or `mdx-raw`/`mdx-bev` offsets do not grow after bounded verification, treat dynamic REST add as failed and use the generic static RTSP `[source-list]` fallback in `configure-cameras.md` with the same calibrated `sensor_id=rtsp://...` mappings.

## Bundled Resource Conflict

Symptom: bundled Kafka, Mosquitto, or the DeepStream REST endpoint fails to start; compose output shows bind failures; or a fixed container name such as `kafka` already exists.

Fix: run the bundled resource preflight in `references/deploy-rtvi-cv-3d-stack.md` before launch. It reuses existing standalone containers without rewriting ports, rejects foreign fixed-name collisions, and for a fresh start selects free Kafka, MQTT, and DeepStream REST ports in standalone `docker/.env`.

## Staged Broker Mismatch

Symptom: port preflight selected a fallback such as `KAFKA_BOOTSTRAP=localhost:19092`, but `mdx-raw` does not grow and `generated/configs/ds-main-config-mv3dt.txt` still contains `localhost;9092;mdx-raw`.

Fix: rerun the workflow in the correct order: bundled resource preflight first, then `generate-configs.sh`, then `stage-configs.sh`, then assert staged `msg-broker-conn-str` matches `KAFKA_BOOTSTRAP` using `configure-cameras.md`. Do not continue with a staged config that points at a stale Kafka port.

## Bundled Or External Broker Problems

Symptom: perception fails at MQTT init, Kafka dump cannot connect, BEV Fusion remains unhealthy, or `mdx-bev` does not grow.

```bash
docker ps --format '{{.Names}}	{{.Status}}'   | awk '$1 ~ /^(vss-mosquitto-mv3dt|kafka|vss-rtvi-cv-bev-fusion)$/ {print}'
docker logs --tail 100 vss-mosquitto-mv3dt 2>&1 | tail -30 || true
docker logs --tail 100 kafka 2>&1 | tail -30 || true
docker logs --tail 100 vss-rtvi-cv-bev-fusion 2>&1 | tail -30
```

For external brokers, confirm the basic endpoints, required Kafka topics, and regenerated MQTT config:

```bash
cd "${RTCV3D_APP}"
MQTT_BROKERS="${MQTT_HOST}:${MQTT_PORT}" ./scripts/generate-configs.sh "${CALIBRATION_JSON}"
cd "${RTCV3D_APP}/docker"
timeout 30s docker compose --profile kafka run --rm --no-deps kafka kafka-topics --bootstrap-server "${KAFKA_BOOTSTRAP}" --describe --topic "${RAW_TOPIC:-mdx-raw}"
timeout 30s docker compose --profile kafka run --rm --no-deps kafka kafka-topics --bootstrap-server "${KAFKA_BOOTSTRAP}" --describe --topic "${FUSED_TOPIC:-mdx-bev}"
cd "${RTCV3D_APP}"
# Use the Kafka CLI offset helpers from verify-and-view.md with KAFKA_BOOTSTRAP.
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}" --topic "${RAW_TOPIC:-mdx-raw}" --count 5
```

If advanced Kafka/MQTT TLS/auth is required, use the standalone README custom-broker section.

## TensorRT Engine Build Or Cache Permission

Symptom: first run appears idle for several minutes during model initialization, or logs show TensorRT engine rebuilds followed by permission denied while saving `.engine` files under `MODELS_DIR`.

Fixes:

- Expect cold engine builds to take 5-10 minutes, especially after TensorRT/runtime changes; keep the BEV recorder alive through EOS in the same long-lived shell/session instead of treating the quiet period as a failure.
- Verify the model cache directories mounted into `/opt/storage` are writable by the perception container runtime UID/GID, commonly `1000:1000`.
- With approval, apply a scoped ACL only to the needed model directories, for example `sudo setfacl -m u:1000:rwx -m d:u:1000:rwx "$MODELS_DIR/mv3dt/BodyPose3DNet"`. Do not use broad `chmod 777` or broad recursive `chown`.

## Host Tool Or Python Prerequisite Missing

Symptom: saved-output verification cannot parse videos because `ffprobe` is missing, `scripts/ensure-venv.sh` fails while creating `utils/venv`, or the BEV visualizer Python environment cannot import OpenCV/Kafka dependencies.

Checks:

```bash
command -v ffprobe || echo 'missing ffprobe; install/provide ffmpeg tools before saved-output verification'
python3 -m venv --help >/dev/null || echo 'python3 venv/ensurepip support is missing'
cd "${RTCV3D_APP}"
# shellcheck disable=SC1091
source scripts/ensure-venv.sh
ensure_venv
"${VENV_PY}" - <<'PY'
try:
    import cv2
    import confluent_kafka
    import numpy
    import yaml
except Exception as exc:
    raise SystemExit(f"BEV visualizer Python dependencies are not usable: {exc}")
PY
```

Fixes:

- Install or provide `ffprobe` before saved-output runs; saved grid/BEV success requires parseable videos.
- Install the platform Python venv/ensurepip package, then rerun `scripts/ensure-venv.sh`; if the distribution disables ensurepip, bootstrap pip according to the OS Python packaging guidance before running the BEV helper.
- If OpenCV import fails because system graphics libraries are missing, install the minimal OS packages needed by the selected OpenCV wheel or provide a host image with those runtime libraries.

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
- If no display is available, restage with `SAVE_VIDEO=1` and saved BEV output when BEV assets are present.

## File OSD Blank Or No Active Sources

Symptom: `INPUT_MODE=file` with `OSD=1` reaches `Pipeline running`, but the OSD window remains empty, `Active sources : 0` persists, FPS stays zero, and `mdx-raw` does not grow.

For file input, the staged config should disable live-source latency dropping:

```bash
cd "${RTCV3D_APP}"
awk '/^\[source-list\]/{s=1} /^\[/{if($0!="[source-list]")s=0} s && /^low-latency-mode=/' generated/configs/ds-main-config-mv3dt.txt
awk '/^\[source-attr-all\]/{s=1} /^\[/{if($0!="[source-attr-all]")s=0} s && /^(drop-on-latency|latency)=/' generated/configs/ds-main-config-mv3dt.txt
```

Expected values for `INPUT_MODE=file` are `[source-list] low-latency-mode=0`, `[source-attr-all] drop-on-latency=0`, and `[source-attr-all] latency=100000`. If not, rerun the current `scripts/stage-configs.sh`, then rerun the staging assertions in `configure-cameras.md` before starting perception. Keep `low-latency-mode=1` and `drop-on-latency=1` for live RTSP stream mode.

## File-Input Completion Versus Crash

For `INPUT_MODE=file`, `vss-rtvi-cv-mv3dt` exits after EOS by design. It may never emit `ds-ready: YES`. `Pipeline running` is useful startup evidence when present, but `Exited (0)` with `App run successful` in current-run logs is success, not a failed deployment.

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
- Start the BEV recorder in the same long-lived shell/session that will run perception and verification. `nohup env ... ./scripts/bev-visualizer.sh &` is fine inside that long-running command, but do not run it as a standalone completed tool call in runners that reap background process groups. Wait for Kafka consumer group assignment evidence and verify its PID is still alive before file-mode perception or before RTSP stream registration.
- Select the saved artifact from the current recorder log's `Video saved: ... (N frames)` line; do not glob old `fused_trajectory_video_*.mp4` files.
- Verify the selected artifact is non-empty, newer than the run start, and parseable by `ffprobe`.
