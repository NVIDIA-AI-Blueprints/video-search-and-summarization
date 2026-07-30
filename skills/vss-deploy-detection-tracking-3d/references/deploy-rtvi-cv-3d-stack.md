# Deploy Standalone RT-CV-3D MV3DT

Load this reference for setup, environment preparation, compose launch, or redeploy of the standalone RT-CV-3D MV3DT stack.

## Resolve The App Directory

```bash
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
RTCV3D_APP="${RTCV3D_APP:-${REPO_ROOT}/services/rtvi/rt-cv-3d/rt-cv-mv3dt}"
test -f "${RTCV3D_APP}/README.md" || { echo "ERROR: RT-CV-3D app not found: ${RTCV3D_APP}"; exit 1; }
test -f "${RTCV3D_APP}/docker/compose.yml" || { echo "ERROR: standalone compose missing under ${RTCV3D_APP}/docker"; exit 1; }
cd "${RTCV3D_APP}"
```

Do not switch to warehouse compose paths unless the user explicitly asked for the warehouse blueprint.

## What Compose Starts

Compose source: `docker/compose.yml` under `RTCV3D_APP`. Image values come from the checked-out compose package and `docker/.env`; do not infer image tags from this skill's version.

| Service | Container | Image expression | Role |
|---|---|---|---|
| `perception` | `vss-rtvi-cv-mv3dt` | `${PERCEPTION_IMAGE}:${PERCEPTION_TAG}` | RT-DETR plus MV3DT perception; publishes `mdx-raw`. |
| `bev-fusion` | `vss-rtvi-cv-bev-fusion` | `${BEV_FUSION_IMAGE}:${BEV_FUSION_TAG}` | Fuses `mdx-raw` measurements and publishes `mdx-bev`. |
| `mosquitto` | `vss-mosquitto-mv3dt` | `${MOSQUITTO_IMAGE}` | Bundled MQTT broker for `/trck/*`, profile `mosquitto`. |
| `kafka` | `kafka` | `${KAFKA_IMAGE}` | Bundled Kafka broker for `mdx-raw` and `mdx-bev`, profile `kafka`. |
| `kafka-topic-init` | `kafka-topic-init` | `${KAFKA_IMAGE}` | One-shot topic creation, profile `kafka`. |

Inspect resolved runtime images only from Compose:

```bash
cd "${RTCV3D_APP}/docker"
docker compose config --images | sort -u
```

Use platform-specific image tags only when they are already supplied by the checked-out compose package/`docker/.env` or explicitly provided by the user. Do not set, derive, or recommend a specific `PERCEPTION_TAG` or `BEV_FUSION_TAG` in this skill.

## Prerequisites

Run safe checks before launch:

```bash
cd "${RTCV3D_APP}"
test -w . || { echo "ERROR: RT-CV-3D app directory is not writable: ${RTCV3D_APP}"; exit 1; }
command -v docker >/dev/null || { echo "ERROR: docker is not installed or not on PATH"; exit 1; }
docker ps >/dev/null || { echo "ERROR: docker daemon is not reachable"; exit 1; }
docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q nvidia   || docker run --help 2>/dev/null | grep -q -- '--gpus'   || { echo "ERROR: Docker GPU runtime was not detected"; exit 1; }
nvidia-smi >/dev/null || { echo "ERROR: nvidia-smi failed; fix driver/GPU visibility before deployment"; exit 1; }
```

Read only the named values needed from `docker/.env`; do not source it as shell code:

```bash
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
MODELS_DIR="${MODELS_DIR:-$(read_env MODELS_DIR)}"
test -d "${MODELS_DIR}/mtmc" || { echo "ERROR: MODELS_DIR/mtmc missing: ${MODELS_DIR}/mtmc"; exit 1; }
test -d "${MODELS_DIR}/mv3dt/BodyPose3DNet" || { echo "ERROR: BodyPose3DNet missing under ${MODELS_DIR}/mv3dt"; exit 1; }
```

