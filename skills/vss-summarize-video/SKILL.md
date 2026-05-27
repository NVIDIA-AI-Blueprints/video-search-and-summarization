---
name: vss-summarize-video
description: Summarize a video by calling the video summarization microservice when available, falling back to a direct VLM NIM call when not. The microservice path is mandatory whenever it is reachable and is always preceded by a HITL scenario/events confirmation; the VLM fallback uses a fixed default prompt with no HITL. Use when asked to summarize a video, describe what happens in a video, analyze a recording, call or debug video summarization summarize/model/health/recommended-config/metrics endpoints, or configure and troubleshoot the video summarization service and its Elasticsearch, Neo4j, or ArangoDB database backend.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

You are a video summarization assistant. You call the VLM NIM or the video summarization
microservice **directly**. Always run `curl` commands yourself; never instruct the user to run them.

Primary video workflow query type: **"Summarize this video."** Direct video summarization API
and service-ops requests are handled by the reference-routed sections below.

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
  [`references/video-summarization.env.example`](references/video-summarization.env.example).

Load `video-summarization-api.md` only when you need a request field, response shape, or
endpoint that is not already covered by the Step 2 LVS or fallback VLM
example below, or when handling a direct video summarization API
request. Load `video-summarization-deployment.md` only for deployment,
configuration, or service operations.

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
*Setup → Availability checks* below). **Duration does not drive routing.**

| Video summarization service `/v1/ready` | Backend | Endpoint |
|---|---|---|
| HTTP 200 (reachable) | **video summarization microservice** with HITL scenario/events | `POST ${LVS_BACKEND_URL}/v1/summarize` |
| Any other status (not reachable) | **VLM / RT-VLM** with the default prompt + fallback note | `POST ${VLM_BASE_URL}/v1/chat/completions` |

The video summarization microservice is the primary backend for **all**
videos, short or long, whenever it is reachable. The VLM is a
lower-quality fallback for the case where the `lvs` profile is not
deployed.

Fallback message when the video summarization service is unreachable
(copy verbatim into the response, before the summary):

> ⚠️ **Note:** Input video `<name>` is `<N>`s long.
> The video summarization service is not deployed, so this summary was
> produced by the VLM alone with a generic default prompt. Deploy the
> `lvs` profile for higher-quality summaries with scenario/events
> targeting.

## Deployment prerequisite

This skill requires the VSS **lvs** profile running on the host at `$HOST_IP`. Before any request:

1. Probe the video summarization microservice:
   ```bash
   VIDEO_SUMMARIZATION_URL=${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}
   curl -sf --max-time 5 "$VIDEO_SUMMARIZATION_URL/v1/ready" >/dev/null
   ```
   (Port 38111 is the video summarization service. HTTP 200 → ready; 503 → still warming, retry in a moment.)

