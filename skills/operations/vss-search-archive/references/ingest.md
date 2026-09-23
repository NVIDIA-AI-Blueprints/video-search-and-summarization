# Ingest a source for search

Register a source with `vss vios add`; the deployment's mounted notification
config fans it out to RT-CV, RT-Embed, and RT-VLM tagging. Never send a
mutating request directly to RTVI-CV, RTVI-Embed, storage-ms, VST, or
Elasticsearch — those receive every source from VIOS. Read
[source setup](source_setup.md) first: it owns the origin, the one
`SEARCH_READINESS_DEADLINE`, the `index_count` helper, and the readiness tuple
table this file polls.

## Pre-ingestion cleanup

At the start of one source-setup operation, after deployment, public-origin
selection, and `vss configure` are complete, the deadline is already assigned
in [source setup](source_setup.md). Cleanup is a listing plus a delete of any
exact or duplicate remnant of the fixtures the request names — never a bare
backend mutation:

```bash
VST_SENSOR_LIST=$(vss vios list) || exit 1
mapfile -t SENSORS_TO_DELETE < <(
  printf '%s' "${VST_SENSOR_LIST}" |
    jq -r '.sensors[] | select(.name == "airport" or
                        .name == "warehouse_sample" or
                        .name == "warehouse-ladder" or
                        .name == "sample-warehouse-ladder") |
            .sensor_id | select(type == "string" and length > 0)'
)
for SENSOR_TO_DELETE in "${SENSORS_TO_DELETE[@]}"; do
  test -n "${SENSOR_TO_DELETE}" || exit 1
  vss vios delete --type video --sensor "${SENSOR_TO_DELETE}" || exit 1
done

while :; do
  VST_SENSOR_LIST=$(vss vios list) || exit 1
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

The fixture names above are the canonical search-eval fixtures; a request that
names different media cleans those instead. `vss vios delete` triggers the
`camera_remove` webhooks, which withdraw the source from the consumers and run
the upload-anchor Elasticsearch cleanups. If a delete fails, stop; do not repair
partial state through a backend.

## File source

List current sources with `vss vios list`; do not register an exact existing
source. Confirm an interactive upload, then register one file per call. `vss
vios add` stores the bytes, pins the `2025-01-01` upload anchor, and blocks until
VIOS has indexed the timeline (exit 7 if it never does) — so the source is
registered and recorded before any readiness wait. Pass `--name` to register
under the canonical source name the request intends; the readiness tuples read
that name back from `vss vios list`, so they are correct regardless of the name
form.

```bash
: "${VSS_ORIGIN:?resolve the selected search origin}"
: "${FILE_PATH:?set the local media path}"
test -r "${FILE_PATH}" || exit 1
CANONICAL_SOURCE="${UPLOAD_FILENAME:-$(basename -- "${FILE_PATH}")}"

vss vios add "${FILE_PATH}" --name "${CANONICAL_SOURCE}" || exit 1
# Re-run configure: ingestion lazily creates the index inventory.
vss configure --base-url "${VSS_ORIGIN}" >/dev/null || exit 1
VST_SENSOR_LIST=$(vss vios list) || exit 1
printf '%s' "${VST_SENSOR_LIST}" | jq -e \
  --arg name "${CANONICAL_SOURCE}" \
  'any(.sensors[]; .name == $name)' >/dev/null || {
    echo "VIOS did not register ${CANONICAL_SOURCE}" >&2
    exit 1
  }
