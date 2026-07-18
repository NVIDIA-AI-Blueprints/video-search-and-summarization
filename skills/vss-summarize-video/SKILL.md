---
name: vss-summarize-video
description: Use to summarize a recorded video via the LVS summarization microservice (HITL-gated). If LVS is unavailable, ask before using the lower-quality VLM fallback. Not for report generation or live RTSP captioning.
license: Apache-2.0
metadata:
  version: "3.2.1"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---
## Instructions

Follow the routing tables and step-by-step workflows below. Each section that ends in *workflow*, *quick start*, or *flow* is intended to be executed top-to-bottom. Detailed reference material lives in `references/`.

## Examples

Worked end-to-end examples are kept under `evals/` (each `*.json` manifest contains a runnable scenario) and inline in the per-workflow `curl` blocks below. Run a Tier-3 evaluation with `nv-base validate <this-skill-dir> --agent-eval` to replay them.

Call the video summarization microservice **directly** for normal summaries.
Call the VLM NIM only when the LVS service is unavailable and the user has
explicitly accepted the lower-quality fallback.
Always run `curl` commands yourself; never instruct the user to run them.

Primary video workflow query type: **"Summarize this video."** Direct video summarization API
and service-ops requests are handled by the reference-routed sections below.

## Purpose

Produce a single, polished narrative summary of one recorded video clip, with
timestamped events when the LVS microservice path is reachable.

**Do NOT use this skill for:**
- Live RTSP captioning — use `vss-deploy-dense-captioning`.
- Report generation, including incident or alert-window reports — use `vss-generate-video-report` Mode B.
- Semantic search across the archive — use `vss-search-archive`.

## Prerequisites

- VSS `lvs` profile running on `$HOST_IP` (port 38111). The
  `vss-deploy-profile` skill brings it up.
- Network reachability from the agent host to the LVS endpoint; clip URLs from
  VIOS must be fetchable by the LVS service. If the user explicitly approves
  fallback, the fallback VLM endpoint must also be reachable.
- `jq` and `curl` available on the agent host.

## Limitations

- Direct VLM fallback is an explicit user-approved degradation path only. It
  uses a single fixed prompt and cannot target scenario/events, so output
  quality is lower than the LVS path.
