---
name: vss-search-archive
description: Use this skill when a user wants to search archived VSS video or ingest or delete a source for search. Do not use it for visual Q&A, live captioning, or video summarization.
license: Apache-2.0
metadata:
  author: "NVIDIA Video Search and Summarization team"
  version: "3.4.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

## Purpose

Operate archive search from the caller's host without entering VSS containers
or pods. Docker Compose uses the checked-out `vss search run` CLI. Kubernetes
uses the public VSS Agent Ingress for search, ingestion, deletion, and VIOS
access; it never starts a `kubectl port-forward`. Both paths retain the
agent-backed source ingestion and deletion lifecycle.

## Prerequisites

- A running VSS search deployment.
- For Kubernetes, its public Ingress origin in `VSS_PUBLIC_URL`.
- For Docker Compose, a checkout containing `services/agent`, host `uv`, and
  Docker access.
- The `vss-manage-video-io-storage` skill for source listing and inspection.
- `curl` and `jq`. Ordinary search needs no API key.

Do not execute `vss` inside a distroless VSS container or a pod. Do not
wrap it with `docker exec`, `kubectl exec`, or `sh -lc`.

For a Docker deployment, `vss` does not need to be installed globally and
`which vss` is not an availability check. Resolve the checkout once, allowing
the operator to override Harbor's default, and validate it before the first
Docker search:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || {
  echo "VSS checkout not found at ${VSS_REPO_ROOT}; set VSS_REPO_ROOT explicitly" >&2
  exit 1
}
cd "${VSS_REPO_ROOT}" &&
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss search run --help
```

`$HOME/video-search-and-summarization` is only the Harbor/default workspace;
set `VSS_REPO_ROOT` for a checkout elsewhere. If validation or the command
fails, report the error and stop. Do not manually call Docker search backends.

For Kubernetes, do not require a repository checkout, `uv`, Docker, or
`kubectl`. The public Agent endpoint is the supported search interface.

## Deployment prerequisite

This skill requires the VSS **search** profile. Resolve its public or Compose
endpoints exactly once. See
[Deployment resolution](references/deployment_resolution.md) for the shared
`VSS_PUBLIC_URL` ↔ `VST_EXTERNAL_URL` mapping and derived variables
(`VSS_VIOS_URL`, `VST_API_BASE`):

```bash
if [ -n "${VSS_PUBLIC_URL:-}" ]; then
  DEPLOYMENT_KIND="kubernetes"
  VSS_PUBLIC_URL="${VSS_PUBLIC_URL%/}"
  AGENT_URL="${VSS_PUBLIC_URL}"
  VST_URL="${VSS_PUBLIC_URL}"
  VSS_VIOS_URL="${VSS_PUBLIC_URL}/vst"
else
  DEPLOYMENT_KIND="docker"
  : "${VSS_REPO_ROOT:=${HOME}/video-search-and-summarization}"
  PROFILE="${PROFILE:-search}"
  DOCKER_ENDPOINTS=$(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
    python -c 'import json,sys; from vss_cli.deployment import discover_docker_host_endpoints; print(json.dumps(discover_docker_host_endpoints(sys.argv[1])))' \
    "${PROFILE}") || exit 1
  AGENT_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.agent_url') || exit 1
  VST_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.vst_url') || exit 1
  VSS_VIOS_URL="${VST_URL%/}/vst"
  ES_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.es_url') || exit 1
  RTVI_VLM_URL=$(printf '%s' "${DOCKER_ENDPOINTS}" | jq -er '.rtvi_vlm_url') || exit 1
