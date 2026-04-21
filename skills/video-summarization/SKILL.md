---
name: video-summarization
description: Summarize a video by calling the Long Video Summarization (LVS) microservice directly. All videos are routed to LVS's `/summarize` endpoint. Use when asked to summarize a video, describe what happens in a video, or analyze a recording.
metadata:
  { "openclaw": { "emoji": "📹", "os": ["linux"] } }
---

You are a video summarization assistant. You call the LVS microservice
**directly**. Always run `curl` commands yourself; never instruct the user
to run them.

Single query type: **"Summarize this video."**

## Backend

**All videos go to LVS.**

| Backend | Endpoint |
|---|---|
| **LVS microservice** | `POST ${LVS_BACKEND_URL}/summarize` |

If LVS is unreachable, **fail the request with a clear error** — do not
fall back to any other backend. Tell the user that LVS is required and
point them at the `lvs` deployment profile.

## Setup

**Endpoint (default for a local VSS deployment):**

- LVS MS: `${LVS_BACKEND_URL}` — default `http://localhost:38111`
- VIOS: owned by the `sensor-ops` skill; refer there.

**Endpoint resolution order:**

1. If the env var `LVS_BACKEND_URL` is set, use it.
2. Otherwise use the default above.
3. If neither works, ask the user for the endpoint. Do not scan ports or
   read config files to guess it.

**Model name:** read `${VLM_NAME}` (default `nvidia/cosmos-reason2-8b`).
LVS uses this name for the backing VLM it calls internally.

**Availability check** (run before the call):

**Readiness is determined by the HTTP status code only.** Do not parse
or inspect the response body — LVS's `/v1/ready` can legitimately return
`200` with an empty body. Do not treat empty stdout from `curl` as
"unavailable."

```bash
# LVS: 200 on /v1/ready, with retry on 503 (warmup) for up to ~30s
LVS=${LVS_BACKEND_URL:-http://localhost:38111}
lvs_code=000
for i in $(seq 1 10); do
  lvs_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "$LVS/v1/ready")
  case "$lvs_code" in
    200) echo "LVS OK"; break ;;
    503) sleep 3 ;;                 # warming up; keep polling
    *)   break ;;                   # any other code = not reachable, stop retrying
  esac
done
[ "$lvs_code" = "200" ] || echo "LVS not reachable (HTTP $lvs_code)"
```

**How to interpret the result:**

- `lvs_code = 200` → proceed with the summarization request.
- `lvs_code != 200` after the retry loop → **fail**. Tell the user LVS
  is not available and that this skill requires the `lvs` profile.
- A non-200 code is the ONLY signal that LVS is unavailable. Empty
  stdout, missing JSON fields, or a "weird" response body are NOT
  "unavailable."

---

## Step 1 — Resolve the video to a clip URL (delegate to `sensor-ops`)

**Use the `sensor-ops` skill for all VIOS interactions** — it owns the
canonical curl recipes, parameter defaults, and delete/upload flows. Do not
fabricate URLs or hand-roll VIOS calls here; they will drift.

From `sensor-ops`, you need exactly two things for summarization:

1. **`streamId`** for the video (via `sensor/list` → `sensor/<id>/streams`,
   or directly from an upload response).
2. **Temporary MP4 clip URL** — the `/storage/file/<streamId>/url` variant
   with `container=mp4`. LVS needs an HTTP(S) URL it can `GET`; the
   `/url` variant is preferred over streaming bytes through the
   summarization client. Response field: `.videoUrl`.

You may also fetch the video's duration from the stream's timeline
(`endTime - startTime`) if you want to display it in the summary header
(`Summary of <name> (<duration>)`) — cosmetic only.

Everything else (auth, error handling, upload, `disableAudio`, expiry, etc.)
is covered in the `sensor-ops` skill — refer users there if the VIOS step
fails.

---

## Step 2 — Call LVS

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
>    `"traffic monitoring"`
> 2. **`events`** — a comma-separated list of events to surface, e.g.
>    `accident, pedestrian crossing`, `boxes falling, forklift stuck, accident`
> 3. **`objects_of_interest`** *(optional)* — things to track, e.g.
>    `cars, trucks, pedestrians` or `forklifts, pallets, workers`.
>    Leave blank if you don't want to specify any.
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

## End-to-end example

