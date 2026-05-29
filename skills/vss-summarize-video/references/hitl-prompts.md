# Video Summarization — HITL Prompt Walkthroughs

### HITL: collect scenario and events first (REQUIRED — do not skip)

**Before any call to `POST /v1/summarize`, you MUST ask the user for
`scenario`, `events`, and `objects_of_interest`, and wait for their
response.** Do not call the video summarization service with defaults silently — if the user wants
defaults, they must say so explicitly (e.g., "use the generic
defaults").

This HITL step collects summarization parameters; it is not a second
permission prompt after the user has already asked for a summary. Once the user
provides values or replies `defaults`, send the `/v1/summarize` request without
asking for another confirmation. Pause for an additional review-before-send
confirmation only if the user explicitly requested that review, or if the
resolved backend would send private media outside the user's expected VSS
deployment boundary. Ordinary configured `LVS_BACKEND_URL`,
`VIDEO_SUMMARIZATION_URL`, `HOST_IP`, and localhost targets do not require that
extra confirmation.

You MAY reuse previously confirmed `scenario` / `events` /
`objects_of_interest` from earlier in the same chat **only if** the user
is asking to re-summarize the **same video** (same `streamId` / clip
URL) — in that case, remind the user which parameters you're about to
reuse, then call the service unless they change them or cancel. For any
**different video**, re-run the HITL from scratch.

Post the message as follows (literal template — fill the `{video_name}`
and `{duration}` placeholders):

> To summarize **{video_name}** ({duration}s), I need three parameters:
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
and send the video summarization request. After that reply, send the request
without asking for a separate approval to call `/v1/summarize`.

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

**Defaults opt-in via the original query (autonomous mode).** When HITL
is bypassed (e.g. the caller said "run autonomously without prompting
for confirmation") and the original query contains the word `default`
or `defaults` for scenario/events, treat that as the same opt-in as a
HITL `defaults` reply: use `scenario="activity monitoring"` and
`events=["notable activity"]` **verbatim** - do not infer the scenario
from the video filename, sensor name, or any other context. In the
final reply, note that you used the generic defaults and offer to redo
with more specific parameters. The same rule applies if the original
query gives no scenario/events at all and HITL is bypassed - use the
canonical defaults rather than guessing.

**Request:**

```bash
VIDEO_SUMMARIZATION_URL="${LVS_BACKEND_URL:-${VIDEO_SUMMARIZATION_URL:-http://${HOST_IP:-localhost}:38111}}"
VIDEO_SUMMARIZATION_URL="${VIDEO_SUMMARIZATION_URL%/}"

curl -s -X POST "$VIDEO_SUMMARIZATION_URL/v1/summarize" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "<clip_url_from_vss_manage_video_io_storage>",
    "model": "'"${VLM_NAME:-nim_nvidia_cosmos-reason2-8b_hf-1208}"'",
    "scenario": "<scenario>",
    "events": ["<event1>", "<event2>"],
    "chunk_duration": 10,
    "num_frames_per_second_or_fixed_frames_chunk": 20,
    "use_fps_for_chunking": false,
    "seed": 1
  }' | jq .
```

Omit `objects_of_interest` if the user did not provide any. Include it as a
JSON array otherwise. `num_frames_per_chunk` still exists in the OpenAPI schema
for compatibility, but it is deprecated in 3.2.0; prefer
`num_frames_per_second_or_fixed_frames_chunk` with `use_fps_for_chunking`.

**Response shape:** OpenAI-style envelope. `choices[0].message.content` is a
**JSON string** — parse it to get the actual summary and event list.

```bash
jq -r '.choices[0].message.content' response.json | jq '{video_summary, events}'
```