fi
```

For Kubernetes, `VSS_PUBLIC_URL` must be an operator-provided public Ingress
origin. Never run `kubectl port-forward`, use an in-cluster Service name,
guess a NodePort, or derive a Helm release name. Elasticsearch and RTVI remain
private implementation details and are not host-side Kubernetes prerequisites.
Authentication, if configured, must use the operator's approved mechanism and
must not be copied into prompts or logs.

The deployment is not ready for archive search until its public VIOS route is
reachable from the host that will consume search results. For Kubernetes this
is `${VSS_VIOS_URL}`. For Docker, use the fully expanded
`VST_EXTERNAL_URL`. For a Brev deployment, follow `vss-deploy-profile`'s secure-link setup (including
making `BREV_ENV_ID` from `/etc/environment` and any platform-provided
`BREV_LINK_DOMAIN` available to the deployment command) and require an HTTPS
public origin, not localhost or an internal IP. Domain selection belongs to the
deployment workflow: do not construct a Brev hostname in this skill. After local
agent and VST health succeeds, read the fully expanded generated public origin
and validate it with a bounded GET. Stop or repair deployment routing within the
bounded setup deadline when that request fails; do not download or ingest sample
media first. Do not rewrite returned media URLs after search to compensate for a
bad deployment.
Before an agent-backed source mutation:

1. Probe the stack:
   ```bash
   if [ "${DEPLOYMENT_KIND}" = "kubernetes" ]; then
     curl -sfS --max-time 5 "${AGENT_URL}/openapi.json" >/dev/null \
       && curl -sfS --max-time 5 "${VSS_VIOS_URL}/api/v1/sensor/version" >/dev/null
   else
     curl -sfS --max-time 5 "${AGENT_URL}/health" >/dev/null \
       && curl -sfS --max-time 5 "${VSS_VIOS_URL}/api/v1/sensor/version" >/dev/null \
       && curl -sfS --max-time 5 "${ES_URL}/" >/dev/null \
       && curl -sfS --max-time 5 "${RTVI_VLM_URL}/v1/models" >/dev/null
   fi
   ```
   Direct Elasticsearch and RT-VLM probes are Docker-only. On Kubernetes the
   public Agent owns those private dependencies; do not expose or forward them
   merely to satisfy a host-side readiness check.

2. **If the probe fails**, ask the user:
   > *"The selected VSS `search` profile endpoints are not reachable. Shall I deploy or reconnect it using the `/vss-deploy-profile` skill with `-p search`?"*

   - If yes → hand off to the `/vss-deploy-profile` skill. Return here once it succeeds.
   - If no → stop. Do not run this skill against a missing or wrong-profile stack.

   (If the caller has granted explicit pre-authorization to deploy autonomously,
   skip confirmation and invoke `/vss-deploy-profile` directly.)

3. If the probe passes, proceed.

---

## Ingestion prerequisite

For a source to be searchable it must be ingested **through the VSS agent
backend**, not through VIOS alone. The agent's ingest routes own the VIOS upload
RTVI-CV registration + RTVI-Embed pipeline as one transaction; a bare VIOS
PUT only stores the bytes and never wires them into Elasticsearch.

Confirm the source exists in VIOS first (Mandatory workflow step 2). If it is
missing, ingest it with one of the recipes below before running `vss search
run`. After ingestion succeeds, the source appears in `sensor/list` under the
name you provided and can be selected with `--video-source`.

### File upload — universal three-step flow

This three-step flow is mandatory for every file ingestion performed while
following this skill. Never call the deprecated single-step
`PUT /api/v1/videos-for-search/{filename}` route, even if it is available: that
compatibility route does not provide the separate `/complete` response this
workflow must validate.

Use the timestamped upload form below. The VSS agent/search profile uses
`2025-01-01T00:00:00.000Z` as the uploaded `video_file` base timestamp; VIOS
storage and embeddings must share that timeline, otherwise screenshot URLs and
visual-verification frame fetches can fail.

```bash
FILE_PATH="/path/to/<filename.mp4>"
[ -f "${FILE_PATH}" ] && [ -r "${FILE_PATH}" ] \
  || { echo "Upload failed: file is missing or unreadable: ${FILE_PATH}"; exit 1; }
SOURCE_FILENAME=$(basename -- "${FILE_PATH}")
# Optional: set UPLOAD_FILENAME before this recipe to give the registered source
# a canonical name. Use the same value for every upload and completion field.
UPLOAD_FILENAME="${UPLOAD_FILENAME:-${SOURCE_FILENAME}}"

# 1. Ask the agent for the chunked-upload URL
UPLOAD_REQUEST=$(jq -cn --arg filename "${UPLOAD_FILENAME}" '{filename: $filename}')
UPLOAD_URL_RESPONSE=$(curl -sfS --max-time 30 -X POST "${AGENT_URL}/api/v1/videos" \
  -H "Content-Type: application/json" \
  -d "${UPLOAD_REQUEST}")
UPLOAD_URL=$(printf '%s' "${UPLOAD_URL_RESPONSE}" \
  | jq -er '.url | select(type == "string" and length > 0)') \
  || { echo "Upload failed: invalid upload URL response: ${UPLOAD_URL_RESPONSE}"; exit 1; }

# 2. Chunked POST the file to that VST URL (nvstreamer protocol).
#    The final-chunk response carries sensorId.
IDENTIFIER=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
UPLOAD_RESPONSE=$(curl -sfS --connect-timeout 10 --max-time 300 -X POST "${UPLOAD_URL}" \
  -H "nvstreamer-chunk-number: 1" \
  -H "nvstreamer-total-chunks: 1" \
  -H "nvstreamer-is-last-chunk: true" \
  -H "nvstreamer-identifier: ${IDENTIFIER}" \
  -H "nvstreamer-file-name: ${UPLOAD_FILENAME}" \
  -F "mediaFile=@${FILE_PATH};filename=${UPLOAD_FILENAME}" \
  -F "filename=${UPLOAD_FILENAME}" \
  -F 'metadata={"timestamp":"2025-01-01T00:00:00"}')

