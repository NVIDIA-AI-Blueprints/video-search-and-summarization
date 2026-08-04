# Video Summarization — HITL Prompt Walkthroughs

### HITL: collect scenario and events first (REQUIRED — do not skip)

**Before any call to `POST /v1/summarize`, you MUST ask the user for
`scenario`, `events`, and `objects_of_interest`, and wait for their
response.** Do not call the video summarization service with defaults silently — if the user wants
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

> I'm about to send **{video_name}** ({duration}s) to the video summarization service. I need three
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
and send the video summarization request.

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

Resolve endpoints first (`SKILL.md` / `end-to-end-example.md`). On Docker,
discover the live schema and model from the LVS host port. On Kubernetes, skip
LVS `/openapi.json` / `/models` (not on Ingress) — use Exact RT-VLM
`/v1/models` (or `VLM_NAME`) and the checked-in summarize contract.

```bash
LVS_REQUEST=/tmp/vss-summarize-video-request.json
LVS_RESPONSE=/tmp/vss-summarize-video-response.json
# After endpoint resolution: K8s forces LVS_BACKEND_URL=${VSS_PUBLIC_URL};
# Docker keeps ${LVS_BACKEND_URL:-http://localhost:38111}.
LVS_BASE=${LVS_BACKEND_URL:-http://localhost:38111}

# --- Model discovery (gated) ---
if [ "${DEPLOYMENT_KIND:-docker}" = "kubernetes" ]; then
  # Stock LVS Ingress does not publish /openapi.json or /models.
  LVS_MODEL=$(curl -fsS "${VLM:-$LVS_BASE}/v1/models" | jq -er --arg preferred "${VLM_NAME:-}" '
    [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
    | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
      elif ($ids | length) == 1 then $ids[0]
      else empty end
  ') || { echo "Set VLM_NAME to an advertised RT-VLM model id"; return 1 2>/dev/null || exit 1; }
else
  LVS_OPENAPI=/tmp/vss-lvs-openapi.json
  curl -fsS "$LVS_BASE/openapi.json" > "$LVS_OPENAPI"
  jq -e '.paths["/v1/summarize"].post.requestBody.content["application/json"].schema' \
    "$LVS_OPENAPI" >/dev/null
  LVS_MODEL=$(curl -fsS "$LVS_BASE/models" | jq -er --arg preferred "${VLM_NAME:-}" '
    [.data[]?.id | select(type == "string" and length > 0)] | unique as $ids
    | if $preferred != "" and ($ids | index($preferred)) != null then $preferred
      elif ($ids | length) == 1 then $ids[0]
      else empty end
  ') || { echo "Set VLM_NAME to an advertised model id"; return 1 2>/dev/null || exit 1; }
fi

jq -n \
  --arg url "<fresh_vios_clip_url_from_stage_2>" \
  --arg model "$LVS_MODEL" \
  --arg scenario "<scenario>" \
  --argjson events '["<event1>", "<event2>"]' \
  '{
    url: $url,
    model: $model,
    scenario: $scenario,
    events: $events,
    chunk_duration: 10,
    seed: 1
  }' > "$LVS_REQUEST"

# Execute exactly once; retain the complete response for parsing or diagnosis.
LVS_HTTP_CODE=$(curl -sS --max-time 300 -o "$LVS_RESPONSE" -w '%{http_code}' \
  -X POST "${LVS_BASE}/v1/summarize" \
  -H "Content-Type: application/json" \
  --data-binary "@$LVS_REQUEST")
```

Do not repeat this POST when `$LVS_HTTP_CODE` is non-2xx or the body is empty.
Inspect `$LVS_RESPONSE` and service logs instead.

Omit `objects_of_interest` if the user did not provide any. Include it as a
JSON array otherwise. Also omit the frame sampling fields in the standard
workflow so RT-VLM uses the model-specific deployment default. The deprecated
`num_frames_per_chunk` field must not be used.

**Response shape:** OpenAI-style envelope. `choices[0].message.content` is a
**JSON string** — parse it to get the actual summary and event list.

```bash
jq '{
  usage: (.usage // {}),
  result: (.choices[0].message.content | fromjson | {video_summary, events})
}' "$LVS_RESPONSE"
```

When both result fields are empty, report whether
`usage.total_chunks_processed` is positive. Zero or missing usage does not
prove that LVS processed the media; do not describe that case as "no events
detected."
