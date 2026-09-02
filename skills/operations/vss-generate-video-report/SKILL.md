---
name: vss-generate-video-report
description: Use this skill when producing a VSS analysis report — Mode A per-clip VLM, Mode B incident-range via video-analytics, Mode C SOP compliance via the SOP tools. Not for standalone video summarization, real-time alerts or ad-hoc Q&A.
license: Apache-2.0
metadata:
  version: "3.3.0"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# Report

Generate a video analysis report by routing to one of three backends — **never via** `POST /generate` on the VSS agent.

| Mode | Backend |
|---|---|
| **A. Video clip** | `A1` `/vss-manage-video-io-storage` → clip URL → **VLM chat/completions** OR `A2` local video file on disk or base64 video + explicit VLM endpoint |
| **B. Incident range** | `/vss-query-analytics` → incident list → narrative report |
| **C. SOP compliance** | VA-MCP `get_sop_report` (direct MCP call on `${VA_MCP_URL}`) → SOP compliance report |

If the request is ambiguous (e.g. "report on `<sensor>`" with no time range and no incident wording), default to **Mode A**. Ask only if the user mentions both a sensor and a time range. See **Examples** below for the request phrasings that route to each mode.

---

## Instructions

0. **Set `SKILL_DIR`** to the "Base directory for this skill" path announced when this skill loads. All skill-relative reads (e.g. the default VLM prompt) resolve under `$SKILL_DIR` — never via cwd-relative paths.
1. **Pick the mode** — Mode A for a single recorded clip/sensor video, Mode B when the request names a time range or incidents/alerts, Mode C when the request asks for an SOP / compliance report (match against *Examples*).
2. **Verify runtime prerequisites** for that mode under *Runtime prerequisites*; hand off only when required services are missing (Mode A / B → `/vss-deploy-profile`; Mode C → `/vss-build-vision-ai` for the SOP tools).
3. **Apply HITL mode** under *HITL prompt mode (legacy runtime flag)* before Mode A Step 3. (Mode B and Mode C have no prompt-approval step.)
4. **Run that mode's numbered steps** — *Mode A*, *Mode B*, or *Mode C* below.
5. **Rewrite every user-facing clip URL** before embedding it in the report: prefer
   `VSS_PUBLIC_URL` origin rewrite on Kubernetes; fall back to
   `$VSS_PUBLIC_HOST:$VSS_PUBLIC_PORT` on Docker Compose (*Browser-playable clip URL*).
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
- Mode C top title MUST be exactly `# SOP Compliance Report`, with the template's Basic Information / Compliance Summary / SOP Violations sections.

---

## Examples

- "Generate a report for this video" / "report on `<sensor-id>`" → **Mode A**
- "Analyze warehouse_01.mp4" / "create an analysis report on the uploaded video" → **Mode A**
- "Report on incidents from 12:31Z to 12:32Z" → **Mode B**
- "Report on alerts today" / "what incidents happened on `<sensor>` last hour" → **Mode B**
- "Summarize alerts on `<sensor>` between `<t1>` and `<t2>`" → **Mode B**
- "Generate an SOP compliance report for `<sensor>` from `<t1>` to `<t2>`" / "compliance report on `<sensor>` last hour" / "SOP status report for `<sensor>`" → **Mode C**

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
**Mode C** needs a **VA-MCP that exposes the SOP tools** (`get_sop_*`) over Elasticsearch `mdx-vlm-captions-*` — deployed by the SOP profile (compose via `/vss-build-vision-ai`; see `skills/vss-build-vision-ai/references/services/sop.md` § Patch specifics).

### Endpoint resolution (Kubernetes vs Docker)

When operating against a deployed VSS stack (**base**, **lvs**, or **alerts** on
Helm), resolve public endpoints once. Follow
[`../vss-build-vision-ai/references/deployment_resolution.md`](../../vss-build-vision-ai/references/deployment_resolution.md):

