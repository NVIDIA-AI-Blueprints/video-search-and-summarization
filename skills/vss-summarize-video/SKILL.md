---
name: vss-summarize-video
description: Summarize recorded video through HITL-gated LVS, with an explicitly approved VLM fallback. Not for reports, archive search, or live RTSP captioning.
license: Apache-2.0
metadata:
  version: "3.2.2"
  author: "NVIDIA Video Search and Summarization team"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# VSS Summarize Video

## Instructions

- Execute the five workflow stages below in order.
- Run API commands yourself; do not tell the user to run them.
- Use the required references at their named decision points.

## Examples

Runnable scenarios live under `evals/`. The command implementations are in
[`references/end-to-end-example.md`](references/end-to-end-example.md).

## Purpose

Produce one polished narrative summary with timestamped events when LVS is
available.

Do not use this skill for:

- Live RTSP captioning: use `vss-deploy-dense-captioning`.
- Incident or alert-window reports: use `vss-generate-video-report` Mode B.
- Archive search: use `vss-search-archive`.

## Required References

Load these files only as directed:

- [`references/end-to-end-example.md`](references/end-to-end-example.md): load
  before executing the recorded-video workflow. It contains the exact
  readiness, VIOS preparation, one-request LVS, and VLM fallback commands.
- [`references/video-summarization-api.md`](references/video-summarization-api.md):
  load before constructing any live LVS operation. Follow its **Runtime
  OpenAPI Discovery** procedure on Docker. On Kubernetes, follow the K8s
  note there — stock LVS Ingress does not publish LVS `/openapi.json`.
- [`references/hitl-prompts.md`](references/hitl-prompts.md): load when
  collecting LVS scenario, events, and optional objects of interest.
- [`references/video-summarization-debugging.md`](references/video-summarization-debugging.md):
  load only when diagnosing a failed or empty response.
- [`references/video-summarization-deployment.md`](references/video-summarization-deployment.md):
  load only for deployment, configuration, logs, or service operations.
- [`references/video-summarization-environment-variables.md`](references/video-summarization-environment-variables.md)
  and `assets/video-summarization.env.example`: use when configuring the
  service environment.
- [`../vss-build-vision-agent/references/deployment_resolution.md`](../vss-build-vision-agent/references/deployment_resolution.md):
  Kubernetes `VSS_PUBLIC_URL` contract and LVS Exact `/v1` routes.

## Core Invariants

- Route by LVS readiness, never by video duration.
- HTTP 200 from `/v1/ready` selects LVS. Empty response bodies do not mean
  unavailable.
- Once LVS is selected, do not call a VLM `/v1/chat/completions` endpoint.
- Issue exactly one `POST /v1/summarize` per user summarize request. Never
  retry, hedge, broaden events, or run a second backend automatically.
- Save the complete request and response. Diagnose failures from those files,
  service logs, and non-mutating GET requests.
- Render `video_summary` and every returned event verbatim. Do not paraphrase,
  truncate descriptions, add fields, or fabricate `id`.
- Direct VLM fallback requires explicit user approval unless the original
  request pre-authorized it.

## Prerequisites

- VSS `lvs` profile reachable either on Docker (`$HOST_IP:38111`) or through
  the public Ingress (`VSS_PUBLIC_URL` with Exact `/v1/ready`).
- `curl` and `jq` on the agent host.
- Network reachability from the LVS service to the final VIOS clip URL (Docker:
  from `vss-lvs`; Kubernetes: deploy must mint a URL the LVS pod can fetch).

The `vss-deploy-profile` skill can deploy the profile. A remote fallback VLM
must be able to fetch the clip URL; it generally cannot fetch localhost or
private addresses.

## Limitations

- Direct VLM fallback cannot target LVS scenarios or events and is lower
  quality.
- Private VIOS URLs may be unreachable from remote VLM endpoints.
- Each user request permits one LVS summarize POST, with no automatic retry.
- Stock LVS Helm Ingress does not publish LVS `/models`, LVS `/openapi.json`,
  `/recommended_config`, or `/metrics` — those remain Docker `:38111` only.

