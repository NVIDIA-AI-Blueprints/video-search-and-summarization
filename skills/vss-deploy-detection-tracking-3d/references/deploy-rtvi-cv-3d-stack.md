# Deploy Standalone RT-CV-3D MV3DT

Load this reference for setup, environment preparation, compose launch, or redeploy of the standalone RT-CV-3D MV3DT stack.

## Contents

- [Resolve The App Directory](#resolve-the-app-directory)
- [What Compose Starts](#what-compose-starts)
- [Prerequisites](#prerequisites)
- [Preflight Config](#preflight-config)
- [NGC Login And Image Access](#ngc-login-and-image-access)
- [Bundled Resource Preflight](#bundled-resource-preflight)
- [Selected Output Tool Preflight](#selected-output-tool-preflight)
- [External Kafka Topic Preflight](#external-kafka-topic-preflight)
- [Current-Run State](#current-run-state)
- [Support Service Helpers](#support-service-helpers)
- [Launch Without BEV Prestart](#launch-without-bev-prestart)
- [Two-Phase Launch For BEV](#two-phase-launch-for-bev)
- [Redeploy](#redeploy)

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
| `perception` | `vss-rtvi-cv-mv3dt` | `${VSS_RT_CV_IMAGE}:${VSS_RT_CV_TAG}` | RT-DETR plus MV3DT perception; publishes `mdx-raw`. |
| `bev-fusion` | `vss-rtvi-cv-bev-fusion` | `${BEV_FUSION_IMAGE}:${BEV_FUSION_TAG}` | Fuses `mdx-raw` measurements and publishes `mdx-bev`. |
| `mosquitto` | `vss-mosquitto-mv3dt` | `${MOSQUITTO_IMAGE}` | Bundled MQTT broker for `/trck/*`, profile `mosquitto`. |
| `kafka` | `kafka` | `${KAFKA_IMAGE}` | Bundled Kafka broker for `mdx-raw` and `mdx-bev`, profile `kafka`. |
| `kafka-topic-init` | `kafka-topic-init` | `${KAFKA_IMAGE}` | One-shot topic creation, profile `kafka`. |

Inspect resolved runtime images only from Compose:

```bash
cd "${RTCV3D_APP}/docker"
docker compose config --images | sort -u
```

Use platform-specific image tags only when they are already supplied by the checked-out compose package/`docker/.env` or explicitly provided by the user. Do not set, derive, or recommend a specific `VSS_RT_CV_TAG` or `BEV_FUSION_TAG` in this skill.

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

Cold TensorRT engine initialization can take 5-10 minutes on the first run or when packaged engines are incompatible with the installed TensorRT runtime. The perception container commonly writes rebuilt engines as UID/GID `1000`; preflight cache writeability before launch and use a scoped ACL only after user approval if needed:

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
MODELS_DIR="${MODELS_DIR:-$(read_env MODELS_DIR)}"
CONTAINER_UID="${RTCV3D_CONTAINER_UID:-1000}"
CONTAINER_GID="${RTCV3D_CONTAINER_GID:-1000}"
for d in "${MODELS_DIR}/mtmc" "${MODELS_DIR}/mv3dt/BodyPose3DNet"; do
  test -d "${d}" || { echo "ERROR: model cache dir missing: ${d}" >&2; exit 1; }
  if command -v getfacl >/dev/null 2>&1; then getfacl -cp "${d}" | sed -n '1,12p'; fi
  if python3 - "${CONTAINER_UID}" "${CONTAINER_GID}" "${d}" <<'PYCHECK'
import os, stat, sys
uid = int(sys.argv[1]); gid = int(sys.argv[2]); path = sys.argv[3]
st = os.stat(path)
mode = st.st_mode
ok = ((st.st_uid == uid and mode & stat.S_IWUSR and mode & stat.S_IXUSR) or
      (st.st_gid == gid and mode & stat.S_IWGRP and mode & stat.S_IXGRP) or
      (mode & stat.S_IWOTH and mode & stat.S_IXOTH))
sys.exit(0 if ok else 1)
PYCHECK
  then
    :
  elif command -v getfacl >/dev/null 2>&1 && getfacl -cp "${d}" | grep -Eq "^(user:${CONTAINER_UID}:.*w.*x|group:${CONTAINER_GID}:.*w.*x)"; then
    :
  else
    echo "WARN: container uid ${CONTAINER_UID}:${CONTAINER_GID} may not persist TensorRT engines in ${d}." >&2
    echo "      With approval, use a scoped ACL: sudo setfacl -m u:${CONTAINER_UID}:rwx -m d:u:${CONTAINER_UID}:rwx '${d}'" >&2
  fi
done
```

Do not fix model-cache write failures with broad `chmod 777` or broad recursive `chown`.

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
KAFKA_CONTROLLER_PORT="${KAFKA_CONTROLLER_PORT:-$(read_env KAFKA_CONTROLLER_PORT)}"
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
export INPUT_MODE RAW_TOPIC FUSED_TOPIC KAFKA_PORT KAFKA_CONTROLLER_PORT USE_EXTERNAL_BROKERS MQTT_HOST MQTT_PORT KAFKA_BOOTSTRAP
```

## NGC Login And Image Access

Use current-session or existing NGC credentials without printing keys. Do not write credentials into the repo, command arguments, logs, or final answer.

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

IMAGES="$(docker compose config --images | sort -u)"
test -n "${IMAGES}" || { echo "ERROR: no images resolved from compose" >&2; exit 1; }
printf '%s\n' "${IMAGES}"
```

If the user asks you to pull/check images before launching, use only the images reported by `docker compose config --images`. Before pulling or starting the stack, fail fast on inaccessible images without downloading layers:

```bash
cd "${RTCV3D_APP}/docker"
IMAGES="$(docker compose config --images | sort -u)"
test -n "${IMAGES}" || { echo "ERROR: no images resolved from compose" >&2; exit 1; }
for img in ${IMAGES}; do
  echo "Checking image access: ${img}"
  if ! docker manifest inspect "${img}" >/dev/null 2>&1; then
    echo "ERROR: cannot access image ${img}. Log in to the required registry, confirm NGC Catalog access for nvcr.io images, then retry." >&2
    exit 1
  fi
done
```

## Bundled Resource Preflight

Run this before `generate-configs.sh`, `stage-configs.sh`, or bundled-broker launch. If standalone RT-CV-3D containers from this app already exist, reuse them and do not rewrite broker ports. For a fresh bundled start, verify fixed container-name ownership and choose free host ports for Kafka, MQTT, and the DeepStream REST endpoint. Skip this section for explicit external-broker mode.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
APP_COMPOSE_DIR="$(readlink -f "${RTCV3D_APP}/docker")"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
write_env() {
  key="$1"; value="$2"; file="${RTCV3D_APP}/docker/.env"; tmp="${file}.tmp"
  awk -F= -v key="${key}" -v value="${value}" '
    BEGIN {done=0}
    $1 == key {print key "=" value; done=1; next}
    {print}
    END {if (!done) print key "=" value}
  ' "${file}" > "${tmp}"
  mv "${tmp}" "${file}"
}
container_owner_dir() {
  owner="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project.working_dir" }}' "$1" 2>/dev/null || true)"
  readlink -f "${owner}" 2>/dev/null || printf '%s\n' "${owner}"
}
port_free() {
  python3 - "$1" <<'PYCHECK'
import socket, sys
port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    try:
        s.bind(("0.0.0.0", port))
    except OSError:
        raise SystemExit(1)
PYCHECK
}
pick_port() {
  current="$1"; shift
  for p in "${current}" "$@"; do
    [ -n "${p}" ] || continue
    if port_free "${p}"; then
      printf '%s\n' "${p}"
      return 0
    fi
  done
  return 1
}
USE_EXTERNAL_BROKERS="${USE_EXTERNAL_BROKERS:-$(read_env USE_EXTERNAL_BROKERS)}"; USE_EXTERNAL_BROKERS="${USE_EXTERNAL_BROKERS:-0}"
if [ "${USE_EXTERNAL_BROKERS}" != 1 ]; then
  reuse_existing=0
  for name in vss-rtvi-cv-mv3dt vss-rtvi-cv-bev-fusion vss-mosquitto-mv3dt kafka kafka-topic-init; do
    if docker inspect "${name}" >/dev/null 2>&1; then
      owner="$(container_owner_dir "${name}")"
      if [ "${owner}" != "${APP_COMPOSE_DIR}" ]; then
        echo "ERROR: fixed container name ${name} already exists and is not owned by ${APP_COMPOSE_DIR}; stop/rename the foreign container or use external brokers." >&2
        exit 1
      fi
      reuse_existing=1
    fi
  done

  if [ "${reuse_existing}" = 1 ]; then
    echo "Reusing existing standalone RT-CV-3D containers; preserving docker/.env broker ports and container-local Kafka state."
    KAFKA_PORT="${KAFKA_PORT:-$(read_env KAFKA_PORT)}"; KAFKA_PORT="${KAFKA_PORT:-9092}"
    KAFKA_CONTROLLER_PORT="${KAFKA_CONTROLLER_PORT:-$(read_env KAFKA_CONTROLLER_PORT)}"; KAFKA_CONTROLLER_PORT="${KAFKA_CONTROLLER_PORT:-9093}"
    MQTT_PORT="${MQTT_PORT:-$(read_env MQTT_PORT)}"; MQTT_PORT="${MQTT_PORT:-1883}"
    DS_HTTP_PORT="${DS_HTTP_PORT:-$(read_env DS_HTTP_PORT)}"; DS_HTTP_PORT="${DS_HTTP_PORT:-9000}"
    KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"; KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT}}"
  else
    current_data="${KAFKA_PORT:-$(read_env KAFKA_PORT)}"; current_data="${current_data:-9092}"
    current_ctrl="${KAFKA_CONTROLLER_PORT:-$(read_env KAFKA_CONTROLLER_PORT)}"; current_ctrl="${current_ctrl:-9093}"
    selected_pair=""
    for pair in "${current_data}:${current_ctrl}" "19092:19093" "29092:29093" "39092:39093"; do
      data_port="${pair%%:*}"; ctrl_port="${pair##*:}"
      if port_free "${data_port}" && port_free "${ctrl_port}"; then
        selected_pair="${pair}"
        break
      fi
    done
    test -n "${selected_pair}" || { echo "ERROR: no free bundled Kafka port pair found; set KAFKA_PORT/KAFKA_CONTROLLER_PORT or use external Kafka" >&2; exit 1; }
    KAFKA_PORT="${selected_pair%%:*}"
    KAFKA_CONTROLLER_PORT="${selected_pair##*:}"
    KAFKA_BOOTSTRAP="localhost:${KAFKA_PORT}"
    MQTT_PORT="$(pick_port "${MQTT_PORT:-$(read_env MQTT_PORT)}" 1883 1884 2883 3883)" || { echo "ERROR: no free bundled MQTT port found; set MQTT_PORT or use external MQTT" >&2; exit 1; }
    DS_HTTP_PORT="$(pick_port "${DS_HTTP_PORT:-$(read_env DS_HTTP_PORT)}" 9000 9001 19000 29000)" || { echo "ERROR: no free DeepStream REST port found; set DS_HTTP_PORT" >&2; exit 1; }
    write_env KAFKA_PORT "${KAFKA_PORT}"
    write_env KAFKA_CONTROLLER_PORT "${KAFKA_CONTROLLER_PORT}"
    write_env KAFKA_BOOTSTRAP "${KAFKA_BOOTSTRAP}"
    write_env MQTT_PORT "${MQTT_PORT}"
    write_env DS_HTTP_PORT "${DS_HTTP_PORT}"
  fi
  export KAFKA_PORT KAFKA_CONTROLLER_PORT KAFKA_BOOTSTRAP MQTT_PORT DS_HTTP_PORT
fi
```

## Selected Output Tool Preflight

Run this before launching a saved-output or BEV visualization/recording workflow. Saved artifacts are part of the success criteria, so verify the host can parse outputs before starting the finite run.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
SAVE_VIDEO="${SAVE_VIDEO:-$(read_env SAVE_VIDEO)}"; SAVE_VIDEO="${SAVE_VIDEO:-0}"
if [ "${SAVE_VIDEO}" = 1 ] || [ "${BEV_SAVE_VIDEO:-0}" = 1 ]; then
  command -v ffprobe >/dev/null 2>&1 || { echo "ERROR: ffprobe is required to verify saved grid/BEV videos; install ffmpeg or provide ffprobe on PATH before launch" >&2; exit 1; }
fi
if [ "${BEV_SAVE_VIDEO:-0}" = 1 ] || [ -n "${BEV_DATASET_PATH:-}" ]; then
  # shellcheck disable=SC1091
  source "${RTCV3D_APP}/scripts/ensure-venv.sh"
  ensure_venv || { echo "ERROR: BEV visualizer Python environment could not be created; see troubleshooting for python3-venv/ensurepip/pip bootstrap" >&2; exit 1; }
  "${VENV_PY}" - <<'PYCHECK'
try:
    import cv2  # noqa: F401
    import confluent_kafka  # noqa: F401
    import numpy  # noqa: F401
    import yaml  # noqa: F401
except Exception as exc:
    raise SystemExit(f"ERROR: BEV visualizer Python dependencies are not usable: {exc}")
PYCHECK
fi
```

## External Kafka Topic Preflight

For explicit external-broker mode, require the selected `RAW_TOPIC` and `FUSED_TOPIC` to exist and be describable before `bev-fusion` or `perception` starts. Managed Kafka clusters commonly disable auto-topic creation; if a topic is missing, stop and ask the user to create it with the cluster's replication/auth/TLS policy, then rerun. The support-service helper below enforces this with bounded `kafka-topics --describe` calls against `KAFKA_BOOTSTRAP`.

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
verify_external_kafka_topics() {
  [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ] || return 0
  bootstrap="${KAFKA_BOOTSTRAP:?set external KAFKA_BOOTSTRAP}"
  cd "${RTCV3D_APP}/docker"
  for topic in "${RAW_TOPIC:-mdx-raw}" "${FUSED_TOPIC:-mdx-bev}"; do
    if ! out="$(timeout "${KAFKA_TOPIC_TIMEOUT:-30}s" docker compose --profile kafka run --rm --no-deps kafka kafka-topics --bootstrap-server "${bootstrap}" --describe --topic "${topic}" 2>&1)"; then
      echo "ERROR: external Kafka topic ${topic} is missing or not describable on ${bootstrap}." >&2
      printf '%s\n' "${out}" >&2
      echo "Create the topic with the correct cluster replication/auth/TLS policy, then rerun." >&2
      return 1
    fi
    printf '%s\n' "${out}" | awk -v topic="${topic}" '$0 ~ "^Topic: " topic "([[:space:]]|$)" {found=1} END {exit found ? 0 : 1}' || {
      echo "ERROR: kafka-topics did not describe expected topic ${topic} on ${bootstrap}." >&2
      printf '%s\n' "${out}" >&2
      return 1
    }
  done
}
start_support_services() {
  cd "${RTCV3D_APP}/docker" || return 1
  if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
    verify_external_kafka_topics || return 1
    docker compose up -d bev-fusion || return 1
  else
    COMPOSE_PROFILES=mosquitto,kafka docker compose up -d mosquitto kafka kafka-topic-init bev-fusion || return 1
    wait_healthy vss-mosquitto-mv3dt 120 || return 1
    wait_healthy kafka 180 || return 1
    wait_topic_init 120 || return 1
  fi
  wait_healthy vss-rtvi-cv-bev-fusion 120 || return 1
}
capture_file_kafka_baseline() {
  if [ "${INPUT_MODE:-}" = file ]; then
    cd "${RTCV3D_APP}" || return 1
    capture_kafka_offsets "${RUN_STATE_DIR}/kafka-baseline-${RUN_ID}.json" "${RAW_TOPIC:-mdx-raw}" "${FUSED_TOPIC:-mdx-bev}" || return 1
  fi
}
start_perception() {
  cd "${RTCV3D_APP}/docker" || return 1
  if [ "${INPUT_MODE:-}" = file ]; then
    if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
      docker compose up -d --no-deps --force-recreate perception || return 1
    else
      COMPOSE_PROFILES=mosquitto,kafka docker compose up -d --no-deps --force-recreate perception || return 1
    fi
  else
    if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
      docker compose up -d perception || return 1
    else
      COMPOSE_PROFILES=mosquitto,kafka docker compose up -d perception || return 1
    fi
  fi
  if docker inspect vss-rtvi-cv-mv3dt >/dev/null 2>&1; then
    state_dir="${RUN_STATE_DIR:-${RTCV3D_APP}/generated/run-state}"
    mkdir -p "${state_dir}" || return 1
    docker inspect --format '{{.Id}}' vss-rtvi-cv-mv3dt > "${state_dir}/perception-container-id" || return 1
    docker inspect --format '{{.State.StartedAt}}' vss-rtvi-cv-mv3dt > "${state_dir}/perception-started-at" || return 1
  else
    echo "ERROR: perception container was not created" >&2
    return 1
  fi
}
```

## Launch Without BEV Prestart

Use this only when no BEV visualizer/recorder must be active before perception starts. For file input, still start support services first and capture Kafka baselines before perception.

```bash
if [ "${INPUT_MODE:-}" = file ]; then
  start_support_services || exit 1
  capture_file_kafka_baseline || exit 1
  start_perception || exit 1
else
  cd "${RTCV3D_APP}/docker" || exit 1
  if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
    docker compose up -d || exit 1
  else
    COMPOSE_PROFILES=mosquitto,kafka docker compose up -d || exit 1
  fi
fi
```

## Two-Phase Launch For BEV

Use this whenever saved output is selected/defaulted, or whenever file input needs live or saved BEV visualization. Display mode starts the DeepStream OSD camera grid and a separate live fused BEV window by default. The BEV visualizer uses a fresh `latest` Kafka consumer group, so the workflow waits for the expected consumer group assignment before starting perception. The recorder/visualizer must survive long first-run TensorRT engine builds. In agent tool runners that reap background children when a command finishes, run the BEV launch, perception launch, EOS wait, verification, BEV finalization, and cleanup in one long-lived shell/session; do not execute the BEV start as a standalone completed tool call.

```bash
start_support_services || exit 1
capture_file_kafka_baseline || exit 1
```

For finite file input, keep the next BEV launch block and the later `start_perception`/verification steps in the same foreground-controlled command or persistent terminal session. `nohup ... &` protects against terminal hangup, but it does not protect against tool runners that kill the process group after a short command returns.


Stop any previously tracked BEV recorder before launching a replacement; use `references/teardown.md` safe PID validation, never `pkill`.

```bash
cd "${RTCV3D_APP}"
RUN_ID="${RUN_ID:-$(cat generated/run-state/run-id 2>/dev/null || date +%Y%m%d_%H%M%S)}"
RUN_STATE_DIR="${RTCV3D_APP}/generated/run-state"
mkdir -p "${RUN_STATE_DIR}" bev-output
BEV_LOG="${RTCV3D_APP}/bev-output/bev-visualizer-${RUN_ID}.log"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
OSD="${OSD:-$(read_env OSD)}"; OSD="${OSD:-0}"
SAVE_VIDEO="${SAVE_VIDEO:-$(read_env SAVE_VIDEO)}"; SAVE_VIDEO="${SAVE_VIDEO:-0}"
if [ -z "${DISPLAY:-}" ] && [ -f "${RUN_STATE_DIR}/display.env" ]; then
  DISPLAY="$(awk -F= '$1 == "DISPLAY" {sub("^[^=]*=", "", $0); print; exit}' "${RUN_STATE_DIR}/display.env")"
  [ -n "${DISPLAY}" ] && export DISPLAY
fi
BEV_SOURCE="${BEV_SOURCE:-fused}"
if [ -z "${BEV_SAVE_VIDEO+x}" ]; then
  if [ "${OSD}" = 1 ] && [ "${SAVE_VIDEO}" != 1 ] && [ -n "${DISPLAY:-}" ]; then
    BEV_SAVE_VIDEO=0
  else
    BEV_SAVE_VIDEO=1
  fi
fi
BEV_KAFKA_TOPIC="${BEV_KAFKA_TOPIC:-${FUSED_TOPIC:-mdx-bev}}"
BEV_KAFKA_BROKER="${BEV_KAFKA_BROKER:-${KAFKA_BOOTSTRAP:-localhost:${KAFKA_PORT:-9092}}}"

nohup env PYTHONUNBUFFERED=1 DISPLAY="${DISPLAY:-}" BEV_SAVE_VIDEO="${BEV_SAVE_VIDEO}" BEV_SOURCE="${BEV_SOURCE}" BEV_KAFKA_TOPIC="${BEV_KAFKA_TOPIC}" BEV_KAFKA_BROKER="${BEV_KAFKA_BROKER}" BEV_DATASET_PATH="${BEV_DATASET_PATH:?set BEV_DATASET_PATH}" ./scripts/bev-visualizer.sh < /dev/null > "${BEV_LOG}" 2>&1 &
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
bev_recorder_alive() {
  pid="$(cat "${RUN_STATE_DIR}/bev-visualizer.pid" 2>/dev/null || true)"
  printf '%s' "${pid}" | grep -Eq '^[0-9]+$' || { echo "ERROR: invalid BEV recorder PID: ${pid}" >&2; return 1; }
  kill -0 "${pid}" 2>/dev/null || { echo "ERROR: BEV recorder is not running before perception starts; see ${BEV_LOG}" >&2; tail -80 "${BEV_LOG}" >&2 || true; return 1; }
}
wait_bev_assignment "${BEV_GROUP}" "${BEV_KAFKA_TOPIC}" || exit 1
bev_recorder_alive || exit 1
```

In that same long-lived shell/session, start perception only after the BEV Kafka consumer group assignment is confirmed and the recorder PID is still alive:

```bash
bev_recorder_alive || exit 1
start_perception || exit 1
```

For RTSP, start the BEV recorder/visualizer before the direct REST stream registration step; no video data flows until streams are registered. For file input, always use this sequence when BEV is enabled because clips play once immediately. A cold first run can spend several minutes compiling TensorRT engines before messages appear; keep the recorder/visualizer running through EOS in the same long-lived shell/session and use `references/verify-and-view.md` to detect premature exit. For live display file runs, tell the user that `DeepStreamTest5App` is the camera grid and `Bird-Eye View of Multi-View 3D Tracking` is the separate BEV window; after EOS, have them press `q` in the BEV window or safely stop only the tracked current-run PID.

Do not use `deploy/docker/compose.yml`, `MODE=mv3dt`, `BP_PROFILE`, warehouse `generated.env`, warehouse `overrides.env`, or warehouse app-data deployment profiles in this skill.

After launch, go to `references/verify-and-view.md`.

## Redeploy

When config, calibration, input mode, `NUM_CAMS`, broker mode, or visualization settings changed:

1. Stop the previously tracked BEV recorder with the safe PID flow in `references/teardown.md` if BEV was running.
2. Restage configs.
3. Branch on `INPUT_MODE` before restarting anything.

For `INPUT_MODE=file`, never use full-stack `docker compose up -d` or `docker compose up -d --force-recreate`. Preserve the prestarted broker/topic/group state by reusing the same ordering as a fresh file run:

```bash
cd "${RTCV3D_APP}"
./scripts/stage-configs.sh || exit 1
start_support_services || exit 1
capture_file_kafka_baseline || exit 1
```

If BEV recording/viewing is enabled, run the `Two-Phase Launch For BEV` recorder block above through `wait_bev_assignment`, then start perception. If BEV is not enabled, start perception directly:

```bash
start_perception || exit 1
```

For `INPUT_MODE=stream` when no BEV prestart is required, a full Compose recreate is acceptable because stream registration happens only after REST `/api/v1/ready` reports `ds-ready=YES`:

```bash
cd "${RTCV3D_APP}" || exit 1
./scripts/stage-configs.sh || exit 1
cd docker || exit 1
if [ "${USE_EXTERNAL_BROKERS:-0}" = 1 ]; then
  docker compose up -d --force-recreate || exit 1
else
  COMPOSE_PROFILES=mosquitto,kafka docker compose up -d --force-recreate || exit 1
fi
```

For stream redeploy with saved/live BEV prestart, start the BEV recorder/visualizer first with the two-phase BEV block, then register streams with the direct REST registration block in `references/configure-cameras.md`; it waits on REST readiness before registering.
