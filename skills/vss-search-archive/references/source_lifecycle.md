# Search source lifecycle

Use the search Agent endpoints for source mutations so VST, VIOS, and every
search index stay consistent. Never mutate VST, storage, RTVI-CV, or
Elasticsearch directly.

## Deployment state

Source operations require a prepared `search` profile and the project-local
CLI. Resolve the checkout and reuse the origin already recorded by the
deployment:

```bash
VSS_REPO_ROOT="${VSS_REPO_ROOT:-$HOME/video-search-and-summarization}"
test -f "${VSS_REPO_ROOT}/services/agent/pyproject.toml" || exit 1
VSS=(uv run --project "${VSS_REPO_ROOT}/services/agent" --no-dev --extra cli vss)
CONFIG_JSON=$("${VSS[@]}" configure show) || exit 1
VSS_ORIGIN=$(printf '%s' "${CONFIG_JSON}" |
  jq -er '.base_url | select(type == "string" and length > 0)') || exit 1
```

Do not redeploy, restart containers, edit routing, or repeat public-origin
selection to repair a source operation. On Brev, the deployment workflow owns
the one public-origin decision through `scripts/select_brev_origin.sh`. If it
selected the documented host fallback, media URLs remain host-local until the
public route is repaired.

Indexes are created lazily. Never guess index names or reuse the embedding
index for behavior or raw documents; read all three distinct names from
`vss configure show` after ingestion.

## Ingest the bundled search fixtures

For the release fixtures, run the bundled operation once:

```bash
INGEST_FIXTURES="${VSS_REPO_ROOT}/skills/vss-search-archive/scripts/ingest_search_fixtures.sh"
test -x "${INGEST_FIXTURES}" || exit 1
"${INGEST_FIXTURES}" 2400
```

The script owns one absolute deadline and the complete operation: prerequisite
health, idempotent Agent cleanup, pinned NGC download, both Agent upload
handshakes and transfers, simultaneous VST registration evidence, concurrent
completion, configuration refresh, and exact index readiness checks. Do not
recreate those steps in shell, rerun the script after a failure, or use logs as
readiness evidence.

Success is one JSON object containing the configured origin, both canonical
source UUIDs, three distinct index names, and positive document counts. A
failure is a nonzero exit with a structured `error`; report it without
redeploying or resetting the deadline.

The script downloads `nvidia/vss-developer/dev-profile-sample-data:3.2.0` into
a fresh `mktemp -d` directory and uploads only the extracted
`warehouse_sample.mp4` and `sample-warehouse-ladder.mp4` bytes. It names the
second source `warehouse-ladder.mp4`. Its upload authority translation is
restricted to the exact VST storage path when the already-recorded origin is a
literal non-global IP fallback; otherwise the Agent-returned URL is unchanged.

## Ingest another file source

First list registered sources and refuse an exact duplicate. For a non-fixture
file, use the same three Agent-backed stages documented by the Agent API:
request `{\"filename\": \"<name>\"}` with `POST /api/v1/videos`, upload the
bytes to the exact returned `url` using the returned upload contract, and send
the preserved upload response to `POST /api/v1/videos/<sensor-id>/complete`.
Keep the filename consistent across all three stages and validate the separate
completion response. Never send a mutating request directly to a backend.
Re-run project-local `vss configure --base-url "${VSS_ORIGIN}"` after the
upload, then wait boundedly for the relevant configured indexes.

## Ingest an RTSP source

Use the Agent's `POST /api/v1/rtsp-streams/add` endpoint with JSON fields
`sensorUrl`, `name`, `username`, `password`, `location`, and `tags`. Preserve
the exact operator-supplied URL and credentials; do not copy credentials into
logs, prompts, or final output. The response reports status rather than a
sensor UUID, so list VST afterward and require one exact canonical source match
before searching. If the source is absent, continue polling only within the
caller's bounded operation; do not mutate VIOS directly.

## Delete an uploaded file source

Use the bundled operation once with the exact canonical source name:

```bash
DELETE_SOURCE="${VSS_REPO_ROOT}/skills/vss-search-archive/scripts/delete_search_source.sh"
test -x "${DELETE_SOURCE}" || exit 1
"${DELETE_SOURCE}" warehouse-ladder 600
```

This operation is for uploaded files. Uploaded files use the fixed timestamped
search indexes configured by the Agent; do not use this `/videos` route for an
RTSP registration. The script resolves and saves the canonical name and UUID,
reads those distinct indexes from project-local `vss configure show`, issues
exactly one canonical Agent `DELETE /api/v1/videos/<video-id>`, and verifies
bounded convergence. It
checks VST absence by both UUID and name, because VST can retain a same-name
sensor under a suffixed UUID. It also checks these exact tuples:

- embedding: `sensor.id.keyword=<saved UUID>` in the embedding index
- behavior: `sensor.id.keyword=<canonical name>` in the behavior index
- raw: `sensorId.keyword=<canonical name>` in the raw index

Success is one JSON object containing the Agent status and warnings, VST
presence, and all three index/field/value/count tuples. Overall success
requires Agent status `success`, VST absence, and all three counts equal to
zero. Every failure emits structured JSON and exits nonzero. Do not retry the
DELETE, rerun the script, probe alternate route spellings, or replace its
verification with direct backend commands.

## Delete an RTSP source

Resolve the exact canonical stream name first, then issue one Agent-backed
`DELETE /api/v1/rtsp-streams/delete/<encoded-name>` request through
`${AGENT_URL}`. This is a different lifecycle from uploaded-file deletion; do
not call `delete_search_source.sh` or `/api/v1/videos/<video-id>` for an RTSP
source. Validate the response and boundedly require the exact name and UUID to
disappear from VST before reporting success.