## Endpoint resolution (Kubernetes vs Docker)

Resolve endpoints once before probing. Follow
[`../vss-build-vision-agent/references/deployment_resolution.md`](../vss-build-vision-agent/references/deployment_resolution.md).

```bash
# Prefer VSS_PUBLIC_URL; accept legacy VSS_ENDPOINT as the same public origin.
if [ -z "${VSS_PUBLIC_URL:-}" ] && [ -n "${VSS_ENDPOINT:-}" ]; then
  VSS_PUBLIC_URL="${VSS_ENDPOINT}"
fi

if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  DEPLOYMENT_KIND="kubernetes"
  VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
  # Force public origin — ignore leftover Docker LVS_BACKEND_URL / VLM_* env.
  # Origin only — skill appends /v1/ready and /v1/summarize. Never …/v1 here.
  LVS_BACKEND_URL="${VSS_PUBLIC_URL}"
  VIDEO_SUMMARIZATION_URL="${LVS_BACKEND_URL}"
  VSS_VIOS_URL="${VSS_PUBLIC_URL}/vst"
  VST_API_BASE="${VSS_VIOS_URL}/api/v1"
  # Exact /v1/models and /v1/chat/completions → RT-VLM (not Prefix /v1).
  VLM="${VSS_PUBLIC_URL}"
else
  DEPLOYMENT_KIND="docker"
  LVS_BACKEND_URL="${LVS_BACKEND_URL:-http://${HOST_IP:-localhost}:38111}"
  VIDEO_SUMMARIZATION_URL="${LVS_BACKEND_URL}"
  VSS_VIOS_URL="http://${HOST_IP:-localhost}:30888/vst"
  VST_API_BASE="${VSS_VIOS_URL}/api/v1"
  VLM="${VLM_BASE_URL:-${RTVI_VLM_BASE_URL:-http://${HOST_IP:-localhost}:8018}}"
  VLM="${VLM%/v1}"
fi
```

On Kubernetes, do not use `kubectl port-forward`, Service DNS, NodePorts,
`docker exec`, or `docker inspect`. Do not append `/v1` to `LVS_BACKEND_URL`.
Ignore Docker-derived `LVS_BACKEND_URL` / `VLM_BASE_URL` / `RTVI_VLM_BASE_URL`
when `VSS_PUBLIC_URL` is set. Do not treat public `/openapi.json` as the LVS
schema (that path is Agent on stock Ingress).

## Routing

| Service | Base URL |
|---|---|
| LVS | `${VIDEO_SUMMARIZATION_URL}` (K8s: `${VSS_PUBLIC_URL}`; Docker: `http://${HOST_IP}:38111`) |
| VLM / RT-VLM | `${VLM}` then append `/v1/...` (K8s: public origin; Docker: `:8018`) |
| VIOS | `${VST_API_BASE}` |

Strip a trailing `/v1` from the VLM base because this skill appends it. Do not
scan ports or inspect configuration files to guess endpoints.

Probe LVS `/v1/ready` using the loop in the end-to-end reference. Readiness is
the HTTP status only: retry 503 warmup responses for about 30 seconds, and do
not inspect the body.

| LVS result | Action |
|---|---|
| HTTP 200 | Use LVS for every video duration. |
| Anything else | Ask to deploy LVS or ask before using VLM fallback. |

If LVS is unavailable, ask:

> The VSS `lvs` profile isn't reachable
> (`${VSS_PUBLIC_URL:-$HOST_IP:38111}`). Shall I deploy it now using
> `/vss-deploy-profile -p lvs`? Reply `no` to stop here; I can use the
> lower-quality VLM-only fallback only if you explicitly ask for it.

- Deployment approved or pre-authorized: invoke `vss-deploy-profile`, re-probe,
  and continue only after LVS returns 200.
- Deployment declined: ask separately whether to use VLM fallback. Stop unless
  the user approves it.