```bash
VSS_CAPABILITY_RECEIPT="${HOME}/.vss/agent-capabilities.json"
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -f "$VSS_CAPABILITY_RECEIPT" ]; then
  VSS_RECEIPT_ORIGIN=$(jq -er \
    '(.vss_origin // "") | select(type == "string")' \
    "$VSS_CAPABILITY_RECEIPT") || exit 1
  [ -z "$VSS_RECEIPT_ORIGIN" ] || VSS_PUBLIC_URL="$VSS_RECEIPT_ORIGIN"
fi
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  DEPLOYMENT_KIND="kubernetes"
  VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
  VSS_VIOS_URL="${VSS_PUBLIC_URL}/vst"
  VST_API_BASE="${VSS_VIOS_URL}/api/v1"
  # RT-VLM is at /rtvi-vlm on every profile; nothing is mounted at the origin /v1.
  : "${VLM_ENDPOINT:=${VSS_PUBLIC_URL}/rtvi-vlm/v1}"
  # Alerts / Mode B — force public VA-MCP; ignore leftover Docker :9901.
  VA_MCP_URL="${VSS_PUBLIC_URL}/va-mcp"
else
  DEPLOYMENT_KIND="docker"
  VSS_VIOS_URL="http://${HOST_IP}:30888/vst"
  VST_API_BASE="${VSS_VIOS_URL}/api/v1"
  VA_MCP_URL="http://${HOST_IP}:9901"
fi
```

On Kubernetes, do not use `kubectl port-forward`, Service DNS, NodePorts, or
host-side container discovery for VIOS, the VLM, or VA-MCP. Mode A uses
`${VST_API_BASE}` and `${VLM_ENDPOINT}` only; Mode B uses `${VA_MCP_URL}`.

### Mode-by-mode checklist (required)

| Mode / Path | User must provide | Services that must be reachable | Storage/location requirement | Not required |
|---|---|---|---|---|
| **Mode A / A1 (VIOS clip URL)** | sensor and/or clip time range | VIOS + VLM endpoint | Clip is fetched from VIOS timeline/URL APIs | VA-MCP analytics |
| **Mode A / A2 (local file or base64)** | local `VIDEO_FILE` path **or** `VIDEO_BASE64`, plus explicit VLM endpoint/model | VLM endpoint only | For `VIDEO_FILE`, file must exist on the same machine/container filesystem where OpenClaw/agent executes and be readable by that process | VIOS, VA-MCP analytics |
| **Mode B (incident range)** | `start_time` / `end_time` (and optional sensor scope) | VA-MCP analytics (`/vss-query-analytics` + `video_analytics__get_incidents`) | Incident data must already exist in analytics backend for requested range/scope | VIOS, direct VLM path |
| **Mode C (SOP compliance)** | sensor and time range (relative phrases resolved against host clock) | VA-MCP with the SOP tools (`get_sop_*`) on `${VA_MCP_URL}` + Elasticsearch `mdx-vlm-captions-*` | SOP detection docs must already be indexed for the requested sensor/range | VIOS, direct VLM path, report-time VLM |

Hard gate behavior:
- If required services for the chosen row are not reachable, stop and report the missing dependency.
- Do not silently switch modes because a dependency is missing.
- Offer `/vss-deploy-profile` only after user confirmation.

Probe examples:

```bash
# Mode A path A1 — VIOS reachable
curl -sf --max-time 5 "${VST_API_BASE}/sensor/version" >/dev/null

# Mode A — VLM reachable (Kubernetes public /v1, or caller-supplied / Docker host port)
curl -sf --max-time 5 "${VLM_ENDPOINT:-http://${HOST_IP}:30082/v1}/models" >/dev/null

# Mode B — VA-MCP reachable via /health (K8s: ${VA_MCP_URL}/health; Docker: :9901/health)
curl -sf --max-time 5 "${VA_MCP_URL:-http://${HOST_IP}:9901}/health" >/dev/null

# Mode C — reachability is NOT sufficient; also REQUIRE the SOP tools on VA-MCP:
# tools/list on ${VA_MCP_URL}/mcp (two-step JSON-RPC, see Mode C Step 1) must include
# video_analytics__get_sop_report. If absent, the deployment lacks the SOP patch —
# hand off to /vss-build-vision-ai and do NOT proceed with Mode C.
```

If required local services are missing and the user wants local deployment, hand off to `/vss-deploy-profile` (typically `-p base` for Mode A path A1, `-p alerts` for Mode B), or to `/vss-build-vision-ai` to compose the SOP profile for the SOP tools (Mode C). **Always** confirm deploy with the user first.

---

## VLM selection when unclear

If VLM/deployment choice is unclear and no default selection has been made, ask the user what VLM to use with these options:

1. **Provide an endpoint** — user supplies `VLM_ENDPOINT` and model id.
2. **Use the public Ingress VLM** — when `VSS_PUBLIC_URL` is set, probe
   `${VSS_PUBLIC_URL%/}/rtvi-vlm/v1/models` (the RT-VLM mount, same on every
   profile). Do **not** use `/vlm/v1` or the bare origin `/v1`.
