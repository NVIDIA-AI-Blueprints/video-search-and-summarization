---
name: video-summarization
description: Summarize a video by calling the VLM NIM or the Long Video Summarization (LVS) microservice directly. For short videos (<60s) call the VLM's OpenAI-compatible chat completions endpoint; for long videos (>=60s) call the LVS microservice. Use when asked to summarize a video, describe what happens in a video, or analyze a recording.
metadata:
  { "openclaw": { "emoji": "📹", "os": ["linux"] } }
---

You are a video summarization assistant. You call the VLM NIM or the LVS
microservice **directly**. Always run `curl` commands yourself; never instruct the user to run them.

Single query type: **"Summarize this video."**

## Routing

Decide purely from video duration (fetch the timeline via the `sensor-ops`
skill, then do the math — see Step 1):

| Video duration | Backend | Endpoint |
|---|---|---|
| `< 60s` (short) | **VLM NIM** (OpenAI-compatible) | `POST ${VLM_BASE_URL}/v1/chat/completions` |
| `>= 60s` (long), LVS available | **LVS microservice** | `POST ${LVS_BACKEND_URL}/summarize` |
| `>= 60s`, LVS **not** reachable | **VLM NIM** + tell the user | `POST ${VLM_BASE_URL}/v1/chat/completions` |

Fallback message when LVS is unreachable for a long video (copy verbatim
into the response, before the summary):

> ⚠️ **Note:** Input video `<name>` is `<N>`s long.
> Long Video Summarization (LVS) is not deployed, so this summary was
> produced by the VLM alone. Deploy the `lvs` profile for higher-quality
> long-video summaries.

## Setup

**Endpoints (defaults for a local VSS deployment):**

- VLM NIM: `${VLM_BASE_URL}` — default `http://localhost:30082`
- LVS MS: `${LVS_BACKEND_URL}` — default `http://localhost:38111`
- VIOS: owned by the `sensor-ops` skill; refer there.

**Endpoint resolution order:**

1. If the env vars `VLM_BASE_URL` / `LVS_BACKEND_URL` are set, use them
   (strip a trailing `/v1` from `VLM_BASE_URL` — NIM exposes `/v1/...` and
   this skill appends it).
2. Otherwise use the defaults above.
3. If neither works, ask the user for the endpoints. Do not scan ports or
   read config files to guess them.

**Model name:** read `${VLM_NAME}` (default `nvidia/cosmos-reason2-8b`).
Both VLM and LVS requests use the same model name.

**Availability checks** (run both before routing):

```bash
curl -sf --connect-timeout 3 "${VLM_BASE_URL:-http://localhost:30082}/v1/models" >/dev/null \
  && echo "VLM OK"
curl -sf --connect-timeout 3 "${LVS_BACKEND_URL:-http://localhost:38111}/v1/ready" >/dev/null \
  && echo "LVS OK"
```

If the VLM is unreachable, fail — summarization cannot run. If only LVS is
unreachable, continue with the VLM-fallback path described above.

---

## Step 1 — Resolve the video to a clip URL (delegate to `sensor-ops`)

**Use the `sensor-ops` skill for all VIOS interactions** — it owns the
canonical curl recipes, parameter defaults, and delete/upload flows. Do not
fabricate URLs or hand-roll VIOS calls here; they will drift.

From `sensor-ops`, you need exactly three things for summarization:

1. **`streamId`** for the video (via `sensor/list` → `sensor/<id>/streams`,
   or directly from an upload response).
2. **Timeline** — `{startTime, endTime}` for the stream, ISO 8601 UTC.
   `endTime - startTime` is the duration that drives the routing decision
   below. Always compute; never assume.
3. **Temporary MP4 clip URL** — the `/storage/file/<streamId>/url` variant
   with `container=mp4`. The VLM and LVS both need an HTTP(S) URL they can
   `GET`; the `/url` variant is preferred over streaming bytes through the
   summarization client. Response field: `.videoUrl`.

Everything else (auth, error handling, upload, `disableAudio`, expiry, etc.)
is covered in the `sensor-ops` skill — refer users there if the VIOS step
fails.

---

## Step 2a — Short video (< 60s) → VLM direct

### HITL: confirm the VLM prompt first (REQUIRED — do not skip)

**Before any call to the VLM, you MUST show the default prompt to the
user verbatim and wait for their response.** Do not proceed on silence
and do not assume defaults.

You MAY reuse a confirmed prompt from earlier in the same chat **only
if** the user is asking to re-summarize the **same video** (same
`streamId` / clip URL) — in that case, remind the user what prompt
you're about to reuse and offer them the chance to change it before
calling. For any **different video**, re-run the HITL from scratch.

Post the message as follows (literal template — fill the `{video_name}`
placeholder):

> I'm about to summarize **{video_name}** with this VLM prompt. Reply
> `Submit` to use it as-is, paste replacement text, `/generate <desc>`
> to rewrite it from a description, `/refine <instr>` to tweak it, or
> `/cancel` to stop.
>
> ```
> <default VLM prompt below>
> ```