- Fallback pre-authorized: use the fallback without another prompt.
- Non-interactive run: the original task is the only approval source. If it
  pre-authorizes neither deployment nor fallback, report blocked and stop.

## Recorded Video Workflow

### Stage 1: Select the Backend

Load the end-to-end and API references. Run the LVS readiness probe before
preparing the clip. Also probe VLM `/v1/models` so an approved fallback can be
validated, but do not infer against it while LVS is ready.

Discover model IDs from the selected service:

- **Docker:** honor `${VLM_NAME}` only if it matches an id from LVS `GET /models`;
  otherwise use the sole advertised LVS id.
- **Kubernetes:** LVS `/models` is not on Ingress. Prefer `${VLM_NAME}` when set;
  otherwise take the sole id from Exact `GET ${VLM}/v1/models` (RT-VLM). If
  multiple ids exist and no valid preference selects one, report them and stop.

A non-200 LVS readiness result after warmup is the only unavailability signal.
An empty summary, empty events, missing optional fields, or empty readiness
stdout must not trigger fallback.

### Stage 2: Prepare the Video Through VIOS

Execute VIOS API operations directly as part of this workflow; do not invoke a
separate skill. Follow **Prepare the video through VIOS** in the end-to-end
reference (uses `${VST_API_BASE}`).

1. List sensors and reuse the exact requested recording when present.
2. If absent and the exact local file is available, upload it through the VIOS
   file API. For uploaded or sample media without a requested timestamp, use
   `2025-01-01T00:00:00.000Z` so timeline resolution is deterministic.
3. Poll the returned stream's timelines and obtain the complete minimum start
   and maximum end time.
4. Generate a fresh temporary MP4 URL for that full interval with audio
   disabled. Pass that minted URL into LVS **as returned** (after stripping a
   doubled `http://` scheme if present). Do not rewrite it for browser Ingress
   paths before `POST /v1/summarize`.
5. If LVS was selected, verify one-byte reachability:
   - **Docker:** `docker exec vss-lvs` Python range probe in the reference.
   - **Kubernetes:** bounded Range GET of the minted URL from the agent host
     (no `docker exec` / `kubectl exec`). Deploy must mint a URL the LVS pod
     can fetch.

Require the exact recording, full timeline, and fresh clip URL before
continuing. When the source file is available, compare VIOS timeline duration
with source duration. An upload response or byte probe proves reachability, not
complete media readiness.

If preparation fails, stop and report the missing prerequisite. Do not choose
an arbitrary `/tmp` video, alternate recording, local HTTP server, NvStreamer,
or RTSP source unless the user explicitly requested that source.

Do not use the `vss-lvs` container's lightweight `curl` shim for reachability;
it can write the entire video into tool output. Use the one-byte Python probe
on Docker.

### Stage 3: Collect LVS Settings

When LVS is selected, load the HITL reference and collect `scenario`, `events`,
and optional `objects_of_interest` before the summarize POST.

When the caller explicitly says to run autonomously without prompting and asks
for defaults or supplies no settings, use these values verbatim:

```text
scenario="activity monitoring"
events=["notable activity"]
```

This is the only HITL bypass. Do not infer defaults from filenames or sensor
names. Mention defaults in the final response and offer a separate rerun with
specific settings.

### Stage 4: Discover the Contract and Submit Once

Prefer `POST /v1/summarize`; `/summarize` is only a Docker compatibility alias
and is **not** on stock LVS Ingress.

- **Docker:** fetch `/openapi.json` from the same LVS instance immediately
  before building the operation. Confirm `/v1/summarize`, inspect its live JSON
  schema, and use that schema rather than a hardcoded copy. Discover the model
  via LVS `GET /models`.
- **Kubernetes:** do not fetch public `/openapi.json` (Agent) or LVS `/models`
  (not published). Confirm the request against the checked-in summarize
  contract in the API reference, resolve the model as in Stage 1, and POST
  Exact `/v1/summarize` on `${VIDEO_SUMMARIZATION_URL}`.

