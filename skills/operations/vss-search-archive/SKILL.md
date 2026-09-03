---
name: vss-search-archive
description: Use this skill when a user wants to search archived VSS video or ingest or delete a source for search. Do not use it for visual Q&A, live captioning, or video summarization.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.3.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

## Shell contract

Run every fenced `bash` recipe with Bash. If a command-string exec tool may
default to POSIX `sh`, invoke the recipe through `bash -lc` or as a Bash script;
never submit Bash syntax directly to that default shell.

## Purpose

Operate archive search from the caller's host. Compose and Kubernetes use the
same `vss configure` and `vss search run` commands; only the deployment origin
differs. Source ingestion and deletion are Agent-backed **when the deployment has
an agent `/api` route**; on a build without one, they belong to
`vss-manage-video-io-storage` `references/provision-vios-source.md`.

## Hard boundaries

- Run the project-local CLI on the host. Never use `docker exec`, `kubectl
  exec`, a pod shell, or a globally installed `vss` as a substitute.
- Never improvise a mutation against Elasticsearch, RTVI-CV, RTVI-Embed,
  storage-ms, or VST. Two paths are sanctioned, and the deployment picks which:
  the Agent upload/delete lifecycle where an agent `/api` route answers, and
  `vss-manage-video-io-storage` `references/provision-vios-source.md` where none
  does. That recipe owns the direct calls this rule otherwise forbids.
- Never remove, broaden, or silently substitute a requested source constraint.
- Similarity is retrieval evidence, not proof of visual presence.
- When the capability receipt enables VSS UI artifacts, publishing the exact
  validated search result is part of a successful search. Do not finish with
  prose alone.
- The CLI attempts critic verification by default. Do not separately inspect
  screenshots or call another verifier during the initial search turn.
- Offer delegated verification only when every displayed result is
  `unverified`, and only after displaying them and receiving explicit user
  confirmation. If any result is `confirmed` or `rejected`, do not hand off
  any result to another verifier.

## Prerequisites

- A running VSS `search` profile and its host-reachable Compose or Ingress
  origin.
- A checkout containing `services/agent`, host `uv`, `curl`, and `jq`.
- `vss vios list` for source listing and inspection (same CLI, same recorded origin).

Resolve and validate the checkout once:

```bash
VSS_CAPABILITY_RECEIPT="${VSS_CAPABILITY_RECEIPT:-${HOME}/.vss/agent-capabilities.json}"
if [ -z "${VSS_REPO_ROOT:-}" ] && [ -f "$VSS_CAPABILITY_RECEIPT" ]; then
  VSS_REPO_ROOT=$(jq -er '.runtime.repo_root | select(type == "string" and length > 0)' \
    "$VSS_CAPABILITY_RECEIPT") || exit 1
fi
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly" >&2
  exit 1
}
VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
cd "${VSS_REPO_ROOT}" && "${VSS[@]}" search run --help >/dev/null || exit 1
```

`--extra cli` is mandatory because the base distribution contains the core
libraries, while `nvidia-vss-cli` declares the `vss` executable.

Resolve the deployment through its one public/host origin:

```bash
if [ -z "${VSS_ORIGIN:-}" ] && [ -f "$VSS_CAPABILITY_RECEIPT" ]; then
  VSS_RECEIPT_ORIGIN=$(jq -er '(.vss_origin // "") | select(type == "string")' \
    "$VSS_CAPABILITY_RECEIPT") || exit 1
  if [ -n "$VSS_RECEIPT_ORIGIN" ]; then
    VSS_ORIGIN="$VSS_RECEIPT_ORIGIN"
  fi
fi
if [ -z "${VSS_ORIGIN:-}" ]; then
  VSS_ORIGIN=$("${VSS[@]}" configure show 2>/dev/null |
    jq -er '.base_url | select(type == "string" and length > 0)') || true
fi
if [ -z "${VSS_ORIGIN:-}" ] && [ -n "${HOST_IP:-}" ]; then
  VSS_ORIGIN="http://${HOST_IP}:7777"
fi
if [ -z "${VSS_ORIGIN:-}" ]; then
  echo "Provide the Compose or Ingress origin" >&2
  exit 1
fi
VSS_ORIGIN="${VSS_ORIGIN%/}"
VST_URL="${VSS_ORIGIN}"
VSS_VIOS_URL="${VSS_ORIGIN}/vst"
"${VSS[@]}" configure --base-url "${VSS_ORIGIN}" || exit 1
```

In a persisted multi-step workflow, reuse the origin recorded by the prepared
deployment as above. Do not repeat public-origin selection, edit routing, or
redeploy merely because the next agent turn did not inherit shell variables.

See [deployment resolution](../../vss-build-vision-ai/references/deployment_resolution.md)
for the deployment-owned `VSS_PUBLIC_URL` contract. On Kubernetes, never use
port-forwarding, Service DNS, NodePorts, or a guessed Helm release. Routes not
exposed through the Ingress are recorded as absent and a search path needing
one exits 4.