If models/assets are missing, follow the standalone README model-download section and the public VSS docs at https://docs.nvidia.com/vss/latest/object-detection-tracking.html. Do not print NGC keys.

## Preflight Config

Run `references/configure-cameras.md` before this section when calibration, input mode, display mode, broker mode, or staged configs are not already prepared.

Render the compose services with the selected broker mode:

```bash
cd "${RTCV3D_APP}/docker"
# Bundled broker mode:
COMPOSE_PROFILES=mosquitto,kafka docker compose config --services

# External broker mode, only when the user explicitly provided external brokers:
docker compose config --services
```

Initialize broker/input state in the deployment shell. Read only named values from `docker/.env`; do not source it as shell code:

```bash
cd "${RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
INPUT_MODE="${INPUT_MODE:-$(read_env INPUT_MODE)}"
RAW_TOPIC="${RAW_TOPIC:-$(read_env RAW_TOPIC)}"; RAW_TOPIC="${RAW_TOPIC:-mdx-raw}"
FUSED_TOPIC="${FUSED_TOPIC:-$(read_env FUSED_TOPIC)}"; FUSED_TOPIC="${FUSED_TOPIC:-mdx-bev}"
KAFKA_PORT="${KAFKA_PORT:-$(read_env KAFKA_PORT)}"
USE_EXTERNAL_BROKERS="${USE_EXTERNAL_BROKERS:-$(read_env USE_EXTERNAL_BROKERS)}"; USE_EXTERNAL_BROKERS="${USE_EXTERNAL_BROKERS:-0}"
if [ "${USE_EXTERNAL_BROKERS}" = 1 ]; then
  MQTT_HOST="${MQTT_HOST:-$(read_env MQTT_HOST)}"; MQTT_HOST="${MQTT_HOST:?set external MQTT_HOST}"
  MQTT_PORT="${MQTT_PORT:-$(read_env MQTT_PORT)}"; MQTT_PORT="${MQTT_PORT:?set external MQTT_PORT}"
  KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"; KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:?set external KAFKA_BOOTSTRAP}"
else
  MQTT_HOST="${MQTT_HOST:-$(read_env MQTT_HOST)}"; MQTT_HOST="${MQTT_HOST:-localhost}"
  MQTT_PORT="${MQTT_PORT:-$(read_env MQTT_PORT)}"; MQTT_PORT="${MQTT_PORT:-1883}"
  KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"; KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}"
fi
export INPUT_MODE RAW_TOPIC FUSED_TOPIC KAFKA_PORT USE_EXTERNAL_BROKERS MQTT_HOST MQTT_PORT KAFKA_BOOTSTRAP
```

## NGC Login And Image Access

```bash
cd "${RTCV3D_APP}/docker"
if [ -z "${NGC_CLI_API_KEY:-}" ] && [ -f "$HOME/.ngc/config" ]; then
  NGC_CLI_API_KEY="$(awk -F'= ' '/^apikey/{print $2}' "$HOME/.ngc/config" 2>/dev/null || true)"
  export NGC_CLI_API_KEY
fi
if [ -n "${NGC_CLI_API_KEY:-}" ]; then
  printf '%s' "${NGC_CLI_API_KEY}" | docker login nvcr.io --username '$oauthtoken' --password-stdin
else
  echo "WARN: NGC_CLI_API_KEY is not set; image pulls may fail if nvcr.io is not already logged in."
fi
docker compose config --images | sort -u
```

If the user asks you to pull/check images before launching, use only the images reported by `docker compose config --images`.

## Current-Run State

Before every file-input run, and before any saved-output run, create a run id and record start/output baselines. Capture Kafka offsets only after the selected brokers, topic init, and `bev-fusion` are ready, immediately before perception starts.

