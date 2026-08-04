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

## Purpose

Operate archive search from the caller's host without entering VSS containers
or pods. Compose and Kubernetes use the same commands: point `vss configure` at
the deployment origin once, then run searches. Source ingestion and deletion
stay agent-backed.

## Prerequisites

- A running VSS search deployment, and its origin — the one host:port (Compose)
  or Ingress origin (Kubernetes) that fronts the profile.
- A checkout containing `services/agent` and host `uv`, to run the CLI.
- The `vss-manage-video-io-storage` skill for source listing and inspection.
- `curl` and `jq`. Ordinary search needs no API key.

Do not execute `vss` inside a distroless VSS container or a pod. Do not
wrap it with `docker exec`, `kubectl exec`, or `sh -lc`.

`vss` does not need to be installed globally and `which vss` is not an
availability check. Resolve the checkout once, allowing the operator to
override Harbor's default, and validate it before the first search:

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
fails, report the error and stop. Do not manually call search backends.

## Deployment prerequisite

This skill requires the VSS **search** profile. Resolve its public or Compose
endpoints exactly once. See
[Deployment resolution](../vss-build-vision-agent/references/deployment_resolution.md)
for the deployment-owned
`VSS_PUBLIC_URL` contract and derived variables (`VSS_VIOS_URL`,
`VST_API_BASE`):

```bash
: "${VSS_ORIGIN:?Provide the deployment origin, e.g. http://localhost:7777 or https://vss-search.example.com}"
VSS_ORIGIN="${VSS_ORIGIN%/}"
AGENT_URL="${VSS_ORIGIN}"
VST_URL="${VSS_ORIGIN}"
VSS_VIOS_URL="${VSS_ORIGIN}/vst"

# Probes every route once and records it in ~/.vss/config.json,
# including index names and model ids read off the backends.
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss configure --base-url "${VSS_ORIGIN}" || exit 1
```

Prints one line per route (`routed` / `absent`); exits non-zero if none
answered.

**Re-run it after ingestion, before any readiness check or search.** The
recorded index list is a snapshot, and `mdx-*` indices are created by ingestion,
not by deployment — so configuring a fresh stack records none, and the record
stays empty until you re-run. Search still appears to work (the runtime falls
back to built-in index names) while `vss configure show` reports no indexes and
frame-level lookups are silently disabled. `vss configure` warns when it records
an Elasticsearch with no `mdx-*` indices; treat that as "ingest, then re-run".

- `vss configure check` — re-probe a recorded config; exits 3 if a service went away.
- `vss configure show` — print the recorded deployment. Authoritative for index
  names; do not read `ELASTIC_SEARCH_INDEX` or parse `.env`.

On Kubernetes, `VSS_ORIGIN` is the operator-provided public Ingress origin
(`VSS_PUBLIC_URL`). Never run `kubectl port-forward`, use an in-cluster Service
name, guess a NodePort, or derive a Helm release name. Routes the Ingress does
not expose are absent from the recorded config; a path needing one exits 4
naming it. Authentication, if configured, must use the operator's approved
mechanism and must not be copied into prompts or logs.

The deployment is not ready for archive search until its public VIOS route is
reachable from the host that will consume search results — `${VSS_VIOS_URL}`.
Media URLs in results are minted from the configured origin, so there is no
separate internal/external URL to reconcile.
For a Brev deployment, follow `vss-deploy-profile`'s secure-link setup (including
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
   uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss configure check
   ```
   Exits 3 if a recorded route went away. Missing Elasticsearch means the wrong
   profile is deployed. Do not expose or forward a private service to satisfy a
   host-side readiness check.

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

This is the **agent-backed** (full-stack) ingestion path, and it is unchanged.
When a deployment has **no agent tier** — a `vss-build-vision-agent` headless
build — the equivalent register-and-fan-out is driven by direct REST from
`vss-manage-video-io-storage`
[`references/provision-vios-source.md`](../vss-manage-video-io-storage/references/provision-vios-source.md);
return here for the query itself once the source is ingested.

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

Read the endpoints and all three indexes from the recorded deployment. Re-run
`vss configure` first if anything has been ingested since it last ran, or this
resolver reads an empty index list. Never reuse `ELASTIC_SEARCH_INDEX` for
behavior or raw-data checks: it names only the video embedding index.

```bash
CONFIG_JSON=$(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss configure show) || exit 1

ES_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er '.services.elasticsearch.url') || exit 1
# `first` matches what the CLI resolves internally. Uploaded video files share
# the 2025-01-01 base timeline; live streams create later dated indexes, so
# `last` would point the readiness check at stream data the search never reads.
pick_index() {
  printf '%s' "${CONFIG_JSON}" \
    | jq -er --arg p "$1" '[.services.elasticsearch.indices[] | select(startswith($p))] | sort | first'
}
EMBED_INDEX=$(pick_index "mdx-embed-") || exit 1
BEHAVIOR_INDEX=$(pick_index "mdx-behavior-") || exit 1
RAW_INDEX=$(pick_index "mdx-raw-") || exit 1