# 3. Tell the agent the upload finished — this fans out to RTVI-CV + RTVI-Embed
SENSOR=$(printf '%s' "${UPLOAD_RESPONSE}" \
  | jq -er '.sensorId | select(type == "string" and length > 0)') \
  || { echo "Upload failed: no sensorId in response: ${UPLOAD_RESPONSE}"; exit 1; }
COMPLETE_RESPONSE=$(printf '%s' "${UPLOAD_RESPONSE}" \
  | jq --arg filename "${UPLOAD_FILENAME}" '. + {filename: $filename}' \
  | curl -sfS --connect-timeout 10 --max-time 300 -X POST "${AGENT_URL}/api/v1/videos/${SENSOR}/complete" \
      -H "Content-Type: application/json" \
      -d @-)
printf '%s' "${COMPLETE_RESPONSE}" \
  | jq -e --arg sensor "${SENSOR}" \
      '.sensor_id == $sensor and (.chunks_processed | type == "number" and . > 0)' \
      >/dev/null \
  || { echo "Upload completion failed validation: ${COMPLETE_RESPONSE}"; exit 1; }
printf '%s' "${COMPLETE_RESPONSE}" | jq .
```

By default, the upload name is the source file basename. When a stable alias is
needed, set `UPLOAD_FILENAME` (including the extension) before the recipe. The
upload request, VST chunk metadata, multipart filename, and `/complete` body must
all use that same value; mixing names can produce incompatible VST, behavior,
and raw-index source identifiers.

The validated `/complete` response proves that embedding generation processed
at least one chunk. Probe the required final state once and continue immediately
when it is already ready. Otherwise, retry only the incomplete condition with
bounded requests and backoff for at most five minutes. Embed search requires
documents in the configured embedding index for the resolved sensor UUID.
Attribute or fusion search additionally requires behavior documents under
`sensor.id.keyword` and raw documents under `sensorId.keyword`. Record the exact
identifier used by each index instead of assuming that the VST name, sensor UUID,
behavior identifier, and raw identifier are textually identical. RTVI-CV
registration is asynchronous, so `/complete` alone does not prove those two
indexes are ready.

For a deployed Docker profile, resolve the endpoints and all three indexes in
one operation from the same sources used by `vss`. This recipe is Docker-only;
never call Kubernetes discovery from this skill. Do not reuse
`ELASTIC_SEARCH_INDEX` for behavior or raw-data checks: that variable names only
the video embedding index.

```bash
PROFILE="${PROFILE:-search}"
RUNTIME_JSON=$(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  python -c 'import json,sys; from cli.deployment import discover_docker,discover_docker_host_endpoints; from lib.search_core.runtime import RuntimeSnapshot; d=discover_docker(sys.argv[1]); r=RuntimeSnapshot.from_config_file(d.config_path, env=d.env).runtime; h=discover_docker_host_endpoints(sys.argv[1]); print(json.dumps({"agent_url":h["agent_url"],"es_url":h["es_url"],"vst_url":h["vst_url"],"rtvi_vlm_url":h["rtvi_vlm_url"],"vst_external_url":r.vst_external_url,"video_embed_index":r.video_embed_index,"behavior_index":r.behavior_index,"raw_index":r.frames_index})); d.close()' \
  "${PROFILE}") || exit 1

printf '%s' "${RUNTIME_JSON}" | jq -e '
  (.agent_url | type == "string" and length > 0) and
  (.es_url | type == "string" and length > 0) and
  (.vst_url | type == "string" and length > 0) and
  (.rtvi_vlm_url | type == "string" and length > 0) and
  (.video_embed_index | type == "string" and length > 0) and
  (.behavior_index | type == "string" and length > 0) and
  (.raw_index | type == "string" and length > 0) and
  (.video_embed_index != .behavior_index) and
  (.video_embed_index != .raw_index) and
  (.behavior_index != .raw_index)
' >/dev/null || { echo "Invalid or aliased search runtime indexes: ${RUNTIME_JSON}" >&2; exit 1; }

AGENT_URL=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.agent_url') || exit 1
ES_URL=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.es_url') || exit 1
VST_URL=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.vst_url') || exit 1
RTVI_VLM_URL=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.rtvi_vlm_url') || exit 1
EMBED_INDEX=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.video_embed_index') || exit 1
BEHAVIOR_INDEX=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.behavior_index') || exit 1
RAW_INDEX=$(printf '%s' "${RUNTIME_JSON}" | jq -er '.raw_index') || exit 1

