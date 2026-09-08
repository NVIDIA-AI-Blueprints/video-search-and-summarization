---
name: vss-generate-video-report
description: Use this skill when producing a VSS analysis report — Mode A per-clip VLM, Mode B incident-range via video-analytics, Mode C SOP compliance via the SOP tools. Not for standalone video summarization, real-time alerts or ad-hoc Q&A.
license: Apache-2.0
metadata:
  version: "3.4.0"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# Report

Generate a video analysis report by routing to one of three backends — **never via** `POST /generate` on the VSS agent.

| Mode | Backend | Steps |
|---|---|---|
| **A. Video clip** | `A1` `/vss-manage-video-io-storage` → clip URL → **VLM chat/completions** OR `A2` local video file on disk or base64 video + explicit VLM endpoint | [`references/report-types/video-analysis.md`](references/report-types/video-analysis.md) |
| **B. Incident range** | `/vss-query-analytics` → incident list → narrative report | [`references/report-types/incident-range.md`](references/report-types/incident-range.md) |
| **C. SOP compliance** | VA-MCP `get_sop_report` (direct MCP call on `${VA_MCP_URL}`) → SOP compliance report | [`references/report-types/sop-compliance.md`](references/report-types/sop-compliance.md) |

If the request is ambiguous (e.g. "report on `<sensor>`" with no time range and no incident wording), default to **Mode A**. Ask only when the request names a sensor and a time range **and** carries neither incident / alert wording (→ Mode B) nor SOP / compliance wording (→ Mode C). Never run any mode's probe or gate until the mode is settled. See **Examples** below for the request phrasings that route to each mode.

---

## Instructions

0. **Set `SKILL_DIR`** to the "Base directory for this skill" path announced when this skill loads. All skill-relative reads (e.g. the default VLM prompt) resolve under `$SKILL_DIR` — never via cwd-relative paths. If no base directory was announced (this file was opened directly), `SKILL_DIR` is the directory containing this `SKILL.md`. Re-set `SKILL_DIR=<that path>` at the top of every shell block you run — each fenced block is its own shell — the same way the *HITL prompt mode* bullet has you set `FINAL_PROMPT` at the top of the Mode A Step 3 block.
1. **Pick the mode** — Mode A for a single recorded clip/sensor video, Mode B when the request is about incidents / alerts (usually with a time range), Mode C when the request asks for an SOP / compliance report (match against *Examples*).
2. **Verify runtime prerequisites** for that mode under *Runtime prerequisites*; hand off only when required services are missing (Mode A / B → `/vss-deploy-profile`; Mode C → `/vss-build-vision-ai` for the SOP tools).
3. **Apply HITL mode** under *HITL prompt mode (runtime-first, harness fallback)* before Mode A Step 3 (`references/report-types/video-analysis.md`). (Mode B and Mode C have no prompt-approval step.)
4. **Run that mode's numbered steps** from its report-type file — the *Steps* column of the mode table above; open only the one you routed to, via `$SKILL_DIR/references/report-types/<file>`. This file holds routing, gates and the shared setup (endpoint resolution, VLM selection, HITL, clip-URL rewrite); the numbered steps live only in the report-type files.
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
- Mode B zero results: do not invent or seed incidents, and do not fall back to Mode A or call the VLM.
- Mode C top title MUST be exactly `# SOP Compliance Report`, with the template's Basic Information / Compliance Summary / SOP Violations sections.
- Mode C empty-range output MUST be exactly one plain-text line, no heading, table, or template: `No SOP messages found for sensor <sensor_id> in range <start_time> to <end_time>.` `get_sop_report` signals an empty range as the tool result `{"error": "No VisionLLM messages found for the given filters."}` — that result, and only that, renders this line (see `references/report-types/sop-compliance.md` Step 3). A failed `tools/call`, an empty or non-SSE / non-JSON body, a JSON-RPC `error` envelope, `result.isError: true`, or any other error text is a failure: surface it per *Error Handling* and never render this line for it.

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
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  DEPLOYMENT_KIND="kubernetes"
  VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
  VSS_VIOS_URL="${VSS_PUBLIC_URL}/vst"
  VST_API_BASE="${VSS_VIOS_URL}/api/v1"
  # RT-VLM is at /rtvi-vlm on every profile; nothing is mounted at the origin /v1.
  : "${VLM_ENDPOINT:=${VSS_PUBLIC_URL}/rtvi-vlm/v1}"
  # Alerts / Mode B and Mode C — force public VA-MCP; ignore leftover Docker :9901.
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
`${VST_API_BASE}` and `${VLM_ENDPOINT}` only; Mode B and Mode C use `${VA_MCP_URL}`.

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
- Mode A: a clip **120 seconds or longer** never takes the direct VLM path — stop and prompt the user to deploy / use LVS (`/vss-deploy-profile` + `/vss-summarize-video`, confirm first) — unless LVS is already ready per the Mode A file's LVS check, in which case use it directly — then continue with the report template; details in `references/report-types/video-analysis.md` § Long-video rule.

