## End-to-end example

Use these implementations with the ordered stages in `SKILL.md`.

- [Resolve endpoints](#resolve-endpoints)
- [Probe readiness](#probe-readiness)
- [Prepare the video through VIOS](#prepare-the-video-through-vios)
- [Submit one summarize job](#submit-one-summarize-job)
- [Run an approved VLM fallback](#run-an-approved-vlm-fallback)

Do not run a direct VLM fallback when LVS is ready, and do not rerun the
summarize job with broader events when the result is empty.

### Resolve endpoints

Run once before any probe. Docker keeps host ports; Kubernetes uses
`VSS_PUBLIC_URL` (LVS client base = origin, **no** `/v1` suffix).

```bash
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi

if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  DEPLOYMENT_KIND="kubernetes"
  VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
  # Force public origin — ignore leftover Docker LVS_BACKEND_URL / VLM_* env.
  LVS_BACKEND_URL="${VSS_PUBLIC_URL}"
  VIDEO_SUMMARIZATION_URL="${LVS_BACKEND_URL}"
  VST_API_BASE="${VSS_PUBLIC_URL}/vst/api/v1"
  VLM="${VSS_PUBLIC_URL}"
else
  DEPLOYMENT_KIND="docker"
  LVS_BACKEND_URL="${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}"
  VIDEO_SUMMARIZATION_URL="${LVS_BACKEND_URL}"
  VST_API_BASE="http://${HOST_IP:-localhost}:30888/vst/api/v1"
  VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
  VLM="${VLM%/v1}"
fi

```

Readiness and the VIOS preparation below use these. The summarize request
itself takes no endpoint: `vss configure` recorded it (SKILL.md prerequisites).

### Probe readiness

```bash
vlm_code=$(curl -s -o /dev/null -w '%{http_code}' \
  --connect-timeout 3 --max-time 10 "$VLM/v1/models")
[ "$vlm_code" = "200" ] || echo "VLM not reachable (HTTP $vlm_code)"

# Readiness = HTTP 200 on /v1/ready. Body may be empty — do not inspect it.
# Retry on 503 (warmup) for up to ~30s before concluding the service is unavailable.
video_sum_code=000
for i in $(seq 1 10); do
  video_sum_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 10 "$VIDEO_SUMMARIZATION_URL/v1/ready")
  case "$video_sum_code" in 200) break ;; 503) sleep 3 ;; *) break ;; esac
done

if [ "$video_sum_code" != "200" ]; then
  cat <<EOF
video summarization service not ready (HTTP $video_sum_code).

Decision point:
- Interactive run: ask the user whether to deploy the VSS lvs profile with /vss-deploy-profile -p lvs.
- If deployment is approved or was pre-authorized in the original task, invoke that deploy skill, then rerun the readiness probe and continue with the LVS request below.
- If lower-quality VLM fallback is explicitly approved or was pre-authorized in the original task, follow the SKILL.md Stages 3-4 VLM fallback.
- Non-interactive / Harbor run: if neither deployment nor fallback was pre-authorized in the original task, report BLOCKED because the LVS service is unavailable and no user decision is available. Do not wait for input and do not silently fall back to VLM.
EOF
  # This is not a shell failure. The next action requires user approval or prior
  # authorization, and the example intentionally does not run an automatic VLM
  # fallback. In Harbor/non-interactive runs, report BLOCKED if neither path was
  # pre-authorized by the original task.
  return 0 2>/dev/null || exit 0
fi
```

### Prepare the video through VIOS

Reuse the requested recording when present. Otherwise replace `SOURCE_FILE`
with the exact requested local file and upload it directly. Preserve the
returned stream ID, full timeline, and fresh MP4 URL for later stages.

```bash
VIOS_API="${VST_API_BASE:-http://${HOST_IP:-localhost}:30888/vst/api/v1}"
SOURCE_FILE=/path/to/video.mp4
FILENAME=$(basename "$SOURCE_FILE")
UPLOAD_TIMESTAMP=2025-01-01T00:00:00.000Z
FILE_SIZE=$(stat -c%s "$SOURCE_FILE")

SENSOR_ID=$(curl -fsS "$VIOS_API/sensor/list" | jq -er \
  --arg filename "$FILENAME" --arg stem "${FILENAME%.*}" \
  '[.[] | select(.name == $filename or .name == $stem)][0].sensorId // empty' \
  || true)
if [ -n "$SENSOR_ID" ]; then
  STREAM_ID=$(curl -fsS "$VIOS_API/sensor/$SENSOR_ID/streams" | jq -er \
    '([.[] | select(.isMain == true)][0].streamId // .[0].streamId)')
else
  curl -fsS -X PUT \
    "$VIOS_API/storage/file/$FILENAME?timestamp=$UPLOAD_TIMESTAMP" \
    -H "Content-Type: application/octet-stream" \
    -H "Content-Length: $FILE_SIZE" \
    --upload-file "$SOURCE_FILE" > /tmp/vios-upload.json
  # The upload answers with both ids. Read the sensor one rather than assuming
  # the stream id equals it: it does for an uploaded file today, but the record
  # is keyed by sensor, and a sensor carrying several streams breaks that.
  STREAM_ID=$(jq -er '.streamId' /tmp/vios-upload.json)
  SENSOR_ID=$(jq -er '.sensorId' /tmp/vios-upload.json)
  # VIOS anchors an uploaded file's timeline to this, so it is the media start.
  UPLOADED_AT="$UPLOAD_TIMESTAMP"
fi

for _ in $(seq 1 20); do
  curl -fsS "$VIOS_API/storage/$STREAM_ID/timelines" \
    > /tmp/vios-timeline.json
  jq -e 'length > 0' /tmp/vios-timeline.json >/dev/null && break
  sleep 3
done
START_TIME=$(jq -er 'map(.startTime) | min' /tmp/vios-timeline.json)
END_TIME=$(jq -er 'map(.endTime) | max' /tmp/vios-timeline.json)
curl -fsSG "$VIOS_API/storage/file/$STREAM_ID/url" \
  --data-urlencode "startTime=$START_TIME" \
  --data-urlencode "endTime=$END_TIME" \
  --data-urlencode "container=mp4" \
  --data-urlencode "disableAudio=true" > /tmp/vios-clip-url.json
CLIP=$(jq -er '.videoUrl | sub("^http://http://"; "http://")' \
  /tmp/vios-clip-url.json)
```

When LVS is selected, verify the URL is fetchable without writing the video
body into tool output.

**Docker** — probe from inside `vss-lvs`:

```bash
if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
  docker exec vss-lvs python3 -c '
import sys
import urllib.request
request = urllib.request.Request(sys.argv[1], headers={"Range": "bytes=0-0"})
with urllib.request.urlopen(request, timeout=30) as response:
    response.read(1)
    print(response.status)
' "$CLIP"
fi
```

**Kubernetes** — no `docker exec` / `kubectl exec`. Probe from the agent host
with a bounded Range GET. The URL passed to LVS must remain the minted VIOS
URL (deploy should set `VST_EXTERNAL_URL` to the public origin so the LVS pod
can fetch it):

```bash
if [ "${DEPLOYMENT_KIND:-docker}" = "kubernetes" ]; then
  curl -fsS --connect-timeout 5 --max-time 60 --range 0-0 -o /dev/null "$CLIP" \
    || { echo "CLIP not reachable from agent host: $CLIP"; return 1 2>/dev/null || exit 1; }
fi
```

### Submit one summarize job

Assume video preparation established `$CLIP`, and the SKILL.md prerequisites
resolved `${VSS[@]}` and ran `vss configure`. One `vss summarize run` is exactly
one `POST /v1/summarize`; the CLI resolves the LVS endpoint and the default
model from the recorded deployment, so no model discovery is needed here and no
payload is built by hand.

```bash
# HITL (required, before the run): collect the Stage 3 scenario/events and wait
# for the user's reply. Substitute their values (or the `defaults` opt-in) into
# $SCENARIO and the arrays below. Do not run the command without that reply.
SCENARIO='warehouse monitoring'            # or whatever the user gave
EVENTS=("notable activity")                # one array element per event
OBJECTS=()                                 # empty to omit

# The record's identity is the sensor, never the stream: `list --sensor-id` and
# time-windowed recall key on the sensor, so a summary filed under a stream id
# is one nothing can find again. Both preparation paths above resolved one.
VIDEO_ID="$SENSOR_ID"
[ -n "$VIDEO_ID" ] || {
  echo "no VIOS sensor id resolved; do not persist under a stream id"
  return 1 2>/dev/null || exit 1
}
# The media's absolute start: the timestamp this run anchored the upload to, or
# for a recording that was already in VIOS, the start VIOS reports for it --
# never a constant standing in for media someone else uploaded. Without
# --creation-time the event times are clip offsets, which unified memory cannot
# store as instants (exit 6, summary intact).
CREATION_TIME="${UPLOADED_AT:-$START_TIME}"
[ -n "$CREATION_TIME" ] || {
  echo "no VIOS timeline start resolved; event times would not be instants"
  return 1 2>/dev/null || exit 1
}
SUMMARIZE_OUT=/tmp/vss-summarize-video-run.json

SUMMARIZE_COMMAND=(
  "${VSS[@]}" summarize run
  --url "$CLIP"
  --video-id "$VIDEO_ID"
  --scenario "$SCENARIO"
  --creation-time "$CREATION_TIME"
  --chunk-duration 10
  --seed 1
)
for event in "${EVENTS[@]}"; do
  SUMMARIZE_COMMAND+=(--event "$event")
done
for object in "${OBJECTS[@]}"; do
  SUMMARIZE_COMMAND+=(--object-of-interest "$object")
done

# Exactly one run, ever. Keep stdout: its final line is the completion marker.
if "${SUMMARIZE_COMMAND[@]}" > "$SUMMARIZE_OUT"; then
  SUMMARIZE_EXIT=0
else
  SUMMARIZE_EXIT=$?
fi

# A call refused before a job is minted -- a rejected flag, no recorded
# deployment -- writes no marker, so the stderr diagnostic is the whole result
# and parsing stdout would replace it with a jq error. Test emptiness, not the
# exit code: an exit 2 from LVS rejecting the request does carry a marker, and
# the failure branch below reports it.
if [ ! -s "$SUMMARIZE_OUT" ]; then
  echo "no job was created (exit $SUMMARIZE_EXIT): fix the call or run vss configure, then run once"
  return 1 2>/dev/null || exit 1
fi
SUMMARIZE_RESULT=$(sed -n '1p' "$SUMMARIZE_OUT")
COMPLETION_MARKER=$(tail -1 "$SUMMARIZE_OUT")
JOB_ID=$(printf '%s\n' "$COMPLETION_MARKER" | jq -er '.job_id')
echo "exit=$SUMMARIZE_EXIT job=$JOB_ID"

# Only exits 0 and 6 carry a summary. Every other marker reports status,
# record, and error in its place, so it is the whole result: print it and stop
# rather than parsing a summary that is not there.
case "$SUMMARIZE_EXIT" in
  0) ;;
  6) echo "summary produced; an ES or Markdown write failed — present the result and both memory outcomes" ;;
  7)
    printf '%s\n' "$COMPLETION_MARKER" | jq -e '{job_id, status, persisted}'
    echo "timed out — reconcile once: ${VSS[*]} summarize get --job-id $JOB_ID"
    return 1 2>/dev/null || exit 1
    ;;
  *)
    printf '%s\n' "$COMPLETION_MARKER" | jq -e '{job_id, status, persisted}'
    echo "summarize failed (exit $SUMMARIZE_EXIT, job $JOB_ID)"
    echo "the marker means it was submitted: report this job, do not resubmit it"
    return 1 2>/dev/null || exit 1
    ;;
esac

# The LVS envelope is nested under .summary, otherwise unchanged.
printf '%s\n' "$SUMMARIZE_RESULT" | jq -e '{
  usage: (.summary.usage // {}),
  result: (.summary.choices[0].message.content | fromjson | {video_summary, events})
}'
```

The result and completion marker report separate memory outcomes, so nothing
needs to be read back:

```bash
# `.persist` is absent when static policy selected stdout-only execution.
printf '%s\n' "$SUMMARIZE_RESULT" | jq '{persist: (.persist // null), memory_note: (.memory_note // null)}'
printf '%s\n' "$COMPLETION_MARKER" | jq '{job_id, status, persisted, exit_hint}'
```

For any failure, inspect `$SUMMARIZE_OUT`, the stderr diagnostic, and service
logs. Never repeat the run to obtain a different view of the result — exits 6
and 7 mean the summarization already happened.

If both result fields are empty, use `summary.usage.total_chunks_processed` from
the same payload to report whether LVS processed any media. Do not infer "no
detections" when that value is zero or missing.

### Run an approved VLM fallback

Run this only after LVS remains unavailable and the user explicitly approves
the lower-quality fallback. `$CLIP` must be reachable from the VLM endpoint.

```bash
VLM_MODEL=$(curl -fsS "$VLM/v1/models" | jq -er --arg preferred "${VLM_NAME:-}" '
  [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
  | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
    elif ($ids | length) == 1 then $ids[0]
    else empty end
') || { echo "Set VLM_NAME to an advertised model id"; return 1 2>/dev/null || exit 1; }

PROMPT='Describe in detail what is happening in this video,
including all visible people, vehicles, equipment, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.'

curl -sS --max-time 300 -X POST "$VLM/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg model "$VLM_MODEL" --arg text "$PROMPT" --arg url "$CLIP" '{
    model: $model,
    temperature: 0.0,
    max_tokens: 1024,
    messages: [{role: "user", content: [
      {type: "text", text: $text},
      {type: "video_url", video_url: {url: $url}}
    ]}]
  }')" | jq -r '.choices[0].message.content'
```
