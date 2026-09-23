# Search source setup and readiness

Read this for deployment readiness, ingestion, fixture cleanup, index
checks, RTSP, or deletion. It owns the origin, the one source-setup
deadline, the Elasticsearch count helper, the readiness tuple table, and
the bounded-origin selection. Mutations are `vss vios add` / `vss vios
delete`; the deployment's mounted notification config owns the fan-out to
RT-CV, RT-Embed, and RT-VLM tagging. Never send a mutating request directly
to RTVI-CV, RTVI-Embed, storage-ms, VST, or Elasticsearch.

## Deployment and runtime state

Use the operator-provided Compose or Ingress origin. Never inspect Compose or
Kubernetes internals to rediscover it. On Brev, run the public-origin selection
block below first and set `VSS_ORIGIN` to its result. Do not configure a
provisional origin and change it afterward. Record that final origin, then read
the backends' own service, model, and index inventory:

```bash
: "${VSS_ORIGIN:?set the deployment origin}"
VSS_ORIGIN="${VSS_ORIGIN%/}"

vss search run --help >/dev/null || exit 1
vss configure --base-url "${VSS_ORIGIN}" || exit 1
CONFIG_JSON=$(vss configure show) || exit 1

ES_URL=$(printf '%s' "${CONFIG_JSON}" | jq -er '.services.elasticsearch.url') || exit 1
RTSP_EMBED_INDEX="mdx-embed-filtered-*"
resolve_upload_indexes() {
  # File uploads always land in the fixed epoch anchors. Read those exact names
  # from the configured inventory so file readiness and deletion never absorb
  # same-named live-stream documents from another date shard.
  CONFIG_JSON=$(vss configure show) || return 1
  printf '%s' "${CONFIG_JSON}" |
    jq -e '.services.elasticsearch.indices | type == "array"' >/dev/null || return 1
  EMBED_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(. == "mdx-embed-filtered-2025-01-01")] | first') || return 1
  BEHAVIOR_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(. == "mdx-behavior-2025-01-01")] | first') || return 1
  RAW_INDEX=$(printf '%s' "${CONFIG_JSON}" | jq -er \
    '[.services.elasticsearch.indices[] | select(. == "mdx-raw-2025-01-01")] | first') || return 1
  [ "${EMBED_INDEX}" != "${BEHAVIOR_INDEX}" ] &&
    [ "${EMBED_INDEX}" != "${RAW_INDEX}" ] &&
    [ "${BEHAVIOR_INDEX}" != "${RAW_INDEX}" ]
}
```

Use `RTSP_EMBED_INDEX` only for live-stream readiness: wall-clock RTSP documents
may land in any date shard, and the sensor-id filter scopes the wildcard count.
Use `resolve_upload_indexes` only after file ingestion or before file deletion;
it reads the three exact epoch anchors from `vss configure show` and fails until
all are present. Re-run `vss configure --base-url "${VSS_ORIGIN}"` after file
ingestion so its lazy-created index inventory is current. Never substitute
`ELASTIC_SEARCH_INDEX`, an index template, a wildcard for an upload tuple, or a
single date shard for live-stream readiness.

Runtime readiness is one command: `vss configure check` re-probes every routed
service and reports the served model list for each. If RT-VLM is recorded, the
check shows its nonempty served models; if it is absent, continue and let search
hits remain `unverified`. A particular eval or deployment request may explicitly
require RT-VLM and should then stop when that stronger prerequisite is unmet.
RTVI-CV may build its TensorRT engine for several minutes, so poll readiness
with backoff rather than probing once.

## One source-setup budget

Cleanup, upload, RTVI-CV readiness, and post-ingest index readiness draw on ONE
shared 40-minute source-setup budget, not 40 minutes each. Deployment and
public-origin selection are prerequisite work outside this ingestion budget.
Carry the remaining source-setup budget forward instead of restarting the
clock, and never redeploy, restart, or re-ingest to recover time already spent.
Assign the deadline once, at the start of a source-setup operation; never create
`DEADLINE`, `READINESS_DEADLINE`, `CLEANUP_DEADLINE`, or another phase timer, and
never reserve or subtract a fixed number of seconds from it:

```bash
: "${SEARCH_READINESS_DEADLINE:=$(($(date +%s) + 2400))}"
export SEARCH_READINESS_DEADLINE
source_timeout() {
  local deadline_epoch=$1 request_cap=$2 remaining
  remaining=$(( deadline_epoch - $(date +%s) ))
  (( remaining > 0 )) || { echo "Search source-setup deadline exhausted" >&2; return 1; }
  (( request_cap < remaining )) && printf '%s\n' "${request_cap}" || printf '%s\n' "${remaining}"
}
```