For deployment readiness, ingestion, fixture cleanup, index checks, RTSP, or
deletion, read [source lifecycle](references/source_lifecycle.md) completely
before acting. Re-run `vss configure` after ingestion because the recorded
index inventory is a snapshot.

## Mandatory search workflow

1. Confirm the selected deployment is the `search` profile. If required routes
   are unavailable, ask whether to reconnect or deploy it with
   `vss-deploy-profile -p search`; do not target another profile.

2. When the user names a file, camera, or sensor, list registered sources with
   `"${VSS[@]}" vios list` before invoking the search CLI — it reads the origin
   `vss configure` recorded, so it takes no endpoint. Accept only an exact
   source, stream ID, or one unambiguous normalized substring match.

   - No match: report the missing source, list available names, and ask the
     user to clarify or explicitly request ingestion. Stop without probing the
     search CLI, deploying, or ingesting. **Never continue with a different
     source.** Answering about `warehouse_sample` when the request named
     `warehouse-ladder` returns a confident answer about the wrong video, and
     nothing downstream can tell it was substituted.
   - Several matches: ask the user to choose and stop.
   - Never substitute another video or run an unrestricted search as a probe.

   Preserve both the matched source's `.sensorId` and `.name`. The required
   `--video-source` value depends on the search path, not the source type:
   `embed` and `fusion` use the sensor ID; `attribute` and `object` use the
   name. The CLI matches this value literally and does no name↔ID conversion.
   Set `--source-type video_file` for uploads or `--source-type rtsp` for live
   streams; this chooses the index partition independently of the identifier.

3. Preserve the complete object/action, source, time bounds, result limit, and
   visual attributes. Choose one path:

   - text query only → `run embed`
   - visual attributes only → `run attribute`
   - text query plus attributes → `run fusion`
   - explicit tracked object IDs → `run object`

   `--attribute` is for specific detectable properties such as `white jacket`
   or `red hard hat`, not generic nouns or actions. Keep `red forklift` wholly
   in `--query`. For `person in a red jacket running`, preserve the action and
   attribute: `run fusion --query "person in a red jacket running" --attribute
   "red jacket"`.

4. Construct the invocation as a Bash array and validate only its exact
   stdout. Read [CLI usage](references/cli_usage.md) for every supported flag.

```bash
: "${SEARCH_PATH:?set embed|attribute|fusion|object}"
: "${SOURCE_TYPE:?set video_file or rtsp}"
TOP_K="${TOP_K:-3}"
VIDEO_SOURCES=() # sensor IDs for embed/fusion; names for attribute/object
: "${SOURCE_SCOPED:?set true for a resolved scope; false only when unrestricted}"
if [ "${SOURCE_SCOPED}" = true ] && [ "${#VIDEO_SOURCES[@]}" -eq 0 ]; then
  echo "Resolved source scope is empty; refusing an unrestricted search" >&2
  exit 1
fi
SEARCH_COMMAND=(
  "${VSS[@]}" search run "${SEARCH_PATH}"
  --source-type "${SOURCE_TYPE}" --top-k "${TOP_K}" --raw
)
for source in "${VIDEO_SOURCES[@]}"; do
  SEARCH_COMMAND+=(--video-source "${source}")
done
# Append --query, repeatable --attribute, --object-id, and time bounds as needed.
if ! SEARCH_STREAM=$("${SEARCH_COMMAND[@]}"); then
  echo "Search command failed" >&2
  exit 1
fi

# Every job command writes exactly two JSON documents: its result body on the
# first line and the compact lifecycle completion marker on the final line.
# Preserve the first line byte-for-byte for the UI artifact, while requiring
# the marker to prove that the successful body belongs to a completed search.
mapfile -t SEARCH_DOCUMENTS <<<"${SEARCH_STREAM}"
if [ "${#SEARCH_DOCUMENTS[@]}" -ne 2 ]; then
  echo "Search did not return one result body and one completion marker" >&2
  exit 1
fi
SEARCH_JSON=${SEARCH_DOCUMENTS[0]}
SEARCH_COMPLETION=${SEARCH_DOCUMENTS[1]}
SEARCH_JOB_ID=$(printf '%s' "${SEARCH_JSON}" |
  jq -er 'select(type == "object" and (.data | type == "array")) |
          .job_id | select(type == "string" and length > 0)') || {
    echo "Search did not return a SearchOutput object with a data array and job_id" >&2
    exit 1
  }
printf '%s' "${SEARCH_COMPLETION}" |
  jq -e --arg job_id "${SEARCH_JOB_ID}" '
    type == "object" and
    .event == "vss_job_completed" and
    .group == "search" and
    .job_id == $job_id and
    .status == "completed" and
    .exit_hint == 0' >/dev/null || {
    echo "Search completion marker did not validate" >&2
    exit 1
  }

# Publish from the same exec result that validated the search. Harnesses that
# expose server-tool output let the gateway consume this envelope directly.
VSS_CAPABILITY_RECEIPT="${VSS_CAPABILITY_RECEIPT:-${HOME}/.vss/agent-capabilities.json}"
if [ -f "$VSS_CAPABILITY_RECEIPT" ] &&
   jq -e '.ui_artifacts.version == "1.0"' "$VSS_CAPABILITY_RECEIPT" >/dev/null; then
  VSS_UI_ARTIFACT=$(jq -cn --argjson payload "$SEARCH_JSON" \
    '{version:"1.0",kind:"vss.search.results",payload:$payload}') || exit 1
  printf '<vss-ui-artifact>%s</vss-ui-artifact>\n' "$VSS_UI_ARTIFACT"
fi
```