- Remote VLM endpoints generally cannot reach `localhost`/private clip URLs.
- One `POST /v1/summarize` per LVS request; no parallel hedging, retries,
  event broadening, or multi-pass summaries.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/v1/ready` returns 503 repeatedly | LVS service still warming up | Retry up to ~30 s as shown in *Stage 1*; if it never returns 200 the service may not be deployed |
| Empty `video_summary` and `events` | The requested events were not returned, or LVS stopped before processing media | Inspect the saved response's `usage.total_chunks_processed`: a positive value proves processing; zero or missing does not. Report the exact result; do not retry automatically |
| VLM returns `<think>` block | Cosmos reasoning mode | Strip everything up to `</think>` before rendering |
| Empty stdout from `curl /v1/ready` | Service legitimately returns 200 with empty body | Always check HTTP status with `-o /dev/null -w '%{http_code}'`, never inspect the body |

See [`references/video-summarization-debugging.md`](references/video-summarization-debugging.md) for deeper diagnostics.

## Reference Map

Use these references only when the user asks for the relevant detail, or when
the core workflow below needs deeper video summarization information:

- **video summarization API details**: [`references/video-summarization-api.md`](references/video-summarization-api.md) for
  `/v1/summarize`, `/summarize`, `/v1/generate_captions`,
  `/v1/stream_summarize`, health probes, `/models`, `/recommended_config`,
  `/metrics`, request fields, response shapes, and API gotchas.
- **video summarization service configuration and ops**:
  [`references/video-summarization-deployment.md`](references/video-summarization-deployment.md) for
  the VSS `lvs` profile, ports, required env vars, logs, status, dry-runs,
  teardown, model/backend swaps, Elasticsearch/Neo4j/ArangoDB backend
  selection, and service-level troubleshooting.
- **Extended video summarization ops references**:
  [`references/video-summarization-environment-variables.md`](references/video-summarization-environment-variables.md),
  [`references/video-summarization-debugging.md`](references/video-summarization-debugging.md), and
  `assets/video-summarization.env.example`.

Before constructing or issuing any live LVS API operation, load
`video-summarization-api.md` and follow its **Runtime OpenAPI Discovery**
procedure. Fetch `/openapi.json` from the same service instance and treat that
runtime document as authoritative; the inline examples and checked-in schema
summary are not substitutes for the deployed contract. The bootstrap OpenAPI
fetch and health probes are exempt. Load `video-summarization-deployment.md`
only for deployment, configuration, or service operations.

## Video Summarization API And Service Ops Requests

If the user asks to call or debug video summarization endpoints directly, answer from
[`references/video-summarization-api.md`](references/video-summarization-api.md) instead of running the
end-to-end video summarization workflow. Examples: list video summarization models, check
readiness, get recommended chunking config, inspect metrics, explain a 422
response, or build a `/v1/summarize` request body.

If the user asks to configure, deploy, restart, tear down, or troubleshoot the
video summarization service, prefer the `vss-deploy-profile` skill for full VSS profile
deployment and use [`references/video-summarization-deployment.md`](references/video-summarization-deployment.md)
for video summarization-specific service details.

## Routing

Decide purely from video summarization service availability (probed in
*Stage 1 → Availability checks* below). **Duration does not drive routing.**

| `/v1/ready` | Backend | Endpoint |
|---|---|---|
| HTTP 200 | LVS microservice with HITL | `POST ${LVS_BACKEND_URL}/v1/summarize` |
| Anything else | Ask to deploy LVS or ask before VLM / RT-VLM fallback | `POST ${VLM_BASE_URL}/v1/chat/completions` only after explicit user approval |

Fallback message when the LVS service is unreachable — copy verbatim above the summary:

> ⚠ **Note:** Input video `<name>` is `<N>`s long.
> The video summarization service is not deployed, so this summary was
> produced by the VLM alone with a generic default prompt. Deploy the
> `lvs` profile for higher-quality summaries with scenario/events
> targeting.

## Deployment prerequisite

The VSS **lvs** profile on `$HOST_IP` is the primary backend. If the
`/v1/ready` probe (see *Stage 1 → Availability checks*) returns anything
other than 200 after the warmup retries, ask the user:

> *"The VSS `lvs` profile isn't running on `$HOST_IP`. Shall I deploy it now using the `/vss-deploy-profile` skill with `-p lvs`? Reply `no` to stop here; I can use the lower-quality VLM-only fallback only if you explicitly ask for it."*

- **Yes** → hand off to `/vss-deploy-profile`, then re-probe and continue the recorded video summarization workflow.
- **No** → ask whether the user wants the lower-quality **VLM fallback**. Only proceed if they explicitly approve; prepend the Routing fallback note. Do not run scenario/events HITL.
- **Pre-authorized to deploy autonomously** (caller said so explicitly) → skip the confirmation and invoke `/vss-deploy-profile` directly.
- **Pre-authorized to use VLM fallback** ("skip lvs, just use the VLM") → go straight to the VLM fallback without prompting.
- **Non-interactive / Harbor run** → treat the original task text as the only
  possible approval source. If it did not pre-authorize deployment or fallback,
  report blocked because the LVS service is unavailable and no user decision is
  available. Do not wait for input and do not silently fall back to VLM.

---

## Recorded video summarization workflow

Complete these stages in order for a recorded-video summary:

- [ ] Select the summarization backend from service availability.
- [ ] Make the requested video available through VIOS and obtain its full
  timeline and a fresh temporary MP4 URL.
- [ ] When using LVS, verify that the `vss-lvs` container can fetch the MP4
  URL.
- [ ] If LVS was selected, collect the scenario, events, and optional objects
  of interest.
- [ ] Follow either the LVS stages or the explicitly approved VLM fallback,
  then submit one summarization request through the selected backend.
- [ ] Render the returned summary and events without changing their content.

### Stage 1 - Select the backend

**Endpoints (defaults for a local VSS `lvs` deployment):**

- VLM / RT-VLM: `${VLM_BASE_URL}` — default `${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}`
- LVS service: `${LVS_BACKEND_URL}` — default `http://${HOST_IP:-localhost}:38111`

Use env vars when set (strip trailing `/v1` from the VLM base — the skill appends it). Otherwise use the defaults. If neither works, ask the user — do not scan ports or read config files to guess.

