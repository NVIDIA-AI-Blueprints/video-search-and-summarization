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

| Mode | Backend | Steps |
|---|---|---|
| **A. Video clip** | `A1` `/vss-manage-video-io-storage` → clip URL → **VLM chat/completions** OR `A2` local video file on disk or base64 video + explicit VLM endpoint | [`references/report-types/video-analysis.md`](references/report-types/video-analysis.md) |
| **B. Incident range** | `/vss-query-analytics` → incident list → narrative report | [`references/report-types/incident-range.md`](references/report-types/incident-range.md) |
| **C. SOP compliance** | VA-MCP `get_sop_report` (direct MCP call on `${VA_MCP_URL}`) → SOP compliance report | [`references/report-types/sop-compliance.md`](references/report-types/sop-compliance.md) |

If the request is ambiguous (e.g. "report on `<sensor>`" with no time range and no incident wording), default to **Mode A**. Ask only when the request names a sensor and a time range **and** carries neither incident / alert wording (→ Mode B) nor SOP / compliance wording (→ Mode C). Never run any mode's probe or gate until the mode is settled. See **Examples** below for the request phrasings that route to each mode.

---

## Instructions

0. **Set `SKILL_DIR`** to the "Base directory for this skill" path announced when this skill loads. All skill-relative reads (e.g. the default VLM prompt) resolve under `$SKILL_DIR` — never via cwd-relative paths. If no base directory was announced (this file was opened directly), `SKILL_DIR` is the directory containing this `SKILL.md`. Each fenced block is its own shell and nothing survives it, so the skill hands state over explicitly: the blocks that resolve shared values end by printing shell-quoted `NAME=value` lines (*Endpoint resolution* → `DEPLOYMENT_KIND`, `VSS_PUBLIC_URL`, `HOST_IP`, `VST_API_BASE`, `VA_MCP_URL`, `VLM_ENDPOINT`; Mode A Step 1 → `VIDEO_URL`; Mode A Step 2 → `VLM_BACKEND`, `VLM_ENDPOINT`, `VLM_MODEL`; the clip-URL rewrite blocks (Kubernetes / Docker) take `RAW_URL` in and print `BROWSER_CLIP_URL`). Paste those lines as printed, plus `SKILL_DIR=<that path>` and any gate result (`HITL_RESOLVED` / `HITL_PROMPT_FILE`, *HITL prompt mode*), at the top of the next block you run, with any caller-supplied value (e.g. `VLM_ENDPOINT` / `VLM_MODEL`) pasted **after** them so it wins; consuming blocks refuse to run (`${VAR:?}`) when a required value is missing.
1. **Pick the mode** — Mode A for a single recorded clip/sensor video (path `A1` VST clip URL or `A2` local file / base64 — the *Mode-by-mode checklist* rows), Mode B when the request is about incidents / alerts (usually with a time range), Mode C when the request asks for an SOP / compliance report (match against *Examples*).
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
- Mode C empty-range output MUST be exactly one plain-text line, no heading, table, or template: `No SOP messages found for sensor <sensor_id> in range <start_time> to <end_time>.` `get_sop_report` signals an empty range as the tool result `{"error": "No VisionLLM messages found for the given filters."}` — that result, and only that, renders this line (see `references/report-types/sop-compliance.md` Step 3). A failed `tools/call`, an empty body or one with no JSON-RPC response for the request (neither SSE `data:` events nor plain JSON), a JSON-RPC `error` envelope, `result.isError: true`, or any other error text is a failure: surface it per *Error Handling* and never render this line for it.

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
# VSS_ENDPOINT is the legacy alias for VSS_PUBLIC_URL (deployment_resolution.md); honour it here so
# every mode resolves the same way (the Mode C blocks apply the same alias).
[ -n "${VSS_PUBLIC_URL:-}" ] || VSS_PUBLIC_URL="${VSS_ENDPOINT:-}"
VLM_ENDPOINT="${VLM_ENDPOINT%/}"   # a caller-supplied endpoint keeps working with a trailing slash
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
  HOST_IP="${HOST_IP:-localhost}"   # effective host — later blocks read HOST_IP directly, so hand it over resolved
  VSS_VIOS_URL="http://${HOST_IP}:30888/vst"
  VST_API_BASE="${VSS_VIOS_URL}/api/v1"
  VA_MCP_URL="http://${HOST_IP}:9901"
fi
# Hand-off — shell state does not survive this block: paste these lines, as printed, at the top of
# every later block (probes, Mode A Step 2), BEFORE any caller-supplied VLM_ENDPOINT / VLM_MODEL line
# (later lines win). VLM_ENDPOINT is printed only when known (Kubernetes route or caller-supplied);
# on Docker, Mode A Step 2 discovers it.
printf 'DEPLOYMENT_KIND=%q\nVSS_PUBLIC_URL=%q\nHOST_IP=%q\nVST_API_BASE=%q\nVA_MCP_URL=%q\n' \
  "$DEPLOYMENT_KIND" "${VSS_PUBLIC_URL:-}" "${HOST_IP:-}" "$VST_API_BASE" "$VA_MCP_URL"