```bash
cd "${RTCV3D_APP}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
RUN_START_EPOCH="$(date +%s)"
RUN_STATE_DIR="${RTCV3D_APP}/generated/run-state"
mkdir -p "${RUN_STATE_DIR}" video-output bev-output
printf '%s\n' "${RUN_ID}" > "${RUN_STATE_DIR}/run-id"
printf '%s\n' "${RUN_START_EPOCH}" > "${RUN_STATE_DIR}/run-start-epoch"
kafka_client() {
  if docker ps --format '{{.Names}}' | grep -qx kafka; then
    docker exec kafka "$@"
  else
    (cd "${RTCV3D_APP}/docker" && docker compose --profile kafka run --rm --no-deps kafka "$@")
  fi
}
kafka_high_watermark() {
  topic="$1"
  bootstrap="${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}"
  if ! output="$(kafka_client kafka-get-offsets --bootstrap-server "${bootstrap}" --topic "${topic}" --time -1 2>&1)"; then
    echo "ERROR: kafka-get-offsets failed for ${topic} on ${bootstrap}" >&2
    printf '%s\n' "${output}" >&2
    return 1
  fi
  printf '%s\n' "${output}" | awk -F: -v topic="${topic}" '
    $1 == topic {
      if ($3 !~ /^[0-9]+$/) { printf "ERROR: non-numeric offset line: %s\n", $0 > "/dev/stderr"; bad=1; next }
      found=1; sum += $3
    }
    END {
      if (bad) exit 1
      if (!found) { printf "ERROR: no partitions returned for topic %s\n", topic > "/dev/stderr"; exit 1 }
      print sum
    }'
}
capture_kafka_offsets() {
  out="$1"; shift
  tmp="${out}.tmp"
  {
    printf '{\n'
    sep=''
    for topic in "$@"; do
      high="$(kafka_high_watermark "${topic}")" || exit 1
      printf '%s  "%s": {"high": %s}' "${sep}" "${topic}" "${high}"
      sep=$',\n'
    done
    printf '\n}\n'
  } > "${tmp}"
  mv "${tmp}" "${out}"
}
find video-output bev-output -maxdepth 1 -type f -printf '%p\t%T@\t%s\n' > "${RUN_STATE_DIR}/output-baseline-${RUN_ID}.txt" || true
```

For file input, call `capture_file_kafka_baseline` only after bundled/external brokers and `bev-fusion` are ready, but before `perception` starts.

## Support Service Helpers

Use these helpers for both no-BEV file launches and BEV two-phase launches. `kafka-topic-init` is a one-shot; poll until it exits and require exit code 0.

```bash
wait_healthy() {
  container="$1"
  deadline=$((SECONDS + ${2:-120}))
  status=""
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null || true)"
    [ "${status}" = healthy ] && return 0
    sleep 2
  done
  echo "ERROR: ${container} did not become healthy; final status=${status:-missing}" >&2
  return 1
}
wait_topic_init() {
  deadline=$((SECONDS + ${1:-120}))
  status=""; exit_code=""
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    status="$(docker inspect --format '{{.State.Status}}' kafka-topic-init 2>/dev/null || true)"
    exit_code="$(docker inspect --format '{{.State.ExitCode}}' kafka-topic-init 2>/dev/null || true)"
    if [ "${status}" = exited ]; then
      [ "${exit_code}" = 0 ] && return 0
      echo "ERROR: kafka-topic-init exited with code ${exit_code}" >&2
      docker logs --tail 80 kafka-topic-init >&2 || true
      return 1
    fi
    sleep 2
  done
  echo "ERROR: kafka-topic-init did not finish before timeout; status=${status:-missing}" >&2
  docker logs --tail 80 kafka-topic-init >&2 || true
  return 1
}
start_support_services() {
  cd "${RTCV3D_APP}/docker"
  if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
    docker compose up -d bev-fusion
  else
    COMPOSE_PROFILES=mosquitto,kafka docker compose up -d mosquitto kafka kafka-topic-init bev-fusion
    wait_healthy vss-mosquitto-mv3dt 120
    wait_healthy kafka 180
    wait_topic_init 120
  fi
  wait_healthy vss-rtvi-cv-bev-fusion 120
}
capture_file_kafka_baseline() {
  if [ "${INPUT_MODE:-}" = file ]; then
    cd "${RTCV3D_APP}"
    capture_kafka_offsets "${RUN_STATE_DIR}/kafka-baseline-${RUN_ID}.json" "${RAW_TOPIC:-mdx-raw}" "${FUSED_TOPIC:-mdx-bev}"
  fi
}
start_perception() {
  cd "${RTCV3D_APP}/docker"
  if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
    docker compose up -d perception
  else
    COMPOSE_PROFILES=mosquitto,kafka docker compose up -d perception
  fi
}
```

