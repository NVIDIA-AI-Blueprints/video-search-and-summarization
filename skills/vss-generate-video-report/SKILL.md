---
name: vss-generate-video-report
description: Use this skill when producing a VSS analysis report — Mode A per-clip VLM, Mode B incident-range via video-analytics. Not for standalone video summarization, real-time alerts or ad-hoc Q&A.
license: Apache-2.0
metadata:
  version: "3.2.9"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# Report

Generate a video analysis report by routing to one of two backends — **never via** `POST /generate` on the VSS agent.

| Mode | Backend |
|---|---|
| **A. Video clip** | `A1` `/vss-manage-video-io-storage` → clip URL → **VLM chat/completions** OR `A2` local video file on disk or base64 video + explicit VLM endpoint |
| **B. Incident range** | `/vss-query-analytics` → incident list → narrative report |

If the request is ambiguous (e.g. "report on `<sensor>`" with no time range and no incident wording), default to **Mode A**. Ask only if the user mentions both a sensor and a time range. See **Examples** below for the request phrasings that route to each mode.

---

## Instructions

0. **Set `SKILL_DIR`** to the "Base directory for this skill" path announced when this skill loads. All skill-relative reads (e.g. the default VLM prompt) resolve under `$SKILL_DIR` — never via cwd-relative paths.
1. **Pick the mode** — Mode A for a single recorded clip/sensor video, Mode B when the request names a time range or incidents/alerts (match against *Examples*).
2. **Verify runtime prerequisites** for that mode under *Runtime prerequisites*; hand off to `/vss-deploy-profile` only when the required local services are needed and missing.
3. **Apply HITL mode** under *HITL prompt mode (legacy runtime flag)* before Mode A Step 3. (Mode B has no prompt-approval step.)
4. **Run that mode's numbered steps** — *Mode A* or *Mode B* below.
5. **Rewrite every user-facing clip URL** with the `$VSS_PUBLIC_HOST:$VSS_PUBLIC_PORT` one-liner (*Browser-playable clip URL*) before embedding it in the report.
6. **Return the rendered report markdown** to the user.

Output contract for evaluators:
- Mode A top title MUST be exactly `# Video Analysis Report`.
- Mode A MUST include `## Basic Information` followed by a pipe-table (`Field | Value`) with the exact required rows from the template: Report Identifier, Date of Analysis, Time of Analysis, Video Source, Clip Range, Clip URL, VLM, Analysis Request — every row filled with concrete values.
- Mode A MUST include `## Analysis Results` containing the VLM caption/summary (with any `<think>…</think>` block stripped).
- Mode B top title MUST be exactly `# Incident Range Report` (never `# Incident Report` or sensor-named variants).
- Mode B MUST include `## Basic Information` with the exact required rows from the template (Report Identifier, Range, Scope, Total Incidents, Confirmed / Rejected / Unverified).
- Mode B MUST use heading level `#` for the top title. Do not use `## Incident Report`, `## Incident Range Report`, or any alternate wording.
- Mode B empty-range output MUST be exactly one plain-text line (no markdown heading/table/list/extra lines) in this format:
  `No incidents found for scope <scope> in range <start_time> to <end_time>.`

---

## Examples

- "Generate a report for this video" / "report on `<sensor-id>`" → **Mode A**
- "Analyze warehouse_01.mp4" / "create an analysis report on the uploaded video" → **Mode A**
- "Report on incidents from 12:31Z to 12:32Z" → **Mode B**
- "Report on alerts today" / "what incidents happened on `<sensor>` last hour" → **Mode B**
- "Summarize alerts on `<sensor>` between `<t1>` and `<t2>`" → **Mode B**

---

## Negative Triggers

Do **not** use this skill when the request is one of the following:

- Ad-hoc visual Q&A on a clip that do not ask explicitly for a report ("what color is the truck?", "what happens at 00:12?") → use `/vss-ask-video`.
- Archive/semantic similarity retrieval ("find forklifts", "search all videos for tailgating") → use `/vss-search-archive`.
- Read-only incident/metrics lookup without report rendering needs → use `/vss-query-analytics`.
- Deploy/teardown/profile changes ("deploy alerts", "switch profile", "bring up base") → use `/vss-deploy-profile`.
- Real-time alert/rule management requests → use `/vss-manage-alerts`.

Never route reports through VSS-agent `POST /generate`.

---

## Runtime prerequisites