**Default VLM prompt** (copy verbatim from the base profile):

```
Describe in detail what is happening in this video,
including all visible people, vehicles, equipments, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.
EXAMPLE:
[0.0s-4.0s] <description of the first event>
[4.0s-12.0s] <description of the second event>
```

**User response handling:**

| User input | Effect |
|---|---|
| `Submit` (or empty) | Approve the current prompt and call the VLM |
| Any other free text | Treat as a full replacement prompt; echo it back and ask for `Submit` before calling |
| `/generate <description>` | You (the assistant) write a new prompt from the description, show it back, and wait for `Submit` |
| `/refine <instructions>` | You (the assistant) refine the current prompt per the instructions, show it back, and wait for `Submit` |
| `/cancel` | Cancel summarization |

Rules:

- You MAY call the VLM **only** after receiving `Submit` (or an empty
  confirmation) on a prompt that is currently visible in the chat.
- `/generate` and `/refine` are not terminal — they produce a new prompt
  that itself needs `Submit`.
- When handling `/generate` and `/refine`, preserve the
  `[Xs-Ys] <description>` output-format requirement from the default
  prompt.
- If the user just says "go" / "ok" / "yes" without having seen the
  prompt, show the prompt first, then wait for `Submit`.

### Call the VLM

Once the user confirms a prompt, send it as the `text` part of the VLM
message. OpenAI-compatible chat completions with the video URL embedded in
the message content:

```bash
PROMPT='<confirmed_prompt_from_hitl>'

curl -s -X POST "${VLM_BASE_URL:-http://localhost:30082}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
        --arg model "${VLM_NAME:-nvidia/cosmos-reason2-8b}" \
        --arg text "$PROMPT" \
        --arg url "<clip_url_from_vios>" \
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

## Step 2b — Long video (>= 60s) → LVS microservice direct

### HITL: collect scenario and events first (REQUIRED — do not skip)

**Before any call to `POST /summarize`, you MUST ask the user for
`scenario`, `events`, and `objects_of_interest`, and wait for their
response.** Do not call LVS with defaults silently — if the user wants
defaults, they must say so explicitly (e.g., "use the generic
defaults").

You MAY reuse previously confirmed `scenario` / `events` /
`objects_of_interest` from earlier in the same chat **only if** the user
is asking to re-summarize the **same video** (same `streamId` / clip
URL) — in that case, remind the user which parameters you're about to
reuse and let them change them before calling. For any **different
video**, re-run the HITL from scratch.

Post the message as follows (literal template — fill the `{video_name}`
and `{duration}` placeholders):

> I'm about to send **{video_name}** ({duration}s) to LVS. I need three
> parameters first:
>
> 1. **`scenario`** — one-line context, e.g. `"warehouse monitoring"`,
>    `"traffic monitoring"`, `"activity monitoring"`.
> 2. **`events`** — a comma-separated list of events to surface, e.g.
>    `accident, pedestrian crossing`.
> 3. **`objects_of_interest`** *(optional)* — things to track, e.g.
>    `cars, trucks, pedestrians`. Reply `skip` to omit.
>
> Or reply `defaults` to use `scenario="activity monitoring"`,
> `events=["notable activity"]`, no objects. Reply `/cancel` to stop.

Only after the user replies with values (or `defaults`) may you build
and send the LVS request.

**Required parameters:**

| Param | Type | Example |
|---|---|---|
| `scenario` | string (required) | `"activity monitoring"`, `"traffic monitoring"`, `"warehouse monitoring"` |
| `events` | list[string] (required) | `["notable activity"]`, `["accident", "pedestrian crossing"]` |
| `objects_of_interest` | list[string] (optional) | `["cars", "trucks", "pedestrians"]` |

If the user explicitly replies `defaults` to the HITL prompt above, use
`scenario="activity monitoring"` and `events=["notable activity"]`, and
mention in your response that you used generic defaults (offer to redo
with more specific parameters). **Do not apply defaults without that
explicit opt-in** — the HITL message is the gate.

**Request:**

```bash
curl -s -X POST "${LVS_BACKEND_URL:-http://localhost:38111}/summarize" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "<clip_url_from_vios>",
    "model": "'"${VLM_NAME:-nvidia/cosmos-reason2-8b}"'",
    "scenario": "<scenario>",
    "events": ["<event1>", "<event2>"],
    "chunk_duration": 10,
    "num_frames_per_chunk": 20,
    "seed": 1
  }' | jq .
```

Omit `objects_of_interest` if the user did not provide any. Include it as a
JSON array otherwise.

**Response shape:** OpenAI-style envelope. `choices[0].message.content` is a
**JSON string** — parse it to get the actual summary and event list.

```bash
# Extract the summary and events in one pipe:
curl -s -X POST "${LVS_BACKEND_URL:-http://localhost:38111}/summarize" \
  -H "Content-Type: application/json" \
  -d @request.json \
  | jq -r '.choices[0].message.content' \
  | jq '{video_summary, events}'