Do not pass endpoint, index, model, deployment, profile, or base-URL flags to
`search run`; `vss configure` owns those values. Do not replace a failed CLI
call with `/api/v1/search` or private backend access.

5. Validate each nonempty hit's exact returned `screenshot_url` with a bounded
GET for availability only. Its normalized scheme, host, and effective port
always match the origin recorded by `vss configure`, because the CLI stamps
that origin into every hit — a localhost media URL means the deployment was
configured against a localhost origin, not that the URL is malformed. On Brev,
prefer the public HTTPS secure-link origin. If setup used the documented
host-reachable fallback after its one bounded public probe failed, accept only
that exact recorded origin and label its media URLs host-local; do not restart
routing diagnosis. Reject credentials in the URL and never rewrite the URL or
add a `streamId` routing header. Discard the response body; availability is not
visual evidence.

6. Read every hit's `verification` object:

   - `confirmed`: the critic found all requested visual criteria in that clip.
   - `rejected`: the critic found a visual criterion was not met.
   - `unverified`: no usable critic verdict was produced. This includes a
     missing VLM, inaccessible media, and malformed or inconclusive output.

The CLI is fail-open: verification failure must not discard or fail retrieval.
Never derive a verdict from similarity, filenames, object IDs, or screenshot
availability. Treat boolean `criteria_met` values as critic evidence only.

7. Format nonempty results without user-visible raw JSON:

```text
## Video Search Results
<each hit's exact source, start/end, similarity, complete media URL,
verification result, and criteria when present>

Similarity scores are retrieval evidence; the separate verification result
records whether the bounded clip satisfied the visual request.

## Verification Step
Would you like me to verify the unverified search results?
```

Include `## Verification Step` only when the nonempty displayed result set is
entirely `unverified`. If any displayed result is `confirmed` or `rejected`,
omit it even when other hits are unverified. Never deploy a VLM or call
`vss-ask-video` automatically during this results turn.

When the search command prints the machine-readable envelope, publish its
exact JSON object without reconstructing, summarizing, fencing, or modifying
the payload:

- If `vss_ui_publish_artifact` is an available tool, call it exactly once with
  that JSON object. After its success result, finish the human-facing answer
  without copying the XML envelope into prose.
- Otherwise, copy the command's single envelope line verbatim into the final
  response. The gateway strips it from prose when the upstream exposes only
  final text.

The gateway emits `artifact.created`, and the Search tab consumes the payload
to render result cards. Never publish an artifact when the search command or
JSON validation failed.

8. If the user explicitly confirms, read
[search-result verification](references/result_verification.md) completely and
delegate the displayed hits only after confirming again that every one is
still `unverified`. Preserve their exact bounded intervals and the complete
original visual intent. Keep at most three delegations in flight. Never hand
off a partially verified result set.

9. If `.data` is empty, report zero candidates faithfully and emit the same
   `vss.search.results` artifact with its empty `data` array when UI artifacts
   are enabled. Do not claim that the object is absent; offer a specific query
   or similarity-threshold refinement while preserving the source. Never
   broaden the search silently.

## Natural-language Agent responses

Use the host CLI for deterministic structured search. If a caller explicitly
requires the deployment Agent to decompose a natural-language request, its
`/api/v1/search` response is conversational text, not `SearchOutput`. Validate
the known text field and present it as prose; never run `.data[]`, screenshot,
or verification parsing against that response or invent structured hit rows.

## Troubleshooting

- CLI unavailable: retain `--extra cli`, verify `VSS_REPO_ROOT`, and stop.
- Exit 2: read the selected path's `--help`; do not guess flags.
- Exit 3: a recorded backend is unreachable; repair routing and reconfigure.
- Exit 4: run `vss configure --base-url <origin>` or choose a path whose
  required services are actually routed.
- Exit 5: ingest the source, wait for readiness, and re-run `vss configure`.
- Missing/ambiguous source: stop for clarification; never substitute.
- Missing RT-VLM: retrieval remains valid and results remain `unverified`.
- Authentication: use the operator-approved route. Never place secrets in
  prompts, flags, generated files, logs, or skill output.