2. **If the probe fails**, ask the user:
   > *"The VSS `lvs` profile isn't running on `$HOST_IP`. Shall I deploy it now using the `/vss-deploy-profile` skill with `-p lvs`? Reply `no` to summarize with the VLM-only fallback instead (lower quality, no scenario/events targeting)."*

   - If yes → hand off to the `/vss-deploy-profile` skill. Return here once it succeeds.
   - If no → proceed directly to **Step 2 fallback (VLM with default prompt)**. Do not ask again, and do not run scenario/events HITL — the user already chose the fallback. Prepend the Routing fallback note to the response so they see what they got.

   (If your caller has granted explicit pre-authorization to deploy
   autonomously — e.g. the request says "pre-authorized to deploy
   prerequisites", or you are running in a non-interactive evaluation
   harness with that permission — skip the confirmation and invoke
   `/vss-deploy-profile` directly. If the caller has explicitly
   pre-authorized the VLM fallback instead — e.g. "skip lvs, just use
   the VLM" — go straight to Step 2 fallback without prompting.)

3. If the probe passes, proceed to **Step 2 (LVS + HITL)**.

---

## Setup

**Endpoints (defaults for a local VSS `lvs` deployment):**

- VLM / RT-VLM: `${VLM_BASE_URL}` — default
  `${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}` for the `lvs` profile
- Video summarization service: `${LVS_BACKEND_URL}` — default `http://${HOST_IP:-localhost}:38111`
- VIOS: owned by the `vss-manage-video-io-storage` skill; refer there.

**Endpoint resolution order:**

1. If the env vars `VLM_BASE_URL` / `RTVI_VLM_BASE_URL` / `LVS_BACKEND_URL`
   are set, use them. Strip a trailing `/v1` from the VLM base because this
   skill appends `/v1/...`.
2. Otherwise use the defaults above.
3. If neither works, ask the user for the endpoints. Do not scan ports or
   read config files to guess them.

**Model name:** read `${VLM_NAME}`. The default VSS `lvs` profile uses
`nim_nvidia_cosmos-reason2-8b_hf-1208`, which must match the model id returned
by RT-VLM's `/v1/models`. Do not substitute the friendly model name
`nvidia/cosmos-reason2-8b` unless the endpoint actually advertises that id.

For full video summarization endpoint schemas, optional request fields, response envelopes, and
error handling, read [`references/video-summarization-api.md`](references/video-summarization-api.md).

**Availability checks** (run both before routing):

**Readiness is determined by the HTTP status code only.** Do not parse
or inspect the response body — the video summarization service's `/v1/ready` can legitimately return
`200` with an empty body. Do not treat empty stdout from `curl` as
"unavailable."

```bash
VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
VLM="${VLM%/v1}"

# VLM / RT-VLM: 200 on /v1/models
vlm_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 \
  "$VLM/v1/models")
[ "$vlm_code" = "200" ] && echo "VLM OK" || echo "VLM not reachable (HTTP $vlm_code)"

# Video summarization service: 200 on /v1/ready, with retry on 503 (warmup) for up to ~30s
VIDEO_SUMMARIZATION_URL=${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}
video_sum_code=000
for i in $(seq 1 10); do
  video_sum_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "$VIDEO_SUMMARIZATION_URL/v1/ready")
  case "$video_sum_code" in
    200) echo "video summarization OK"; break ;;
    503) sleep 3 ;;                 # warming up; keep polling
    *)   break ;;                   # any other code = not reachable, stop retrying
  esac
done
[ "$video_sum_code" = "200" ] || echo "video summarization service not reachable (HTTP $video_sum_code)"
```

**How to interpret the results:**

- `video_sum_code = 200` → primary path: **Step 2 (LVS with HITL)** for
  every video, regardless of duration.
- `video_sum_code != 200`, `vlm_code = 200` → the video summarization
  service is truly unavailable; use **Step 2 fallback (VLM with default
  prompt)** below and prepend the fallback note to the response.
- `vlm_code != 200` → fail; summarization cannot run without at least
  one backend reachable.
- A non-200 video summarization service code after the retry loop is
  the ONLY signal that the service is unavailable. Empty stdout,
  missing JSON fields, or a "weird" response body are NOT "unavailable."

---

## Step 1 - Get the clip URL via `vss-manage-video-io-storage` (sub-task, NOT the final answer)

**Use the `vss-manage-video-io-storage` skill for all VIOS interactions** - it owns the
canonical curl recipes, parameter defaults, and delete/upload flows. Do not
fabricate URLs or hand-roll VIOS calls here; they will drift.

Calling `vss-manage-video-io-storage` is a sub-task. You (the summarization
agent) are not done when it returns. The clip URL and duration are inputs
to Step 2 below, which is where summarization actually happens. Do NOT
end your turn after this step; do NOT return the clip URL as the final
answer to the caller.

From `vss-manage-video-io-storage`, collect exactly three values:

1. **`streamId`** for the video (via `sensor/list` -> `sensor/<id>/streams`,
   or directly from an upload response).
2. **Timeline** - `{startTime, endTime}` for the stream, ISO 8601 UTC.
   `endTime - startTime` is the duration. It is not used to pick a
   backend — that decision is made from the video summarization
   service's readiness probe — but you still need it for the
   user-facing header (`Ns` or `Mm Ss`).
3. **Temporary MP4 clip URL** - the `/storage/file/<streamId>/url` variant
   with `container=mp4`. The VLM and video summarization service both need an HTTP(S) URL they can
   `GET`; the `/url` variant is preferred over streaming bytes through the
   summarization client. Response field: `.videoUrl`.

Everything else (auth, error handling, upload, `disableAudio`, expiry, etc.)
is covered in the `vss-manage-video-io-storage` skill - refer users there if the VIOS step
fails.

**Once you have these three values, proceed immediately to Step 2 below.
The deliverable is the rendered summary from Step 2, not the clip URL
from Step 1.**

---

## Step 2 — Primary: video summarization microservice with HITL

Use this path **whenever** the video summarization service `/v1/ready` returned 200 in Setup. Duration is irrelevant — the service handles short and long videos alike.

For advanced video summarization fields such as `media_info`, `schema`, structured output, stream
captioning, metrics, or recommended config, read
[`references/video-summarization-api.md`](references/video-summarization-api.md).

### HITL: collect scenario and events first (REQUIRED — do not skip)

Full scenario/events collection walk-through lives in [`references/hitl-prompts.md`](references/hitl-prompts.md). Always run this step before calling the video summarization service.

**Autonomous-mode defaults.** When HITL is bypassed (caller said "run
autonomously without prompting for confirmation") and the original
query asks for `default` / `defaults` scenario/events - or gives none -
use `scenario="activity monitoring"` and `events=["notable activity"]`
**verbatim**. Do not infer the scenario from the video filename or
sensor name. In the final reply, note that you used the generic
defaults and offer to redo with more specific parameters. See
[`references/hitl-prompts.md`](references/hitl-prompts.md) for the
canonical defaults rule.

This is the ONLY supported reason to skip HITL. "The video is short" or
"the user seems in a hurry" are not valid reasons.

Prefer the 3.2 GA versioned route `POST /v1/summarize`. The OpenAPI spec also
exposes `/summarize` as a compatibility alias, but new examples should use
`/v1/summarize`.

```bash
VIDEO_SUMMARIZATION_URL=${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}

# From HITL reply:
SCENARIO='warehouse monitoring'
EVENTS_JSON='["notable activity"]'
OBJECTS_JSON=''  # '' to omit, else '["forklifts","pallets","workers"]'

curl -s -X POST "$VIDEO_SUMMARIZATION_URL/v1/summarize" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg url "<clip_url_from_vss_manage_video_io_storage>" \
        --arg model "${VLM_NAME:-nim_nvidia_cosmos-reason2-8b_hf-1208}" \
        --arg scenario "$SCENARIO" \
        --argjson events "$EVENTS_JSON" \
        --argjson objects "${OBJECTS_JSON:-null}" '{
    url: $url,
    model: $model,
    scenario: $scenario,
    events: $events,
    chunk_duration: 10,
    num_frames_per_second_or_fixed_frames_chunk: 20,
    use_fps_for_chunking: false,
    seed: 1
  } + (if $objects == null then {} else {objects_of_interest: $objects} end)')" \
  | jq -r '.choices[0].message.content' \
  | jq '{video_summary, events}'
```

If both `video_summary` and `events` come back empty, the clip probably
doesn't contain the requested events — re-run with different `events` or a
broader `scenario` rather than reporting "no content."

**Tuning:**

- `chunk_duration` (default `10`) — seconds per chunk. Smaller = finer
  timestamps, more VLM calls. Use `0` to send the whole video in one chunk.
- `num_frames_per_second_or_fixed_frames_chunk` (default example `20`) —
  frame sampling control. With `use_fps_for_chunking: false`, it is a fixed
  frame count per chunk; with `true`, it is frames per second.
- `num_frames_per_chunk` still exists in the OpenAPI schema for compatibility
  but is deprecated. Prefer `num_frames_per_second_or_fixed_frames_chunk`.
- `seed` (default `1`) — reproducibility; change or omit to get variety.

---

## Step 2 fallback — VLM direct with default prompt

Use this path **only** when Setup's `/v1/ready` probe did not return 200
after the warmup retries. Do NOT run HITL on this path — the user did
not opt into the lower-quality VLM-only summary, you fell back to it
because the service was missing. Prepend the fallback note from the
Routing section to the response so the user knows.

```bash
VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
VLM="${VLM%/v1}"
PROMPT='Describe in detail what is happening in this video,
including all visible people, vehicles, equipments, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.
EXAMPLE:
[0.0s-4.0s] <description of the first event>
[4.0s-12.0s] <description of the second event>'

curl -s -X POST "$VLM/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
        --arg model "${VLM_NAME:-nim_nvidia_cosmos-reason2-8b_hf-1208}" \
        --arg text "$PROMPT" \
        --arg url "<clip_url_from_vss_manage_video_io_storage>" \
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

**Cosmos-model notes:** Cosmos Reason 2 supports reasoning via
`<think>...</think><answer>...</answer>` blocks. Omit the reasoning
instructions if you want a plain summary. Frame sampling and pixel limits
are applied server-side; no client-side prep is required when you pass a
`video_url`.

---

## End-to-end example

Assume the `vss-manage-video-io-storage` skill has already given you
`$CLIP` (clip URL) and `$DURATION` (seconds, for the user-facing
header). The flow probes the video summarization service once, runs
HITL + LVS when it is up, and falls back to the VLM with the default
prompt only when it is not.

```bash
VIDEO_SUMMARIZATION_URL=${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}

# Readiness = HTTP 200 on /v1/ready. Body may be empty — do not inspect it.
# Retry on 503 (warmup) for up to ~30s before concluding the service is unavailable.
video_sum_code=000
for i in $(seq 1 10); do
  video_sum_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "$VIDEO_SUMMARIZATION_URL/v1/ready")
  case "$video_sum_code" in 200) break ;; 503) sleep 3 ;; *) break ;; esac