index_count() {
  INDEX=$1 FIELD=$2 VALUE=$3
  QUERY=$(jq -cn --arg field "${FIELD}" --arg value "${VALUE}" \
    '{query:{term:{($field):$value}}}') || return 1
  curl -fsS --max-time 15 -H 'Content-Type: application/json' \
    "${ES_URL}/${INDEX}/_count" -d "${QUERY}" | jq -er '.count | numbers'
}
```

For uploaded video files, poll these exact tuples independently and require
each count to become greater than zero within the bounded deadline:

- `EMBED_INDEX`, `sensor.id.keyword`, resolved VST sensor UUID;
- `BEHAVIOR_INDEX`, `sensor.id.keyword`, canonical source name;
- `RAW_INDEX`, `sensorId.keyword`, canonical source name.

Print the three resolved index names and final counts. A count from a different
index or field does not satisfy readiness.

For Kubernetes, do not query Elasticsearch directly. After `/complete`
succeeds, poll `${VSS_VIOS_URL}/api/v1/sensor/list` until the canonical source
is present, then run the requested search through `${AGENT_URL}/generate` with
bounded retries while ingestion finishes. A valid Agent response proves the
public workflow is operational, but do not claim direct index-level validation
because Elasticsearch remains private. Never create a port-forward to restore
the Docker-only index checks.

> Compatibility note: `PUT /api/v1/videos-for-search/{filename}` remains wired
> only for pre-existing external legacy callers. Do not select or invoke it for
> a workflow performed under this skill; always use and validate the three-step
> `/api/v1/videos` upload and `/complete` flow above.

### RTSP stream — single endpoint

```bash
curl -sfS -X POST "${AGENT_URL}/api/v1/rtsp-streams/add" \
  -H "Content-Type: application/json" \
  -d '{
    "sensorUrl": "rtsp://<host>:<port>/<path>",
    "name": "<sensor-name>",
    "username": "",
    "password": "",
    "location": "",
    "tags": ""
  }' | jq .
```

The response shape is `{status, message, error}` — no `sensorId` (the agent
keys the stream by the `name` you provided). On any step's failure earlier steps
roll back. The `start_embedding_generation` step is fire-and-verify: a 2xx
confirms the request was accepted and the embedding pipeline is running in the
background, **not** that the stream is searchable yet. Search hits start
appearing only after enough chunks land in Elasticsearch.

Use a bounded readiness check before searching: poll the selected embed index
for documents whose source/sensor identifier equals the registered stream name
or resolved sensor ID. Require a count greater than zero, retry with backoff for
at most five minutes, and fail with the last count/backend error when the
deadline expires. Do not treat elapsed time alone as readiness.

### Delete source — agent-backed cleanup

Delete through the agent backend, not bare VIOS, so VIOS storage and search
embeddings are cleaned up together.

```bash
# For video files: video_id is the VIOS sensor/video UUID
curl -sfS -X DELETE "${AGENT_URL}/api/v1/videos/<video_id>" | jq .