[ -z "${VLM_ENDPOINT:-}" ] || printf 'VLM_ENDPOINT=%q\n' "$VLM_ENDPOINT"
```

On Kubernetes, do not use `kubectl port-forward`, Service DNS, NodePorts, or
host-side container discovery for VIOS, the VLM, or VA-MCP. Mode A uses
`${VST_API_BASE}` and `${VLM_ENDPOINT}` only; Mode B and Mode C use `${VA_MCP_URL}`.

### Mode-by-mode checklist (required)

| Mode / Path | User must provide | Services that must be reachable | Storage/location requirement | Not required |
|---|---|---|---|---|
| **Mode A / A1 (VIOS clip URL)** | sensor and/or clip time range | VIOS + VLM endpoint | Clip is fetched from VIOS timeline/URL APIs | VA-MCP analytics |
| **Mode A / A2 (local file or base64)** | local `VIDEO_FILE` path **or** `VIDEO_B64_FILE` (base64 written to a file, never pasted into a shell block), plus explicit VLM endpoint/model | VLM endpoint only | For `VIDEO_FILE`, file must exist on the same machine/container filesystem where OpenClaw/agent executes and be readable by that process | VIOS, VA-MCP analytics |
| **Mode B (incident range)** | `start_time` / `end_time` (and optional sensor scope) | VA-MCP analytics (`/vss-query-analytics` + `video_analytics__get_incidents`) | Incident data must already exist in analytics backend for requested range/scope | VIOS, direct VLM path |
| **Mode C (SOP compliance)** | sensor and time range (relative phrases resolved against host clock) | VA-MCP with the SOP tools (`get_sop_*`) on `${VA_MCP_URL}` + Elasticsearch `mdx-vlm-captions-*` | SOP detection docs must already be indexed for the requested sensor/range | VIOS, direct VLM path, report-time VLM |

Hard gate behavior:
- If required services for the chosen row are not reachable, stop and report the missing dependency.
- Do not silently switch modes because a dependency is missing.
- Offer `/vss-deploy-profile` only after user confirmation.
- Mode A: a clip **120 seconds or longer** never takes the direct VLM path — stop and prompt the user to deploy / use LVS (`/vss-deploy-profile` + `/vss-summarize-video`, confirm first) — unless LVS is already ready per the Mode A file's LVS check, in which case use it directly — then continue with the report template; details in `references/report-types/video-analysis.md` § Long-video rule.

Probe examples:

```bash
# Fresh shell: paste the Endpoint resolution hand-off lines above these probes and set the mode you
# picked in Instructions step 1 (A1 | A2 | B | C). No Docker fallbacks here — a missing value must
# fail loudly, not silently probe the wrong deployment. Only the probes for that mode run and decide
# the exit code (non-zero = a required service is missing: stop and report it).
: "${REPORT_MODE:?set REPORT_MODE=A1|A2|B|C at the top of this block (Instructions step 1; A1 = VST clip URL, A2 = local file / base64 — the Mode-by-mode checklist rows)}"
case "${DEPLOYMENT_KIND:?paste the Endpoint resolution output at the top of this block}" in
  kubernetes|docker) ;;
  *) echo "ERROR: DEPLOYMENT_KIND must be kubernetes or docker, got '${DEPLOYMENT_KIND}'" >&2; exit 1 ;;
esac
VLM_ENDPOINT="${VLM_ENDPOINT%/}"   # tolerate a trailing slash on a caller-supplied endpoint
FAIL=0
case "${REPORT_MODE}" in
  A1|A2)
    if [ "${REPORT_MODE}" = "A1" ]; then   # A2 (local file / base64) needs no VIOS
      curl -sf --max-time 5 "${VST_API_BASE:?}/sensor/version" >/dev/null \
        && echo "VIOS ok: ${VST_API_BASE}" || { echo "VIOS unreachable: ${VST_API_BASE}" >&2; FAIL=1; }
    fi
    # VLM. Kubernetes: the public route from Endpoint resolution (a missing line is a paste error).
    # Docker: VLM_ENDPOINT is unknown until Mode A Step 2 discovers it — that block IS the VLM probe.
    if [ -n "${VLM_ENDPOINT:-}" ]; then
      curl -sf --max-time 5 "${VLM_ENDPOINT}/models" >/dev/null \
        && echo "VLM ok: ${VLM_ENDPOINT}" || { echo "VLM unreachable: ${VLM_ENDPOINT}" >&2; FAIL=1; }
    elif [ "${DEPLOYMENT_KIND}" = "docker" ]; then
      echo "VLM probe deferred: run Mode A Step 2 (discovery) next — Docker has no VLM_ENDPOINT yet" >&2
    else
      echo "ERROR: VLM_ENDPOINT line missing — re-paste the Endpoint resolution output" >&2; FAIL=1
    fi ;;
  B|C)
    curl -sf --max-time 5 "${VA_MCP_URL:?}/health" >/dev/null \
      && echo "VA-MCP ok: ${VA_MCP_URL}" || { echo "VA-MCP unreachable: ${VA_MCP_URL}" >&2; FAIL=1; }
    [ "${REPORT_MODE}" = "C" ] && echo "Mode C: reachability is not sufficient — now run the tools/list gate (bullet below)" ;;
  *) echo "ERROR: REPORT_MODE must be A1, A2, B or C, got '${REPORT_MODE}'" >&2; exit 1 ;;
