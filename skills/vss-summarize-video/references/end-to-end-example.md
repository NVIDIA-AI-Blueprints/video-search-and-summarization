## End-to-end example

Use these implementations with the ordered stages in `SKILL.md`.

- [Resolve endpoints](#resolve-endpoints)
- [Probe readiness](#probe-readiness)
- [Prepare the video through VIOS](#prepare-the-video-through-vios)
- [Submit one LVS request](#submit-one-lvs-request)
- [Run an approved VLM fallback](#run-an-approved-vlm-fallback)

Do not run a direct VLM fallback when LVS is ready, and do not rerun the LVS
POST with broader events when the response is empty.

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

LVS_REQUEST=/tmp/vss-summarize-video-request.json
LVS_RESPONSE=/tmp/vss-summarize-video-response.json
```

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
  STREAM_ID=$(jq -er '.streamId' /tmp/vios-upload.json)
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

### Submit one LVS request

Assume video preparation established `$CLIP` and `$DURATION`. Resolve the model,
then issue exactly one summarize request.

```bash
# HITL (required, before the curl): collect the Stage 3 scenario/events and
# wait for the user's reply. Substitute their values (or the `defaults` opt-in)
# into $SCENARIO, $EVENTS_JSON, and $OBJECTS_JSON below. Do not run the curl
# without that reply.
SCENARIO='warehouse monitoring'            # or whatever the user gave
EVENTS_JSON='["notable activity"]'         # jq-compatible JSON array
OBJECTS_JSON=''                            # '' to omit, else '["cars","trucks"]'

# --- Model discovery ---
# Docker: LVS GET /models (authoritative for summarize).
# Kubernetes: LVS /models is not on Ingress — use Exact RT-VLM /v1/models
# (or VLM_NAME when set).
if [ "${DEPLOYMENT_KIND:-docker}" = "kubernetes" ]; then
  LVS_MODEL=$(curl -fsS "$VLM/v1/models" | jq -er --arg preferred "${VLM_NAME:-}" '
    [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
    | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
      elif ($ids | length) == 1 then $ids[0]
      else empty end
  ') || { echo "Set VLM_NAME to an advertised RT-VLM model id"; return 1 2>/dev/null || exit 1; }
else
  LVS_OPENAPI=/tmp/vss-lvs-openapi.json
  curl -fsS "$VIDEO_SUMMARIZATION_URL/openapi.json" > "$LVS_OPENAPI"
  jq -e '.paths["/v1/summarize"].post.requestBody.content["application/json"].schema' \
    "$LVS_OPENAPI" >/dev/null
  LVS_MODEL=$(curl -fsS "$VIDEO_SUMMARIZATION_URL/models" | jq -er --arg preferred "${VLM_NAME:-}" '
    [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
    | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
      elif ($ids | length) == 1 then $ids[0]
      else empty end
  ') || { echo "Set VLM_NAME to an advertised model id"; return 1 2>/dev/null || exit 1; }
fi

jq -n --arg url "$CLIP" \
      --arg model "$LVS_MODEL" \
      --arg scenario "$SCENARIO" \
      --argjson events "$EVENTS_JSON" \
      --argjson objects "${OBJECTS_JSON:-null}" '{
    url: $url,
    model: $model,
    scenario: $scenario,
    events: $events,
    chunk_duration: 10,
    seed: 1
  } + (if $objects == null then {} else {objects_of_interest: $objects} end)' \
  > "$LVS_REQUEST"

# Exactly one summarize POST. Save the raw body for parsing and diagnosis.
# Keep a long timeout — LVS chunk→VLM→aggregate can exceed 50s; Ingress allows 600s.
LVS_HTTP_CODE=$(curl -sS --max-time 300 -o "$LVS_RESPONSE" -w '%{http_code}' \
  -X POST "$VIDEO_SUMMARIZATION_URL/v1/summarize" \
  -H "Content-Type: application/json" \
  --data-binary "@$LVS_REQUEST")
LVS_CURL_EXIT=$?

if [ "$LVS_CURL_EXIT" -ne 0 ]; then
  echo "video summarization request failed (curl exit $LVS_CURL_EXIT, HTTP $LVS_HTTP_CODE)"
elif [[ "$LVS_HTTP_CODE" != 2* ]]; then
  echo "video summarization request failed (HTTP $LVS_HTTP_CODE)"
  jq . "$LVS_RESPONSE" 2>/dev/null || cat "$LVS_RESPONSE"
elif ! jq -e '{
         usage: (.usage // {}),
         result: (.choices[0].message.content | fromjson | {video_summary, events})
       }' "$LVS_RESPONSE"; then
  echo "video summarization returned no parseable choices[0].message.content"
  jq . "$LVS_RESPONSE" 2>/dev/null || cat "$LVS_RESPONSE"
fi
```

For any failure, inspect `$LVS_RESPONSE` and service logs. Never repeat the POST
to obtain a different view of the response.

If both result fields are empty, use `usage.total_chunks_processed` from the
same parsed response to report whether LVS processed any media. Do not infer
"no detections" when that value is zero or missing.

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