Build the request with the selected model, fresh VIOS URL, exact HITL values,
`chunk_duration: 10`, and `seed: 1`. Include `objects_of_interest` only when
provided.

Do not send `num_frames_per_second_or_fixed_frames_chunk`,
`use_fps_for_chunking`, or deprecated `num_frames_per_chunk` in the standard
workflow. RT-VLM owns frame sampling; remote Docker LVS configures five fixed
frames per chunk for endpoints with that image limit.

Use the one-request implementation in the end-to-end reference. Preserve its
HTTP status and complete body in files. Keep a long client timeout (at least
300s; Ingress allows up to 600s). After that POST:

- On curl or HTTP failure, report the exact failure and saved response.
- If `choices[0].message.content` cannot be parsed, report the exact body.
- Never repeat the POST for diagnosis. A new POST requires a separate user
  request.
- If `video_summary` and `events` are empty, inspect the same response's
  `usage.total_chunks_processed`. A positive integer confirms processing; zero
  or missing means processing was not confirmed. Do not claim "no detections."

### VLM Fallback for Stages 3-4

Use the fallback command in the end-to-end reference only when LVS remained
unavailable after warmup and the user explicitly approved fallback. Do not run
LVS HITL, and never use fallback to repair or replace an LVS response.

Before the result, include:

> **Note:** Input video `<name>` is `<N>`s long. The video summarization
> service is not deployed, so this summary was produced by the VLM alone with
> a generic default prompt. Deploy the `lvs` profile for higher-quality
> summaries with scenario/events targeting.

If the VLM cannot fetch the VIOS URL, report that blocker instead of sending
an inference request.

### Stage 5: Present the Result

Start with exactly one header:

```text
Summary of <video_name> (<duration>)
```

Use `Ns` below 60 seconds and `Mm Ss` otherwise.

For LVS, parse the JSON string in `choices[0].message.content` while preserving
top-level `usage`. Render `video_summary` verbatim, followed by every event in
service order. Preserve every returned field and the full `description`; use a
per-event list if a table would truncate text.

For VLM, render `choices[0].message.content` verbatim. For Cosmos output, omit
the `<think>...</think>` block and show the answer. Do not add emojis or
re-voice either backend's content.

## Troubleshooting

| Symptom | Action |
|---|---|
| `/v1/ready` remains 503 | Treat LVS as unavailable after the warmup loop. |
| Readiness stdout is empty | Use the HTTP status; a 200 body may be empty. |
| Summary and events are empty | Inspect saved `usage.total_chunks_processed`; do not retry. |
| VLM returns `<think>` | Remove reasoning through `</think>` when rendering. |
| K8s `/openapi.json` looks like Agent | Expected — do not use it as LVS schema. |
| K8s `/models` 404 / HTML | Expected — use Exact `/v1/models` (RT-VLM) or `VLM_NAME`. |

Use the debugging reference for deeper diagnostics and the deployment
reference for logs or configuration. Match image tags to the host: `3.3.0-rc2` on
x86/Jetson Thor and `3.3.0-rc2-sbsa` on SBSA/DGX Spark/Grace.

## Direct API and Service Operations

For direct API questions such as models, readiness, recommended configuration,
metrics, schemas, or 422 responses, use the API reference instead of the
recorded-video workflow. On Kubernetes, only Exact `/v1/ready` and
`/v1/summarize` are public for LVS; other LVS admin routes need Docker
`:38111` or a chart change. For deployment, restart, teardown, backend
selection, or service logs, prefer `vss-deploy-profile` and use the deployment
reference.

## Cross-reference

- `vss-deploy-profile`: deploy the `lvs` profile.
- `vss-manage-video-io-storage`: general VIOS administration outside this
  ordered workflow.
- `vss-search-archive`: search archived video.
- `vss-query-analytics`: query stored incidents and events.
- `vss-generate-video-report`: Mode A delegates here when LVS `/v1/ready` is 200.

bump:3
