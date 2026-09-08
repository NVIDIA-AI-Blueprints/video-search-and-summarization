# Video analysis report (Mode A)

Numbered steps for Mode A, loaded on demand from [`SKILL.md`](../../SKILL.md) (`$SKILL_DIR/SKILL.md`); routing, gates and the shared setup live there and the skeleton is defined in `SKILL.md` § Report types.

- **Trigger phrases:** `SKILL.md` § Examples — the Mode A rows.
- **Prerequisite gate:** `SKILL.md` § Runtime prerequisites — the *Mode-by-mode checklist* row(s) for Mode A and the long-video hard gate; apply `SKILL.md` § HITL prompt mode before Step 3.
- **Inputs to resolve:** Step 1 below.
- **Template:** Step 4 below.
- **Output contract:** `SKILL.md` § Instructions → *Output contract for evaluators*, the Mode A lines.
- **Failure modes:** `SKILL.md` § Error Handling, plus the rules stated in the steps below.

---

## Mode A — Report on a recorded video clip

**If the VSS `lvs` profile is deployed** — probe LVS readiness, then hand off:

```bash
# Kubernetes public Exact path when VSS_PUBLIC_URL is set; Docker host port otherwise.
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  _lvs_ready="${VSS_PUBLIC_URL%/}/lvs/v1/ready"
else
  _lvs_ready="http://${HOST_IP}:38111/v1/ready"
fi
curl -sf --max-time 5 "${_lvs_ready}" >/dev/null
```

When that returns HTTP 200, run `/vss-summarize-video` to produce the summary,
then paste its output into the report template in Step 4 and skip Steps 1–3
(the VLM-direct path). Run Steps 1–3 only when `/v1/ready` is non-200.

### Step 1 — Resolve Mode A input (A1 clip URL or A2 local-file/base64)

Choose one path:

#### A1 — VST clip URL path

Hand off to `/vss-manage-video-io-storage` to:

1. List sensors and confirm the named `<sensor-id>` exists (upload first if not).
2. Fetch `/storage/<streamId>/timelines` for the recorded range when the user did not supply `startTime` / `endTime`.
3. Request a clip URL:

   ```bash
   # Resolves the sensor by name, mints the clip URL, normalises it, and warms the render.
   # Omit the window to take the whole recorded segment; the response echoes what it resolved.
   # CLI bootstrap and exit codes: AGENTS.md at the repo root
   VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
   VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
   VSS_ORIGIN="${VSS_PUBLIC_URL:-http://${HOST_IP:-localhost}:7777}"
   "${VSS[@]}" configure --base-url "${VSS_ORIGIN%/}"   # once per deployment

   # Captured, not piped: `vss ... | jq` hides the CLI's exit code behind jq's,
   # so a failed command with empty stdout reads as an empty answer.
   CLIP=$("${VSS[@]}" vios clip --sensor <sensor-name> [--start-time <startTime> --end-time <endTime>]) || {
     echo "vss vios clip failed for <sensor-name>" >&2; exit 1; }
   VIDEO_URL=$(printf '%s' "${CLIP}" | jq -r .media_url)
   ```

The block sets `VIDEO_URL` (used by the VLM in Step 3). Also set `RAW_URL="$VIDEO_URL"` before applying the report-link rewrite for Step 4.

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

| Backend | Discovery input | Typical host endpoint | Picked when |
|---|---|---|---|
| **Public Ingress RT-VLM** | `VSS_PUBLIC_URL` / `VLM_ENDPOINT` | `${VSS_PUBLIC_URL}/rtvi-vlm/v1` | Kubernetes / Helm, any profile, when `VSS_PUBLIC_URL` is set (preferred) |
| **NIM Cosmos** | Explicit `VLM_ENDPOINT`, or successful `/models` probe | `http://${HOST_IP}:30082/v1` | Docker: port 30082 responds with at least one model |
| **RT-VLM Cosmos** | Explicit `VLM_ENDPOINT`, or successful `/models` probe | `http://${HOST_IP}:8018/v1` | Docker: port 8018 responds with at least one model |

If the user already supplied a `VLM_ENDPOINT` + model id, use those directly.

When `VSS_PUBLIC_URL` is set and `VLM_ENDPOINT` is still empty, use the public
Ingress RT-VLM route (do **not** probe `/vlm/v1`):