# For RTSP streams: name is the registered source name
curl -sfS -X DELETE "${AGENT_URL}/api/v1/rtsp-streams/delete/<name>" | jq .
```

Require the agent DELETE response's `status` to be `success`; `partial` means
cleanup is incomplete and must not be reported as success. Then poll with a
bounded timeout until the source is absent from the VST sensor list. For
Docker, additionally require the selected embedding index to contain zero
documents for the resolved video UUID under `sensor.id.keyword`, and the
behavior and raw indexes to contain zero documents for the exact identifiers
recorded during readiness validation. Reuse the Docker-only `RUNTIME_JSON`
resolver and the exact three index/field tuples above; do not derive
behavior/raw indexes from `ELASTIC_SEARCH_INDEX`. For Kubernetes, report the
Agent status and VIOS absence without claiming direct Elasticsearch cleanup
verification. Never port-forward Elasticsearch for this check.

---

## Mandatory workflow

1. Confirm this is the **search** profile. If it is unavailable, ask whether
   the user wants it deployed; do not target an unrelated profile.
2. If the user names a file, camera, or sensor, list registered sources using
   `vss-manage-video-io-storage` before searching. Accept an exact source,
   stream ID, or one unambiguous normalized substring match only.

   - If there is no match, stop. Report the registered names and ask whether
     the user meant one of them, wants to ingest the named source using the
     agent-backed workflow above, or wants an unrestricted search.
   - If several sources match, stop and ask the user to choose.
   - Never remove a requested source constraint, substitute a different video,
     or run a broad search as a probe.
   - If the source is absent, do not test or invoke the search CLI; clarification
     is the final action for that request.

3. Preserve the requested object/action, source, time bounds, result limit, and
   attributes. For Docker, decompose these into explicit CLI fields using
   [Query decomposition](references/query_decomposition.md). For Kubernetes,
   include the same constraints explicitly in `SEARCH_PROMPT`; the VSS Agent
   performs its own query decomposition.
4. Run the interface selected by `DEPLOYMENT_KIND`.

   **Docker Compose:** use the host CLI. It validates named sources again
   against that deployment's VST listing before querying ES. Use
   `--output json --raw` when parsing the result. See
   [CLI usage](references/cli_usage.md) for the full flag reference. Put the
   complete invocation in a `SEARCH_COMMAND` array, then capture and validate
   its exact stdout:

   ```bash
   : "${QUERY:?set the decomposed visual query}"
   : "${SEARCH_MODE:?set the explicit search mode}"
   : "${VIDEO_SOURCE:?set the resolved source name or stream ID}"
   PROFILE="${PROFILE:-search}"
   TOP_K="${TOP_K:-3}"
   SEARCH_COMMAND=(
     uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev
     vss search run
     --deployment docker --profile "${PROFILE}"
     --query "${QUERY}" --search-mode "${SEARCH_MODE}"
     --video-source "${VIDEO_SOURCE}" --top-k "${TOP_K}"
     --output json --raw
   )
   # Add any required attributes, time bounds, or decomposition flags to the
   # array before executing it.
   if ! SEARCH_JSON=$("${SEARCH_COMMAND[@]}"); then
     echo "Search command failed" >&2
     exit 1
   fi
   printf '%s' "${SEARCH_JSON}" |
     jq -e 'type == "object" and (.data | type == "array")' >/dev/null || {
       echo "Search did not return a SearchOutput object with a data array" >&2
       exit 1
     }
   ```

   `SEARCH_COMMAND` must invoke the project-local `vss search run`, not a
   shell string. Media validation must consume each hit's returned
   `screenshot_url` from `SEARCH_JSON`.

   **Kubernetes / Helm:** use only the public Agent Ingress. Do not invoke the
   host CLI's Kubernetes deployment selector, cluster discovery, `kubectl`, or
   any backend endpoint. Build `SEARCH_PROMPT` from the user's request and
   the source resolved in step 2, explicitly retaining search mode, attributes,
   time bounds, and top-k when requested:

   ```bash
   : "${SEARCH_PROMPT:?set the complete search request with resolved source and controls}"
   SEARCH_REQUEST=$(jq -cn --arg input_message "${SEARCH_PROMPT}" \
     '{input_message:$input_message}')
   if ! SEARCH_JSON=$(curl -sfS --connect-timeout 10 --max-time 3600 \
     -X POST "${AGENT_URL}/generate" \
     -H "Content-Type: application/json" \
     -d "${SEARCH_REQUEST}"); then
     echo "Kubernetes search through ${AGENT_URL}/generate failed" >&2
     exit 1
   fi
   printf '%s' "${SEARCH_JSON}" |
     jq -e 'if type == "string" then length > 0 else type == "object" or type == "array" end' \
     >/dev/null || {
       echo "Kubernetes search returned an empty or malformed response" >&2
       exit 1
     }
   SEARCH_TEXT=$(printf '%s' "${SEARCH_JSON}" |
     jq -r 'if type == "string" then . else (.output // .value // .response // .) end')
   ```

   Treat the Agent response as authoritative. Do not replace a failed public
   request with private service access or a port-forward.
   If the command cannot start or returns a configuration error, report the
   error and stop; never replace it with another search interface.
5. Handle results by `DEPLOYMENT_KIND`. Do **not** run the Docker CLI
   `SearchOutput` pipeline (`.data[]`, `screenshot_url`, `HIT_COUNT`) against a
   Kubernetes `/generate` response — that response is conversational agent text,
   not CLI JSON.

   **Docker Compose only:** validate each returned media URL with a bounded GET
   of the exact URL. The stream identifier is already encoded in the VST replay
   path; do not add a `streamId` routing header because that can route an
   otherwise valid public replay URL to an unhealthy upstream. For
   availability-only validation, discard the body; this is not visual inspection.

   For every hit, extract the URL from the result object. Compare the
   normalized origins (scheme, hostname, and effective port), then issue the GET
   against the **same, unmodified** `SCREENSHOT_URL`:

   First resolve the expected origin. Do not assume `VST_EXTERNAL_URL`
   is exported in the operation shell (`PROFILE` must match `--profile`):

   ```bash
   PROFILE="${PROFILE:-search}"
   EXPECTED_VST_EXTERNAL_URL=$(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
     python -c 'import sys; from cli.deployment import discover_docker; print(discover_docker(sys.argv[1]).env["VST_EXTERNAL_URL"])' \
     "${PROFILE}")
   ```

   Then validate the exact returned URLs. A media-bearing external VST origin
   must be public HTTPS: reject HTTP, localhost, single-label/internal hostnames,
   and non-global IP addresses even if the returned URL matches the configured
   value. This is mandatory on Brev and prevents an internally valid but
   user-inaccessible URL from passing verification.

   ```bash
   : "${EXPECTED_VST_EXTERNAL_URL:?resolve the effective VST external URL first}"

   url_origin() {
     URL_ORIGIN_PY=$(printf '%s\n' \
       'import ipaddress' \
       'import sys' \
       'from urllib.parse import urlsplit' \
       'url = urlsplit(sys.argv[1])' \
       'hostname = (url.hostname or "").lower().rstrip(".")' \
       'if url.scheme != "https" or not hostname or url.username or url.password:' \
       '    raise SystemExit("media origin must be an unauthenticated HTTPS URL")' \
       'if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):' \
       '    raise SystemExit("localhost/internal media origin is forbidden")' \
       'try:' \
       '    address = ipaddress.ip_address(hostname)' \
       'except ValueError:' \
       '    if "." not in hostname:' \
       '        raise SystemExit("single-label/internal media origin is forbidden")' \
       'else:' \
       '    if not address.is_global:' \
       '        raise SystemExit("non-global media origin is forbidden")' \
       'print(f"https://{hostname}:{url.port or 443}")') || return 1
     uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
       python -c "${URL_ORIGIN_PY}" "$1"
   }

   EXPECTED_ORIGIN=$(url_origin "${EXPECTED_VST_EXTERNAL_URL}") || exit 1
   HIT_COUNT=$(printf '%s' "${SEARCH_JSON}" | jq -er '.data | length') || exit 1

   # Set VERIFY_PIXELS=true only after the user authorizes visual inspection.
   VERIFY_PIXELS="${VERIFY_PIXELS:-false}"
   if [ "${VERIFY_PIXELS}" = "true" ] && [ "${HIT_COUNT}" -gt 0 ]; then
     INSPECTION_DIR=$(mktemp -d /tmp/vss-search-verification.XXXXXX)
   fi
   VALIDATED_COUNT=0

   if [ "${HIT_COUNT}" -gt 0 ]; then
     HITS_JSONL=$(printf '%s' "${SEARCH_JSON}" | jq -cer '.data[]') || exit 1
     while IFS= read -r HIT; do
       SCREENSHOT_URL=$(printf '%s' "${HIT}" | jq -er '.screenshot_url | select(type == "string" and length > 0)') || exit 1
       ACTUAL_ORIGIN=$(url_origin "${SCREENSHOT_URL}") || exit 1
       [ "${ACTUAL_ORIGIN}" = "${EXPECTED_ORIGIN}" ] || {
         echo "Media URL origin mismatch: ${ACTUAL_ORIGIN} != ${EXPECTED_ORIGIN}" >&2
         exit 1
       }

       OUTPUT_PATH=/dev/null
       if [ "${VERIFY_PIXELS}" = "true" ]; then
         OUTPUT_PATH="${INSPECTION_DIR}/hit-${VALIDATED_COUNT}.jpg"
       fi
       curl -fS --connect-timeout 10 --max-time 20 \
         "${SCREENSHOT_URL}" -o "${OUTPUT_PATH}" || exit 1
       if [ "${VERIFY_PIXELS}" = "true" ]; then
         [ -s "${OUTPUT_PATH}" ] || { echo "Empty screenshot: ${OUTPUT_PATH}" >&2; exit 1; }
       fi
       VALIDATED_COUNT=$((VALIDATED_COUNT + 1))
     done <<< "${HITS_JSONL}"
   fi

   [ "${VALIDATED_COUNT}" -eq "${HIT_COUNT}" ] || {
     echo "Validated ${VALIDATED_COUNT} of ${HIT_COUNT} search hits" >&2
     exit 1
   }
   ```

   A zero-length `data` array has zero media URLs to validate and therefore
   satisfies the count equality above. Handle it with workflow step 8. Only a
   query whose contract explicitly requires candidates should reject zero hits.

   When `VERIFY_PIXELS=true`, inspect every saved file in `INSPECTION_DIR` and
   report a verdict for every hit. Merely saving the files is not inspection.

   `SCREENSHOT_URL` must come only from the CLI hit. Never replace it with
   `VST_EXTERNAL_URL`, a localhost URL, or any reconstructed URL for the probe.
   A failed origin comparison or GET is a result/configuration error to report,
   not permission to rewrite the URL.

   **Kubernetes / Helm only:** skip the Docker CLI media-validation block above.
   Require a nonempty `SEARCH_TEXT`. Do not call `jq` on `.data`, `.data[]`, or
   `.screenshot_url`. If the Agent reply embeds concrete public media URLs, you
   may optionally GET those exact URLs (no `streamId` header, no URL rewrite)
   against `${VSS_PUBLIC_URL}` / `${VST_EXTERNAL_URL:-${VSS_PUBLIC_URL}}` origins;
   never invent structured hit fields from prose.
6. Format the final reply by `DEPLOYMENT_KIND`. Never paste raw JSON wrappers
   into the reply.

   **Docker Compose:** parse the compact CLI JSON internally. Use this exact
   response structure for nonempty results:

   ```text
   ## Video Search Results
   <formatted hits with source, start/end timestamps, similarity, and media URL —
   print each hit COMPLETE screenshot/clip URL exactly as the CLI returned it
   (never a shortened form, a status code, or an https://... placeholder)>

   Similarity scores are retrieval evidence; they do not visually confirm the requested object or action.

   ## Verification Step
   <offer visual inspection — do NOT fetch, save, or view screenshots yourself
   unless the user already opted in; when authorized, label every inspected
   hit with exactly one of: confirmed / rejected / uncertain (never
   MATCH / PARTIAL MATCH / NO MATCH phrasing)>
   ```

   Copy every evidence field verbatim from CLI output. Never invent or normalize
   evidence.

   **Kubernetes / Helm:** present `SEARCH_TEXT` under `## Video Search Results`.
   Preserve the Agent's evidence as returned; do not reformat it into fake CLI
   hit rows. Offer `## Verification Step` only when the reply includes concrete
   media URLs the user can inspect.
7. **Never fetch, save, or visually inspect screenshots without explicit user
   opt-in or prior authorization** — validating that URLs resolve is setup work;
   *looking at the pixels* is a user decision. Without opt-in, do not save or inspect media
   pixels. When authorized on Docker, repeat the bounded GET without adding routing headers,
   save each returned screenshot under `/tmp/`, inspect the saved pixels, and
   report a grounded confirmed/rejected/uncertain verdict for each hit under
   `## Verification Step`. On Kubernetes, apply the same opt-in rule only to
   concrete media URLs present in `SEARCH_TEXT`.
8. If the result set is empty (Docker: zero-length `data`; Kubernetes: Agent
   reports no matches), say that no matches were found. Keep all source
   constraints, explain that the object may be absent or the query too narrow,
   and offer a specific query or similarity-threshold refinement. Never broaden
   the search silently or fabricate a result.

9. For source ingestion or deletion, use the agent-backed flows above. Never
   substitute a bare VIOS or Elasticsearch mutation.

## Host CLI

Always invoke the checked-out `services/agent` project with `uv run`:

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss search run [deployment options] [query options]
```

The `uv run --project` prefix creates the project-local console entry point;
do not require or search for a global `vss` executable.

Direct low-level invocation remains environment-free. Use explicit runtime
flags or `--config` with explicit `--config-env KEY=VALUE` values only when a
deployment selector is not appropriate. The CLI never reads endpoint variables
from the host process.

### Docker

Docker requires the checkout's shared VST/RTVI service defaults plus the
deployed profile's checked-in `.env` and runtime `generated.env`. The command
applies the shared defaults first, then reads the profile files in Docker
Compose order—`.env` followed by `generated.env` as the authoritative
overlay—expands the effective values, and uses that environment with the
profile's checked-out agent config. It translates Compose-only service DNS to
the loopback ports published for Elasticsearch, RTVI-Embed, RTVI-CV, and VST.
The embedding index is resolved from the profile layers; behavior and raw index
names are resolved from the interpolated agent config.

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run \
  --deployment docker --profile search \
  --query "find all instances of forklifts" \
  --search-mode embed --source-type video_file --top-k 10 \
  --output json --raw
```

Before running this, start the profile with `dev-profile.sh` so `generated.env`
exists. The checked-in `.env` supplies stable profile values but is not, by
itself, proof of a running initialized deployment. Private service ports are
loopback-only; do not expose them to a LAN simply to run a search.

### Kubernetes / Helm

Kubernetes operations use one operator-provided public Ingress origin. No
cluster discovery or Kubernetes credentials are required:

```bash
: "${VSS_PUBLIC_URL:?Provide the public VSS search Ingress origin}"
AGENT_URL="${VSS_PUBLIC_URL%/}"
VSS_VIOS_URL="${AGENT_URL}/vst"
curl -sfS --max-time 5 "${AGENT_URL}/openapi.json" >/dev/null
curl -sfS --max-time 5 "${VSS_VIOS_URL}/api/v1/sensor/version" >/dev/null

SEARCH_PROMPT='Find up to 10 instances of a person wearing a white jacket in warehouse-camera-3. Use attribute search with the attribute "white jacket".'
curl -sfS --max-time 3600 -X POST "${AGENT_URL}/generate" \
  -H "Content-Type: application/json" \
  -d "$(jq -cn --arg input_message "${SEARCH_PROMPT}" '{input_message:$input_message}')"
```

Do not inspect Deployments, ConfigMaps, Services, Secrets, or Helm values. Do
not invoke the Kubernetes deployment selector in the host CLI: it uses private
backend access and is outside this no-port-forward skill contract.

## Search behavior and safeguards

- For Docker CLI search, `ELASTIC_SEARCH_INDEX` wins whenever the deployment provides it. The only
  fallback is `mdx-embed-filtered-2025-01-01`, never `video_embeddings`.
  Missing indexes fail with nearby MDX index diagnostics; ingest video before
  retrying.
- For Docker CLI search, the configured Cosmos/RTVI Embed model is verified through `/v1/models`.
  The CLI never guesses a replacement model ID. Choose one explicitly from the
  reported deployed IDs if the configured model is unavailable.
- Docker attribute/fusion search performs a short RTVI-CV text-embedding capability
  preflight. It fails by default rather than hanging or silently changing the
  search. `--allow-embed-only-fallback` is the only opt-in way to remove
  attributes and continue as embed-only search.
- Result object IDs that are missing or `unknown` are not merged together.
- Search retrieval is distinct from visual verification. Visual verification is the
  explicit screenshot-inspection step described above; it is not a CLI flag.
- For Docker, `vss search embed` and `vss search attribute` expose the lower-level
  primitives for callers that explicitly need one primitive or for focused
  troubleshooting. Normal archive-search requests should use `search run` with
  an explicit mode so they retain unified source validation, routing, and the
  `SearchOutput.data` contract.

## Query examples

```bash
# Embed-only search across all ingested files
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run \
  --deployment docker --profile search \
  --query "red forklift near a loading bay" --search-mode embed \
  --source-type video_file --output json --raw

# Kubernetes attribute search; source must have been resolved first
SEARCH_PROMPT='Find a person wearing a white jacket in warehouse-camera-3. Use attribute search with the attribute "white jacket".'
curl -sfS --max-time 3600 -X POST "${VSS_PUBLIC_URL%/}/generate" \
  -H "Content-Type: application/json" \
  -d "$(jq -cn --arg input_message "${SEARCH_PROMPT}" '{input_message:$input_message}')"

# Deliberate fallback when a deployment has no RTVI-CV text endpoint
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run \
  --deployment docker --profile search \
  --query "forklift near a loading bay" --attribute "yellow forklift" \
  --search-mode fusion --allow-embed-only-fallback --output json --raw
```

## Troubleshooting

- **Host CLI preflight fails**: preserve the output from
  `uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run
  --help`, verify `VSS_REPO_ROOT` and host `uv`, and stop. Do not switch search
  interfaces.

- **Docker profile environment missing**: both `.env` and runtime
  `generated.env` are required. Start the selected profile with
  `deploy/docker/scripts/dev-profile.sh` when `generated.env` is absent; do not
  treat `.env` alone as initialized runtime state.
- **Kubernetes public endpoint unavailable**: verify `VSS_PUBLIC_URL`, DNS,
  TLS, Ingress status, and the `/openapi.json` and `/vst/api/v1/sensor/version`
  routes. Do not fall back to `kubectl`, Service DNS, a NodePort, or a pod
  shell.
- **Source unavailable or ambiguous**: stop and clarify; do not substitute.
- **Zero results**: report the empty outcome, retain the selected source, and
  offer an explicit query or similarity-threshold refinement. Run a broader
  search only after the user accepts it.
- **Missing index (Docker CLI)**: verify ingestion completion and the
  `ELASTIC_SEARCH_INDEX` value resolved from `.env` plus the `generated.env`
  overlay.
- **Model preflight failure (Docker CLI)**: pass an explicit deployed model ID after
  reviewing the reported list.
- **RTVI-CV preflight failure (Docker CLI)**: repair the service or use the explicit
  `--allow-embed-only-fallback` option only when an embed-only result is
  acceptable.
- **Visual verification needs an authenticated media route**: stop and use the
  operator-managed route. Never copy API keys into CLI flags, generated files,
  logs, or skill output.