3. **Suggest options based on auto-discover** — on Docker, probe the standard
   local VLM ports. For shared VLM-selection guidance, follow `/vss-ask-video`.
4. **Deploy a local VLM** — hand off to `/vss-deploy-profile` (with user confirmation) and then continue.

Auto-discover hints:

```bash
# Kubernetes / public Ingress (preferred when VSS_PUBLIC_URL is set)
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  curl -sf --max-time 5 "${VSS_PUBLIC_URL%/}/rtvi-vlm/v1/models" | jq -r '.data[].id'
fi

# Docker only — probe common local endpoints without inspecting any container.
if [ "${DEPLOYMENT_KIND:-docker}" != "kubernetes" ]; then
  curl -sf --max-time 5 "http://${HOST_IP}:30082/v1/models" | jq -r '.data[].id'   # local NIM / base default
  curl -sf --max-time 5 "http://${HOST_IP}:8018/v1/models" | jq -r '.data[].id'    # RT-VLM / alerts default
fi
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

VST may return clip URLs using an agent-internal host:port (Compose
`${HOST_IP}:30888`, or an in-cluster name). Keep that original URL as
`VIDEO_URL` for local / in-cluster VLM frame pulls when the VLM can reach it.
Do **not** rewrite the VLM input URL just to make it browser-playable.

Only create `BROWSER_CLIP_URL` for URLs shown in the rendered report.

**Kubernetes** — rewrite to the public Ingress origin **and keep the clip under the
public VIOS route**. Ingress serves VIOS only under `/vst`, and VIOS `/url`
responses return a bare `/storage/temp_files/...` path (and can carry a doubled
`http://` scheme — upstream Finding 8). Swapping only the authority would produce
`${VSS_PUBLIC_URL}/storage/...`, which Ingress hands to the UI catch-all instead of
VIOS. Reduce to a path, then restore `/vst` — the same compat mapping Docker HAProxy
applies:

```bash
: "${VSS_PUBLIC_URL:?Set VSS_PUBLIC_URL before rewriting clip URLs on Kubernetes}"
CLIP_PATH=$(printf '%s' "${RAW_URL}" | sed -E 's|^(https?://)+||; s|^[^/]*||')
case "${CLIP_PATH}" in
  /vst/*) BROWSER_CLIP_URL="${VSS_PUBLIC_URL%/}${CLIP_PATH}" ;; # already public VIOS
  /storage/*) BROWSER_CLIP_URL="${VSS_PUBLIC_URL%/}/vst${CLIP_PATH}" ;; # bare VIOS path
  *)
    echo "Cannot construct a public VIOS clip link from: ${RAW_URL}" >&2
    BROWSER_CLIP_URL=""
    ;;
esac
```

Verify the result before putting it in the report — it must begin with
`${VSS_PUBLIC_URL}/vst/`. Probe with GET, not HEAD: VST lazy-renders clips and
returns 404 to HEAD until a GET materializes the file. If the URL fails either
check, omit it from the report and call out why; do not block local VLM analysis:

```bash
case "${BROWSER_CLIP_URL}" in
  "${VSS_PUBLIC_URL%/}"/vst/*)
    # A GET materializes lazy VIOS clips. Fail fast when Ingress is unreachable,
    # but allow bounded time for the first render and fetch only the first byte.
    curl -fsS --connect-timeout 5 --max-time 125 --range 0-0 -o /dev/null \
      "${BROWSER_CLIP_URL}" || BROWSER_CLIP_URL=""
    ;;
  "") ;;  # unsupported source URL shape; already reported above
  *)
    echo "Refusing to render a clip link outside the public VIOS route" >&2
    BROWSER_CLIP_URL=""
    ;;
esac
```

**Docker Compose** — the deploy layer exports the browser-facing host:port as
`$VSS_PUBLIC_HOST` / `$VSS_PUBLIC_PORT` (and scheme as `$VSS_PUBLIC_HTTP_PROTOCOL`)
in every profile `.env` — Brev or bare-metal — so the report-link rewrite is:

```bash
: "${VSS_PUBLIC_HOST:?Set VSS_PUBLIC_HOST before rewriting clip URLs}"
: "${VSS_PUBLIC_PORT:?Set VSS_PUBLIC_PORT before rewriting clip URLs}"
VSS_PUBLIC_HTTP_PROTOCOL="${VSS_PUBLIC_HTTP_PROTOCOL:-http}"
BROWSER_CLIP_URL=$(echo "$RAW_URL" | sed -E "s|^https?://[^/]+|${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}|")
```