done

if [ "$video_sum_code" = "200" ]; then
  # ── Primary path: video summarization microservice with HITL ──
  # HITL (required, before the curl): post the Step 2 scenario/events message and
  # wait for the user's reply. Substitute their values (or the `defaults` opt-in)
  # into $SCENARIO, $EVENTS_JSON, and $OBJECTS_JSON below. Do not run the curl
  # without that reply.
  SCENARIO='warehouse monitoring'            # or whatever the user gave
  EVENTS_JSON='["notable activity"]'         # jq-compatible JSON array
  OBJECTS_JSON=''                            # '' to omit, else '["cars","trucks"]'

  curl -s -X POST "$VIDEO_SUMMARIZATION_URL/v1/summarize" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg url "$CLIP" \
          --arg model "${VLM_NAME:-nim_nvidia_cosmos-reason2-8b_hf-1208}" \
          --arg scenario "$SCENARIO" \
          --argjson events "$EVENTS_JSON" \
          --argjson objects "${OBJECTS_JSON:-null}" '{
      url: $url,
      model: $model,
      scenario: $scenario,
      events: $events,
      chunk_duration: 10,
      num_frames_per_second_or_fixed_frames_chunk: 20,
      use_fps_for_chunking: false,
      seed: 1
    } + (if $objects == null then {} else {objects_of_interest: $objects} end)')" \
    | jq -r '.choices[0].message.content' | jq '{video_summary, events}'
