# Verify And Visualize Standalone RT-CV-3D

Load this reference after compose launch, when checking health, Kafka data flow, OSD, saved perception video, or BEV visualization.

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

Use bounded log checks; do not wait forever:

```bash
docker logs --tail 200 vss-rtvi-cv-mv3dt 2>&1 | tail -80

deadline=$((SECONDS + 120))
until docker logs vss-rtvi-cv-mv3dt 2>&1 | grep -q 'ds-ready: YES'; do
  [ "${SECONDS}" -lt "${deadline}" ] || { echo "ERROR: ds-ready: YES not observed before timeout"; exit 1; }
  sleep 2
done
echo 'perception reached ds-ready: YES'

PERCEPTION_STATUS="$(docker inspect --format '{{.State.Status}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
PERCEPTION_EXIT="$(docker inspect --format '{{.State.ExitCode}}' vss-rtvi-cv-mv3dt 2>/dev/null || true)"
if [ "${INPUT_MODE:-}" = file ] && [ "${PERCEPTION_STATUS}" = exited ] && [ "${PERCEPTION_EXIT}" = 0 ]    && docker logs vss-rtvi-cv-mv3dt 2>&1 | grep -q 'App run successful'; then
  echo 'perception completed finite file input successfully'
fi
```

For `INPUT_MODE=stream`, 0 FPS before RTSP registration is normal. After streams are registered, the perception container should remain running until stopped; an unexpected exit is a failure.

For `INPUT_MODE=file`, clips start immediately and the perception container exits when all files finish. `Exited (0)` plus `App run successful` is expected. Verify Kafka offsets and saved artifacts instead of restarting perception.

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

For live RTSP deployment, success requires exact stream registration and recent non-zero FPS for every expected source.

```bash
cd "${RTCV3D_APP}"
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
docker logs --since 90s vss-rtvi-cv-mv3dt 2>&1 > generated/run-state/fps.log
EXPECTED_IDS="${EXPECTED_IDS}" python3 - <<'PY'
import os, re, sys
expected = sorted([x for x in os.environ['EXPECTED_IDS'].split(',') if x])
if not expected:
    raise SystemExit('ERROR: EXPECTED_IDS is empty')
stream_text = open('generated/run-state/stream-info-verify.txt', encoding='utf-8').read()
pairs = [(int(src), cam) for src, cam in re.findall(r'source_id=(\d+)\s+camera_id=([^\s]+)', stream_text)]
if len(pairs) != len(expected):
    raise SystemExit(f'ERROR: stream-info source count {len(pairs)} != expected {len(expected)}')
if sorted(cam for _, cam in pairs) != expected:
    raise SystemExit(f'ERROR: stream-info cameras do not match expected: {pairs} vs {expected}')
if len({src for src, _ in pairs}) != len(pairs) or len({cam for _, cam in pairs}) != len(pairs):
    raise SystemExit(f'ERROR: duplicate source/camera entries in stream-info: {pairs}')
ordered_cameras = [cam for _, cam in sorted(pairs)]
log_text = open('generated/run-state/fps.log', encoding='utf-8', errors='replace').read()
if not log_text.strip():
    raise SystemExit('ERROR: no recent perception logs available for FPS check')

fps = {}
for line in log_text.splitlines():
    # Structured/keyed variants, when present.
    for cam, val in re.findall(r'(?:camera_id|camera|sensorId|sensor_id)[=: ]+([^\s,;]+).*?(?:FPS|fps)[=: ]+([0-9]+(?:\.[0-9]+)?)', line):
        if cam in expected:
            fps[cam] = float(val)
    for src, val in re.findall(r'source_id[=: ]+(\d+).*?(?:FPS|fps)[=: ]+([0-9]+(?:\.[0-9]+)?)', line):
        src_i = int(src)
        for known_src, cam in pairs:
            if known_src == src_i:
                fps[cam] = float(val)

if sorted(fps) != expected:
    perf_lines = [line for line in log_text.splitlines() if '**PERF' in line]
    if not perf_lines:
        raise SystemExit('ERROR: no recent **PERF block found and no keyed FPS lines found')
    latest = perf_lines[-1]
    values = [float(x) for x in re.findall(r'(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?', latest)]
    n = len(ordered_cameras)
    if len(values) == n:
        mapped = values
    elif len(values) == 2 * n and '(' in latest and ')' in latest:
        mapped = values[0::2]
    else:
        raise SystemExit(f'ERROR: ambiguous **PERF format for {n} cameras: {latest!r}')
    fps = dict(zip(ordered_cameras, mapped))

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

BEV visualization is a separate host-side Kafka consumer. Saved-output mode should produce saved fused BEV by default, alongside `video-output/grid-view.mkv`, when `BEV_DATASET_PATH` contains `map.png` and `transforms.yml`.

The deploy flow records the BEV consumer group and Kafka assignment evidence under `generated/run-state/`. Do not start finite file input before that assignment is confirmed.

Expected saved output:

```text
${RTCV3D_APP}/bev-output/fused_trajectory_video_<stamp>.mp4   # default fused BEV
${RTCV3D_APP}/bev-output/trajectory_video_<stamp>.mp4         # raw/per-camera BEV when requested
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

If BEV assets are missing, say saved BEV cannot be produced yet and ask for the map/transforms source before continuing with reduced output.

## Success Report

Report these concrete items:

- Compose file used: `services/rtvi/rt-cv-3d/rt-cv-mv3dt/docker/compose.yml`.
- Runtime images from `docker compose config --images`.
- Broker mode: bundled profiles or explicit external endpoints.
- Service states: perception state/exit code, bev-fusion health, selected broker health, and topic-init status when bundled.
- Input mode and filtered camera count.
- For RTSP: exact registered stream set, no duplicates, every expected source recent non-zero FPS, and growing `mdx-raw`/`mdx-bev` offsets.
- For file input: `Exited (0)` plus `App run successful`, and `mdx-raw`/`mdx-bev` offsets greater than pre-run baselines.
- OSD mode or exact current-run artifact paths, including `video-output/grid-view.mkv` and saved BEV output when selected/defaulted, with ffprobe evidence.

Do not report VST URLs or warehouse overlay URLs for this standalone skill.