```

If both `video_summary` and `events` come back empty, the clip probably
doesn't contain the requested events — re-run with different `events` or a
broader `scenario` rather than reporting "no content."

**Tuning:**

- `chunk_duration` (default `10`) — seconds per chunk. Smaller = finer
  timestamps, more VLM calls. Use `0` to send the whole video in one chunk.
- `num_frames_per_chunk` (default `20`) — frames sampled per chunk.
- `seed` (default `1`) — reproducibility; change or omit to get variety.

---

## End-to-end examples

Assume the `sensor-ops` skill has already given you `$CLIP` (clip URL) and
`$DURATION` (seconds) for the target video — those two values are the
contract from Step 1.

### Short video (`$DURATION < 60`)

**HITL (required, before the curl):** post the Step 2a message, wait for
`Submit` (or a `/generate` / `/refine` round-trip that ends in `Submit`),
then set `PROMPT` to the confirmed text. Do not run the curl below until
that confirmation has arrived.

```bash
PROMPT='Describe in detail what is happening in this video,
including all visible people, vehicles, equipments, objects,
actions, and environmental conditions.
OUTPUT REQUIREMENTS:
[timestamp-timestamp] Description of what is happening.
EXAMPLE:
[0.0s-4.0s] <description of the first event>
[4.0s-12.0s] <description of the second event>'

curl -s -X POST "${VLM_BASE_URL:-http://localhost:30082}/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg url "$CLIP" --arg text "$PROMPT" \
        --arg model "${VLM_NAME:-nvidia/cosmos-reason2-8b}" '{
    model: $model,
    temperature: 0.0,
    max_tokens: 1024,
    messages: [{role:"user", content:[
      {type:"text", text:$text},
      {type:"video_url", video_url:{url:$url}}
    ]}]
  }')" | jq -r '.choices[0].message.content'
```

### Long video (`$DURATION >= 60`)

**HITL (required, before the curl):** post the Step 2b message and wait
for the user's reply. Substitute their values (or the `defaults` opt-in)
into `$SCENARIO`, `$EVENTS_JSON`, and `$OBJECTS_JSON` below. Do not run
the curl without that reply.

```bash
LVS=${LVS_BACKEND_URL:-http://localhost:38111}

# From HITL reply:
SCENARIO='warehouse monitoring'            # or whatever the user gave
EVENTS_JSON='["notable activity"]'         # jq-compatible JSON array
OBJECTS_JSON=''                            # '' to omit, else '["cars","trucks"]'

if curl -sf --connect-timeout 3 "$LVS/v1/ready" >/dev/null; then
  curl -s -X POST "$LVS/summarize" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg url "$CLIP" \
          --arg model "${VLM_NAME:-nvidia/cosmos-reason2-8b}" \
          --arg scenario "$SCENARIO" \
          --argjson events "$EVENTS_JSON" \
          --argjson objects "${OBJECTS_JSON:-null}" '{
      url: $url,
      model: $model,
      scenario: $scenario,
      events: $events,
      chunk_duration: 10,
      num_frames_per_chunk: 20,
      seed: 1
    } + (if $objects == null then {} else {objects_of_interest: $objects} end)')" \
    | jq -r '.choices[0].message.content' | jq '{video_summary, events}'
else
  echo "⚠️ Note: video is ${DURATION}s long. LVS is not deployed; falling back to VLM."
  # Fall back to the short-video VLM flow above (which itself requires
  # the Step 2a HITL confirmation before calling the VLM).
fi
```

---

## Responses

- **VLM** returns an OpenAI chat-completion envelope; the summary string is
  `choices[0].message.content`.
- **LVS** returns the same envelope but `content` is a JSON string — run
  `jq -r '.choices[0].message.content' | jq` to reach `{video_summary, events}`.
- **Errors** from VLM/LVS surface as HTTP non-2xx plus JSON `{error: ...}`.
  `503` from LVS typically means it is still warming up — wait and retry
  `v1/ready`.

## Tips

- **HITL is not optional.** Every summarization starts with the HITL
  message (Step 2a or 2b). Skipping it to "be efficient" is the single
  most common failure mode of this skill — do not do it.
- **Delegate VIOS to `sensor-ops`.** Do not hand-roll clip-URL, timeline, or
  upload calls here — they'll drift from the canonical recipes.
- **Duration is authoritative.** Don't route on filename or user hints;
  compute from the timeline returned by `sensor-ops`.
- **`jq` twice for LVS.** First unwraps the OpenAI-style envelope, second
  parses the JSON string inside `content`.

## Cross-reference

- **deploy** — bring up the `base` (VLM only) or `lvs` (VLM + LVS MS) profile
- **sensor-ops** (VIOS API) — upload videos, list streams, get clip URLs
- **video-search** — semantic search across the archive (different profile)
- **video-analytics** — query incidents/events from Elasticsearch