**Model name:** discover the model from the serving endpoint before issuing a
summarization request. Use `${VLM_NAME}` only when it exactly matches an id in
the endpoint's model-list response (`/models` for LVS, `/v1/models` for
RT-VLM). Otherwise, use the sole advertised id. If
multiple ids are advertised and `${VLM_NAME}` does not select one, stop before
the POST and report the available ids instead of guessing.

Before any LVS operation request, fetch and inspect the running service's
`/openapi.json` as described in
[`references/video-summarization-api.md`](references/video-summarization-api.md).
Use it for endpoint schemas, optional fields, response envelopes, and error
handling.

**Availability checks** (run both before routing).
**Readiness is determined by the HTTP status code only** — the LVS
`/v1/ready` may legitimately return `200` with an empty body, so do not
inspect the body.

```bash
VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
VLM="${VLM%/v1}"

# VLM / RT-VLM: 200 on /v1/models
vlm_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 10 \
  "$VLM/v1/models")
[ "$vlm_code" = "200" ] && echo "VLM OK" || echo "VLM not reachable (HTTP $vlm_code)"

# Video summarization service: 200 on /v1/ready, with retry on 503 (warmup) for up to ~30s
VIDEO_SUMMARIZATION_URL=${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}
LVS_REQUEST=/tmp/vss-summarize-video-request.json
LVS_RESPONSE=/tmp/vss-summarize-video-response.json
video_sum_code=000
for i in $(seq 1 10); do
  video_sum_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 10 "$VIDEO_SUMMARIZATION_URL/v1/ready")
  case "$video_sum_code" in
    200) echo "video summarization OK"; break ;;
    503) sleep 3 ;;                 # warming up; keep polling
    *)   break ;;                   # any other code = not reachable, stop retrying
  esac
done
[ "$video_sum_code" = "200" ] || echo "video summarization service not reachable (HTTP $video_sum_code)"
```

**How to interpret the results:**

- `video_sum_code = 200` → continue to **Stage 2** for every video.
- `video_sum_code != 200`, `vlm_code = 200` → ask before using the **VLM fallback**; prepend the Routing fallback note only if the user approves fallback.
- `vlm_code != 200` → fail; at least one backend must be reachable.
- A non-200 LVS code after the readiness retry loop is the ONLY signal of
  unavailability. Empty stdout, an empty `video_summary`, empty `events`, or
  missing optional event fields are NOT "unavailable" and must not trigger a
  VLM fallback.

---

### Stage 2 - Prepare the video through VIOS

Execute the required VIOS API operations directly as part of this ordered
workflow; do not invoke a separate skill for Stage 2. Check whether the
requested recorded video is already present. If it is absent from VIOS and an
exact local source file is available, upload that file directly to VIOS. Use
the resulting stream to obtain its full recorded timeline and generate a fresh
temporary MP4 URL covering that timeline.

If the user asks for an uploaded, pre-seeded, or sample VIOS video, resolve it
through VIOS only. List sensors first; if the requested sample is missing,
upload the canonical sample file. When no timestamp is specified, use the
default upload timestamp
`2025-01-01T00:00:00.000Z`; uploaded-file timelines are relative to the
timestamp supplied at upload time, so a fixed timestamp keeps clip resolution
deterministic. If no VIOS stream/timeline/clip URL can be resolved after that,
report the missing prerequisite and stop. Do not substitute arbitrary
`/tmp/*.mp4` files, start a local file server, or switch to a longer/alternate
local video.

For an absent local MP4, use the direct VIOS upload API rather than
NvStreamer. This compact example shows the required request shape and the
follow-up operations; replace the source path and use the stream ID returned by
the upload:

```bash
VIOS_API="http://${HOST_IP:-localhost}:30888/vst/api/v1"
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
CLIP_URL=$(jq -er '.videoUrl | sub("^http://http://"; "http://")' \
  /tmp/vios-clip-url.json)
```

Record these values for the remaining workflow stages:

