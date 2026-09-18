# Verify And Visualize Standalone RT-CV-3D

Load this reference after compose launch, when checking health, Kafka data flow, OSD, saved perception video, or BEV visualization.

## Contents

- [Container Health](#container-health)
- [Perception Readiness](#perception-readiness)
- [Kafka Offsets](#kafka-offsets)
- [RTSP Stream Set And FPS](#rtsp-stream-set-and-fps)
- [Saved Perception Video](#saved-perception-video)
- [BEV Visualization And Saved BEV](#bev-visualization-and-saved-bev)
- [Success Report](#success-report)

## Container Health

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}/docker"
docker compose ps -a
docker inspect --format 'perception status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' vss-rtvi-cv-mv3dt 2>/dev/null || true
docker ps --format '{{.Names}}	{{.Status}}'   | awk '$1 ~ /^(vss-rtvi-cv-bev-fusion|vss-mosquitto-mv3dt|kafka)$/ {print}'
```

Expected container states:

- RTSP input: `vss-rtvi-cv-mv3dt`, `vss-rtvi-cv-bev-fusion`, and selected broker services stay running until stopped.
- File input while processing: `vss-rtvi-cv-mv3dt` may be running.
- File input after end-of-stream: `vss-rtvi-cv-mv3dt` should be `Exited (0)` and logs should include `App run successful`; this is a clean completed run, not a crash.
- `kafka-topic-init` is a one-shot and should exit successfully in bundled-broker mode.

Treat `vss-rtvi-cv-mv3dt` as failed only if it exits non-zero, is OOMKilled, lacks the success log for completed file input, or logs fatal/error conditions that prevented output.

Check BEV Fusion health:

```bash
docker inspect --format '{{.State.Health.Status}}' vss-rtvi-cv-bev-fusion
```

Expected: `healthy`.

## Perception Readiness

Use current-run bounded log checks; do not wait forever. File-input verification must use the container started for this run, not historical retained logs.

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
mkdir -p generated/run-state
RUN_STATE_DIR="${RTCV3D_APP}/generated/run-state"
RUN_ID="${RUN_ID:-$(cat generated/run-state/run-id 2>/dev/null || date +%Y%m%d_%H%M%S)}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
INPUT_MODE="${INPUT_MODE:-$(read_env INPUT_MODE)}"
PERCEPTION_ID_EXPECTED="$(cat "${RUN_STATE_DIR}/perception-container-id" 2>/dev/null || true)"
PERCEPTION_STARTED_AT="$(cat "${RUN_STATE_DIR}/perception-started-at" 2>/dev/null || true)"
if [ -z "${PERCEPTION_STARTED_AT}" ]; then
  PERCEPTION_STARTED_AT="$(docker inspect --format '{{.State.StartedAt}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
fi
current_logs() {
  if [ -n "${PERCEPTION_STARTED_AT}" ]; then
    docker logs --since "${PERCEPTION_STARTED_AT}" vss-rtvi-cv-mv3dt 2>&1
  else
    docker logs vss-rtvi-cv-mv3dt 2>&1
  fi
}
bev_recorder_has_saved_video() {
  log="$(cat "${RUN_STATE_DIR}/bev-visualizer.log" 2>/dev/null || true)"
  [ -n "${log}" ] && [ -f "${log}" ] || return 1
  awk '/Video saved:/ { line=$0; sub(/^.*\(/, "", line); sub(/ frames\).*$/, "", line); if (line ~ /^[0-9]+$/ && line > 0) ok=1 } END { exit ok ? 0 : 1 }' "${log}"
}
check_bev_recorder_during_file_run() {
  pid="$(cat "${RUN_STATE_DIR}/bev-visualizer.pid" 2>/dev/null || true)"
  [ -n "${pid}" ] || return 0
  printf '%s' "${pid}" | grep -Eq '^[0-9]+$' || { echo "ERROR: invalid tracked BEV recorder PID: ${pid}" >&2; exit 1; }
  if ! kill -0 "${pid}" 2>/dev/null && ! bev_recorder_has_saved_video; then
    log="$(cat "${RUN_STATE_DIR}/bev-visualizer.log" 2>/dev/null || true)"
    echo "ERROR: BEV recorder exited before file-mode EOS without saving a positive-frame video; log=${log}" >&2
    [ -n "${log}" ] && tail -80 "${log}" >&2 || true
    exit 1
  fi
}

docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | tail -80

if [ "${INPUT_MODE:-}" = file ]; then
  current_id="$(docker inspect --format '{{.Id}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
  if [ -n "${PERCEPTION_ID_EXPECTED}" ] && [ "${current_id}" != "${PERCEPTION_ID_EXPECTED}" ]; then
    echo "ERROR: perception container ID changed after launch; expected ${PERCEPTION_ID_EXPECTED}, got ${current_id}" >&2
    exit 1
  fi

  # File input may not emit ds-ready. Treat Pipeline running or ds-ready as useful
  # startup evidence when present, but do not fail file mode solely because the
  # service-ready marker is absent; EOS success and Kafka/artifact evidence are
  # the deterministic success criteria.
  ready_deadline=$((SECONDS + ${PERCEPTION_READY_TIMEOUT:-120}))
  startup_seen=0
  while [ "${SECONDS}" -lt "${ready_deadline}" ]; do
    logs="$(current_logs || true)"
    if printf '%s\n' "${logs}" | grep -qE 'Pipeline running|ds-ready: YES'; then
      startup_seen=1
      break
    fi
    PERCEPTION_STATUS="$(docker inspect --format '{{.State.Status}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
    [ "${PERCEPTION_STATUS}" = exited ] && break
    sleep 2
  done
  if [ "${startup_seen}" = 1 ]; then
    echo 'current file-input run reached startup log evidence'
  else
    echo 'WARN: file-input run did not emit Pipeline running or ds-ready before EOS/timeout; continuing to EOS success checks'
  fi

  eos_deadline=$((SECONDS + ${FILE_EOS_TIMEOUT:-900}))
  while :; do
    check_bev_recorder_during_file_run
    PERCEPTION_STATUS="$(docker inspect --format '{{.State.Status}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
    [ "${PERCEPTION_STATUS}" = exited ] && break
    [ "${SECONDS}" -lt "${eos_deadline}" ] || { echo "ERROR: file-input perception did not reach EOS before timeout; status=${PERCEPTION_STATUS:-missing}" >&2; exit 1; }
    sleep 5
  done
  PERCEPTION_EXIT="$(docker inspect --format '{{.State.ExitCode}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
  test "${PERCEPTION_EXIT}" = 0 || { echo "ERROR: file-input perception exited non-zero: ${PERCEPTION_EXIT}" >&2; exit 1; }
  CURRENT_LOG="generated/run-state/perception-logs-${RUN_ID}.txt"
  current_logs > "${CURRENT_LOG}"
  grep -q 'App run successful' "${CURRENT_LOG}" || { echo "ERROR: current file run lacks App run successful after ${PERCEPTION_STARTED_AT}" >&2; exit 1; }
  echo 'current file-input run completed successfully after EOS'
else
  DS_HTTP_PORT_EFFECTIVE="${DS_HTTP_PORT:-$(read_env DS_HTTP_PORT)}"
  DS_HTTP_PORT_EFFECTIVE="${DS_HTTP_PORT_EFFECTIVE:-9000}"
  READY_URL="${RTCV3D_READY_URL:-http://localhost:${DS_HTTP_PORT_EFFECTIVE}/api/v1/ready}"
  deadline=$((SECONDS + ${PERCEPTION_READY_TIMEOUT:-120}))
  ready_payload=""
  until ready_payload="$(curl -fsS --max-time 2 "${READY_URL}" 2>/dev/null)" &&
        printf "%s\n" "${ready_payload}" | grep -Eq "\"ds-ready\"[[:space:]]*:[[:space:]]*\"YES\""; do
    [ "${SECONDS}" -lt "${deadline}" ] || {
      echo "ERROR: REST readiness did not report ds-ready YES before timeout" >&2
      current_logs | tail -80 >&2 || true
      exit 1
    }
    sleep 2
  done
  echo "perception REST readiness reported ds-ready YES"
  if ! current_logs | grep -q "ds-ready: YES"; then
    echo "INFO: ds-ready log marker not observed; using REST readiness evidence"
  fi
fi
```

For `INPUT_MODE=stream`, 0 FPS before RTSP registration is normal. After streams are registered, the perception container should remain running until stopped; an unexpected exit is a failure.

For `INPUT_MODE=file`, clips start immediately and the perception container exits when all files finish. Do not require `ds-ready: YES` in file mode; `Pipeline running` is useful startup evidence when present, but `Exited (0)` plus `App run successful`, Kafka offset deltas, and saved artifact checks determine success. Cold TensorRT compilation can keep the run active for 5-10 minutes before messages appear, so keep the BEV recorder process alive until EOS/finalization in the same long-lived shell/session that launched it. Verify Kafka offsets and saved artifacts instead of restarting perception.

## Kafka Offsets

Use Kafka high-watermark offsets for deployment success checks. Do not rely on unbounded live-tail consumers for completed file runs.

Define these helpers in the shell where verification runs:

```bash
cd "${RTCV3D_APP:?set RTCV3D_APP}"
read_env() {
  awk -F= -v key="$1" '$1 == key {v=$0; sub("^[^=]*=", "", v); gsub(/^"|"$/, "", v); gsub(/^\047|\047$/, "", v); print v; exit}' "${RTCV3D_APP}/docker/.env"
}
RAW_TOPIC="${RAW_TOPIC:-$(read_env RAW_TOPIC)}"; RAW_TOPIC="${RAW_TOPIC:-mdx-raw}"
FUSED_TOPIC="${FUSED_TOPIC:-$(read_env FUSED_TOPIC)}"; FUSED_TOPIC="${FUSED_TOPIC:-mdx-bev}"
KAFKA_PORT="${KAFKA_PORT:-$(read_env KAFKA_PORT)}"
KAFKA_BOOTSTRAP_EFFECTIVE="${KAFKA_BOOTSTRAP:-$(read_env KAFKA_BOOTSTRAP)}"
KAFKA_BOOTSTRAP_EFFECTIVE="${KAFKA_BOOTSTRAP_EFFECTIVE:-localhost:${KAFKA_PORT:-9092}}"

kafka_client() {
  if docker ps --format '{{.Names}}' | grep -qx kafka; then
    docker exec kafka "$@"
  else
    (cd "${RTCV3D_APP}/docker" && docker compose --profile kafka run --rm --no-deps kafka "$@")
  fi
}
kafka_high_watermark() {
  topic="$1"
  if ! output="$(kafka_client kafka-get-offsets --bootstrap-server "${KAFKA_BOOTSTRAP_EFFECTIVE}" --topic "${topic}" --time -1 2>&1)"; then
    echo "ERROR: kafka-get-offsets failed for ${topic} on ${KAFKA_BOOTSTRAP_EFFECTIVE}" >&2
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
```


### RTSP Active Stream Growth

For live RTSP success, offsets must grow while streams are active. Use bounded polling:

```bash
cd "${RTCV3D_APP}"
mkdir -p generated/run-state
BEFORE="$(mktemp)"; AFTER="$(mktemp)"
capture_kafka_offsets "${BEFORE}" "${RAW_TOPIC}" "${FUSED_TOPIC}"
deadline=$((SECONDS + 90))
while [ "${SECONDS}" -lt "${deadline}" ]; do
  sleep 5
  capture_kafka_offsets "${AFTER}" "${RAW_TOPIC}" "${FUSED_TOPIC}"
  BEFORE="${BEFORE}" AFTER="${AFTER}" RAW_TOPIC="${RAW_TOPIC}" FUSED_TOPIC="${FUSED_TOPIC}" python3 - <<'PYCHECK' && break || true
import json, os
before = json.load(open(os.environ['BEFORE'], encoding='utf-8'))
after = json.load(open(os.environ['AFTER'], encoding='utf-8'))
for topic in [os.environ['RAW_TOPIC'], os.environ['FUSED_TOPIC']]:
    if after[topic]['high'] <= before[topic]['high']:
        raise SystemExit(1)
print('Kafka offsets grew for mdx-raw and mdx-bev')
PYCHECK
done
BEFORE="${BEFORE}" AFTER="${AFTER}" RAW_TOPIC="${RAW_TOPIC}" FUSED_TOPIC="${FUSED_TOPIC}" python3 - <<'PYCHECK'
import json, os
before = json.load(open(os.environ['BEFORE'], encoding='utf-8'))
after = json.load(open(os.environ['AFTER'], encoding='utf-8'))
for topic in [os.environ['RAW_TOPIC'], os.environ['FUSED_TOPIC']]:
    if after[topic]['high'] <= before[topic]['high']:
        raise SystemExit(f"ERROR: {topic} did not grow: {before[topic]['high']} -> {after[topic]['high']}")
PYCHECK
```

Active RTSP sample dumps may use live-tail sampling, but always bound the process with `timeout`:

```bash
cd "${RTCV3D_APP}"
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP_EFFECTIVE}" --topic "${RAW_TOPIC}" --count 20
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP_EFFECTIVE}" --topic "${FUSED_TOPIC}" --count 20
```

### Finite File Input Offset Verification

For file mode, capture Kafka baselines before starting perception. After EOS, offsets only need to be greater than the baselines; they do not need to continue growing.

```bash
cd "${RTCV3D_APP}"
RUN_ID="${RUN_ID:-$(cat generated/run-state/run-id)}"
BASELINE="generated/run-state/kafka-baseline-${RUN_ID}.json"
AFTER="generated/run-state/kafka-after-${RUN_ID}.json"
capture_kafka_offsets "${AFTER}" "${RAW_TOPIC}" "${FUSED_TOPIC}"
BASELINE="${BASELINE}" AFTER="${AFTER}" RAW_TOPIC="${RAW_TOPIC}" FUSED_TOPIC="${FUSED_TOPIC}" python3 - <<'PYCHECK'
import json, os
before = json.load(open(os.environ['BASELINE'], encoding='utf-8'))
after = json.load(open(os.environ['AFTER'], encoding='utf-8'))
for topic in [os.environ['RAW_TOPIC'], os.environ['FUSED_TOPIC']]:
    b, a = before[topic]['high'], after[topic]['high']
    if a <= b:
        raise SystemExit(f"ERROR: {topic} did not exceed file-run baseline: {b} -> {a}")
    print(f"{topic}: {b} -> {a}")
PYCHECK
```

For completed file-input runs, do not use unbounded live-tail dumps. If the broker/topic is known fresh for the current run, use an explicitly bounded beginning read:

```bash
cd "${RTCV3D_APP}"
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP_EFFECTIVE}" --topic "${RAW_TOPIC}" --from-beginning --count 20
timeout 20s ./scripts/kafka-dump.sh --bootstrap "${KAFKA_BOOTSTRAP_EFFECTIVE}" --topic "${FUSED_TOPIC}" --from-beginning --count 20
```

## RTSP Stream Set And FPS

For live RTSP deployment, success requires exact stream registration and recent non-zero FPS for every expected source. `STREAM_ADD_SUCCESS` and `stream-count` only prove REST registration; they do not prove DeepStream is processing frames. If the stream set is correct but `Active sources : 0`, FPS is zero, or Kafka offsets do not grow after bounded checks, return to `configure-cameras.md` and use the generic static RTSP `[source-list]` fallback with the same `sensor_id=rtsp://...` mappings, then rerun this verification.

```bash
cd "${RTCV3D_APP}"
mkdir -p generated/run-state
EXPECTED_IDS="$(find generated/camInfo -maxdepth 1 -type f -name '*.yml' -printf '%f
' | sed 's/\.yml$//' | LC_ALL=C sort | paste -sd, -)"
./scripts/add-streams.sh --list > generated/run-state/stream-info-verify.txt
EXPECTED_IDS="${EXPECTED_IDS}" python3 - <<'PY'
import os, re
expected = sorted([x for x in os.environ['EXPECTED_IDS'].split(',') if x])
text = open('generated/run-state/stream-info-verify.txt', encoding='utf-8').read()
count_match = re.search(r'stream-count:\s*(\d+)', text)
count = int(count_match.group(1)) if count_match else -1
ids = sorted(re.findall(r'camera_id=([^\s]+)', text))
if count != len(expected):
    raise SystemExit(f"ERROR: stream-count {count} != expected {len(expected)}")
if ids != expected:
    raise SystemExit(f"ERROR: registered IDs {ids} != expected {expected}")
if len(ids) != len(set(ids)):
    raise SystemExit(f"ERROR: duplicate registered IDs: {ids}")
print('registered stream set is exact')
PY
```

Check recent FPS from logs and require every expected camera to have a fresh positive FPS value. Fail closed if the `**PERF` format cannot be mapped to the registered camera set.

```bash
cd "${RTCV3D_APP}"
mkdir -p generated/run-state
docker logs --since 90s vss-rtvi-cv-mv3dt > generated/run-state/fps.log 2>&1
EXPECTED_IDS="${EXPECTED_IDS}" python3 - <<'PY'
import os, re
expected = sorted([x for x in os.environ['EXPECTED_IDS'].split(',') if x])
if not expected:
    raise SystemExit('ERROR: EXPECTED_IDS is empty')
stream_text = open('generated/run-state/stream-info-verify.txt', encoding='utf-8').read()
pairs = []
for src, cam in re.findall(r'source_id[=: ]+(\d+).*?camera_id[=: ]+([^\s,;]+)', stream_text):
    pairs.append((int(src), cam))
for cam, src in re.findall(r'camera_id[=: ]+([^\s,;]+).*?source_id[=: ]+(\d+)', stream_text):
    pairs.append((int(src), cam))
pairs = sorted(set(pairs))
if len(pairs) != len(expected):
    raise SystemExit(f'ERROR: stream-info source count {len(pairs)} != expected {len(expected)}')
if sorted(cam for _, cam in pairs) != expected:
    raise SystemExit(f'ERROR: stream-info cameras do not match expected: {pairs} vs {expected}')
if len({src for src, _ in pairs}) != len(pairs) or len({cam for _, cam in pairs}) != len(pairs):
    raise SystemExit(f'ERROR: duplicate source/camera entries in stream-info: {pairs}')
source_to_camera = dict(pairs)
ordered_cameras = [cam for _, cam in sorted(pairs)]
log_lines = open('generated/run-state/fps.log', encoding='utf-8', errors='replace').read().splitlines()
if not any(line.strip() for line in log_lines):
    raise SystemExit('ERROR: no recent perception logs available for FPS check')

def parse_keyed(lines):
    fps = {}
    for line in lines:
        for cam, val in re.findall(r'(?:camera_id|camera|sensorId|sensor_id)[=: ]+([^\s,;]+).*?(?:FPS|fps)[=: ]+([0-9]+(?:\.[0-9]+)?)', line):
            if cam in expected:
                fps[cam] = float(val)
        for src, val in re.findall(r'source_id[=: ]+(\d+).*?(?:FPS|fps)[=: ]+([0-9]+(?:\.[0-9]+)?)', line):
            cam = source_to_camera.get(int(src))
            if cam:
                fps[cam] = float(val)
        for val, src in re.findall(r'([0-9]+(?:\.[0-9]+)?)\s*\([0-9]+(?:\.[0-9]+)?\).*?source_id\s*:\s*(\d+)', line):
            cam = source_to_camera.get(int(src))
            if cam:
                fps[cam] = float(val)
    return fps

def parse_perf_block(start_idx):
    block = []
    for line in log_lines[start_idx:start_idx + 2 * len(expected) + 12]:
        if block and 'Active sources :' in line:
            break
        block.append(line)
    fps = parse_keyed(block)
    if sorted(fps) == expected:
        return fps

    # Non-dynamic file-source format can print all FPS numbers on the **PERF row.
    perf_rows = [line for line in block if '**PERF' in line or 'PERF(' in line]
    for row in reversed(perf_rows):
        if 'FPS ' in row and '(Avg)' in row:
            continue
        vals = [float(x) for x in re.findall(r'(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?', row)]
        n = len(ordered_cameras)
        if len(vals) == n:
            return dict(zip(ordered_cameras, vals))
        if len(vals) == 2 * n and '(' in row and ')' in row:
            return dict(zip(ordered_cameras, vals[0::2]))
    return {}

fps = parse_keyed(log_lines)
if sorted(fps) != expected:
    perf_starts = [i for i, line in enumerate(log_lines) if '**PERF' in line or 'PERF(' in line]
    for idx in reversed(perf_starts):
        fps = parse_perf_block(idx)
        if sorted(fps) == expected:
            break

missing = sorted(set(expected) - set(fps))
extras = sorted(set(fps) - set(expected))
zeros = {cam: val for cam, val in fps.items() if val <= 0.0}
if missing or extras or zeros:
    raise SystemExit(f'ERROR: FPS check failed missing={missing} extras={extras} non_positive={zeros}')
print('recent non-zero FPS by camera:', ', '.join(f'{cam}={fps[cam]:.3f}' for cam in expected))
PY
```

## Saved Perception Video

Before a saved-output run, `deploy-rtvi-cv-3d-stack.md` records `RUN_START_EPOCH`. Afterward, prove the file belongs to this run:

```bash
cd "${RTCV3D_APP}"
RUN_ID="${RUN_ID:-$(cat generated/run-state/run-id)}"
RUN_START_EPOCH="${RUN_START_EPOCH:-$(cat generated/run-state/run-start-epoch)}"
GRID="${RTCV3D_APP}/video-output/grid-view.mkv"
test -s "${GRID}" || { echo "ERROR: grid video missing or empty: ${GRID}"; exit 1; }
MODIFIED="$(stat -c %Y "${GRID}")"
test "${MODIFIED}" -ge "${RUN_START_EPOCH}" || { echo "ERROR: grid video predates current run: ${GRID}"; exit 1; }
GRID_PROBE="${RTCV3D_APP}/generated/run-state/grid-ffprobe-${RUN_ID}.txt"
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "${GRID}" > "${GRID_PROBE}"
cat "${GRID_PROBE}"
```

For live RTSP, stop the stack or perception container when done; the `.mkv` may need remuxing for seek metadata.

## BEV Visualization And Saved BEV

BEV visualization is a separate host-side Kafka consumer. Display mode should show two windows when BEV assets are available: `DeepStreamTest5App` for the camera-grid OSD and `Bird-Eye View of Multi-View 3D Tracking` for fused BEV. Saved-output mode should produce saved fused BEV by default, alongside `video-output/grid-view.mkv`, when `BEV_DATASET_PATH` contains `map.png` and `transforms.yml`.

The deploy flow records the BEV consumer group and Kafka assignment evidence under `generated/run-state/`. Do not start finite file input before that assignment is confirmed.

For live display runs, tell the user the OSD window closes with perception when file input reaches EOS, but the separate BEV window may remain open; press `q` in the BEV window to close it. For agent-managed or unattended closeout, stop only the tracked current-run BEV PID using the safe identity validation from `references/teardown.md`; never use broad process-kill patterns.

Expected saved output:

```text
${RTCV3D_APP}/bev-output/fused_trajectory_video_<stamp>.mp4   # default fused BEV
${RTCV3D_APP}/bev-output/trajectory_video_<stamp>.mp4         # raw/per-camera BEV when requested
```

Finalize the current saved-BEV recorder before parsing its log. For file input, run this after EOS and Kafka offset checks; the offline recorder normally writes `Video saved` only after `BEV_EXIT_ON_IDLE` seconds without messages. For saved RTSP output, first end the requested capture window by removing/stopping streams or stopping perception, then run the same finalization before verifying the artifact.

```bash
cd "${RTCV3D_APP}"
mkdir -p generated/run-state
RUN_STATE_DIR="${RTCV3D_APP}/generated/run-state"
PID_FILE="${RUN_STATE_DIR}/bev-visualizer.pid"
BEV_EXIT_ON_IDLE="${BEV_EXIT_ON_IDLE:-15}"
BEV_FINALIZE_MARGIN="${BEV_FINALIZE_MARGIN:-10}"

safe_stop_bev_recorder() {
  pid="$1"
  current_cwd="$(readlink -f /proc/"${pid}"/cwd 2>/dev/null || true)"
  expected_cwd="$(cat "${RUN_STATE_DIR}/bev-visualizer.cwd" 2>/dev/null || true)"
  current_cmd="$(tr '\0' ' ' < /proc/"${pid}"/cmdline 2>/dev/null || true)"
  current_start="$(awk '{print $22}' /proc/"${pid}"/stat 2>/dev/null || true)"
  expected_start="$(cat "${RUN_STATE_DIR}/bev-visualizer.start_ticks" 2>/dev/null || true)"

  case "${current_cmd}" in
    *kafka_bev_visualizer.py*|*kafka_fused_bev_visualizer.py*|*bev-visualizer.sh*) cmd_ok=1 ;;
    *) cmd_ok=0 ;;
  esac
  cwd_ok=0
  if [ -n "${current_cwd}" ] && [ "${current_cwd}" = "${RTCV3D_APP}" ]; then cwd_ok=1; fi
  if [ -n "${expected_cwd}" ] && [ "${current_cwd}" = "${expected_cwd}" ]; then cwd_ok=1; fi
  start_ok=1
  if [ -n "${expected_start}" ] && [ -n "${current_start}" ] && [ "${current_start}" != "${expected_start}" ]; then start_ok=0; fi

  if [ "${cmd_ok}" = 1 ] && [ "${cwd_ok}" = 1 ] && [ "${start_ok}" = 1 ]; then
    kill -TERM "${pid}" 2>/dev/null || true
  else
    echo "ERROR: refusing to stop BEV PID ${pid}; identity check failed (cmd_ok=${cmd_ok} cwd_ok=${cwd_ok} start_ok=${start_ok})" >&2
    return 1
  fi
}

if [ -f "${PID_FILE}" ]; then
  pid="$(cat "${PID_FILE}")"
  if ! printf '%s' "${pid}" | grep -Eq '^[0-9]+$'; then
    echo "ERROR: invalid BEV recorder PID: ${pid}" >&2
    exit 1
  fi
  if kill -0 "${pid}" 2>/dev/null; then
    deadline=$((SECONDS + BEV_EXIT_ON_IDLE + BEV_FINALIZE_MARGIN))
    while kill -0 "${pid}" 2>/dev/null && [ "${SECONDS}" -lt "${deadline}" ]; do
      sleep 1
    done
    if kill -0 "${pid}" 2>/dev/null; then
      echo "BEV recorder still active after idle wait; sending safe SIGTERM"
      safe_stop_bev_recorder "${pid}"
      deadline=$((SECONDS + 20))
      while kill -0 "${pid}" 2>/dev/null && [ "${SECONDS}" -lt "${deadline}" ]; do
        sleep 1
      done
    fi
    if kill -0 "${pid}" 2>/dev/null; then
      echo "ERROR: BEV recorder did not exit/finalize: pid=${pid}" >&2
      exit 1
    fi
  fi
fi
```

Select the artifact from the current recorder log, not by globbing the newest historical file:

```bash
cd "${RTCV3D_APP}"
RUN_ID="${RUN_ID:-$(cat generated/run-state/run-id)}"
BEV_LOG="$(cat generated/run-state/bev-visualizer.log)"
test -f "${BEV_LOG}" || { echo "ERROR: current BEV log missing: ${BEV_LOG}"; exit 1; }
BEV_LOG="${BEV_LOG}" python3 - <<'PY' > generated/run-state/bev-artifact.txt
import os, re
log_path = os.environ['BEV_LOG']
text = open(log_path, encoding='utf-8', errors='replace').read()
m = re.search(r'Video saved:\s*(.*?)\s*\((\d+) frames\)', text)
if not m:
    raise SystemExit('ERROR: current BEV log does not contain Video saved with frame count')
path, frames = m.group(1), int(m.group(2))
if frames <= 0:
    raise SystemExit(f'ERROR: BEV frame count is not positive: {frames}')
print(path)
print(frames)
PY
BEV_VIDEO="$(sed -n '1p' generated/run-state/bev-artifact.txt)"
BEV_FRAMES="$(sed -n '2p' generated/run-state/bev-artifact.txt)"
test -s "${BEV_VIDEO}" || { echo "ERROR: BEV video missing or empty: ${BEV_VIDEO}"; exit 1; }
MODIFIED="$(stat -c %Y "${BEV_VIDEO}")"
RUN_START_EPOCH="${RUN_START_EPOCH:-$(cat generated/run-state/run-start-epoch)}"
test "${MODIFIED}" -ge "${RUN_START_EPOCH}" || { echo "ERROR: BEV video predates current run: ${BEV_VIDEO}"; exit 1; }
BEV_PROBE="${RTCV3D_APP}/generated/run-state/bev-ffprobe-${RUN_ID}.txt"
ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "${BEV_VIDEO}" > "${BEV_PROBE}"
cat "${BEV_PROBE}"
echo "BEV frames=${BEV_FRAMES} path=${BEV_VIDEO}"
```

If BEV assets are missing, report that saved BEV cannot be produced until `map.png` and `transforms.yml` are provided; do not claim a BEV artifact from a reduced output run.

## Success Report

Report these concrete items:

- Compose file used: `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml`.
- Runtime images from `docker compose config --images`.
- Broker mode: bundled profiles or explicit external endpoints.
- Service states: perception state/exit code, bev-fusion health, selected broker health, and topic-init status when bundled.
- Input mode and filtered camera count.
- For RTSP: exact registered stream set, no duplicates, every expected source recent non-zero FPS, and growing `mdx-raw`/`mdx-bev` offsets.
- For file input: `Exited (0)` plus `App run successful`, and `mdx-raw`/`mdx-bev` offsets greater than pre-run baselines.
- OSD mode and BEV mode: for live display, report both expected windows and BEV closeout (`q` or safe tracked-PID stop); for saved output, report exact current-run artifact paths including `video-output/grid-view.mkv` and saved BEV output with ffprobe evidence.

Do not report VST URLs or warehouse overlay URLs for this standalone skill.