This skill is profile-agnostic for Mode A. A specific profile does **not** have to be pre-deployed as long as the chosen Mode A input path and VLM path are available.

### Mode-by-mode checklist (required)

| Mode / Path | User must provide | Services that must be reachable | Storage/location requirement | Not required |
|---|---|---|---|---|
| **Mode A / A1 (VIOS clip URL)** | sensor and/or clip time range | VIOS + VLM endpoint | Clip is fetched from VIOS timeline/URL APIs | VA-MCP analytics |
| **Mode A / A2 (local file or base64)** | local `VIDEO_FILE` path **or** `VIDEO_BASE64`, plus explicit VLM endpoint/model | VLM endpoint only | For `VIDEO_FILE`, file must exist on the same machine/container filesystem where OpenClaw/agent executes and be readable by that process | VIOS, VA-MCP analytics |
| **Mode B (incident range)** | `start_time` / `end_time` (and optional sensor scope) | VA-MCP analytics (`/vss-query-analytics` + `video_analytics__get_incidents`) | Incident data must already exist in analytics backend for requested range/scope | VIOS, direct VLM path |

Hard gate behavior:
- If required services for the chosen row are not reachable, stop and report the missing dependency.
- Do not silently switch modes because a dependency is missing.
- Offer `/vss-deploy-profile` only after user confirmation.

Probe examples:

```bash
# Mode A path A1 — VIOS reachable
curl -sf --max-time 5 "http://${HOST_IP}:30888/vst/api/v1/sensor/version" >/dev/null

# Mode B — VA-MCP reachable
curl -sf --max-time 5 "http://${HOST_IP}:9901/" >/dev/null
```

If required local services are missing and the user wants local deployment, hand off to `/vss-deploy-profile` (typically `-p base` for Mode A path A1, `-p alerts` for Mode B). **Always** confirm deploy with the user first.

---

## VLM selection when unclear

If VLM/deployment choice is unclear and no default selection has been made, ask the user what VLM to use with these options:

1. **Provide an endpoint** — user supplies `VLM_ENDPOINT` and model id.
2. **Suggest options based on auto-discover** — inspect running `vss-agent` env and probe default local ports.
3. **Deploy a local VLM** — hand off to `/vss-deploy-profile` (with user confirmation) and then continue.

Auto-discover hints:

```bash
# From running vss-agent env (when present)
docker exec vss-agent sh -lc '
for k in HOST_IP VLM_MODE VLM_MODEL_TYPE VLM_BASE_URL VLM_NAME RTVI_VLM_BASE_URL RTVI_VLM_MODEL_TO_USE; do
  v="$(printenv "$k")"
  [ -n "$v" ] && printf "%s=%s\n" "$k" "$v"
done
'

# Probe common local endpoints
curl -sf --max-time 5 "http://${HOST_IP}:30082/v1/models" | jq -r '.data[].id'   # base RT-VLM default
curl -sf --max-time 5 "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'    # alerts RT-VLM default
```

---

## HITL prompt mode (runtime-first, harness fallback)

Resolve HITL mode for **Mode A only** in this order:

1. Runtime config `video_report_gen.hitl_enabled` (legacy VSS source of truth)
2. Harness override `HITL_ENABLED=true|false` (fallback only when runtime config is unavailable)
3. If neither source is set, default to `false`

Behavior:

- resolved `false`: do not ask clarification; run Mode A with the current default prompt.
- resolved `true`: before Mode A Step 3, show the current prompt and ask the user to choose one of:
  - `APPROVE` — use the current prompt as-is.
  - `EDIT: <instructions>` — apply edits to the current prompt and show the revised prompt.
  - `NEW: <full prompt>` — replace with a brand-new prompt.

Guardrails (required):
- Do **not** treat `yes`, `confirm`, `ok`, or whitespace-only text as approval.
- Do **not** wait for an empty-string confirmation.
- Keep showing the same three choices (`APPROVE | EDIT: ... | NEW: ...`) after **every** `EDIT` or `NEW` response.
- Do not run report generation until the user explicitly responds with `APPROVE`.
- If the response is ambiguous, re-prompt with explicit `APPROVE | EDIT: ... | NEW: ...` options and continue the loop.
- If Step 3 resolves HITL via rule (3) (neither runtime nor fallback is set), include this note on the first report generation response in the session:
  `HITL mode not set; defaulting to off. Set HITL_ENABLED=true to enable HITL.`