1. **`streamId`** (via `sensor/list` → `sensor/<id>/streams`, or directly from an upload response).
2. **Timeline** - `{startTime, endTime}` (ISO 8601 UTC). `endTime - startTime` is the duration; needed only for the user-facing header (routing is driven solely by `/v1/ready`).
3. **Temporary MP4 clip URL** — the `/storage/file/<streamId>/url` variant with `container=mp4`. Response field: `.videoUrl`. Both backends need an HTTP(S) URL they can `GET`.

Before leaving Stage 2, require the timeline and generated clip to cover the
complete requested recording. When the exact source file is available, compare
the timeline duration with the source duration. A successful upload response or
HTTP range probe proves only storage or byte reachability; neither proves that
the complete MP4 is ready. If a direct VIOS upload does not produce its full
timeline, report the VIOS failure and stop before summarization. Do not switch
to NvStreamer or an RTSP recording unless the original request explicitly
requires a live or synthetic stream.

When Stage 1 selected LVS, verify the clip from the `vss-lvs` container without
writing the video into the agent's tool output:

```bash
docker exec vss-lvs python3 -c '
import sys
import urllib.request
request = urllib.request.Request(sys.argv[1], headers={"Range": "bytes=0-0"})
with urllib.request.urlopen(request, timeout=30) as response:
    response.read(1)
    print(response.status)
' "$CLIP_URL"
```

Do not use the `vss-lvs` container's lightweight `curl` shim for this probe. It
ignores output-control options such as `-o` and writes the response body to
stdout. A full MP4 can be hundreds of megabytes and consume the context needed
to finish the workflow.

For an explicitly approved direct VLM fallback, use an HTTP(S) clip URL that
the VLM endpoint can reach. If a remote VLM cannot access the VIOS URL, report
that blocker instead of sending an inference request that cannot fetch its
input.

With the fresh clip URL established, verify its reachability as described
above. When LVS was selected, continue to Stage 3. When VLM fallback was
explicitly approved, skip LVS HITL and follow the Stages 3-4 fallback path.

### Stage 3 - Collect summary settings

Use this path **whenever** `/v1/ready` returned 200 in Stage 1. Duration is irrelevant.
Once this path is selected, do not call any VLM `/v1/chat/completions`
endpoint for this request. The LVS service owns all summarization traffic.

For advanced fields (`media_info`, `schema`, structured output, stream captioning, metrics, recommended config) see [`references/video-summarization-api.md`](references/video-summarization-api.md).

### HITL: collect scenario and events first (REQUIRED — do not skip)

Full walk-through is in [`references/hitl-prompts.md`](references/hitl-prompts.md). Always run HITL before calling the LVS service.