Probe examples:

```bash
# Mode A path A1 — VIOS reachable
curl -sf --max-time 5 "${VST_API_BASE}/sensor/version" >/dev/null

# Mode A — VLM reachable (Kubernetes public /v1, or caller-supplied / Docker host port)
curl -sf --max-time 5 "${VLM_ENDPOINT:-http://${HOST_IP}:30082/v1}/models" >/dev/null

# Mode B — VA-MCP reachable via /health (K8s: ${VA_MCP_URL}/health; Docker: :9901/health)
curl -sf --max-time 5 "${VA_MCP_URL:-http://${HOST_IP}:9901}/health" >/dev/null

# Mode C — reachability is NOT sufficient; also REQUIRE the SOP tools on VA-MCP:
# tools/list on ${VA_MCP_URL}/mcp must include video_analytics__get_sop_report. The runnable
# probe is the initialize -> tools/list block in references/report-types/sop-compliance.md
# Step 1: open that file now and run just that block as this gate (it exits non-zero when
# the tool is missing); when you reach Instructions step 4, continue in that file without
# repeating the probe. If absent, the deployment lacks the SOP patch —
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
- resolved `true`: before Mode A Step 3 (`references/report-types/video-analysis.md`), show the current prompt — the contents of `$SKILL_DIR/references/default-vlm-prompt.md`, loaded with the same non-empty guard Mode A Step 3 uses — and ask the user to choose one of:
  - `APPROVE` — use the current prompt as-is.
  - `EDIT: <instructions>` — apply edits to the current prompt and show the revised prompt.
  - `NEW: <full prompt>` — replace with a brand-new prompt.

Guardrails (required):
- Do **not** treat `yes`, `confirm`, `ok`, or whitespace-only text as approval.
- Do **not** wait for an empty-string confirmation.
- Keep showing the same three choices (`APPROVE | EDIT: ... | NEW: ...`) after **every** `EDIT` or `NEW` response.
- Do not run report generation until the user explicitly responds with `APPROVE`.
- Carry the approved / edited / new text into Mode A Step 3 as `FINAL_PROMPT` — set `FINAL_PROMPT=…` at the top of the Step 3 shell block, since each fenced block is its own shell. Step 3 uses it as the prompt body verbatim; the only addition it may make is the reasoning-format suffix, and only when the user explicitly asked for reasoning.
- If the response is ambiguous, re-prompt with explicit `APPROVE | EDIT: ... | NEW: ...` options and continue the loop.
- If HITL resolved via rule (3) (neither runtime nor fallback is set), include this note on the first report generation response in the session:
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
surfaced in the rendered report** (Mode A Step 4 Clip URL row — `references/report-types/video-analysis.md`; Mode B
per-incident clip sub-bullet — `references/report-types/incident-range.md`). Leave the VLM `video_url` content block in Mode A
Step 3 (`references/report-types/video-analysis.md`) on the original internal URL when the VLM is local / in-cluster. When the
VLM is reached through `${VSS_PUBLIC_URL}/rtvi-vlm/v1` and cannot fetch private VIOS
hosts, download the clip and send inline bytes (same remote-VLM rule as
`/vss-ask-video`).

---

## Report types

Each report type's numbered steps live in its own file under `references/report-types/` — the *Steps* column of the mode table at the top of this file. Read only the file for the mode chosen in *Instructions* step 1, via `$SKILL_DIR/references/report-types/<file>`. Every file follows the same skeleton: a short header (trigger phrases → prerequisite gate → inputs → template → output contract → failure modes, each a pointer to this file or to the step that owns it) followed by the numbered steps; the fill step of each file names its template under `references/report-templates/`.

Templates (read when filling the report): [`references/report-templates/video-analysis-report.md`](references/report-templates/video-analysis-report.md) · [`references/report-templates/incident-range-report.md`](references/report-templates/incident-range-report.md) · [`references/report-templates/sop-compliance-report.md`](references/report-templates/sop-compliance-report.md).

Adding a report type — touch points, in order:

1. `references/report-types/<type>.md` (skeleton above) and `references/report-templates/<type>-report.md`.
2. In this file: frontmatter `description`; the mode table row (Backend + Steps); *Instructions* steps 1–3 (mode pick, deploy hand-off target, HITL applicability); *Examples*; *Runtime prerequisites* (intro sentence, *Mode-by-mode checklist* row, probe / gate line, hand-off sentence, and the mode sentence in *Endpoint resolution* if it shares `${VA_MCP_URL}` or needs a new endpoint); the *HITL prompt mode* scope sentence if the type has a prompt-approval step; *Output contract for evaluators*; the per-mode clip-URL sentence if the report embeds clips; the *Templates* link line in this section; optionally *Error Handling* and *Cross-Reference* lines.
3. `skill-card.md` (description, use case, references), the skill's `evals/`, and — when the mode list changes — the `skills/README.md` rows that enumerate this skill's modes (not updated in this refactor; follow-up).

Mode letters are stable aliases (evals and other skills reference them); files are named by report type.

---

## Error Handling

- If a probe, `curl`, VLM call, or `/vss-query-analytics` request fails, stop the workflow and report the failing endpoint, HTTP status or command error, and the next useful recovery step. Do not fabricate a report from partial or missing data.
- If the VLM response is empty, malformed, or contains only a reasoning block, surface that response problem and suggest checking model readiness/logs before retrying.
- If a clip URL cannot be rewritten to the public host/port, omit it from the rendered report and call out that the browser-playable URL could not be produced.
- For Mode B, treat missing optional incident fields (`info.reasoning`, `objectIds`, clip URL) as omissions in the report, but treat missing `id`, `timestamp`, or `category` as a data-quality error that should be reported.
- For Mode C, the tool result `{"error": "No VisionLLM messages found for the given filters."}` means zero messages for the range/scope: render only the Mode C empty-range line from the *Output contract* (`references/report-types/sop-compliance.md` Step 3). Inspect the raw `tools/call` response before extracting `.result.content[0].text`: a non-2xx or empty / non-SSE body, a JSON-RPC `error` envelope, `result.isError: true`, or any other error text is a failure — do NOT render that line; report the failing call, the raw response, and the next recovery step.

---

## Cross-Reference

- **`/vss-manage-video-io-storage`** — sensor list, timelines, and clip URL for Mode A Step 1 (`references/report-types/video-analysis.md`).
- **`/vss-query-analytics`** — incident retrieval for Mode B Step 2 (`references/report-types/incident-range.md`). (Mode C does **not** use it — it calls VA-MCP's `get_sop_report` directly; see `references/report-types/sop-compliance.md` Step 2.)
- **`/vss-build-vision-ai`** — composes the SOP profile that deploys the VA-MCP SOP tools (`get_sop_*`) Mode C queries (contracts in `skills/vss-build-vision-ai/references/services/sop/`).
- **`/vss-ask-video`** — ad-hoc VLM Q&A on a single clip (not a structured report).
- **`/vss-summarize-video`** — used by Mode A to produce the summary body when the `lvs` profile is deployed; the report template (Mode A Step 4, `references/report-types/video-analysis.md`) is still filled by this skill.
- **`references/default-vlm-prompt.md`** — default Mode A VLM prompt (edit this file to change the prompt). Mode A Step 3 (`references/report-types/video-analysis.md`) loads it via `$SKILL_DIR/references/default-vlm-prompt.md` and fails if missing or empty.