```bash
if [ -z "${VLM_ENDPOINT:-}" ] && [ -n "${VSS_PUBLIC_URL:-}" ]; then
  VLM_ENDPOINT="${VSS_PUBLIC_URL%/}/rtvi-vlm/v1"
  VLM_BACKEND="rtvlm"
fi
```

Otherwise, on **Docker only**, probe the standard host endpoints directly,
following the same endpoint-selection contract as `/vss-ask-video`.

```bash
if [ -z "${VLM_ENDPOINT:-}" ] && [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
  for _candidate in \
    "nim_cosmos|http://${HOST_IP}:30082/v1" \
    "rtvlm|http://${HOST_IP}:8018/v1"; do
    _backend="${_candidate%%|*}"
    _endpoint="${_candidate#*|}"
    if _models="$(curl -sf --max-time 5 "${_endpoint}/models")" &&
       _model="$(printf '%s' "${_models}" | jq -er '.data[0].id')"; then
      VLM_BACKEND="${_backend}"
      VLM_ENDPOINT="${_endpoint}"
      VLM_MODEL="${VLM_MODEL:-$_model}"
      break
    fi
  done
fi

[ -n "${VLM_ENDPOINT:-}" ] || {
  echo "ERROR: no VLM found on ${HOST_IP}:30082 or ${HOST_IP}:8018; provide VLM_ENDPOINT and VLM_MODEL" >&2
  exit 1
}
```

Probe `/v1/models` before sending a chat request to confirm the chosen endpoint is alive and the model is loaded:

```bash
_models="$(curl -sf --max-time 5 "${VLM_ENDPOINT}/models")" || {
  echo "ERROR: VLM endpoint is not reachable: ${VLM_ENDPOINT}" >&2
  exit 1
}
printf '%s' "${_models}" | jq -er '.data[].id'
[ -n "${VLM_MODEL:-}" ] ||
  VLM_MODEL="$(printf '%s' "${_models}" | jq -er '.data[0].id')"
```

If `VLM_MODEL` is empty, adopt the first id the endpoint advertises. If the probe fails or the listed ids don't include `${VLM_MODEL}`, either:
- try a discovered fallback endpoint, or
- ask the user to choose one of the *VLM selection when unclear* options (SKILL.md).

Never silently pick an unknown model.

### Step 3 — Call the VLM directly

Use the OpenAI-compatible `chat/completions` endpoint with a `video_url` content block — the same payload shape **and multimodal settings** `video_understanding` builds in `src/vss_agents/tools/video_understanding.py` (`_build_vlm_messages` + the Cosmos `base_vlm.bind(...)` call).

Use explicit `VIDEO_UNDERSTANDING_*` overrides when supplied; otherwise use
the base-profile defaults (`max_fps=2`, `max_frames=30`, `min_pixels=3136`,
`max_pixels=8388608`).

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

# FINAL_PROMPT must come from the resolved HITL mode gate (SKILL.md § HITL prompt mode).
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

# If Step 3 is run standalone, derive a missing backend from endpoint/model.
[ -z "${VLM_BACKEND:-}" ] && {
  if [[ "${VLM_ENDPOINT:-}" == *":8018/"* ]]; then
    VLM_BACKEND="rtvlm"
  elif [[ "${VLM_MODEL:-}" == nvidia/cosmos* ]]; then
    VLM_BACKEND="nim_cosmos"
  else
    VLM_BACKEND="rtvlm"
  fi
}

# Multimodal settings — explicit overrides or base-profile defaults.
CFG_JSON='{"max_fps":2,"max_frames":30,"min_pixels":3136,"max_pixels":8388608}'

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

Load the matching template from [`$SKILL_DIR/references/report-templates/video-analysis-report.md`](../report-templates/video-analysis-report.md). Treat the template as read-only — copy its structure **verbatim**, keeping its exact headings and `## Basic Information` pipe-table, and fill every placeholder. Fill all placeholders before returning markdown. Never leave template instructions, placeholder tokens (e.g. `<BROWSER_CLIP_URL>`, `<sensor_id>`, `<YYYY-MM-DD>`), or internal-only URLs in user output. Before rendering, verify `BROWSER_CLIP_URL` is set and non-empty, then replace `<BROWSER_CLIP_URL>` with that exact value in the `Clip URL` row. Never use the raw `HOST_IP:30888` URL.