**Autonomous-mode defaults.** When the caller has bypassed HITL ("run
autonomously without prompting") AND the original query asks for
`default`/`defaults` (or gives none), use
`scenario="activity monitoring"` and `events=["notable activity"]`
**verbatim** — do not infer from filename or sensor name. Note the
defaults in the final reply and offer a re-run with more specific
parameters. This is the ONLY supported HITL bypass; "the video is
short" or "the user seems in a hurry" are not valid reasons.

### Stage 4 - Discover the live contract and submit one request

Prefer `POST /v1/summarize` (3.2 GA route); `/summarize` is a compatibility alias.
Issue exactly one `POST /v1/summarize` call for the user's summarize request.
Do not retry the POST, do not run a second POST with broader events, and do not
run a parallel or fallback VLM request. If the response is empty or weak,
render the exact LVS response and offer to run a separate new request with
different parameters.

```bash
VIDEO_SUMMARIZATION_URL=${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}
LVS_OPENAPI=/tmp/vss-lvs-openapi.json
LVS_MODELS_RESPONSE=${LVS_MODELS_RESPONSE:-/tmp/vss-summarize-video-models.json}
curl -fsS --connect-timeout 3 --max-time 15 \
  "$VIDEO_SUMMARIZATION_URL/openapi.json" > "$LVS_OPENAPI"
jq -e '.paths["/v1/summarize"].post.requestBody.content["application/json"].schema' \
  "$LVS_OPENAPI" >/dev/null
curl -fsS --connect-timeout 3 --max-time 10 \
  "$VIDEO_SUMMARIZATION_URL/models" > "$LVS_MODELS_RESPONSE"

LVS_MODEL=$(jq -er --arg preferred "${VLM_NAME:-}" '
  [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
  | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
    elif ($ids | length) == 1 then $ids[0]
    else empty
    end
' "$LVS_MODELS_RESPONSE") || {
  echo "Unable to select an LVS model. Set VLM_NAME to one of:"
  jq -r '.data[]?.id // empty' "$LVS_MODELS_RESPONSE"
  return 1 2>/dev/null || exit 1
}

# From HITL reply:
SCENARIO='warehouse monitoring'
EVENTS_JSON='["notable activity"]'
OBJECTS_JSON=''  # '' to omit, else '["forklifts","pallets","workers"]'

jq -n --arg url "<fresh_vios_clip_url_from_stage_2>" \
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

# This is the one permitted summarize POST. Preserve its status and complete
# body so errors or empty choices can be diagnosed without issuing another POST.
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

Do not repeat the POST to inspect a failure. Read `$LVS_RESPONSE`, inspect
service logs, or use non-mutating readiness and `/models` GET requests. A new
POST is a separate user-requested summarize operation, not a diagnostic step.

If both `video_summary` and `events` are empty, inspect the already printed
`usage.total_chunks_processed`. A positive integer proves LVS processed the
media; report the processed chunk count and that no requested events were
returned. If the value is zero or missing, report that LVS returned an empty
result and that media processing could not be confirmed. Do not claim that the
model detected nothing, and do not rerun automatically. A new request with
broader `scenario`/`events` requires a separate user request.

**Tuning:** `chunk_duration` (default `10`s; `0` = single chunk) and `seed`
(default `1`). Do not send `num_frames_per_second_or_fixed_frames_chunk` or
`use_fps_for_chunking` in the standard workflow. RT-VLM owns the model-specific
frame sampling default; remote Docker LVS deployments configure five fixed
frames per chunk for endpoints with that image limit. `num_frames_per_chunk`
is deprecated.

---

### Stages 3-4 fallback - VLM direct with default prompt

Use this path **only** when `/v1/ready` did not return 200 after warmup AND the
user explicitly approved the lower-quality fallback. Do NOT run HITL. Prepend
the Routing fallback note to the response. Never use this path to improve,
repair, validate, or replace a successful LVS `/v1/summarize` response.

```bash
VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
VLM="${VLM%/v1}"
VLM_MODELS_RESPONSE=/tmp/vss-vlm-models.json
curl -fsS --connect-timeout 3 --max-time 10 \
  "$VLM/v1/models" > "$VLM_MODELS_RESPONSE"
VLM_MODEL=$(jq -er --arg preferred "${VLM_NAME:-}" '
  [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
  | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
    elif ($ids | length) == 1 then $ids[0]
    else empty
    end
' "$VLM_MODELS_RESPONSE") || {
  echo "Unable to select a VLM model. Set VLM_NAME to one of:"
  jq -r '.data[]?.id // empty' "$VLM_MODELS_RESPONSE"
  return 1 2>/dev/null || exit 1
}
PROMPT='Describe in detail what is happening in this video,
including all visible people, vehicles, equipments, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.
EXAMPLE:
[0.0s-4.0s] <description of the first event>
[4.0s-12.0s] <description of the second event>'

curl -s --max-time 300 -X POST "$VLM/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
        --arg model "$VLM_MODEL" \
        --arg text "$PROMPT" \
        --arg url "<fresh_vios_clip_url_from_stage_2>" \
        '{
          model: $model,
          temperature: 0.0,
          max_tokens: 1024,
          messages: [{
            role: "user",
            content: [
              {type: "text", text: $text},
              {type: "video_url", video_url: {url: $url}}
            ]
          }]
        }')" | jq -r '.choices[0].message.content'
```

**Response:** standard OpenAI chat-completion envelope. The summary is in
`choices[0].message.content`.

**Cosmos-model notes:** Cosmos models may return reasoning via
`<think>...</think><answer>...</answer>` blocks. Omit the reasoning
instructions if you want a plain summary. Frame sampling and pixel limits
are applied server-side; no client-side prep is required when you pass a
`video_url`.

---

## End-to-end example

See [`references/end-to-end-example.md`](references/end-to-end-example.md) for
the LVS-only script that probes `/v1/ready` and issues one
`POST /v1/summarize` call when ready.

---

## Responses

- **VLM** returns an OpenAI chat-completion envelope; summary is
  `choices[0].message.content`.
- **LVS service** returns the same envelope but `content` is a JSON string.
  Preserve top-level `usage` while parsing `content` to reach
  `{video_summary, events}`; `usage.total_chunks_processed` distinguishes an
  empty processed result from an unconfirmed processing failure.
- **Errors** surface as HTTP non-2xx plus JSON `{error: ...}`. LVS `503` usually
  means warmup — retry `/v1/ready`.

### Stage 5 - Present the output to the user

Surface backend output with **minimal transformation** — do not paraphrase,
re-voice, add emojis, or reformat. **One backend call → one rendering**: no
parallel hedging, no duplicate headers, never call both LVS and VLM for the
same video.

**Header line.** Start with exactly one:

```
Summary of <video_name> (<duration>)
```

`<duration>` = `Ns` for `< 60 s`, else `Mm Ss` (e.g. `3m 30s`).

**LVS output:** render `video_summary` **verbatim** (polished, tone-controlled
report — rewriting loses fidelity). Render every object in the returned
`events` array, in service order, preserving each returned field verbatim. Do
not fabricate fields such as `id` if the service did not return them. At
minimum, show `start_time`, `end_time`, `type`, and the full `description`
without truncation or paraphrase. Use a table only if it preserves full text;
otherwise use a per-event list. You MAY add a one-line header and a closing
offer to re-run with different parameters.

**VLM output:** render `choices[0].message.content` verbatim. If the model
produced `<think>…</think><answer>…</answer>` blocks, drop the `<think>`
block and show the answer.

**Fallback warning** (when applicable) goes **above** the summary, never
mixed into it.

## Tips

- **Route by service availability, not by duration.** Probe `/v1/ready` once
  in Stage 1; HTTP 200 → LVS+HITL for every clip; anything else → ask before
  VLM fallback.
- **HITL is mandatory on the LVS path.** The `defaults` opt-in is the only
  sanctioned bypass. The VLM fallback path is silent (no HITL), but it still
  requires explicit fallback approval unless the user pre-authorized it.
- **Readiness = HTTP 200 on `/v1/ready`. Nothing else.** Body may be empty.
  Always use `curl -s -o /dev/null -w '%{http_code}'` — never pipe through
  `jq`/`grep`/`head`.
- **Follow the workflow in order.** Prepare and validate the VIOS clip before
  collecting settings and submitting the LVS request.
- **Discard clip probe bodies.** Verify `$CLIP_URL` from `vss-lvs` with the
  one-byte Python probe above; never stream the MP4 into tool output.
- **Preserve LVS usage while parsing.** Parse the JSON string inside `content`
  without dropping top-level `usage`; always expose `total_chunks_processed`
  when the summary and events are empty.
- **Prefer `/v1/summarize` for 3.2 GA**; `/summarize` is a compatibility alias.
- **Discover the VLM model id from the serving endpoint before POST.** Honor
  `VLM_NAME` only when advertised; otherwise use the sole advertised id and
  stop on ambiguity.
- **Render output verbatim** — no paraphrasing, no rewriting, and no truncating
  the `video_summary`, event `description`, or VLM `choices[0].message.content`.
- **One call, one render.** No parallel hedging, no duplicate POSTs, no
  automatic event broadening, and no double renderings.
- **Match the image tag to the host platform.** Use `LVS_TAG=3.2.1`
  (and `RTVI_VLM_IMAGE_TAG=3.2.1`) on x86 / Jetson Thor, and
  `LVS_TAG=3.2.1-sbsa` (and `RTVI_VLM_IMAGE_TAG=3.2.1-sbsa`)
  on SBSA / DGX Spark / Grace (server-class ARM64) hosts.

## Cross-reference

- **vss-deploy-profile** — bring up the `base` (VLM only) or `lvs` (VLM + video summarization service) profile
- **vss-manage-video-io-storage** (VIOS API) — upload videos, list streams, get clip URLs
- **vss-search-archive** — semantic search across the archive (different profile)
- **vss-query-analytics** — query incidents/events from Elasticsearch
- **video summarization API reference** — [`references/video-summarization-api.md`](references/video-summarization-api.md)
- **video summarization service ops reference** — [`references/video-summarization-deployment.md`](references/video-summarization-deployment.md)

bump:3
