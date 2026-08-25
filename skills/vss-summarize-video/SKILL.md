---
name: vss-summarize-video
description: Use when summarizing a recorded video through HITL-gated LVS, with an explicitly approved VLM fallback. Not for reports, archive search, or live RTSP captioning.
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

## When to Use

Use when summarizing a recorded video through HITL-gated LVS, to produce one
polished narrative summary with timestamped events when LVS is available.

Do not use this skill for:

- Live RTSP captioning: use `vss-deploy-dense-captioning`.
- Incident or alert-window reports: use `vss-generate-video-report` Mode B.
- Archive search: use `vss-search-archive`.

## Required References

Load these files only as directed:

- [`references/end-to-end-example.md`](references/end-to-end-example.md): load
  before executing the recorded-video workflow. It contains the exact
  readiness, VIOS preparation, single-run summarize, and VLM fallback commands.
- [`references/cli_usage.md`](references/cli_usage.md): load before Stage 4.
  `vss summarize run` issues the summarize request and persists the result;
  this reference has its flags, exit codes, output shape, and read verbs.
- [`references/video-summarization-api.md`](references/video-summarization-api.md):
  load before constructing a live LVS operation **by hand** — a direct API
  question, or the approved VLM fallback. Follow its **Runtime OpenAPI
  Discovery** procedure on Docker. On Kubernetes, follow the K8s note there —
  stock LVS Ingress does not publish LVS `/openapi.json`. The ordered
  workflow does not build a summarize payload; the CLI owns that.
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
- [`references/deploy-lvs-service.md`](references/deploy-lvs-service.md): load
  when asked about LVS's own container image, GPU/CPU/storage sizing, or
  deployment contract as a peer service (heavier than
  `video-summarization-deployment.md`, which covers operating an already
  running deployment).
- [`references/integrate-lvs-service.md`](references/integrate-lvs-service.md):
  load when another agent or skill needs to integrate with LVS as a peer
  service — required peers, integration interfaces, API schema, and network
  requirements.

## Core Invariants

- Route by LVS readiness, never by video duration.
- HTTP 200 from `/v1/ready` selects LVS. Empty response bodies do not mean
  unavailable.
- Once LVS is selected, do not call a VLM `/v1/chat/completions` endpoint.
- Issue exactly one `vss summarize run` per user summarize request. One run is
  one `POST /v1/summarize`. Never retry, hedge, broaden events, or run a second
  backend automatically.
- Endpoints come from the deployment `vss configure` recorded. Never pass an
  endpoint, index, or model flag the caller did not name, and never replace a
  failed run with hand-rolled curl against `/v1/summarize`.
- Save the complete command and its stdout. Diagnose failures from those files,
  the run's own exit code, service logs, and non-mutating GET requests.
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
- A checkout containing `services/agent` and host `uv`, for `vss summarize run`.
- One recorded deployment origin. Configure it once, before Stage 4:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly" >&2
  exit 1
}
VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
cd "${VSS_REPO_ROOT}" && "${VSS[@]}" summarize run --help >/dev/null || exit 1
# Compose publishes the ingress on :7777; Kubernetes uses VSS_PUBLIC_URL.
VSS_ORIGIN="${VSS_PUBLIC_URL:-http://${HOST_IP:-localhost}:7777}"
"${VSS[@]}" configure --base-url "${VSS_ORIGIN%/}" || exit 1
```

`--extra cli` is mandatory: the base distribution holds the core libraries,
while `nvidia-vss-cli` declares the `vss` executable. Configure against the
ingress origin, never `:38111` — that LVS container port exposes no
Elasticsearch, so a deployment recorded from it cannot persist.

The `vss-deploy-profile` skill can deploy the profile. A remote fallback VLM
must be able to fetch the clip URL; it generally cannot fetch localhost or
private addresses.

## Limitations

- Direct VLM fallback cannot target LVS scenarios or events and is lower
  quality.
- Private VIOS URLs may be unreachable from remote VLM endpoints.
- Each user request permits one `vss summarize run`, with no automatic retry.
- Persistence needs a routed Elasticsearch. A deployment without one summarizes
  and reports the result unpersisted rather than failing the job.
- Both edges are configured to wait an hour, matching the CLI's own default, so
  a long summarization is not cut short by a 504 that would be recorded as a
  failed job. An Ingress the deployment overrides shorter still caps the wait.
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

Load the end-to-end and CLI references. Run the LVS readiness probe before
preparing the clip. Also probe VLM `/v1/models` so an approved fallback can be
validated, but do not infer against it while LVS is ready.

The summarization model needs no discovery: `vss configure` recorded the id LVS
reports serving, and `vss summarize run` defaults to it on both Docker and
Kubernetes. Pass `--model` only when the caller named one, and read the recorded
value from `vss configure show` when it has to be reported.

Discover a model id by hand only for an approved VLM fallback, which does not
run through the CLI:

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
   disabled. Pass that minted URL to `--url` **as returned** (after stripping a
   doubled `http://` scheme if present). Do not rewrite it for browser Ingress
   paths before the summarize run.
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
and optional `objects_of_interest` before the summarize run.

When the caller explicitly says to run autonomously without prompting and asks
for defaults or supplies no settings, use these values verbatim:

```text
scenario="activity monitoring"
events=["notable activity"]
```

This is the only HITL bypass. Do not infer defaults from filenames or sensor
names. Mention defaults in the final response and offer a separate rerun with
specific settings.

### Stage 4: Submit Once Through the CLI