# The three must be distinct; an aliased or missing index means ingestion is incomplete.
[ "${EMBED_INDEX}" != "${BEHAVIOR_INDEX}" ] && [ "${EMBED_INDEX}" != "${RAW_INDEX}" ] \
  && [ "${BEHAVIOR_INDEX}" != "${RAW_INDEX}" ] \
  || { echo "Aliased search indexes: ${EMBED_INDEX} ${BEHAVIOR_INDEX} ${RAW_INDEX}" >&2; exit 1; }

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
is present, then run the requested search with bounded retries while ingestion
finishes. Where the origin does not route Elasticsearch, `vss configure` records
it as absent; report the search result without claiming index-level validation,
and never port-forward to restore the index checks.

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
recorded during readiness validation. Reuse the `vss configure show` resolver
and the exact three index/field tuples above; do not derive behavior/raw
indexes from `ELASTIC_SEARCH_INDEX`. Where Elasticsearch is not routed, report
the agent status and VIOS absence without claiming index-level cleanup
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

   Carry forward the matched source's `sensorId` and its name. Which to pass is
   **mode-dependent, not source-type-dependent**: `embed`/`fusion` take the **`sensorId`**
   (the embed index keys on it; `fusion` reverse-resolves it to the name for its attribute
   leg); `attribute`/`object` take the **name** (behavior/raw key on the name) — for both
   `video_file` and `rtsp`. The CLI does no name↔id translation, so the wrong identifier
   silently returns nothing.

   `--source-type` is a **separate axis**: it picks the index/date partition, not the
   identifier — `video_file` = the uploaded-file index (`2025-01-01` timeline), `rtsp` = the
   live dated indices. Query a **live stream with `--source-type rtsp`**; `video_file` reads
   the upload partition and silently returns zero even with the right `--video-source`.

3. Preserve the requested object/action, source, time bounds, result limit, and
   attributes as explicit CLI fields. Pick the path: `--query` only →
   `run embed`; `--attribute` only → `run attribute`; both → `run fusion`;
   explicit object ids → `run object`.

   `--attribute` takes specific, visually detectable properties — `white
   jacket`, `red hard hat`, `carrying a backpack`. Generic nouns and actions
   (`person`, `forklift`, `running`) are not attribute filters; leave those in
   `--query`. For "red forklift" keep the whole phrase as the query rather than
   splitting `red` into an attribute. For "person in a red jacket running", use
   `run fusion --query "person in a red jacket running" --attribute "red jacket"`.