---

## Clip URLs: VLM input vs browser report link

A clip URL has **three** distinct roles. Keep them separate:

| Role | Which form | Rule |
|---|---|---|
| **Service-facing** — what you POST to LVS `/v1/summarize` or to a local/in-cluster VLM `video_url` block | exactly what VST returned | never rewrite |
| **Agent-verification** — you checking the clip exists | resolve locally if you must | never send the resolved form onward |
| **User-facing** — the `Clip URL` row in the rendered report | `BROWSER_CLIP_URL` | always rewrite |

VST returns clip URLs on an agent-internal host:port — usually `${HOST_IP}:30888`,
but in Compose deployments it is commonly the **Docker network alias**
`http://vst-ingress:30888/...`. That alias is resolvable from LVS / RT-VLM / the
VLM NIM, which share the Compose network, and is **not** resolvable from the
agent's own shell. That is expected and is **not** a reason to rewrite it.

Keep the original URL as `VIDEO_URL` for local / in-cluster VLM frame pulls and
as the `url` field of a `/v1/summarize` request. Do **not** rewrite the
service-facing URL — not to make it browser-playable, and not to make it
resolvable from your shell. Substituting a container IP or `HOST_IP` for
`vst-ingress` can leave the consuming service unable to fetch the media: it will
still answer `HTTP 200` while reporting `total_chunks_processed: 0` and an empty
`video_summary`.

If you need to confirm the clip yourself, resolve the alias locally for that check
only (e.g. `docker exec` inside the Compose network, or map the alias to its
container IP) and still send the original URL to the service.

Only create `BROWSER_CLIP_URL` for URLs shown in the rendered report. The
deploy layer exports the browser-facing host:port as `$VSS_PUBLIC_HOST` /
`$VSS_PUBLIC_PORT` (and scheme as `$VSS_PUBLIC_HTTP_PROTOCOL`) in every
profile `.env` — Brev or bare-metal — so the report-link rewrite is:

```bash
: "${VSS_PUBLIC_HOST:?Set VSS_PUBLIC_HOST before rewriting clip URLs}"
: "${VSS_PUBLIC_PORT:?Set VSS_PUBLIC_PORT before rewriting clip URLs}"
VSS_PUBLIC_HTTP_PROTOCOL="${VSS_PUBLIC_HTTP_PROTOCOL:-http}"
BROWSER_CLIP_URL=$(echo "$RAW_URL" | sed -E "s|^https?://[^/]+|${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}|")
```

If either required public host value is missing, omit the report-facing clip
link and call out that a browser-playable URL could not be produced; do not
block the local VLM analysis path. Apply the rewrite to **every clip URL
surfaced in the rendered report** (Mode A Step 4 Clip URL row; Mode B
per-incident clip sub-bullet). Leave the VLM `video_url` content block in Mode A
Step 3 on the original internal URL when the VLM is local / in-cluster.

---

## Mode A — Report on a recorded video clip

**If the VSS `lvs` profile is deployed** — `curl -sf --max-time 5 "http://${HOST_IP}:38111/v1/ready"` returns HTTP 200 — run `/vss-summarize-video` to produce the summary, then paste its output into the report template in Step 4 and skip Steps 1–3 (the VLM-direct path). Run Steps 1–3 only when `/v1/ready` is non-200.