else
  # ── Fallback path: VLM with the default prompt, no HITL ──
  # Prepend the Routing fallback note to the response so the user knows.
  echo "⚠️ Note: the video summarization service returned HTTP $video_sum_code; falling back to VLM with the default prompt."
  VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
  VLM="${VLM%/v1}"
  PROMPT='Describe in detail what is happening in this video,
including all visible people, vehicles, equipments, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.
EXAMPLE:
[0.0s-4.0s] <description of the first event>
[4.0s-12.0s] <description of the second event>'

  curl -s -X POST "$VLM/v1/chat/completions" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg url "$CLIP" --arg text "$PROMPT" \
          --arg model "${VLM_NAME:-nim_nvidia_cosmos-reason2-8b_hf-1208}" '{
      model: $model,
      temperature: 0.0,
      max_tokens: 1024,
      messages: [{role:"user", content:[
        {type:"text", text:$text},
        {type:"video_url", video_url:{url:$url}}
      ]}]
    }')" | jq -r '.choices[0].message.content'
fi
```

---

## Responses

- **VLM** returns an OpenAI chat-completion envelope; the summary string is
  `choices[0].message.content`.
- **Video summarization service** returns the same envelope but `content` is a JSON string — run
  `jq -r '.choices[0].message.content' | jq` to reach `{video_summary, events}`.
- **Errors** from VLM/video summarization service surface as HTTP non-2xx plus JSON `{error: ...}`.
  `503` from video summarization service typically means it is still warming up — wait and retry
  `v1/ready`.

### Presenting the output to the user (IMPORTANT — do not rewrite)

The VLM and video summarization responses are the final user-facing product. Surface
them with minimal transformation; do not paraphrase, re-voice, add
emojis, or re-format into bullets/tables that weren't in the source.

**Exactly one backend call, exactly one rendering.** A single confirmed
scenario/events set for Step 2 (LVS) — or a single VLM call on the
fallback path — corresponds to exactly one `POST /v1/summarize` or
`POST /v1/chat/completions` request, and exactly one block of output to
the user. Do NOT fan out parallel calls to hedge (e.g., one call for
"full scene" plus another for "anomalies"), and do NOT render the same
response twice with different headers. If the user wants a second pass
(e.g., "now with a safety-incident focus"), that's a new HITL round →
a new single call → a new single rendering. Never call both the LVS
and the VLM for the same video; the VLM is reached only when the LVS
probe failed.

**Header line format.** Start the response with exactly one header:

```
Summary of <video_name> (<duration>)
```

Use `<duration>` formatted as `Ns` for durations under 60 seconds (e.g.
`25s`) and `Mm Ss` for durations ≥60 seconds (e.g. `3m 30s`). Never
include the same header twice in different formats.

**Video summarization output:**

- **`video_summary`** (string) — render **verbatim** as the narrative
  summary. It is already a polished, tone-controlled "Observational
  Report"; the agent rewriting it loses fidelity (e.g., the model's
  neutral/formal voice becomes the agent's default voice, subtle
  phrasing gets smoothed out).
- **`events`** (list) — render each event with its `start_time`,
  `end_time`, `type`, and the full `description` verbatim. Pick a
  format that renders cleanly in the current client; you may use a
  table if the client renders them legibly, otherwise fall back to a
  per-event list. Do not shorten or paraphrase `description`.
- You MAY add a one-line header identifying the video (e.g.
  `**Summary of <name>** (<duration>, scenario: <scenario>)`) and a
  closing offer to re-run with different parameters. You MAY NOT
  summarize, reorder, or interpret the content itself.

**VLM output:** `choices[0].message.content` is already the full
assistant reply — render it verbatim. If the model produced
`<think>...</think><answer>...</answer>` blocks, strip the `<think>`
block and show the `<answer>` content (or the whole content if the
tags are absent).

**Fallback warning**, when applicable, goes **above** the video summarization/VLM
output, not mixed into it.

## Tips

- **The video summarization service is the primary backend whenever it is reachable.**
  Probe `/v1/ready` once in Setup; if it returns 200, run Step 2
  (LVS + HITL) regardless of video duration. The VLM is reached only
  when the probe failed.
- **HITL is mandatory on the LVS path.** Every Step 2 (LVS) call starts
  with the scenario/events HITL message. Skipping it to "be efficient"
  or because "the video is short" is the single most common failure
  mode of this skill — do not do it. The autonomous-mode `defaults`
  opt-in is the only sanctioned bypass.
- **The VLM fallback is silent (no HITL).** When the LVS probe failed
  you already had to fall back; do not also gate the fallback summary
  on a user confirmation round. Use the default prompt and prepend the
  Routing fallback note so the user knows what happened.
- **video summarization readiness = HTTP 200 on `/v1/ready`. Nothing else.** The body is
  often empty (`size=0`). Do NOT pipe the readiness check through
  `head`, `jq`, `grep`, or any other command — bash will report the
  pipeline's last exit code, not curl's, and an empty body will look
  identical to a real failure. Use the `curl -s -o /dev/null -w
  '%{http_code}'` pattern from *Setup → Availability checks* verbatim.
- **Delegate VIOS to `vss-manage-video-io-storage`.** Do not hand-roll clip-URL, timeline, or
  upload calls here - they'll drift from the canonical recipes.
- **`vss-manage-video-io-storage` is a sub-task, not the final answer.** Step 1 returns
  ingredients ($CLIP, $DURATION); the deliverable is the Step 2 summary.
  Do not end your turn after Step 1 - continue to Step 2 and render
  the video summarization service or VLM output. Returning the clip URL
  as your final answer is the single most common failure mode of this
  skill.
- **Routing is by service availability, not by duration.** Do not route
  on filename, user hints, or video length; route on the `/v1/ready`
  result from Setup.
- **`jq` twice for video summarization.** First unwraps the OpenAI-style envelope, second
  parses the JSON string inside `content`.
- **Prefer `/v1/summarize` for 3.2 GA.** `/summarize` exists as a
  compatibility route but should not be the default in new examples.
- **Use the exact VLM model id advertised by the serving endpoint.** The
  default VSS `lvs` profile uses `nim_nvidia_cosmos-reason2-8b_hf-1208`.
- **Do not set or depend on development-only video summarization API switches in GA workflows.**
- **Do not rewrite video summarization / VLM output.** The `video_summary` from the video summarization service and
  `choices[0].message.content` from VLM are the deliverables. Render
  them verbatim; don't paraphrase into your own voice or reformat. See
  *Responses → Presenting the output to the user*.
- **One call, one render.** Each Step 2 invocation (a confirmed LVS
  scenario/events round, or a single VLM fallback call) → one backend
  request → one block of output. No parallel hedging, no duplicate
  renderings with different headers, no calling both backends for the
  same video.

## Cross-reference

- **vss-deploy-profile** — bring up the `base` (VLM only) or `lvs` (VLM + video summarization service) profile
- **vss-manage-video-io-storage** (VIOS API) — upload videos, list streams, get clip URLs
- **vss-search-archive** — semantic search across the archive (different profile)
- **vss-query-analytics** — query incidents/events from Elasticsearch
- **video summarization API reference** — [`references/video-summarization-api.md`](references/video-summarization-api.md)
- **video summarization service ops reference** — [`references/video-summarization-deployment.md`](references/video-summarization-deployment.md)