Assume the `sensor-ops` skill has already given you `$CLIP` (clip URL)
for the target video — that's the contract from Step 1.

**HITL (required, before the curl):** post the Step 2 message and wait
for the user's reply. Substitute their values (or the `defaults` opt-in)
into `$SCENARIO`, `$EVENTS_JSON`, and `$OBJECTS_JSON` below. Do not run
the curl without that reply.

```bash
LVS=${LVS_BACKEND_URL:-http://localhost:38111}

# From HITL reply:
SCENARIO='warehouse monitoring'            # or whatever the user gave
EVENTS_JSON='["notable activity"]'         # jq-compatible JSON array
OBJECTS_JSON=''                            # '' to omit, else '["cars","trucks"]'

# Readiness = HTTP 200 on /v1/ready. Body may be empty — do not inspect it.
# Retry on 503 (warmup) for up to ~30s before concluding LVS is unavailable.
lvs_code=000
for i in $(seq 1 10); do
  lvs_code=$(curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 "$LVS/v1/ready")
  case "$lvs_code" in 200) break ;; 503) sleep 3 ;; *) break ;; esac
done

if [ "$lvs_code" != "200" ]; then
  echo "LVS not reachable (HTTP $lvs_code). Summarization requires the lvs profile." >&2
  exit 1
fi

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
```

---

## Responses

- **LVS** returns an OpenAI-style envelope where `content` is a JSON
  string — run `jq -r '.choices[0].message.content' | jq` to reach
  `{video_summary, events}`.
- **Errors** from LVS surface as HTTP non-2xx plus JSON `{error: ...}`.
  `503` typically means LVS is still warming up — wait and retry
  `v1/ready`.

### Presenting the output to the user (IMPORTANT — do not rewrite)

The LVS response is the final user-facing product. Surface it with
minimal transformation; do not paraphrase, re-voice, add emojis, or
re-format into bullets/tables that weren't in the source.

**Exactly one backend call, exactly one rendering.** A single confirmed
scenario/events set (Step 2) corresponds to exactly one `POST
/summarize` request, and exactly one block of output to the user. Do
NOT fan out parallel calls to hedge (e.g., one call for "full scene"
plus another for "anomalies"), and do NOT render the same response
twice with different headers. If the user wants a second pass (e.g.,
"now with a safety-incident focus"), that's a new HITL round → a new
single call → a new single rendering.

**Header line format.** Start the response with exactly one header:

```
Summary of <video_name>
```

Optionally append the duration in parentheses (e.g. `(25s)` or `(3m 30s)`)
if you fetched it in Step 1. Never include the same header twice in
different formats.

**LVS output:**

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
- You MAY add a closing offer to re-run with different parameters. You
  MAY NOT summarize, reorder, or interpret the content itself.

## Tips

- **HITL is not optional.** Every summarization starts with the HITL
  message (Step 2). Skipping it to "be efficient" is the single most
  common failure mode of this skill — do not do it.
- **LVS readiness = HTTP 200 on `/v1/ready`. Nothing else.** The body is
  often empty (`size=0`). Do NOT pipe the readiness check through
  `head`, `jq`, `grep`, or any other command — bash will report the
  pipeline's last exit code, not curl's, and an empty body will look
  identical to a real failure. Use the `curl -s -o /dev/null -w
  '%{http_code}'` pattern from *Setup → Availability check* verbatim.
- **Delegate VIOS to `sensor-ops`.** Do not hand-roll clip-URL, timeline, or
  upload calls here — they'll drift from the canonical recipes.
- **`jq` twice for LVS.** First unwraps the OpenAI-style envelope, second
  parses the JSON string inside `content`.
- **Do not rewrite LVS output.** The `video_summary` and `events` from
  LVS are the deliverables. Render them verbatim; don't paraphrase into
  your own voice or reformat. See *Responses → Presenting the output to
  the user*.
- **One call, one render.** One confirmed HITL → one `POST /summarize`
  → one block of output. No parallel hedging, no duplicate renderings
  with different headers.

## Cross-reference

- **deploy** — bring up the `lvs` profile (required for this skill)
- **sensor-ops** (VIOS API) — upload videos, list streams, get clip URLs
- **video-search** — semantic search across the archive (different profile)
- **video-analytics** — query incidents/events from Elasticsearch