## Launch Without BEV Prestart

Use this only when no BEV visualizer/recorder must be active before perception starts. For file input, still start support services first and capture Kafka baselines before perception.

```bash
if [ "${INPUT_MODE:-}" = file ]; then
  start_support_services
  capture_file_kafka_baseline
  start_perception
else
  cd "${RTCV3D_APP}/docker"
  if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
    docker compose up -d
  else
    COMPOSE_PROFILES=mosquitto,kafka docker compose up -d
  fi
fi
```

## Two-Phase Launch For BEV

Use this whenever saved output is selected/defaulted, or whenever file input needs live or saved BEV visualization. The BEV visualizer uses a fresh `latest` Kafka consumer group, so the workflow waits for the expected consumer group assignment before starting perception. This uses Kafka CLI inspection; it does not require runtime script changes.

```bash
start_support_services
capture_file_kafka_baseline
```


Stop any previously tracked BEV recorder before launching a replacement; use `references/teardown.md` safe PID validation, never `pkill`.

```bash
cd "${RTCV3D_APP}"
RUN_ID="${RUN_ID:-$(cat generated/run-state/run-id 2>/dev/null || date +%Y%m%d_%H%M%S)}"
RUN_STATE_DIR="${RTCV3D_APP}/generated/run-state"
mkdir -p "${RUN_STATE_DIR}" bev-output
BEV_LOG="${RTCV3D_APP}/bev-output/bev-visualizer-${RUN_ID}.log"
BEV_SOURCE="${BEV_SOURCE:-fused}"
BEV_SAVE_VIDEO="${BEV_SAVE_VIDEO:-1}"
BEV_KAFKA_TOPIC="${BEV_KAFKA_TOPIC:-${FUSED_TOPIC:-mdx-bev}}"
BEV_KAFKA_BROKER="${BEV_KAFKA_BROKER:-${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}}"

BEV_SAVE_VIDEO="${BEV_SAVE_VIDEO}" BEV_SOURCE="${BEV_SOURCE}" BEV_KAFKA_TOPIC="${BEV_KAFKA_TOPIC}" BEV_KAFKA_BROKER="${BEV_KAFKA_BROKER}" BEV_DATASET_PATH="${BEV_DATASET_PATH:?set BEV_DATASET_PATH}" ./scripts/bev-visualizer.sh > "${BEV_LOG}" 2>&1 &
pid="$!"
printf '%s\n' "${pid}" > "${RUN_STATE_DIR}/bev-visualizer.pid"
readlink -f /proc/"${pid}"/cwd > "${RUN_STATE_DIR}/bev-visualizer.cwd" 2>/dev/null || true
tr '\0' ' ' < /proc/"${pid}"/cmdline > "${RUN_STATE_DIR}/bev-visualizer.cmdline" 2>/dev/null || true
awk '{print $22}' /proc/"${pid}"/stat > "${RUN_STATE_DIR}/bev-visualizer.start_ticks" 2>/dev/null || true
printf '%s\n' "${BEV_LOG}" > "${RUN_STATE_DIR}/bev-visualizer.log"

if [ "${BEV_SOURCE}" = fused ]; then
  if [ "${BEV_SAVE_VIDEO}" = 1 ] || [ -z "${DISPLAY:-}" ]; then
    BEV_GROUP="mv3dt_fused_rec_${pid}"
  else
    BEV_GROUP="mv3dt_fused_visualizer_${pid}"
  fi
else
  if [ "${BEV_SAVE_VIDEO}" = 1 ] || [ -z "${DISPLAY:-}" ]; then
    BEV_GROUP="mv3dt_bev_rec_${pid}"
  else
    BEV_GROUP="mv3dt_visualizer_${pid}"
  fi
fi
printf '%s\n' "${BEV_GROUP}" > "${RUN_STATE_DIR}/bev-visualizer.group"

kafka_client() {
  if docker ps --format '{{.Names}}' | grep -qx kafka; then
    docker exec kafka "$@"
  else
    (cd "${RTCV3D_APP}/docker" && docker compose --profile kafka run --rm --no-deps kafka "$@")
  fi
}
wait_bev_assignment() {
  group="$1"; topic="$2"; bootstrap="${BEV_KAFKA_BROKER}"
  deadline=$((SECONDS + ${BEV_ASSIGNMENT_TIMEOUT:-60}))
  out="${RUN_STATE_DIR}/bev-consumer-group-${RUN_ID}.txt"
  while [ "${SECONDS}" -lt "${deadline}" ]; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "ERROR: BEV visualizer exited before Kafka assignment" >&2
      tail -80 "${BEV_LOG}" >&2 || true
      exit 1
    fi
    kafka_client kafka-consumer-groups --bootstrap-server "${bootstrap}" --describe --group "${group}" --members --verbose > "${out}" 2>&1 || true
    if awk -v topic="${topic}" 'index($0, topic "(") > 0 {found=1} END {exit found ? 0 : 1}' "${out}"; then
      echo "BEV Kafka consumer assigned: group=${group} topic=${topic}"
      return 0
    fi
    kafka_client kafka-consumer-groups --bootstrap-server "${bootstrap}" --describe --group "${group}" > "${out}" 2>&1 || true
    if awk -v topic="${topic}" '$2 == topic {found=1} END {exit found ? 0 : 1}' "${out}"; then
      echo "BEV Kafka consumer assigned: group=${group} topic=${topic}"
      return 0
    fi
    sleep 1
  done
  echo "ERROR: BEV consumer group was not assigned before timeout: group=${group} topic=${topic}" >&2
  cat "${out}" >&2 || true
  tail -80 "${BEV_LOG}" >&2 || true
  return 1
}
wait_bev_assignment "${BEV_GROUP}" "${BEV_KAFKA_TOPIC}"
```