esac
[ "${FAIL}" = 0 ] || exit 1
```

- **Mode C gate** — reachability is not sufficient: `tools/list` on `${VA_MCP_URL}/mcp` must include `video_analytics__get_sop_report`. The runnable probe is the initialize → `tools/list` block in `references/report-types/sop-compliance.md` Step 1 (paste the *Endpoint resolution* hand-off at its top so it reuses the validated `VA_MCP_URL`; without it the block re-derives VA-MCP from exported `VSS_PUBLIC_URL` / `HOST_IP`): open that file now and run just that block as this gate; when you reach *Instructions* step 4, continue in that file without repeating it. It exits non-zero for two different reasons — read stderr: `VA-MCP problem` = the `tools/list` call itself failed (report it per *Error Handling*, do **not** hand off); `SOP tools absent` = the deployment lacks the SOP patch — hand off to `/vss-build-vision-ai` and do **not** proceed with Mode C.

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

# Docker only — probe common local endpoints without inspecting any container
# (DEPLOYMENT_KIND comes from the Endpoint resolution hand-off; no silent Docker default).
case "${DEPLOYMENT_KIND:?paste the Endpoint resolution output at the top of this block}" in
  kubernetes|docker) ;;
  *) echo "ERROR: DEPLOYMENT_KIND must be kubernetes or docker, got '${DEPLOYMENT_KIND}'" >&2; exit 1 ;;
esac
if [ "${DEPLOYMENT_KIND}" = "docker" ]; then
  curl -sf --max-time 5 "http://${HOST_IP:-localhost}:30082/v1/models" | jq -r '.data[].id'   # local NIM / base default
  curl -sf --max-time 5 "http://${HOST_IP:-localhost}:8018/v1/models" | jq -r '.data[].id'    # RT-VLM / alerts default
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
- Carry the resolved mode into Mode A Step 3: at the top of the Step 3 shell block (one block serves A1 and A2; each fenced block is its own shell) set `HITL_RESOLVED=true` or `HITL_RESOLVED=false`, and when true write the approved / edited / new text to a file with the Write tool (e.g. `/tmp/vss-hitl-prompt.txt`) and set `HITL_PROMPT_FILE=<that path>` — never paste prompt text into a shell assignment or heredoc, where an apostrophe, metacharacter or delimiter collision would be interpreted; the block reads the file with `cat`. It refuses to run when `HITL_RESOLVED` is unset or is `true` without a non-empty `HITL_PROMPT_FILE`. Step 3 uses the text as the prompt body verbatim; the only addition it may make is the reasoning-format suffix, and only when the user explicitly asked for reasoning.
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

Rewrite and verify in ONE block (fresh shell: paste the Endpoint resolution hand-off and set
`RAW_URL` at its top). The result must begin with `${VSS_PUBLIC_URL}/vst/`. Probe with GET, not
HEAD: VST lazy-renders clips and returns 404 to HEAD until a GET materializes the file. If the
URL fails either check the block prints an empty `BROWSER_CLIP_URL`; omit the link from the
report and call out why; do not block local VLM analysis:

```bash
: "${VSS_PUBLIC_URL:?Set VSS_PUBLIC_URL before rewriting clip URLs on Kubernetes}"
: "${RAW_URL:?set RAW_URL to the clip URL from Mode A Step 1 / the Mode B incident at the top of this block}"
CLIP_PATH=$(printf '%s' "${RAW_URL}" | sed -E 's|^(https?://)+||; s|^[^/]*||')
case "${CLIP_PATH}" in
  /vst/*) BROWSER_CLIP_URL="${VSS_PUBLIC_URL%/}${CLIP_PATH}" ;; # already public VIOS
  /storage/*) BROWSER_CLIP_URL="${VSS_PUBLIC_URL%/}/vst${CLIP_PATH}" ;; # bare VIOS path
  *)
    echo "Cannot construct a public VIOS clip link from: ${RAW_URL}" >&2
    BROWSER_CLIP_URL=""
    ;;
esac
case "${BROWSER_CLIP_URL}" in
  "${VSS_PUBLIC_URL%/}"/vst/*)
    # A GET materializes lazy VIOS clips. Fail fast when Ingress is unreachable,
    # but allow bounded time for the first render and fetch only the first byte.
    curl -fsS --connect-timeout 5 --max-time 125 --range 0-0 -o /dev/null \
      "${BROWSER_CLIP_URL}" || { echo "Clip link did not materialize: ${BROWSER_CLIP_URL}" >&2; BROWSER_CLIP_URL=""; }
    ;;
  "") ;;  # unsupported source URL shape; reported above
  *)
    echo "Refusing to render a clip link outside the public VIOS route" >&2
    BROWSER_CLIP_URL=""
    ;;
esac
# Hand-off for the report step (empty = omit the link and say why).
printf 'BROWSER_CLIP_URL=%q\n' "${BROWSER_CLIP_URL}"
```

**Docker Compose** — the deploy layer exports the browser-facing host:port as
`$VSS_PUBLIC_HOST` / `$VSS_PUBLIC_PORT` (and scheme as `$VSS_PUBLIC_HTTP_PROTOCOL`)
in every profile `.env` — Brev or bare-metal — so the report-link rewrite is:

```bash
: "${VSS_PUBLIC_HOST:?Set VSS_PUBLIC_HOST before rewriting clip URLs}"
: "${VSS_PUBLIC_PORT:?Set VSS_PUBLIC_PORT before rewriting clip URLs}"
: "${RAW_URL:?set RAW_URL to the clip URL from Mode A Step 1 / the Mode B incident at the top of this block}"
VSS_PUBLIC_HTTP_PROTOCOL="${VSS_PUBLIC_HTTP_PROTOCOL:-http}"
BROWSER_CLIP_URL=$(printf '%s' "$RAW_URL" | sed -E "s|^https?://[^/]+|${VSS_PUBLIC_HTTP_PROTOCOL}://${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}|")
# Hand-off for the report step.
printf 'BROWSER_CLIP_URL=%q\n' "${BROWSER_CLIP_URL}"
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
3. When the mode list changes, the `skills/README.md` rows that enumerate this skill's modes.

Mode letters are stable aliases (other skills reference them); files are named by report type.

---

## Error Handling

- If a probe, `curl`, VLM call, or `/vss-query-analytics` request fails, stop the workflow and report the failing endpoint, HTTP status or command error, and the next useful recovery step. Do not fabricate a report from partial or missing data.
- If the VLM response is empty, malformed, or contains only a reasoning block, surface that response problem and suggest checking model readiness/logs before retrying.
- If a clip URL cannot be rewritten to the public host/port, omit it from the rendered report and call out that the browser-playable URL could not be produced.
- For Mode B, treat missing optional incident fields (`info.reasoning`, `objectIds`, clip URL) as omissions in the report, but treat missing `id`, `timestamp`, or `category` as a data-quality error that should be reported.
- For Mode C, the tool result `{"error": "No VisionLLM messages found for the given filters."}` means zero messages for the range/scope: render only the Mode C empty-range line from the *Output contract* (`references/report-types/sop-compliance.md` Step 3). Inspect the raw `tools/call` response before extracting `.result.content[0].text`: a non-200 status, an empty body or one with no JSON-RPC response for the request (neither SSE `data:` events nor plain JSON), a JSON-RPC `error` envelope, `result.isError: true`, or any other error text is a failure — do NOT render that line; report the failing call, the raw response, and the next recovery step.

---

## Cross-Reference

- **`/vss-manage-video-io-storage`** — sensor list, timelines, and clip URL for Mode A Step 1 (`references/report-types/video-analysis.md`).
- **`/vss-query-analytics`** — incident retrieval for Mode B Step 2 (`references/report-types/incident-range.md`). (Mode C does **not** use it — it calls VA-MCP's `get_sop_report` directly; see `references/report-types/sop-compliance.md` Step 2.)
- **`/vss-build-vision-ai`** — composes the SOP profile that deploys the VA-MCP SOP tools (`get_sop_*`) Mode C queries (contracts in `skills/vss-build-vision-ai/references/services/sop/`).
- **`/vss-ask-video`** — ad-hoc VLM Q&A on a single clip (not a structured report).
- **`/vss-summarize-video`** — used by Mode A to produce the summary body when the `lvs` profile is deployed; the report template (Mode A Step 4, `references/report-types/video-analysis.md`) is still filled by this skill.
- **`references/default-vlm-prompt.md`** — default Mode A VLM prompt (edit this file to change the prompt). Mode A Step 3 (`references/report-types/video-analysis.md`) loads it via `$SKILL_DIR/references/default-vlm-prompt.md` and fails if missing or empty.