For every subsequent blocking source-mutation or readiness request, obtain its
`--max-time` from `source_timeout "${SEARCH_READINESS_DEADLINE}" <per-request-cap>`
immediately before the request. A literal `--max-time`, a new epoch-plus-duration
expression, or a phase-local deadline after this initialization violates the
source-setup contract. Deletion carries its own short deadline
(`DELETE_READINESS_DEADLINE`, see [delete](delete.md)) but reuses this same
`source_timeout` helper.

The Elasticsearch count helper is read-only and takes its deadline so it stays
inside whichever budget is in effect:

```bash
index_count() {
  local deadline=$1 index=$2 field=$3 value=$4 timeout query
  timeout=$(source_timeout "${deadline}" 15) || return 1
  query=$(jq -cn --arg field "${field}" --arg value "${value}" \
    '{query:{term:{($field):$value}}}') || return 1
  curl -fsS --connect-timeout 5 --max-time "${timeout}" -H 'Content-Type: application/json' \
    "${ES_URL%/}/${index}/_count" -d "${query}" | jq -er '.count | numbers'
}
```

## Readiness tuples

Search readiness is a fact about the Elasticsearch indexes, not about VST
registration: `vss vios add` already blocks until VIOS indexes the timeline
(exit 7), so the source is registered and recorded before this wait begins. Poll
the three tuples until each count is greater than zero. The `<sensor_id>` and
`<name>` are read back from `vss vios list` — whatever VIOS registered the
source under — so the tuples are correct regardless of the name form:

| index | field | value |
| --- | --- | --- |
| `EMBED_INDEX` (uploads) / `RTSP_EMBED_INDEX` (streams) | `sensor.id.keyword` | resolved VST sensor UUID |
| `BEHAVIOR_INDEX` | `sensor.id.keyword` | registered source name |
| `RAW_INDEX` | `sensorId.keyword` | registered source name |

Embed search requires the first tuple. Fusion requires all three. Behavior and
raw records key on the **source name**, embed on the **sensor UUID** — a mismatch
deletes nothing, silently. RTVI-CV and agent logs are bounded diagnostics only:
the live ES counts above are the readiness contract. Never keep an
otherwise-ready setup waiting for an exact log message.

## Brev origin

Two different origins produce media URLs, and they are easy to conflate:

1. **The host CLI stamps the origin you gave `vss configure`.** `vss search
   run` builds every `screenshot_url` from the recorded deployment origin —
   `vst_external_url` is set to that base URL, not to `VST_EXTERNAL_URL`. So the
   only way to make CLI hits carry browser-usable media links is to run
   `vss configure --base-url` against the public HTTPS secure-link origin.
   Editing `VST_EXTERNAL_URL` in `generated.env` cannot change them, and
   recreating containers to chase that value is wasted work.
2. **`VST_EXTERNAL_URL` governs the Agent-served path.** The profile's
   `config.yml` feeds it to the agent, so it is what the UI and `/api/v1/search`
   responses emit. Give the deployment workflow the Brev values before it writes
   `generated.env` so that path is right too, but do not expect it to affect the
   CLI.

Prefer the public secure-link origin for `vss configure` whenever a bounded probe
shows it answers `/vst/api/v1/sensor/version` from this host. If it does not
answer, configure against the host-reachable origin so retrieval still works,
and report that CLI media URLs will be host-local until the secure link is fixed
— that is a routing failure to report, not to repair in a loop, and it must not
block ingestion, or index readiness.

A probe succeeds only on a non-redirecting HTTP 200 with the VST version schema.
A Cloudflare/Pomerium redirect or HTML login page is a failed public probe even
though plain `curl -f` would return zero for a 3xx response. The bundled selector
owns the sole public request. Execute it exactly once and consume its decision,
even when it selects the fallback. Do not issue a public-origin `curl` before or
after it, reconstruct its command, rerun it to confirm the result, or
troubleshoot a `000`/redirect/schema failure during this workflow:

```bash
: "${VSS_PUBLIC_CANDIDATE:?deployment-minted public HTTPS origin}"
: "${VSS_HOST_ORIGIN:?host-reachable HAProxy origin}"
ORIGIN_SELECTOR="${VSS_REPO_ROOT}/skills/operations/vss-search-archive/scripts/select_brev_origin.sh"
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

Never assemble a Brev hostname — there is no sanctioned construction. Brev
publishes the secure-link hostname per exposed port in
`/etc/brev/environment-context.json`, and the deployment workflow reads it from
there into `VSS_PUBLIC_HOST`; take the origin from that. A
`<port>-<env>.<domain>` pattern is not a rule: the prefix is a name the link's
creator chooses and the domain varies per instance. Never rewrite a media URL
returned in a search result: the CLI already anchors those on the recorded
origin, so editing one only hides which origin answered.

On Kubernetes, use only routed Ingress services. Do not port-forward
Elasticsearch for readiness or cleanup. When Elasticsearch is not routed,
report only the VST state you can actually validate via `vss vios list`, and base
readiness on the source listing rather than direct index counts.