Load the CLI reference. `vss summarize run` issues the summarize request on both
Docker and Kubernetes, and persists the result to unified memory. Do not build a
`/v1/summarize` payload by hand, and do not fetch `/openapi.json` to construct
one — the CLI owns the request shape, and `vss configure` owns the endpoint.

Use the invocation in the end-to-end reference. It passes the fresh VIOS URL
from Stage 2, the exact HITL values from Stage 3, `--chunk-duration 10`, and
`--seed 1`; repeat `--event` per event and add `--object-of-interest` only when
the caller provided objects. Pass no endpoint flag.

Persistence is on by default and needs two values:

- `--video-id`, required alongside `--url`. Use the recording's VIOS **sensor**
  id — from `sensor/list`, or from the `sensorId` an upload returns — never the
  stream id. It becomes the record's sensor, which is what `list --sensor-id`
  and time-windowed recall key on. Without `--video-id` the run exits 2 before
  summarizing rather than after.
- `--creation-time`, the media's absolute start. LVS reports event times as
  offsets into the clip unless this anchors them, and unified memory stores
  instants — so without it the events cannot be written and the run degrades to
  exit 6. For uploaded sample media use the same `2025-01-01T00:00:00.000Z`
  Stage 2 uploaded with.

Do not pass `--num-frames-per-chunk` in the standard workflow. RT-VLM owns frame
sampling; unset fields are absent from the request, so the deployment's own
default applies.

The final line of stdout is one JSON object naming the job. Read that line and
the exit code; the prose on stderr is a diagnostic, not the result. A call
refused before a job exists prints no marker at all and stderr is the whole
result — check stdout is non-empty before parsing it. Emptiness, not the exit
code, is what says whether a job was created.

| exit | meaning | action |
|---|---|---|
| 0 | summarized and persisted | present the result |
| 2 | a flag the CLI refused, before anything was submitted | fix the call, then run once |
| 2 | LVS rejected the request it was sent; the marker names a job closed as failed | report the failure with that `job_id` |
| 3 | LVS unreachable or returned 5xx | report it with the marker's `job_id` |
| 4 | nothing configured, or `lvs`/`elasticsearch` not routed | no job, no marker — `vss configure`, then run once |
| 6 | summary produced, persistence failed | present the summary; report it unpersisted |
| 7 | timed out | reconcile with `vss summarize get --job-id`; do not re-run |

Exits 6 and 7 both mean the summarization already happened. Never repeat the run
to obtain a different view of it, and never repeat it for diagnosis — a second
run requires a separate user request. The exit 2 that carries a marker is the
same story earlier: the request reached LVS and came back refused, so the one
submission this request had is spent and a corrected call belongs to a new
request. Once a job exists, every outcome names its `job_id`; use it rather than
re-running. A call refused before a job exists names
nothing, which is why empty stdout is the test.

The marker's `persist` object is the report on the write: `status` is `complete`
with the index it landed in and how many events went with it, and exit 6 says the
summary survived but the write did not. Alongside it on every marker, `record`
says what the `job_id` is worth to a later read — `closed`, `absent` when nothing
was persisted, or `stale` when the record still reads `submitted` and `status`
would therefore call the job running. Do not read the record back to confirm
it, and never read Elasticsearch directly — recalling memory is a separate
skill's job. The one read that belongs here is reconciling an exit 7, whose
outcome is genuinely unknown until `vss summarize get --job-id <job_id>` answers.

If `video_summary` and `events` are empty, inspect the same payload's
`summary.usage.total_chunks_processed`. A positive integer confirms processing;
zero or missing means processing was not confirmed. Do not claim "no
detections."

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

For LVS, the CLI nests the service's own envelope under `summary`: parse the
JSON string in `summary.choices[0].message.content` while preserving
`summary.usage`. Render `video_summary` verbatim, followed by every event in
service order. Preserve every returned field and the full `description`; use a
per-event list if a table would truncate text.

Close with the job's identity: the `job_id` and whether the record persisted.
State the summary is unpersisted whenever `persist.status` is not `complete`,
including the exit-6 case, instead of implying it was stored.

For VLM, render `choices[0].message.content` verbatim. For Cosmos output, omit
the `<think>...</think>` block and show the answer. Do not add emojis or
re-voice either backend's content.

## Troubleshooting

| Symptom | Action |
|---|---|
| `/v1/ready` remains 503 | Treat LVS as unavailable after the warmup loop. |
| Readiness stdout is empty | Use the HTTP status; a 200 body may be empty. |
| Summary and events are empty | Inspect saved `summary.usage.total_chunks_processed`; do not retry. |
| `vss` not found | Keep `--extra cli` and verify `VSS_REPO_ROOT`; never install globally. |
| Run exits 4 | `vss configure --base-url <ingress origin>`; `:38111` routes no memory. |
| Run exits 6 | Persistence failed. Present the summary; do not re-run the job. |
| Run exits 7 | Timed out. `vss summarize get --job-id <id>`; do not re-run. |
| VLM returns `<think>` | Remove reasoning through `</think>` when rendering. |
| K8s `/openapi.json` looks like Agent | Expected — do not use it as LVS schema. |
| K8s `/models` 404 / HTML | Expected — use Exact `/v1/models` (RT-VLM) or `VLM_NAME`. |

Use the debugging reference for deeper diagnostics and the deployment
reference for logs or configuration. The LVS image is a multi-arch manifest, so
`LVS_TAG=3.3.0-rc2` is the x86/Jetson Thor default; use `3.3.0-rc2-sbsa` on SBSA/DGX Spark/Grace. RT-VLM likewise needs a host-matched tag (`3.3.0-26.08.2` on x86/Jetson Thor, `3.3.0-26.08.2-sbsa` on SBSA/DGX Spark/Grace).

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