If the required public origin values are missing, omit the report-facing clip
link and call out that a browser-playable URL could not be produced; do not
block the local VLM analysis path. Apply the rewrite to **every clip URL
surfaced in the rendered report** (Mode A Step 4 Clip URL row; Mode B
per-incident clip sub-bullet). Leave the VLM `video_url` content block in Mode A
Step 3 on the original internal URL when the VLM is local / in-cluster. When the
VLM is reached through `${VSS_PUBLIC_URL}/rtvi-vlm/v1` and cannot fetch private VIOS
hosts, download the clip and send inline bytes (same remote-VLM rule as
`/vss-ask-video`).

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
   VSS_CAPABILITY_RECEIPT="${HOME}/.vss/agent-capabilities.json"
   if [ -z "${VSS_REPO_ROOT:-}" ] && [ -f "$VSS_CAPABILITY_RECEIPT" ]; then
     VSS_REPO_ROOT=$(jq -er \
       '.runtime.repo_root | select(type == "string" and length > 0)' \
       "$VSS_CAPABILITY_RECEIPT") || exit 1
   fi
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
- ask the user to choose one of the *VLM selection when unclear* options.

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

For each incident keep: `id`, `sensorId`, `timestamp`, `end`, `category`, `place.name`, `info.verdict`, `info.reasoning`, `objectIds`, and the clip URL (commonly `info.clip_url`, `clip_url`, or whichever clip-pointer field the response carries). **Apply the browser-playable rewrite (see *Clip URLs: VLM input vs browser report link* above — `VSS_PUBLIC_URL` on Kubernetes, or `$VSS_PUBLIC_HOST:$VSS_PUBLIC_PORT` on Docker) to every clip URL before pasting it into the report** — the raw value is often a private `HOST_IP:30888` URL the user's browser cannot reach.

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

## Mode C — SOP compliance report

Use for "generate an SOP compliance report" over a sensor + time range. Data comes from the SOP tools on VA-MCP (added by the SOP profile); this skill aggregates and renders the template itself.

### Step 1 — Resolve the sensor + time range

- Capture the named sensor as `sensor_id`. `start_time` / `end_time` are ISO 8601 UTC (`YYYY-MM-DDTHH:MM:SS.sssZ`); resolve relative phrases ("last hour", "today") against the host clock.
- Confirm the SOP tools are present (once). The four `get_sop_*` tools are added by the SOP patch and are **not** in the base `/vss-query-analytics` tool set, so call the VA-MCP endpoint directly (two-step MCP JSON-RPC: `initialize` → `tools/list`):

```bash
# Each fenced block is its own shell — re-derive VA-MCP here (do not rely on
# Endpoint resolution above). Force public path when VSS_PUBLIC_URL is set.
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  VA_MCP_URL="${VSS_PUBLIC_URL%/}/va-mcp"
else
  VA_MCP_URL="http://${HOST_IP:-localhost}:9901"
fi
MCP="${VA_MCP_URL%/}/mcp"
CT='Content-Type: application/json'; AC='Accept: application/json, text/event-stream'
SID=$(curl -si --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')
[ -n "$SID" ] || { echo "VA-MCP initialize failed (no session id) — is VA-MCP up at ${VA_MCP_URL}?" >&2; exit 1; }
curl -s --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
  | grep '^data:' | sed 's/^data: //' | jq -r '.result.tools[].name' | grep -qx video_analytics__get_sop_report \
  || { echo "SOP tools absent — deployment lacks the SOP patch; hand off to /vss-build-vision-ai to compose the SOP profile" >&2; exit 1; }
```

(No bash arrays — POSIX-`sh` safe; the session id is guarded, and the tool check exits non-zero when `get_sop_report` is missing.)

### Step 2 — Fetch the aggregated SOP report from VA-MCP

Call `video_analytics__get_sop_report` on the same endpoint. Each fenced block runs as its own shell, so `$MCP` / `$SID` / `$CT` / `$AC` from Step 1 do NOT carry over — re-establish them and re-`initialize` for a fresh session id here:

