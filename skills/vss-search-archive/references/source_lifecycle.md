# Search source lifecycle

Use the search agent's source endpoints so VST, VIOS, and Elasticsearch remain
consistent. Never replace these operations with direct backend mutations.

These Agent-backed mutations are the full-stack path. For a headless
`vss-build-vision-agent` deployment with no Agent tier, provision the source
through `vss-manage-video-io-storage`'s
[direct register-and-fan-out workflow](../../vss-manage-video-io-storage/references/provision-vios-source.md),
then return here for search. Do not apply the Agent endpoint recipes below to
a deployment that has no Agent.

## Contents

- [Deployment and runtime state](#deployment-and-runtime-state)
- [Pre-ingestion cleanup](#pre-ingestion-cleanup)
- [File source](#file-source)
- [RTSP source](#rtsp-source)
- [Delete source](#delete-source)

## Deployment and runtime state

Use the operator-provided Compose or Ingress origin. Never inspect Compose or
Kubernetes internals to rediscover it. On Brev, run the public-origin selection
block below first and set `VSS_ORIGIN` to its result. Do not configure a
provisional origin and change it afterward. Record that final origin, then read
the backends' own service, model, and index inventory:

```bash
: "${VSS_ORIGIN:?set the deployment origin}"
: "${VSS_REPO_ROOT:?set the validated checkout}"
VSS_ORIGIN="${VSS_ORIGIN%/}"
AGENT_URL="${VSS_ORIGIN}"

VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
"${VSS[@]}" search run --help >/dev/null || exit 1
"${VSS[@]}" configure --base-url "${VSS_ORIGIN}" || exit 1
CONFIG_JSON=$("${VSS[@]}" configure show) || exit 1

ES_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er '.services.elasticsearch.url') || exit 1
RTVI_CV_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er '.services.rtvi_cv.url') || exit 1
RTVI_VLM_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er \
  '.services.rt_vlm.url // empty') || RTVI_VLM_URL=
resolve_search_indexes() {
  CONFIG_JSON=$("${VSS[@]}" configure show) || return 1
  printf '%s' "${CONFIG_JSON}" |
    jq -e '.services.elasticsearch.indices | type == "array"' >/dev/null || return 1
  EMBED_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(startswith("mdx-embed-"))] | sort | first') || return 1
  BEHAVIOR_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(startswith("mdx-behavior-"))] | sort | first') || return 1
  RAW_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(startswith("mdx-raw-"))] | sort | first') || return 1
  [ "${EMBED_INDEX}" != "${BEHAVIOR_INDEX}" ] &&
    [ "${EMBED_INDEX}" != "${RAW_INDEX}" ] &&
    [ "${BEHAVIOR_INDEX}" != "${RAW_INDEX}" ]
}
```

Indexes are created lazily by ingestion, so `resolve_search_indexes` fails on a
fresh stack and keeps failing until the embedding index exists. Never call it
once and treat the failure as fatal — pair it with `vss configure --base-url
"${VSS_ORIGIN}"` inside the bounded readiness wait below, so each pass re-reads
the deployment and a late-created index is picked up.
Never use `ELASTIC_SEARCH_INDEX`, an index template, or a guessed date in place
of `vss configure show`.

Before downloading or ingesting media, require bounded Agent and VST health
through the deployment's host-reachable origin and RTVI-CV readiness. If
RT-VLM is recorded, probe its `/v1/models` endpoint; if it is absent, continue
and let search hits remain `unverified`. A particular eval or deployment
request may explicitly require RT-VLM and should then stop when that stronger
prerequisite is unmet. RTVI-CV may build its TensorRT engine for several
minutes, so poll its readiness with backoff rather than probing once.

Cleanup, upload, RTVI-CV readiness, and post-ingest index readiness draw on ONE
shared 40-minute source-setup budget, not 40 minutes each. Deployment and
public-origin selection are prerequisite work outside this ingestion budget.
Carry the remaining source-setup budget forward instead of restarting the
clock, and never redeploy, restart, or re-ingest to recover time already spent.

On Brev, two different origins produce media URLs, and they are easy to
conflate:

1. **The host CLI stamps the origin you gave `vss configure`.** `vss search
   run` builds every `screenshot_url` from the recorded deployment origin —
   `vst_external_url` is set to that base URL, not to `VST_EXTERNAL_URL`. So
   the only way to make CLI hits carry browser-usable media links is to run
   `vss configure --base-url` against the public HTTPS secure-link origin.
   Editing `VST_EXTERNAL_URL` in `generated.env` cannot change them, and
   recreating containers to chase that value is wasted work.
2. **`VST_EXTERNAL_URL` governs the Agent-served path.** The profile's
   `config.yml` feeds it to the agent, so it is what the UI and
   `/api/v1/search` responses emit. Give the deployment workflow the Brev
   values before it writes `generated.env` so that path is right too, but do
   not expect it to affect the CLI.

Prefer the public secure-link origin for `vss configure` whenever a bounded
probe shows it answers `/vst/api/v1/sensor/version` from this host. If it does
not answer, configure against the host-reachable origin so retrieval still
works, and report that CLI media URLs will be host-local until the secure link
is fixed — that is a routing failure to report, not to repair in a loop, and it
must not block fixture download, Agent-backed ingestion, or index readiness.

A probe succeeds only on a non-redirecting HTTP 200 with the VST version
schema. A Cloudflare/Pomerium redirect or HTML login page is a failed public
probe even though plain `curl -f` would return zero for a 3xx response. The
bundled selector owns the sole public request. Execute it exactly once and
consume its decision, even when it selects the fallback. Do not issue a
public-origin `curl` before or after it, reconstruct its command, rerun it to
confirm the result, or troubleshoot a `000`/redirect/schema failure during
this workflow:

```bash
: "${VSS_PUBLIC_CANDIDATE:?deployment-minted public HTTPS origin}"
: "${VSS_HOST_ORIGIN:?host-reachable HAProxy origin}"
ORIGIN_SELECTOR="${VSS_REPO_ROOT}/skills/vss-search-archive/scripts/select_brev_origin.sh"
test -x "${ORIGIN_SELECTOR}" || exit 1
ORIGIN_SELECTION=$("${ORIGIN_SELECTOR}" \
  "${VSS_PUBLIC_CANDIDATE}" "${VSS_HOST_ORIGIN}") || exit 1
VSS_ORIGIN=$(printf '%s' "${ORIGIN_SELECTION}" |
  jq -er '.origin | select(type == "string" and length > 0)') || exit 1
VSS_MEDIA_SCOPE=$(printf '%s' "${ORIGIN_SELECTION}" |
  jq -er '.media_scope | select(. == "public" or . == "host-local")') || exit 1
if [ "${VSS_MEDIA_SCOPE}" = host-local ]; then
  echo "Public VST probe failed semantic validation; CLI media URLs will be host-local" >&2
fi
```

Never assemble a Brev hostname from guesswork: the documented
`7777-<BREV_ENV_ID>.<BREV_LINK_DOMAIN>` form, built only from values read out
of `/etc/environment`, is the one sanctioned construction, and letting the
deployment workflow write it is preferred. Never rewrite a returned media URL.

On Kubernetes, use only routed Ingress services. Do not port-forward
Elasticsearch for readiness or cleanup. When Elasticsearch is not routed,
report only the Agent and VST state you can actually validate.

```bash
index_count() {
  INDEX=$1 FIELD=$2 VALUE=$3
  QUERY=$(jq -cn --arg field "${FIELD}" --arg value "${VALUE}" \
    '{query:{term:{($field):$value}}}') || return 1
  COUNT_TIMEOUT=$(readiness_timeout 15) || return 1
  curl -fsS --max-time "${COUNT_TIMEOUT}" -H 'Content-Type: application/json' \
    "${ES_URL}/${INDEX}/_count" -d "${QUERY}" | jq -er '.count | numbers'
}
```

## Pre-ingestion cleanup

At the start of one source-setup operation, after deployment, public-origin
selection, and `vss configure` are complete, initialize the one ingestion
deadline. Assign `SEARCH_READINESS_DEADLINE` only here; never create
`DEADLINE`, `READINESS_DEADLINE`, `CLEANUP_DEADLINE`, or another phase timer,
and never reserve or subtract a fixed number of seconds from it:

```bash
: "${SEARCH_READINESS_DEADLINE:=$(($(date +%s) + 2400))}"
export SEARCH_READINESS_DEADLINE
readiness_timeout() {
  local request_cap=$1 current_epoch remaining
  current_epoch=$(date +%s)
  remaining=$((SEARCH_READINESS_DEADLINE - current_epoch))
  (( remaining > 0 )) || {
    echo "Search source-setup deadline exhausted" >&2
    return 1
  }
  (( request_cap < remaining )) && printf '%s\n' "${request_cap}" || printf '%s\n' "${remaining}"
}
```

For every subsequent blocking source-mutation or readiness request, obtain its
`--max-time` from `readiness_timeout <per-request-cap>` immediately before the
request. A literal `--max-time`, a new epoch-plus-duration expression, or a
phase-local deadline after this initialization violates the source-setup
contract.

Cleanup is an Agent operation. Resolve every exact or duplicate fixture entry
from the VST source list, then delete its UUID only through the Agent:

```bash
VST_LIST_TIMEOUT=$(readiness_timeout 15) || exit 1
VST_SENSOR_LIST=$("${VSS[@]}" vios list) || exit 1
mapfile -t SENSORS_TO_DELETE < <(
  printf '%s' "${VST_SENSOR_LIST}" |
    jq -er '.sensors[] | select(.name == "airport" or
                        .name == "warehouse_sample" or
                        .name == "warehouse-ladder" or
                        .name == "sample-warehouse-ladder") |
            .sensorId | select(type == "string" and length > 0)'
)
for SENSOR_TO_DELETE in "${SENSORS_TO_DELETE[@]}"; do
  test -n "${SENSOR_TO_DELETE}" || exit 1
  DELETE_TIMEOUT=$(readiness_timeout 300) || exit 1
  curl -fsS --connect-timeout 5 --max-time "${DELETE_TIMEOUT}" -X DELETE \
    "${AGENT_URL%/}/api/v1/videos/${SENSOR_TO_DELETE}" |
    jq -e '.status == "success"' >/dev/null || exit 1
done

while :; do
  VST_LIST_TIMEOUT=$(readiness_timeout 15) || exit 1
  VST_SENSOR_LIST=$("${VSS[@]}" vios list) || exit 1
  if ! printf '%s' "${VST_SENSOR_LIST}" | jq -e \
    'any(.sensors[]; .name == "airport" or
              .name == "warehouse_sample" or
              .name == "warehouse-ladder" or
              .name == "sample-warehouse-ladder")' >/dev/null; then
    break
  fi
  sleep 10
done
```

Never send a mutating request directly to VST, RTVI-CV, RTVI-Embed,
storage-ms, or Elasticsearch. In particular, do not use `DELETE` on ports
30888, 9000, 8010, 8017, or 9200. If Agent cleanup fails, stop; do not repair
partial state through a backend.

## File source

List current sources with `"${VSS[@]}" vios list`; do not upload an exact
existing source. Confirm an interactive upload, then use the mandatory
three-step agent flow.

**The block below ingests ONE file. Run it again, in full, for each further
file** — `POST /api/v1/videos`, the upload, then `/complete`, every time. Set
`FILE_PATH` (and `UPLOAD_FILENAME` if it differs) and re-run from the top. The
failure mode is doing the first step once and reusing its upload URL or
`SENSOR` for the next file: the second file then has no `/api/v1/videos` call
of its own, is never registered, and its index documents never appear — which
surfaces much later as an empty search rather than an upload error.

**There is no one-step shortcut.** `PUT /api/v1/videos-for-search/{filename}`
is deprecated; calling it fails the ingestion contract even when it appears to
work. Repeating the three steps per file is the supported path — the repetition
is the cost of the contract, not a sign you have taken a wrong turn.

For the release fixtures, download the exact pinned bundle into a fresh
directory; never use `find` to substitute a pre-existing warehouse-looking
file. Ingest only the files the request names:

```bash
FIXTURE_ROOT=$(mktemp -d /tmp/vss-search-fixtures.XXXXXX)
cd "${FIXTURE_ROOT}" || exit 1
ngc registry resource download-version \
  nvidia/vss-developer/dev-profile-sample-data:3.2.0 \
  --org nvidia --team vss-developer || exit 1
tar -xzf dev-profile-sample-data_v3.2.0/dev-profile-sample-data.tar.gz || exit 1
SAMPLE_DIR="${FIXTURE_ROOT}/dev-profile-sample-data"
test -s "${SAMPLE_DIR}/warehouse_sample.mp4" || exit 1
test -s "${SAMPLE_DIR}/sample-warehouse-ladder.mp4" || exit 1
```

```bash
: "${AGENT_URL:?resolve the selected search agent}"
: "${FILE_PATH:?set the local media path}"
test -r "${FILE_PATH}" || exit 1
SOURCE_FILENAME=$(basename -- "${FILE_PATH}")
UPLOAD_FILENAME="${UPLOAD_FILENAME:-${SOURCE_FILENAME}}"
CANONICAL_SOURCE="${UPLOAD_FILENAME%.*}"
RTVI_CV_LOG_SINCE="${RTVI_CV_LOG_SINCE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

UPLOAD_REQUEST=$(jq -cn --arg filename "${UPLOAD_FILENAME}" '{filename: $filename}')
UPLOAD_REQUEST_TIMEOUT=$(readiness_timeout 30) || exit 1
UPLOAD_URL_RESPONSE=$(curl -sfS --max-time "${UPLOAD_REQUEST_TIMEOUT}" -X POST \
  "${AGENT_URL}/api/v1/videos" \
  -H "Content-Type: application/json" -d "${UPLOAD_REQUEST}")
UPLOAD_URL=$(printf '%s' "${UPLOAD_URL_RESPONSE}" |
  jq -er '.url | select(type == "string" and length > 0)') || exit 1

IDENTIFIER=$(uuidgen 2>/dev/null || cat /proc/sys/kernel/random/uuid)
UPLOAD_TIMEOUT=$(readiness_timeout 300) || exit 1
UPLOAD_RESPONSE=$(curl -sfS --connect-timeout 10 --max-time "${UPLOAD_TIMEOUT}" -X POST \
  "${UPLOAD_URL}" \
  -H "nvstreamer-chunk-number: 1" \
  -H "nvstreamer-total-chunks: 1" \
  -H "nvstreamer-is-last-chunk: true" \
  -H "nvstreamer-identifier: ${IDENTIFIER}" \
  -H "nvstreamer-file-name: ${UPLOAD_FILENAME}" \
  -F "mediaFile=@${FILE_PATH};filename=${UPLOAD_FILENAME}" \
  -F "filename=${UPLOAD_FILENAME}" \
  -F 'metadata={"timestamp":"2025-01-01T00:00:00"}')

SENSOR=$(printf '%s' "${UPLOAD_RESPONSE}" |
  jq -er '.sensorId | select(type == "string" and length > 0)') || exit 1
COMPLETE_TIMEOUT=$(readiness_timeout 900) || exit 1
COMPLETE_RESPONSE=$(printf '%s' "${UPLOAD_RESPONSE}" |
  jq --arg filename "${UPLOAD_FILENAME}" '. + {filename: $filename}' |
  curl -sfS --connect-timeout 10 --max-time "${COMPLETE_TIMEOUT}" -X POST \
    "${AGENT_URL}/api/v1/videos/${SENSOR}/complete" \
    -H "Content-Type: application/json" -d @-)
printf '%s' "${COMPLETE_RESPONSE}" | jq -e . >/dev/null || exit 1
printf '%s' "${COMPLETE_RESPONSE}" |
  jq -e --arg sensor "${SENSOR}" \
    '.sensor_id == $sensor and
     (.chunks_processed | type == "number" and . > 0)' >/dev/null ||
  { echo "Upload completion failed validation" >&2; exit 1; }

VST_LIST_TIMEOUT=$(readiness_timeout 15) || exit 1
VST_SENSOR_LIST=$("${VSS[@]}" vios list) || exit 1
printf '%s' "${VST_SENSOR_LIST}" | jq -e \
  --arg sensor "${SENSOR}" --arg name "${CANONICAL_SOURCE}" \
  'any(.sensors[]; .sensor_id == $sensor and .name == $name)' >/dev/null || {
    echo "VST did not register ${CANONICAL_SOURCE} with sensorId ${SENSOR}" >&2
    exit 1
  }
```

Never call the deprecated single-step
`PUT /api/v1/videos-for-search/{filename}`. Use
`UPLOAD_FILENAME` consistently in every request and multipart field; use that
same value for the upload request, VST metadata, and completion body.
Completion alone is not readiness. After completing all intended uploads, run
one bounded readiness wait (at most 20 minutes) until VST lists the sources and
the search indexes contain the required documents:

- `EMBED_INDEX`, `sensor.id.keyword`, resolved VST sensor UUID;
- `BEHAVIOR_INDEX`, `sensor.id.keyword`, canonical source name;
- `RAW_INDEX`, `sensorId.keyword`, canonical source name.

Embed search requires the first tuple. Fusion requires all three. Agent and
RTVI-CV logs are bounded diagnostics only: live VST registration plus the
required embedding, behavior, and raw documents are the readiness contract.
Never keep an otherwise-ready setup waiting for an exact log message.

For the two search fixtures, preserve the upload UUIDs as
`WAREHOUSE_SAMPLE_SENSOR` and `WAREHOUSE_LADDER_SENSOR`, then use this single
bounded wait:

```bash
: "${WAREHOUSE_SAMPLE_SENSOR:?preserve warehouse_sample upload sensorId}"
: "${WAREHOUSE_LADDER_SENSOR:?preserve warehouse-ladder upload sensorId}"
: "${SEARCH_READINESS_DEADLINE:?initialize once when source setup begins}"
while :; do
  # Re-read the deployment every pass. Indexes are created lazily by ingestion,
  # so `configure` + `resolve_search_indexes` run inside this wait, not before
  # it: resolving once while the embedding index does not yet exist fails
  # outright and never reaches the document counts below.
  if "${VSS[@]}" configure --base-url "${VSS_ORIGIN}" >/dev/null 2>&1 &&
     resolve_search_indexes; then
    SAMPLE_EMBED_COUNT=$(index_count "${EMBED_INDEX}" sensor.id.keyword \
      "${WAREHOUSE_SAMPLE_SENSOR}" 2>/dev/null || echo 0)
    LADDER_EMBED_COUNT=$(index_count "${EMBED_INDEX}" sensor.id.keyword \
      "${WAREHOUSE_LADDER_SENSOR}" 2>/dev/null || echo 0)
    LADDER_BEHAVIOR_COUNT=$(index_count "${BEHAVIOR_INDEX}" sensor.id.keyword \
      warehouse-ladder 2>/dev/null || echo 0)
    LADDER_RAW_COUNT=$(index_count "${RAW_INDEX}" sensorId.keyword \
      warehouse-ladder 2>/dev/null || echo 0)
    if (( SAMPLE_EMBED_COUNT > 0 && LADDER_EMBED_COUNT > 0 &&
          LADDER_BEHAVIOR_COUNT > 0 && LADDER_RAW_COUNT > 0 )); then
      break
    fi
  fi
  CURRENT_EPOCH=$(date +%s)
  (( CURRENT_EPOCH < SEARCH_READINESS_DEADLINE )) || break
  sleep 15
done
# Fail loudly if the wait expired without the indexes appearing, rather than
# falling through with EMBED_INDEX unset into a search that reads nothing.
resolve_search_indexes || {
  echo "search indexes never appeared before the deadline (embedding index missing)" >&2
  exit 1
}
printf 'indexes=%s,%s,%s sensors=%s,%s counts=%s,%s,%s,%s\n' \
  "${EMBED_INDEX}" "${BEHAVIOR_INDEX}" "${RAW_INDEX}" \
  "${WAREHOUSE_SAMPLE_SENSOR}" "${WAREHOUSE_LADDER_SENSOR}" \
  "${SAMPLE_EMBED_COUNT}" "${LADDER_EMBED_COUNT}" \
  "${LADDER_BEHAVIOR_COUNT}" "${LADDER_RAW_COUNT}"
(( SAMPLE_EMBED_COUNT > 0 && LADDER_EMBED_COUNT > 0 &&
   LADDER_BEHAVIOR_COUNT > 0 && LADDER_RAW_COUNT > 0 )) || exit 1
```

A timeout or partial registration is an error, not
permission to query another source. Do not automatically delete, repair, or
reingest after `/complete`: that turns a bounded setup into an unbounded
recovery loop and destroys evidence of the original failure. Print the
resolved endpoints, index names, UUIDs, and counts, then collect only bounded
read-only diagnostics:

```bash
DIAGNOSTIC_TIMEOUT=$(readiness_timeout 15) || exit 1
curl -fsS --connect-timeout 5 --max-time "${DIAGNOSTIC_TIMEOUT}" \
  "${ES_URL%/}/_cat/indices/mdx-*?format=json" | jq . || true
for CONTAINER in vss-rtvi-cv vss-behavior-analytics vss-video-analytics-api; do
  docker logs --since "${RTVI_CV_LOG_SINCE}" --tail 200 "${CONTAINER}" 2>&1 || true
done
```

Then stop with an error. Never post directly to RTVI-CV or Elasticsearch to
patch partial state. Use `index_count` with each exact tuple and accept
readiness only when each required count is greater than zero. A count from
another index or field does not satisfy readiness.

For Kubernetes, do not query Elasticsearch directly. After `/complete`
succeeds, poll `${VSS_VIOS_URL}/api/v1/sensor/list` for the canonical source,
then retry the requested Agent search only while ingestion is incomplete. A
valid Agent result proves the public workflow is operational; do not claim
direct index-level validation or create a port-forward.

## RTSP source

Register the exact RTSP URL through the selected search agent:

```bash
curl -sfS -X POST "${AGENT_URL}/api/v1/rtsp-streams/add" \
  -H "Content-Type: application/json" \
  -d '{
    "sensorUrl": "rtsp://<host>:<port>/<path>",
    "name": "<source-name>",
    "username": "",
    "password": "",
    "location": "",
    "tags": ""
  }' | jq .
```

The response is `{status, message, error}` and does not contain a sensor UUID;
the agent keys the stream by `name`. Do not log credentials. Poll boundedly
until the source is registered, then resolve its exact VST sensor identity
before search. A successful add only starts embedding generation; it does not
prove that searchable documents exist. Poll the selected embedding index for
the exact registered stream identity and require a count greater than zero
within five minutes.

## Delete source

Resolve exactly one source and save its UUID and canonical name before deletion.
Confirm the target unless deletion was already explicit:

```bash
: "${SAVED_SENSOR_ID:?save the exact file-source UUID before deletion}"
: "${SAVED_SOURCE_NAME:?save the canonical source name before deletion}"
: "${EMBED_INDEX:?resolve from vss configure show}"
: "${BEHAVIOR_INDEX:?resolve from vss configure show}"
: "${RAW_INDEX:?resolve from vss configure show}"

DELETE_READINESS_DEADLINE=$(($(date +%s) + 600))
delete_timeout() {
  local request_cap=$1 remaining
  remaining=$((DELETE_READINESS_DEADLINE - $(date +%s)))
  (( remaining > 0 )) || return 1
  (( request_cap < remaining )) && printf '%s\n' "${request_cap}" || printf '%s\n' "${remaining}"
}
delete_index_count() {
  local index=$1 field=$2 value=$3 timeout query
  timeout=$(delete_timeout 15) || return 1
  query=$(jq -cn --arg field "${field}" --arg value "${value}" \
    '{query:{term:{($field):$value}}}') || return 1
  curl -fsS --max-time "${timeout}" -H 'Content-Type: application/json' \
    "${ES_URL%/}/${index}/_count" -d "${query}" | jq -er '.count | numbers'
}

DELETE_TIMEOUT=$(delete_timeout 60) || exit 1
DELETE_RESPONSE=$(curl -sfS --max-time "${DELETE_TIMEOUT}" -X DELETE \
  "${AGENT_URL%/}/api/v1/videos/${SAVED_SENSOR_ID}") || exit 1
printf '%s' "${DELETE_RESPONSE}" | jq -e '.status == "success"' >/dev/null || exit 1

# Last-known state, so an expiry can say what is still present rather than
# only that it gave up.
VST_PRESENT=unknown EMBED_COUNT=unknown BEHAVIOR_COUNT=unknown RAW_COUNT=unknown
while :; do
  VST_TIMEOUT=$(delete_timeout 15) || {
    # The delete was accepted; cleanup did not finish inside the deadline.
    # Report what is still there and exit 6 (partial) -- exiting 1 with a bare
    # message loses the half that did succeed, and reads as "delete failed"
    # when the source may already be gone and only an index still draining.
    printf 'delete_status=partial vst_present=%s counts=%s,%s,%s\n' \
      "${VST_PRESENT}" "${EMBED_COUNT}" "${BEHAVIOR_COUNT}" "${RAW_COUNT}" >&2
    echo "cleanup did not finish within the deadline; the values above are what is still present" >&2
    exit 6
  }
  VST_SENSORS=$("${VSS[@]}" vios list) || exit 1
  VST_PRESENT=$(printf '%s' "${VST_SENSORS}" | jq -r \
    --arg id "${SAVED_SENSOR_ID}" --arg name "${SAVED_SOURCE_NAME}" \
    'any(.sensors[]; .sensor_id == $id or .name == $name)') || exit 1
  case "${VST_PRESENT}" in true|false) ;; *) exit 1 ;; esac
  EMBED_COUNT=$(delete_index_count "${EMBED_INDEX}" sensor.id.keyword \
    "${SAVED_SENSOR_ID}") || exit 1
  BEHAVIOR_COUNT=$(delete_index_count "${BEHAVIOR_INDEX}" sensor.id.keyword \
    "${SAVED_SOURCE_NAME}") || exit 1
  RAW_COUNT=$(delete_index_count "${RAW_INDEX}" sensorId.keyword \
    "${SAVED_SOURCE_NAME}") || exit 1
  if [ "${VST_PRESENT}" = false ] &&
     (( EMBED_COUNT == 0 && BEHAVIOR_COUNT == 0 && RAW_COUNT == 0 )); then
    break
  fi
  sleep 10
done
printf 'delete_status=success vst_present=%s counts=%s,%s,%s\n' \
  "${VST_PRESENT}" "${EMBED_COUNT}" "${BEHAVIOR_COUNT}" "${RAW_COUNT}"
```

Require response `status` to be `success`; `partial` is not success. Reuse the
same runtime values and poll until VST no longer lists the source, the embedding
tuple for the saved UUID is zero, and behavior/raw tuples for the canonical name
are zero. Report all counts. Never delete an ambiguous source or issue
independent backend cleanup. RTSP deletion uses the advertised
`DELETE /api/v1/rtsp-streams/delete/<name>` Agent route and the same bounded
absence checks; never substitute a direct backend mutation.

For storage API version details use `vss-manage-video-io-storage`; use schemas
advertised by the exact running deployment rather than guessing.