4. Run the search. The CLI matches `--video-source` **literally** against the index
   and does **no** name↔id resolution against VST, so pass the identifier resolved in
   step 2 for the chosen path, and set `--source-type` to the source's partition
   (`video_file` for uploads, `rtsp` for live streams). See [CLI usage](references/cli_usage.md)
   for the full flag reference. Put the complete invocation in a `SEARCH_COMMAND` array,
   then capture and validate its exact stdout:

   ```bash
   : "${SEARCH_PATH:?set embed|attribute|fusion|object}"
   TOP_K="${TOP_K:-3}"
   # VIDEO_SOURCES: identifiers resolved in step 2 — sensorId(s) for embed/fusion,
   # name(s) for attribute/object (both source types). Leave EMPTY to search across all
   # sources; add one entry per source to scope to several. Each becomes a repeated
   # --video-source. Set --source-type to the source's partition (video_file | rtsp).
   VIDEO_SOURCES=( )   # e.g. ( "$SENSOR_ID" ) or ( "$NAME_A" "$NAME_B" )
   SEARCH_COMMAND=(
     uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev
     vss search run "${SEARCH_PATH}"
     --top-k "${TOP_K}" --raw
   )
   for src in "${VIDEO_SOURCES[@]}"; do SEARCH_COMMAND+=( --video-source "$src" ); done
   # Append --query for embed/fusion, --attribute (repeatable) for
   # attribute/fusion, --object-id for object, plus any time bounds.
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

   **Natural-language requests:** when the user's phrasing should be decomposed
   by the deployment's LLM rather than by this skill, POST it to the agent
   instead. The response is conversational text, not `SearchOutput`:

   ```bash
   : "${SEARCH_PROMPT:?set the complete search request with resolved source and controls}"
   SEARCH_REQUEST=$(jq -cn --arg query "${SEARCH_PROMPT}" \
     '{query:$query, source_type:"video_file", agent_mode:true}')
   if ! SEARCH_JSON=$(curl -sfS --connect-timeout 10 --max-time 3600 \
     -X POST "${VSS_ORIGIN}/api/v1/search" \
     -H "Content-Type: application/json" \
     -d "${SEARCH_REQUEST}"); then
     echo "Agent search through ${VSS_ORIGIN}/api/v1/search failed" >&2
     exit 1
   fi
   SEARCH_TEXT=$(printf '%s' "${SEARCH_JSON}" | jq -r '
     if type == "string" then .
     elif type == "object" then (.output // .value // .response // empty)
     else empty
     end' 2>/dev/null)
   if [ -z "$(printf '%s' "${SEARCH_TEXT}" | tr -d '[:space:]')" ]; then
     echo "Agent search returned an empty or malformed response" >&2
     exit 1
   fi
   ```

   Validate the extracted `SEARCH_TEXT`, not the response envelope: `{}`, `[]`,
   `{"output": null}`, and `{"output": ""}` are all failures, not results. Never
   fall back to the whole response body when no known text field is present —
   that presents a raw JSON wrapper as if it were the Agent's answer.

   Treat the Agent response as authoritative. Do not replace a failed public
   request with private service access or a port-forward.
   If the command cannot start or returns a configuration error, report the
   error and stop; never replace it with another search interface.
5. Handle results. Do **not** run the `SearchOutput` pipeline (`.data[]`,
   `screenshot_url`, `HIT_COUNT`) against an `/api/v1/search` agent response —
   that response is conversational agent text, not CLI JSON.

   Validate each returned media URL with a bounded GET
   of the exact URL. The stream identifier is already encoded in the VST replay
   path; do not add a `streamId` routing header because that can route an
   otherwise valid public replay URL to an unhealthy upstream. For
   availability-only validation, discard the body; this is not visual inspection.

   For every hit, extract the URL from the result object. Compare the
   normalized origins (scheme, hostname, and effective port), then issue the GET
   against the **same, unmodified** `SCREENSHOT_URL`:

   First resolve the expected origin. Media URLs are minted from the configured
   origin, so that is what results must match:

   ```bash
   EXPECTED_VST_EXTERNAL_URL=$(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
     vss configure show | jq -er '.base_url')
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
   Require a nonempty `SEARCH_TEXT`, already gated in step 4; never present a
   result that did not clear that gate. Do not call `jq` on `.data`, `.data[]`, or
   `.screenshot_url`. If the Agent reply embeds concrete public media URLs, you
   may optionally GET those exact URLs (no `streamId` header, no URL rewrite)
   against `${VSS_PUBLIC_URL}` origins; never invent structured hit fields from
   prose.
6. Format the final reply. Never paste raw JSON wrappers into the reply.

   Parse the compact CLI JSON internally. Use this exact response structure for
   nonempty results:

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
  vss search run <path> [query options]
```

The `uv run --project` prefix creates the project-local console entry point;
do not require or search for a global `vss` executable.

The command takes no endpoints — backend URLs, index names and model ids all
come from the recorded deployment. The CLI never reads endpoint variables from
the host process.

### The four retrieval paths

`run` takes the retrieval path as a sub-action. Each accepts only the fields
that path uses, so an unusable combination cannot be expressed:

| path | matches on | needs |
|---|---|---|
| `run embed` | `--query` embedded and compared against video-chunk embeddings (`mdx-embed-*`) | Elasticsearch, RT-Embed |
| `run attribute` | `--attribute` (repeatable) against detected-object attributes (`mdx-behavior-*`) | Elasticsearch, RT-CV |
| `run fusion` | embedding retrieval re-ranked by attribute evidence: `--query` **and** `--attribute` | Elasticsearch, RT-Embed, RT-CV |
| `run object` | `--object-id` (repeatable), identity lookup on a tracked object | Elasticsearch, RT-CV |

An embedding search works on a deployment without RT-CV; attribute, fusion and
object do not.

```bash
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run embed \
  --query "find all instances of forklifts" \
  --source-type video_file --top-k 10 --raw
```

Start the profile, then run `vss configure --base-url "${VSS_ORIGIN}"` once.
Individual service ports need not be reachable; the origin fronts them.

### Kubernetes / Helm

Kubernetes operations use one operator-provided public Ingress origin. No
cluster discovery or Kubernetes credentials are required:

```bash
: "${VSS_ORIGIN:?Provide the public VSS search Ingress origin}"
VSS_ORIGIN="${VSS_ORIGIN%/}"
uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev \
  vss configure --base-url "${VSS_ORIGIN}"

uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run attribute \
  --attribute "white jacket" --video-source warehouse-camera-3 --top-k 10 --raw
```

Same commands as Compose; only the origin differs.

Do not inspect Deployments, ConfigMaps, Services, Secrets, or Helm values.

For LLM query decomposition (natural language → query + attributes + mode),
`POST ${VSS_ORIGIN}/api/v1/search` with
`{"query": ..., "source_type": "video_file", "agent_mode": true}`. The CLI does
not decompose.

## Search behavior and safeguards

- Index names and the embedding model id come from `vss configure`. Never pass
  or infer an index, and never read `ELASTIC_SEARCH_INDEX`. A missing index
  means video has not been ingested.
- A path whose services are absent exits 4 naming them, before any request.
  There is no silent downgrade — to search without RT-CV, ask for `run embed`.
- **Gotcha:** merging is on by default. Contiguous same-sensor windows collapse
  into one result whose score is the *mean* of the merged windows, so scores
  and window boundaries will not match a per-window reference.
  `--no-merge-adjacent` reports raw windows.
- **Gotcha:** `run attribute` and `run object` reject `--query`; `run embed`
  rejects `--attribute`. Unknown flags exit 2.
- Result object IDs that are missing or `unknown` are not merged together.
- Search retrieval is distinct from visual verification. Visual verification is the
  explicit screenshot-inspection step described above; it is not a CLI flag.
- Output is always JSON on stdout (`SearchOutput.data`); `--raw` compact,
  `--pretty` indented. Exits: 0 ok, 2 invalid input, 3 backend unreachable,
  4 configuration.

## Query examples

```bash
VSS="uv run --project ${VSS_REPO_ROOT}/services/agent --no-dev vss"

# One-time, after any deployment change
${VSS} configure --base-url "${VSS_ORIGIN}"

# Embed: text matched against video-chunk embeddings. --video-source is the source's
# sensorId for both video_file and rtsp (the embed index keys on it). Set --source-type
# to the source's partition: video_file (uploads) or rtsp (live streams). See step 2.
${VSS} search run embed \
  --query "red forklift near a loading bay" --video-source "<sensorId>" \
  --source-type video_file --top-k 3 --raw

# Fusion: the same retrieval, re-ranked by attribute evidence. Pass the sensorId (the
# embed leg keys on it; fusion reverse-resolves it to the name for the attribute leg).
${VSS} search run fusion \
  --query "person climbing a ladder" --attribute "white jacket" \
  --video-source "<sensorId>" --source-type video_file --top-k 3 --raw
```

`run attribute` and `run object` take `--attribute` / `--object-id` in place of
`--query`. Every remaining flag — time bounds, `--min-cosine-similarity`,
`--no-merge-adjacent`, the fusion weights — is listed with its type and range by:

```bash
${VSS} search run <path> --help
```

Read that rather than guessing; unknown flags exit 2.

## Troubleshooting

- **Host CLI preflight fails**: preserve the output from
  `uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev vss search run
  --help`, verify `VSS_REPO_ROOT` and host `uv`, and stop. Do not switch search
  interfaces.

- **Exit 4, "no deployment configured"**: run
  `vss configure --base-url "${VSS_ORIGIN}"`.
- **Exit 4, "config … has no 'base_url'"**: `~/.vss/config.json` was written by
  something other than `vss configure`. Re-run configure to rewrite it.
- **Exit 4, "`<path>` needs <service>"**: the origin does not route that
  service. Check the `routed`/`absent` lines from `vss configure`; use
  `run embed` if only RT-Embed and Elasticsearch are available.
- **Exit 3 from `vss configure check`**: a previously recorded route stopped
  answering. Repair the deployment, then re-run configure.
- **Exit 2, "No such option"**: the path does not accept that flag — `--query`
  is embed/fusion only, `--attribute` is attribute/fusion only.
- **Endpoint unavailable**: verify the origin, DNS, TLS, and Ingress status. Do
  not fall back to `kubectl`, Service DNS, a NodePort, or a pod shell.
- **Source unavailable or ambiguous**: stop and clarify; do not substitute.
- **Zero results**: report the empty outcome, retain the selected source, and
  offer an explicit query or similarity-threshold refinement. Run a broader
  search only after the user accepts it.
- **Exit 5, "Search index '…' does not exist"**: nothing ingested yet, or the
  index was deleted. Ingest, then re-run `vss configure` so the recorded index
  list matches. Read index names from `vss configure show`, never from
  `ELASTIC_SEARCH_INDEX`.
- **Gotcha:** `vss configure check` probes *routes*, not indexes. A deployment
  can pass `check` while the indexes it recorded have since been deleted.
- **Embed model unavailable**: re-run `vss configure`; the model id is read from
  RT-Embed's own list, never guessed. If it is still missing, the service is
  down — that is a deployment problem, not a search one.
- **Visual verification needs an authenticated media route**: stop and use the
  operator-managed route. Never copy API keys into CLI flags, generated files,
  logs, or skill output.