Start perception only after the BEV Kafka consumer group assignment is confirmed:

```bash
cd "${RTCV3D_APP}/docker"
if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
  docker compose up -d perception
else
  COMPOSE_PROFILES=mosquitto,kafka docker compose up -d perception
fi
```

For RTSP, start the BEV recorder/visualizer before `scripts/add-streams.sh`; no video data flows until streams are registered. For file input, always use this sequence when BEV is enabled because clips play once immediately.

Do not use `deploy/docker/compose.yml`, `MODE=mv3dt`, `BP_PROFILE`, warehouse `generated.env`, warehouse `overrides.env`, or warehouse app-data deployment profiles in this skill.

After launch, go to `references/verify-and-view.md`.

## Redeploy

When config, calibration, input mode, `NUM_CAMS`, broker mode, or visualization settings changed:

1. Stop the previously tracked BEV recorder with the safe PID flow in `references/teardown.md` if BEV was running.
2. Restage configs.
3. If BEV recording/viewing is enabled, run the two-phase launch above again.
4. Otherwise recreate the standalone compose services with the selected broker mode.

```bash
cd "${RTCV3D_APP}"
./scripts/stage-configs.sh
cd docker
if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
  docker compose up -d --force-recreate
else
  COMPOSE_PROFILES=mosquitto,kafka docker compose up -d --force-recreate
fi
```