On this LVS path you still resolve a clip URL (Step 1's A1 lookup) because
`/v1/summarize` needs it. Send that URL to LVS **exactly as VST returned it**
(see § Clip URLs — do not substitute a container IP for `vst-ingress`), and bind
`RAW_URL="$VIDEO_URL"` before Step 4 so the report-link rewrite still produces
`BROWSER_CLIP_URL`. Skipping Steps 1–3 must not mean skipping that binding —
leaving `RAW_URL` unset is what renders the report's `Clip URL` row as `N/A`.

If LVS answers `HTTP 200` but the body reports `total_chunks_processed: 0` or an
empty `video_summary`, it did not process the media — say so explicitly in the
report instead of silently falling back to the direct-VLM path, and check the
clip URL you sent was the unrewritten service-facing form.

### Step 1 — Resolve Mode A input (A1 clip URL or A2 local-file/base64)

Choose one path:

#### A1 — VST clip URL path

Hand off to `/vss-manage-video-io-storage` to:

1. List sensors and confirm the named `<sensor-id>` exists (upload first if not).
2. Fetch `/storage/<streamId>/timelines` for the recorded range when the user did not supply `startTime` / `endTime`.
3. Request a clip URL:

   ```bash
   curl -s "http://${HOST_IP}:30888/vst/api/v1/storage/file/<streamId>/url?startTime=<startTime>&endTime=<endTime>&container=mp4&disableAudio=true" | jq -r .videoUrl
   ```

Bind it to `VIDEO_URL` (used by the VLM in Step 3) and set `RAW_URL="$VIDEO_URL"` before applying the report-link rewrite for Step 4.

Remote VLM reachability guard (required):
- If the selected `VLM_ENDPOINT` is remote/non-local, do not assume it can fetch `VIDEO_URL` when `VIDEO_URL` points to localhost/private VST addresses (for example `127.0.0.1`, `localhost`, `HOST_IP`, `172.16-31.x`, `192.168.x`, `10.x`, or in-cluster/internal DNS).
- Before Step 3, explicitly warn and stop when this mismatch exists: remote VLM + internal-only `VIDEO_URL`.
- In that case, ask the user to choose one of:
  1. Use a local/in-cluster VLM endpoint that can reach VST internal URLs.
  2. Switch to Mode A A2 and send local-file/base64 bytes to the remote VLM.
  3. Expose a browser/publicly reachable clip URL and confirm the remote VLM can fetch it.

#### A2 — Local file on disk or base64 video path (no VST dependency)

If the user provides either:
- a local video file path on disk (where OpenClaw/agent is running), or
- a base64 video payload,
and a VLM endpoint, use that directly in Step 3.

Local file requirement (strict):
- `VIDEO_FILE` must point to a path that is directly readable from the runtime executing this skill (OpenClaw/agent host or container).
- The path cannot be browser-only client storage.
- If the file is only on a user's laptop/browser session and not on the runtime filesystem, ask the user to place it on the runtime disk (or provide base64 instead).

Bind:
- `VIDEO_FILE` = user-provided local path (if using file path input)
- `VIDEO_BASE64` = base64 bytes (if using base64 input; no data-uri prefix)
- `VIDEO_MIME` = `video/mp4` unless user provided another valid mime type
- `VIDEO_DATA_URL` = `"data:${VIDEO_MIME};base64,${VIDEO_BASE64}"` (used by Step 3 when sending inline bytes)

If `VIDEO_FILE` is provided, read/encode it at runtime to produce `VIDEO_BASE64`; do not paste raw base64 into chat output.

For this path, set report `Clip URL` row to `N/A (local/base64 input)` unless a public playback URL is also available.

#### Long-video rule (required)

If user input video/clip duration is **120 seconds (2 mins) or longer**, stop Mode A direct path and prompt:
- deploy and use **LVS** via `/vss-deploy-profile` + `/vss-summarize-video`,
- then continue report templating with LVS output.

Do not continue direct VLM Mode A on videos that are 120 seconds or longer.

### Step 2 — Resolve VLM endpoint and model

The deploy may serve the VLM through either of two stacks. Both expose an OpenAI-compatible `chat/completions` API — pick whichever is live:

| Backend | Env vars | Typical host endpoint | Picked when |
|---|---|---|---|
| **NIM Cosmos** | `VLM_BASE_URL`, `VLM_NAME`, `VLM_MODE`, `VLM_MODEL_TYPE` | `${VLM_BASE_URL}/v1` (no trailing `/v1` on the env var; the agent appends it) | `VLM_MODEL_TYPE != rtvi` **and** `VLM_MODE` ∈ {`local`, `local_shared`, `remote`} **and** `VLM_BASE_URL` is non-empty |
| **RT-VLM Cosmos** | `RTVI_VLM_BASE_URL`, `RTVI_VLM_MODEL_TO_USE`, `VLM_MODEL_TYPE` | `${RTVI_VLM_BASE_URL}/v1` — if unset, derive from `${HOST_IP}` (`http://${HOST_IP}:8018/v1` for alerts, `http://${HOST_IP}:30082/v1` for base) | `VLM_MODEL_TYPE = rtvi`, or `VLM_MODE=none`, or `VLM_BASE_URL` empty; also the only path for `warehouse` |

If the user already supplied a `VLM_ENDPOINT` + model id, use those directly.

Otherwise, read the live values off a running `vss-agent` container (when present) and do not guess:

```bash
docker exec vss-agent sh -lc '
for k in HOST_IP VLM_MODE VLM_MODEL_TYPE VLM_BASE_URL VLM_NAME RTVI_VLM_BASE_URL RTVI_VLM_MODEL_TO_USE; do
  v="$(printenv "$k")"
  [ -n "$v" ] && printf "%s=%s\n" "$k" "$v"
done
'
```

Do not require `RTVI_VLM_ENDPOINT` from `vss-agent` env; several profiles do not inject it.

Selection rule:

```bash
if [ "${VLM_MODEL_TYPE:-}" = "rtvi" ]; then
  VLM_BACKEND="rtvlm"
  VLM_ENDPOINT="${RTVI_VLM_BASE_URL:+${RTVI_VLM_BASE_URL%/}/v1}"
  [ -z "${VLM_ENDPOINT}" ] && VLM_ENDPOINT="http://${HOST_IP}:8018/v1"   # alerts default
  VLM_MODEL="${RTVI_VLM_MODEL_TO_USE}"
elif [ -n "${VLM_BASE_URL}" ] && [ "${VLM_MODE}" != "none" ]; then
  VLM_BACKEND="nim_cosmos"
  VLM_ENDPOINT="${VLM_BASE_URL%/}/v1"
  VLM_MODEL="${VLM_NAME}"
else
  VLM_BACKEND="rtvlm"
  VLM_ENDPOINT="${RTVI_VLM_BASE_URL:+${RTVI_VLM_BASE_URL%/}/v1}"
  [ -z "${VLM_ENDPOINT}" ] && VLM_ENDPOINT="http://${HOST_IP}:30082/v1"  # base default
  VLM_MODEL="${RTVI_VLM_MODEL_TO_USE}"
fi
```

Probe `/v1/models` before sending a chat request to confirm the chosen endpoint is alive and the model is loaded:

```bash
curl -sf --max-time 5 "${VLM_ENDPOINT}/models" | jq -r '.data[].id'
```

If the probe fails or the listed ids don't include `${VLM_MODEL}`, either:
- try a discovered fallback endpoint, or
- ask the user to choose one of the three *VLM selection when unclear* options.

Never silently pick an unknown model.

### Step 3 — Call the VLM directly

Use the OpenAI-compatible `chat/completions` endpoint with a `video_url` content block — the same payload shape **and multimodal settings** `video_understanding` builds in `src/vss_agents/tools/video_understanding.py` (`_build_vlm_messages` + the Cosmos `base_vlm.bind(...)` call).

The frame sampling and visual-token (pixel) budget must mirror the **live** `video_understanding` settings for the active profile when `vss-agent` is running. **Send `mm_processor_kwargs` and `media_io_kwargs`** so the direct call uses the same frame sampling and pixel budget as the in-agent `video_understanding` tool — omitting them lets the VLM apply its own defaults, so the output diverges from the agent path.

When `vss-agent` is absent (Mode A2 / profile-agnostic), fall back to base-profile defaults (`max_fps=2`, `max_frames=30`, `min_pixels=3136`, `max_pixels=8388608`) or explicit `VIDEO_UNDERSTANDING_*` env overrides — do not hard-fail.

```bash
# Default prompt — load from the skill tree (do NOT use a cwd-relative path).
# Set SKILL_DIR to the "Base directory for this skill" announced when this skill loads.
: "${SKILL_DIR:?Set SKILL_DIR to the loaded skill's base directory (from the skill loader)}"
PROMPT_FILE="$SKILL_DIR/references/default-vlm-prompt.md"
[ -s "$PROMPT_FILE" ] || {
  echo "ERROR: missing or empty VLM prompt file: $PROMPT_FILE" >&2
  exit 1
}
DEFAULT_PROMPT="$(cat "$PROMPT_FILE")"
[ -n "$DEFAULT_PROMPT" ] || {
  echo "ERROR: DEFAULT_PROMPT is empty after reading $PROMPT_FILE" >&2
  exit 1
}

# FINAL_PROMPT must come from the resolved HITL mode gate above.
# Resolution order:
#   1) video_report_gen.hitl_enabled
#   2) HITL_ENABLED (fallback only when runtime config is unavailable)
#   3) default false when neither source is set
# - resolved false: FINAL_PROMPT="$DEFAULT_PROMPT"
# - resolved true : FINAL_PROMPT comes from the latest EDIT/NEW value after explicit APPROVE.
FINAL_PROMPT="${FINAL_PROMPT:-$DEFAULT_PROMPT}"
[ -n "$FINAL_PROMPT" ] || { echo "ERROR: FINAL_PROMPT is empty; refusing to call VLM with a blank prompt" >&2; exit 1; }
PROMPT="$FINAL_PROMPT"

# Reasoning is OFF by default — matches the base-profile video_understanding config (`reasoning: false`).
# video_understanding.py uses config.reasoning unless the caller overrides it, so default to non-reasoning.
# Append the Cosmos Reason 2 reasoning suffix ONLY when the user explicitly asks for reasoning
# (drop it for non-cosmos-reason2 VLMs). With reasoning off, the response has no <think> block.
if [ "${REASONING:-false}" = "true" ]; then
PROMPT="${PROMPT}

Answer the question using the following format:

<think>
Your reasoning.
</think>

Write your final answer immediately after the </think> tag."
fi

# If Step 3 is run standalone, derive missing backend from current env/model.
[ -z "${VLM_BACKEND:-}" ] && {
  if [ "${VLM_MODEL_TYPE:-}" = "rtvi" ]; then
    VLM_BACKEND="rtvlm"
  elif [[ "${VLM_MODEL:-}" == nvidia/cosmos* ]]; then
    VLM_BACKEND="nim_cosmos"
  else
    VLM_BACKEND="rtvlm"
  fi
}

# Multimodal settings — prefer live vss-agent config; fall back when container absent (Mode A2).
CFG_JSON=""
if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx vss-agent; then
  CFG_JSON=$(
    docker exec vss-agent python3 -c '
import json, os, yaml
p = os.getenv("VSS_AGENT_CONFIG_FILE")
if not p:
    raise SystemExit("VSS_AGENT_CONFIG_FILE is not set in vss-agent")
if not os.path.isabs(p):
    p = os.path.join("/vss-agent", p.lstrip("./"))
with open(p, encoding="utf-8") as f:
    cfg = yaml.safe_load(f) or {}
vu = (cfg.get("functions", {}) or {}).get("video_understanding", {}) or {}
print(json.dumps({
    "max_fps": int(vu.get("max_fps", 2)),
    "max_frames": int(vu.get("max_frames", 30)),
    "min_pixels": int(vu.get("min_pixels", 3136)),
    "max_pixels": int(vu.get("max_pixels", 8388608)),
}))
' 2>/dev/null
  ) || true
fi

if [ -z "${CFG_JSON}" ]; then
  echo "WARN: vss-agent unavailable; using base-profile video_understanding defaults (override with VIDEO_UNDERSTANDING_MAX_FPS, VIDEO_UNDERSTANDING_MAX_FRAMES, VIDEO_UNDERSTANDING_MIN_PIXELS, VIDEO_UNDERSTANDING_MAX_PIXELS)" >&2
  CFG_JSON='{"max_fps":2,"max_frames":30,"min_pixels":3136,"max_pixels":8388608}'
fi

printf '%s' "${CFG_JSON}" | jq -e . >/dev/null || { echo "Invalid video_understanding config JSON"; exit 1; }
MAX_FPS="$(printf '%s' "${CFG_JSON}" | jq -r '.max_fps')"
MAX_FRAMES="$(printf '%s' "${CFG_JSON}" | jq -r '.max_frames')"
MIN_PIXELS="$(printf '%s' "${CFG_JSON}" | jq -r '.min_pixels')"
MAX_PIXELS="$(printf '%s' "${CFG_JSON}" | jq -r '.max_pixels')"
MAX_FPS="${VIDEO_UNDERSTANDING_MAX_FPS:-$MAX_FPS}"
MAX_FRAMES="${VIDEO_UNDERSTANDING_MAX_FRAMES:-$MAX_FRAMES}"
MIN_PIXELS="${VIDEO_UNDERSTANDING_MIN_PIXELS:-$MIN_PIXELS}"
MAX_PIXELS="${VIDEO_UNDERSTANDING_MAX_PIXELS:-$MAX_PIXELS}"

# num_frames = min(int(clip_seconds) * max_fps, max_frames), min 1 — matches video_understanding.py.
# clip_seconds (Step 1 endTime-startTime) may be fractional; truncate to integer seconds — bash $((...))
# is integer-only and errors on "15.0"/"1.5". Default 15s -> caps at MAX_FRAMES.
CLIP_SECONDS=$(awk -v s="${CLIP_SECONDS:-15}" 'BEGIN{printf "%d", s}')
NUM_FRAMES=$(( CLIP_SECONDS * MAX_FPS ))
[ "$NUM_FRAMES" -gt "$MAX_FRAMES" ] && NUM_FRAMES=$MAX_FRAMES
[ "$NUM_FRAMES" -lt 1 ] && NUM_FRAMES=1

# Only apply Cosmos mm/media kwargs on the NIM Cosmos path.
# RT-VLM mode uses its own server-side preprocessing and should not receive these kwargs.
MM_KWARGS=""
if [ "${VLM_BACKEND}" = "nim_cosmos" ]; then
  case "$VLM_MODEL" in
    *cosmos-reason2*) MM_KWARGS=", \"mm_processor_kwargs\": {\"size\": {\"shortest_edge\": ${MIN_PIXELS}, \"longest_edge\": ${MAX_PIXELS}}}, \"media_io_kwargs\": {\"video\": {\"num_frames\": ${NUM_FRAMES}}}" ;;
    *cosmos*)         MM_KWARGS=", \"mm_processor_kwargs\": {\"videos_kwargs\": {\"min_pixels\": ${MIN_PIXELS}, \"max_pixels\": ${MAX_PIXELS}}}, \"media_io_kwargs\": {\"video\": {\"num_frames\": ${NUM_FRAMES}}}" ;;
    *)                      MM_KWARGS="" ;;
  esac
fi

curl -s --connect-timeout 5 --max-time 120 -X POST "${VLM_ENDPOINT}/chat/completions" \
  -H "Content-Type: application/json" \
  -d @- <<EOF | jq -r '.choices[0].message.content'
{
  "model": $(printf '%s' "${VLM_MODEL}" | jq -Rs .),
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": $(printf '%s' "${PROMPT}" | jq -Rs .)},
        {"type": "video_url", "video_url": {"url": $(printf '%s' "${VIDEO_URL}" | jq -Rs .)}}
      ]
    }
  ],
  "max_tokens": 1024,
  "temperature": 0.0${MM_KWARGS}
}
EOF
```

For Mode A path A2 when using inline bytes, run the same Step 3 preamble above (prompt resolution, `CFG_JSON`, `MM_KWARGS`), then send `VIDEO_DATA_URL` instead of `VIDEO_URL`:

```bash
curl -s --connect-timeout 5 --max-time 120 -X POST "${VLM_ENDPOINT}/chat/completions" \
  -H "Content-Type: application/json" \
  -d @- <<EOF | jq -r '.choices[0].message.content'
{
  "model": $(printf '%s' "${VLM_MODEL}" | jq -Rs .),
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": $(printf '%s' "${PROMPT}" | jq -Rs .)},
        {"type": "video_url", "video_url": {"url": $(printf '%s' "${VIDEO_DATA_URL}" | jq -Rs .)}}
      ]
    }
  ],
  "max_tokens": 1024,
  "temperature": 0.0${MM_KWARGS}
}
EOF
```

> The kwargs block is backend-aware: on `nim_cosmos`, Reason2 variants (`nvidia/cosmos-reason2*`) use `mm_processor_kwargs.size{shortest_edge,longest_edge}` and other NIM Cosmos variants (`nvidia/cosmos*`) use `mm_processor_kwargs.videos_kwargs{min_pixels,max_pixels}`; both also send `media_io_kwargs.video.num_frames`. On `rtvlm`, no Cosmos kwargs are sent.

If the VLM returns a `<think>…</think>` block (Cosmos Reason reasoning mode), keep only the text after `</think>` as the report body.

### Step 4 — Fill the Video Analysis Report template

Load the matching template from [`references/report-templates/video-analysis-report.md`](references/report-templates/video-analysis-report.md). Treat the template as read-only — copy its structure **verbatim**, keeping its exact headings and `## Basic Information` pipe-table, and fill every placeholder. Fill all placeholders before returning markdown. Never leave template instructions, placeholder tokens (e.g. `<BROWSER_CLIP_URL>`, `<sensor_id>`, `<YYYY-MM-DD>`), or internal-only URLs in user output. Before rendering, verify `BROWSER_CLIP_URL` is set and non-empty, then replace `<BROWSER_CLIP_URL>` with that exact value in the `Clip URL` row. Never use the raw `HOST_IP:30888` URL.

---

## Mode B — Report on incidents in a time range

### Step 1 — Resolve the time range and (optionally) sensor

- `start_time` / `end_time` must be ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SS.sssZ`). Resolve relative phrases ("last hour", "today") against the current host clock.
- If the user names a sensor, capture it as `source` + `source_type=sensor`. Otherwise leave both unset for an all-sensors query.

### Step 2 — Fetch incidents via `/vss-query-analytics`

Hand off to `/vss-query-analytics` (initialize → `tools/call`) with:

```json
{
  "jsonrpc": "2.0",
  "method": "tools/call",
  "params": {
    "name": "video_analytics__get_incidents",
    "arguments": {
      "source": "<sensor-id-or-omit>",
      "source_type": "sensor",
      "start_time": "<ISO>",
      "end_time": "<ISO>",
      "max_count": 100,
      "includes": ["objectIds", "info"]
    }
  },
  "id": 1
}
```

Read-only boundary (mandatory):
- Mode B is strictly read-only analytics retrieval. Never write, seed, backfill, or mutate Elasticsearch/VA data.
- Forbidden examples: indexing synthetic incidents, replaying fixture payloads into ES, calling write/update/delete APIs to "make data available" for the report.
- If no incidents exist for the requested range/scope, handle as empty results (see below); do not fabricate data.

For each incident keep: `id`, `sensorId`, `timestamp`, `end`, `category`, `place.name`, `info.verdict`, `info.reasoning`, `objectIds`, and the clip URL (commonly `info.clip_url`, `clip_url`, or whichever clip-pointer field the response carries). **Apply the `$VSS_PUBLIC_HOST:$VSS_PUBLIC_PORT` rewrite (see *Browser-playable clip URL* above) to every clip URL before pasting it into the report** — the raw value is a `HOST_IP:30888` URL the user's browser cannot reach.

### Step 3 — Fill the Incident Range Report template

Load the matching template from [`references/report-templates/incident-range-report.md`](references/report-templates/incident-range-report.md). Treat the template as read-only — copy its structure, then group by sensor (or by category if no sensor scope), tally verdicts, and list each incident with timestamp / category / verdict / reasoning. Fill all placeholders before returning markdown. Never leave template instructions, placeholder tokens, or internal-only URLs in user output. Every incident clip value must be a rewritten browser-playable URL; omit the clip line when the incident carries no clip URL.

For non-empty results, rendered output MUST start exactly with:
- `# Incident Range Report`
- `## Basic Information`
- a pipe table containing rows: `Report Identifier`, `Range`, `Scope`, `Total Incidents`, `Confirmed / Rejected / Unverified`

If `get_incidents` returns zero results, STOP and return exactly this one-line sentence shape (single line only):
`No incidents found for scope <scope> in range <start_time> to <end_time>.`

When zero results:
- Do not render `# Incident Range Report`.
- Do not render `## Basic Information`.
- Do not render any markdown table, bullets, or summary section.
- Do not invent incidents, do not seed test data, and do not fall back to Mode A.

---

## Error Handling

- If a probe, `curl`, VLM call, or `/vss-query-analytics` request fails, stop the workflow and report the failing endpoint, HTTP status or command error, and the next useful recovery step. Do not fabricate a report from partial or missing data.
- If the VLM response is empty, malformed, or contains only a reasoning block, surface that response problem and suggest checking model readiness/logs before retrying.
- If a clip URL cannot be rewritten to the public host/port, omit it from the rendered report and call out that the browser-playable URL could not be produced.
- For Mode B, treat missing optional incident fields (`info.reasoning`, `objectIds`, clip URL) as omissions in the report, but treat missing `id`, `timestamp`, or `category` as a data-quality error that should be reported.

---

## Cross-Reference

- **`/vss-manage-video-io-storage`** — sensor list, timelines, and clip URL for Mode A Step 1.
- **`/vss-query-analytics`** — incident retrieval (and verdict / reasoning enrichment) for Mode B Step 2.
- **`/vss-ask-video`** — ad-hoc VLM Q&A on a single clip (not a structured report).
- **`/vss-summarize-video`** — used by Mode A to produce the summary body when the `lvs` profile is deployed; the report template (Step 4) is still filled here.
- **`references/default-vlm-prompt.md`** — default Mode A VLM prompt (edit this file to change the prompt). Step 3 loads it via `$SKILL_DIR/references/default-vlm-prompt.md` and fails if missing or empty.