```bash
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  VA_MCP_URL="${VSS_PUBLIC_URL%/}/va-mcp"
else
  VA_MCP_URL="http://${HOST_IP:-localhost}:9901"
fi
MCP="${VA_MCP_URL%/}/mcp"
CT='Content-Type: application/json'; AC='Accept: application/json, text/event-stream'
SID=$(curl -si --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" \
  -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"cli","version":"1.0"}},"id":0}' \
  | awk 'tolower($1)=="mcp-session-id:"{print $2}' | tr -d '\r')
[ -n "$SID" ] || { echo "VA-MCP initialize failed (no session id) — is VA-MCP up at ${VA_MCP_URL}?" >&2; exit 1; }
curl -s --max-time 30 -X POST "$MCP" -H "$CT" -H "$AC" -H "mcp-session-id: $SID" \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"video_analytics__get_sop_report","arguments":{"sensor_id":"<sensor>","start_time":"<ISO>","end_time":"<ISO>"}},"id":2}' \
  | grep '^data:' | sed 's/^data: //' | jq -r '.result.content[0].text'
```

Returns `report_summary` (total messages, current / completed cycle, compliance status), `sop_violations` (missing / mis-ordered steps per cycle with timestamps), `actions_observed` (unique actions, latest action — **no total field**; the total action count equals `report_summary` total messages, one action per chunk), and a `formatted_report` markdown string.

Read-only boundary (mandatory): Mode C is strictly read-only. Never write, seed, backfill, or mutate Elasticsearch/VA data. **Reproduce the tool's numbers and action names verbatim** — DS-SOP actions are numbered classifications (e.g. "(1) first fan", "(10) not belong"); never paraphrase, rename, or invent them.

### Step 3 — Fill the SOP Compliance Report template

Copy [`references/report-templates/sop-compliance-report.md`](references/report-templates/sop-compliance-report.md), fill every placeholder from the Step 2 result (message count, compliance status, cycle counts, the missing / mis-ordered step tables, actions observed), and return the rendered markdown. For placeholders `get_sop_report` does not carry: generate `{report_id}` + `{report_date}`, set `{agent_version}` to `vss-generate-video-report (Mode C)`, and set `{video_analysis_details}` / `{snapshot_image}` to `N/A` (Mode C runs no report-time VLM and fetches no media), and set `{total_actions}` to `report_summary`'s total-messages count (`get_sop_report` has no total-actions field — there is one action per chunk, so total actions = total messages; do not invent a separate number). Fill `{notes}` with the data provenance and snapshot caveats (source/scope, the bounded `end_time` used, the doc count vs the 1000-doc `get_sop_report` cap, and that a live stream never reaches EOS so `final_*` counts stay 0 and every violation is per-chunk); fill `{recommendations}` with the compliance interpretation (recurring missing / mis-ordered steps and whether they reflect the source clip rather than an operator fault). Keep the source asset unchanged; never leave a placeholder, and never include template instructions in a filled cell.

If `get_sop_report` returns an error or zero messages for the range/scope, STOP and return a one-line empty-range statement naming the sensor + range. Do not render the full template, invent data, or fall back to another mode.

---

## Error Handling

- If a probe, `curl`, VLM call, or `/vss-query-analytics` request fails, stop the workflow and report the failing endpoint, HTTP status or command error, and the next useful recovery step. Do not fabricate a report from partial or missing data.
- If the VLM response is empty, malformed, or contains only a reasoning block, surface that response problem and suggest checking model readiness/logs before retrying.
- If a clip URL cannot be rewritten to the public host/port, omit it from the rendered report and call out that the browser-playable URL could not be produced.
- For Mode B, treat missing optional incident fields (`info.reasoning`, `objectIds`, clip URL) as omissions in the report, but treat missing `id`, `timestamp`, or `category` as a data-quality error that should be reported.
- For Mode C, if `get_sop_report` returns `{"error": ...}` or no messages, treat it as an empty range (Mode C Step 3), not a crash; surface any tool / ES error with the failing call and next recovery step.

---

## Cross-Reference

- **`/vss-manage-video-io-storage`** — sensor list, timelines, and clip URL for Mode A Step 1.
- **`/vss-query-analytics`** — incident retrieval for Mode B Step 2. (Mode C does **not** use it — it calls VA-MCP's `get_sop_report` directly; see Mode C Step 2.)
- **`/vss-build-vision-ai`** — composes the SOP profile that deploys the VA-MCP SOP tools (`get_sop_*`) Mode C queries (contracts in `skills/vss-build-vision-ai/references/services/sop/`).
- **`/vss-ask-video`** — ad-hoc VLM Q&A on a single clip (not a structured report).
- **`/vss-summarize-video`** — used by Mode A to produce the summary body when the `lvs` profile is deployed; the report template (Step 4) is still filled here.
- **`references/default-vlm-prompt.md`** — default Mode A VLM prompt (edit this file to change the prompt). Step 3 loads it via `$SKILL_DIR/references/default-vlm-prompt.md` and fails if missing or empty.