```

Run the block again, in full, for each further file — a fresh `vss vios add` per
file, every time. The failure mode is reusing one registration for the next
file: the second file is never registered and its index documents never appear,
which surfaces much later as an empty search rather than an upload error. There
is no one-step shortcut and no Agent three-step: `vss vios add` is the supported
registration, and the deployment's webhooks own the fan-out.

## RTSP source

Register the exact RTSP URL; the webhooks fan it out like a file:

```bash
vss vios add rtsp://<host>:<port>/<path> --name "<source-name>" || exit 1
```

A successful add only starts embedding generation; it does not prove that
searchable documents exist. Poll the embed index family wildcard
(`RTSP_EMBED_INDEX`), scoped to the resolved stream identity, and require a
count greater than zero within five minutes. Do not poll a single date-stamped
index: a live stream's docs are in today's index, not the uploads anchor.

## Readiness wait

Completion is not readiness. `vss vios add` returns once VIOS has indexed the
timeline; the consumer fan-out (embeddings, behavior, raw) lands asynchronously.
After registering all intended sources, run one bounded readiness wait (at most
20 minutes) until the Elasticsearch indexes contain the required documents per
the tuple table in [source setup](source_setup.md). Resolve each registered
source's `sensor_id` and `name` from `vss vios list` and use those exact values
— never a hardcoded name:

```bash
: "${SEARCH_READINESS_DEADLINE:?initialize once when source setup begins}"
while :; do
  # Re-read the deployment every pass. Indexes are created lazily by ingestion,
  # so `configure` + `resolve_upload_indexes` run inside this wait, not before
  # it: resolving once while the embedding index does not yet exist fails
  # outright and never reaches the document counts below.
  if vss configure --base-url "${VSS_ORIGIN}" >/dev/null 2>&1 &&
     resolve_upload_indexes; then
    SAMPLE_EMBED_COUNT=$(index_count "${SEARCH_READINESS_DEADLINE}" "${EMBED_INDEX}" sensor.id.keyword \
      "${WAREHOUSE_SAMPLE_SENSOR}" 2>/dev/null || echo 0)
    LADDER_EMBED_COUNT=$(index_count "${SEARCH_READINESS_DEADLINE}" "${EMBED_INDEX}" sensor.id.keyword \
      "${WAREHOUSE_LADDER_SENSOR}" 2>/dev/null || echo 0)
    LADDER_BEHAVIOR_COUNT=$(index_count "${SEARCH_READINESS_DEADLINE}" "${BEHAVIOR_INDEX}" sensor.id.keyword \
      "warehouse-ladder" 2>/dev/null || echo 0)
    LADDER_RAW_COUNT=$(index_count "${SEARCH_READINESS_DEADLINE}" "${RAW_INDEX}" sensorId.keyword \
      "warehouse-ladder" 2>/dev/null || echo 0)
    if (( SAMPLE_EMBED_COUNT > 0 && LADDER_EMBED_COUNT > 0 &&
          LADDER_BEHAVIOR_COUNT > 0 && LADDER_RAW_COUNT > 0 )); then
      break
    fi
  fi
  CURRENT_EPOCH=$(date +%s)
  (( CURRENT_EPOCH < SEARCH_READINESS_DEADLINE )) || break
  sleep 15
done
resolve_upload_indexes || {
  echo "search indexes never appeared before the deadline (embedding index missing)" >&2
  exit 1
}
```

A timeout or partial registration is an error, not permission to query another
source. Do not automatically delete, repair, or re-register after a failed add:
that turns a bounded setup into an unbounded recovery loop and destroys
evidence of the original failure. Print the resolved endpoints, index names,
UUIDs, and counts, then collect only bounded read-only diagnostics:

```bash
curl -fsS --connect-timeout 5 --max-time "$(source_timeout "${SEARCH_READINESS_DEADLINE}" 15)" \
  "${ES_URL%/}/_cat/indices/mdx-*?format=json" | jq . || true
for CONTAINER in vss-rtvi-cv vss-behavior-analytics vss-video-analytics-api; do
  docker logs --since "${RTVI_CV_LOG_SINCE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}" --tail 200 "${CONTAINER}" 2>&1 || true
done
```

Then stop with an error. Never post directly to RTVI-CV or Elasticsearch to
patch partial state. Use `index_count` with each exact tuple and accept readiness
only when each required count is greater than zero. A count from another index
or field does not satisfy readiness.

For Kubernetes, do not query Elasticsearch directly. After `vss vios add`
succeeds, poll `vss vios list` for the canonical source, then retry the
requested search only while ingestion is incomplete. A valid search result
proves the public workflow is operational; do not claim direct index-level
validation or create a port-forward.
