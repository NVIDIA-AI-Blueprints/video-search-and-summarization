# Provision a source (headless)

Registering a source brings **no** perception with it: a bare VIOS add stores or
publishes the media, but nothing detects, embeds, or captions it until the
source reaches the consumers a build deployed. A stock full-stack profile does
this through the agent in one transaction. When **no agent tier is present** —
e.g. a `vss-build-vision-ai` headless `_builds/<name>` deployment — this file is
that recipe: register one source with VIOS, and stop there.

Fan-out is the deployment's, not the caller's: VIOS posts each sensor lifecycle
event to the receivers its mounted notification config declares. Delivery is
asynchronous, so a consumer trails registration by design — waiting is the
answer, never a direct call to make up the lag.

## Headless-only — first line of defense

This recipe is the **agent-free** path. If an agent tier is present, **STOP** —
provisioning is agent-owned and a second provisioning path double-provisions. The
authoritative, ingress-independent signal is caller-supplied: the caller confirms
**no agent tier is deployed** before invoking this recipe, by the same contract it
injects the consumer endpoints (a `vss-build-vision-ai` caller derives it from
the build's service set). Absent that signal, fall back to a status-code-aware
probe — only a `2xx` is a real agent route; a `3xx` is the curated ingress's
catch-all redirect to `/kibana/` (headless), which `curl -sf` would wrongly count
as success:

```bash
# Only a 2xx is a real agent route. A 3xx is the ingress catch-all to /kibana/
# (headless) — do NOT treat it as present.
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "${ORIGIN%/}/api/v1/videos")
case "$code" in
  2*) echo "agent tier present — use the agent-backed ingest instead" >&2; exit 1 ;;
esac
```

Defer full-stack provisioning to the agent-mediated path for the build's
capability — search ingestion to `vss-search-archive` (`/api/v1/videos` +
`/complete`), alert rules to `vss-manage-alerts`, or the agent's ingest
routes. The probe is a coarse public-route
capability check, not internal discovery.

## One registration; the deployment owns the fan-out

Register the source once with VIOS. What happens next is decided by the
deployment, not the caller:

| Deployment state | Provisioning path |
|---|---|
| Agent tier present | Agent-owned ingest (`vss-search-archive` / `vss-manage-alerts`) — this recipe stops at the guard above |
| Headless | Register with VIOS; the mounted notification config fans it out |

On SDRC-routed deployments (warehouse and LVS Docker profiles, all Helm
profiles) the single-registration rule still holds: do **not** also register the
source as an SDRC workload — SDRC auto-fan-in plus a second provisioning path
provisions the same stream twice. Keep SDRC for VST recording/playback only.
Webhook-driven builds run VIOS in direct mode, with no SDRC chain.

The consumer set follows the build's resolved capabilities, not any profile.
**VIOS is the mandatory base** — every build registers exactly one source. Which
consumers actually receive it is decided by the mounted config, not by this
table, which maps each consumer to the owner contract for its API:

| Consumer | Expected when the build resolves… | Owner |
| --- | --- | --- |
| **RT-CV** | detection / tracking / attribute perception (also the base a CV-verification alert runs off) | `vss-deploy-detection-tracking-2d` |
| **RT-Embed** | chunk/video embeddings (retrieval / search) | `vss-deploy-video-embedding` |
| **RT-VLM** dense captioning (free-form prompt) | real-time VLM dense captioning (captions/incidents), and only where the build has **no Alert Bridge** — see the leg split below | `vss-deploy-dense-captioning` |
| **RT-VLM** tagging (controlled JSON-tag prompt) | BM25 tag-search indexing (captions → `mdx-vlm-captions` → Logstash → `default_<streamId>`), and only where the build has no Alert-Bridge leg on the same instance — see the leg split below | `vss-deploy-dense-captioning` + `vss-search-archive` |
| **Alert Bridge** | real-time alerts (`2d_vlm`) — the bridge, not the caller, drives RT-VLM's verification leg | `vss-manage-alerts` |

**Behavior-Analytics is never provisioned here**: it
consumes Kafka (`mdx-raw` / `mdx-embed`), so enabling its workers is config, not a
source call.

Both feed origins — upload and live — reach every receiver; the origin changes
which source URL the event carries.

**RT-VLM carries two independent legs** on one deployment, differing only in the
`generate_captions` prompt — no second service:

- **Dense captioning** — a free-form prompt for captions/incidents. **Skipped
  where the build carries an Alert Bridge** (alerts `2d_vlm` realtime or `2d_cv`
  verification): the rule is created via `vss-manage-alerts`
  `POST :9080/api/v1/realtime` (per-sensor `live_stream_url`) and the bridge
  wires `rtvi-vlm` itself. That orchestration is owned by `vss-manage-alerts`,
  not this recipe, on either path.
- **Tagging** — a controlled JSON-tag prompt (`response_format`
  `json_object`, `temperature=0`, 5s chunks) feeding BM25 tag search. It serves
  search indexing rather than alert verification, but it does not run alongside
  a bridge-driven leg on the same instance: both publish to `mdx-vlm-captions`,
  where a static consumer cannot tell the two apart, and a second caption
  request differing in chunk duration, frame sampling, input size, or audio
  settings is rejected `400 BadParameters`. Builds enable one or the other
  (`vss-build-vision-ai` `references/services/vios.md`).

Where the mounted config enables a tagging receiver, the tagging leg needs no
call from here: that receiver carries the tag prompt in `user_defined_metadata`,
RT-VLM starts captioning on any `stream/add` bearing a `prompt`, and the
matching `camera_remove` item tears it down. Calling it by hand there does not
merely duplicate the work — the webhook has already registered the asset under
the `sensorId`, so the upload call returns `400 AssetAlreadyExists` and the live
registration is rejected as a duplicate stream id. Drive it by hand only where
no such receiver exists — *Driving RT-VLM by hand*, below.

## Endpoints are injected by the caller — never hard-code ports

This recipe takes its endpoints as **inputs** — `VST_API_BASE`, plus
`RTVI_VLM_URL` where a caller drives RT-VLM by hand; it reads no build manifest
itself. A `vss-build-vision-ai` build reads them from its resolved service ports
and hands them in; a human operator supplies them directly. A build can remap
ports, so never hard-code. Address each endpoint by the vantage that uses it:

- **Calls you make from the deploy host** — VIOS/VST, NvStreamer, RT-VLM — use
  `http://localhost:<resolved-port>`. On a build that fronts RT-VLM at
  `/rtvi-vlm`, the origin URL (`http://<origin>/rtvi-vlm`) reaches it from any
  host; loopback (`http://localhost:${RTVI_VLM_PORT:-8018}`) remains the
  lower-latency choice from the deploy host, and is its only form otherwise.
- **URLs a service consumes** — the synthetic RTSP from NvStreamer and the VIOS
  live proxy — are host-reachable `$HOST_IP` URLs produced by those services:
  **read** them, don't build them. VIOS assigns the RTSP port from its pool
  (`30554–30564`) at registration, so only VIOS knows the exact value.

`vss vios` is the exception: it resolves VIOS from the deployment `vss
configure` recorded, so it takes no endpoint (repo `AGENTS.md` for the entry
point, [`libs/vss/cli/AGENTS.md`](../../../../libs/vss/cli/AGENTS.md) for the
commands).

## Register the source

Register one source in VIOS. The origin decides the register call and, on the
live path, a read-back that doubles as the readiness gate.

**Stored file (upload).** `vss vios add` stores the bytes, pins the timeline
anchor at `2025-01-01T00:00:00.000Z` (see the date rule), and returns only once
VIOS has indexed the recording — exit 7 if it never does, rather than a sensor
nothing can use. That wait **is** this origin's readiness gate:

```bash
vss vios add /path/to/clip.mp4     # or an http(s) URL, streamed straight through
vss vios timeline --sensor <name>  # re-check a source registered earlier
```

The equivalent raw call is
`PUT /vst/api/v1/storage/file/<filename>?timestamp=…` without the wait
([`api-reference.md`](api-reference.md) § 8). A bare upload stores bytes only —
no detections or embeddings.

There is no live proxy on this path. Where a caller drives RT-VLM by hand, the
media URL is `vss vios clip --sensor <name>` → `media_url`, which the CLI
re-anchors on the deployment origin so it resolves from inside the consumer's
container. RT-VLM also accepts `file://`, but only under `FILE_URL_ALLOWED_DIRS`
(unset by default), so the HTTP URL is the reliable form.

**Live (RTSP).** Register the RTSP URL — an external camera as-is, or a local file
served as synthetic RTSP by NvStreamer (stage it into
`${VSS_DATA_DIR}/videos/<build>/`, then **read** the generated URL from
`GET http://localhost:<nvstreamer-port>/api/v1/sensor/<stem>/streams` `.[0].url`,
never construct it):

```bash
vss vios add rtsp://…                      # or, raw:
POST http://localhost:<vios-port>/vst/api/v1/sensor/add
#   {"sensorUrl":"rtsp://…","name":"…","username":"","password":""} → {sensorId}
#   the field is `sensorUrl`, not `url`.
```

Then resolve the **live proxy**. The CLI does not surface it, so this read is
raw, and it is this origin's **readiness gate**. VST re-publishes the stream
under a stable, VIOS-managed,
`sensorId`-keyed RTSP handle (e.g. `rtsp://<vios-host>:<pool-port>/live/<sensorId>`),
published asynchronously. SDRC-routed builds (warehouse, LVS, Helm) gate the
republish on the SDRC Envoy, so the per-sensor
`GET /sensor/<sensorId>/streams` → `.url` can stay empty well past the brief
post-add race. Docker search and alerts run VIOS in **direct** mode; the same
aggregate/proxy read + backoff still applies for the brief post-add race.
Resolve the handle from the endpoint that carries it — the aggregate
`GET /sensor/streams` (match your `sensorId`) or `GET /proxy/streams` (`.proxyUrl`
for that `sensorId`) — and **retry with backoff until it is non-empty**. It is
also the URL a hand-driven RT-VLM leg takes. Read it, never construct it; do
**not** fall back to the raw NvStreamer/camera URL — that bypasses the VIOS-managed handle and is the
failure mode the runtime evals guard against. This applies to the live/RTSP origin
only; the uploaded-file origin above has no live proxy.

## The shared-id rule

Every consumer must key on the **one VST `sensorId` returned at registration**,
VOD and RTSP alike. The id-vs-name distinction is load-bearing on both paths: embed
records key on the `sensorId`, behavior and raw records on the **source name**,
and both the search read path and the `camera_remove` ES cleanup resolve each
accordingly. An id/name mismatch deletes nothing, silently.

VIOS threads both itself: `{{event.camera_id}}` is the `sensorId`,
`event.camera_name` the `name` given at registration. Nothing to thread —
register under the canonical source name.

A caller driving RT-VLM by hand threads them instead, and carries them **in the
body** (`id` / `sensor_name`), since RT-VLM does not read the `x-stream-id`
header. Never let a consumer mint its own id: given none, it generates its own
asset UUID and its records land under an id no name- or sensor-scoped query can
reach.

## The upload-date rule

`creation_time`/`timestamp` is **upload-only**:
- VIOS anchors an untimed upload at `2025-01-01T00:00:00.000Z` itself, and
  `vss vios add` pins that same value rather than inherit it silently;
- on the webhook path the anchor rides the event as
  `metadata.file_start_time`, which RT-Embed and RT-VLM map to `creation_time`,
  so records land in the `*-2025-01-01` indices the `camera_remove` cleanup
  targets. VIOS emits that field on every upload, pinned or not — the consumers'
  `created_at` fallback never applies here, and omitting the pin does **not**
  move the records off the anchor;
- a caller driving RT-VLM by hand passes the anchor itself, as `creation_time`
  on `/v1/files` and `generate_captions`. Omit it and frame times are
  file-relative (epoch 0), landing captions in a `…-1970-01-01` index a
  date-pinned read can't see;
- **RTSP carries none** on any path — chunks are stamped from live NTP time.

## Idempotency and teardown

Add = register once. Duplicates are benign and expected on RTSP reconnect, since
VIOS re-fires `camera_streaming` with no dedup: RT-VLM answers
`409 DuplicateStreamId`, the Alert Bridge `STREAM_ADD_ALREADY_ACTIVE` — treat
both as success, never as a reason to re-add.

Teardown = `vss vios delete --type video|stream --sensor <name>` (raw:
`DELETE /sensor/<sensorId>`); the enabled `*-camera-remove` items withdraw the
source from the consumers and run the upload-anchor ES cleanup. Two known
limitations, not failures: the ES cleanups target the `*-2025-01-01` anchor
indices, so live-dated documents survive, and no shipped config cleans the VLM
tag index (`default_<streamId>`), so tag-search hits survive in full. A
hand-driven RT-VLM leg is torn down by its caller, before the sensor goes.

## Driving RT-VLM by hand

Two RT-VLM uses have no webhook receiver to do them for you: **dense
captioning**, which no shipped config carries an item for, and **tagging** on a
build whose config has no tagging receiver. Both take the registered source's
URL — the clip URL for an upload, the live proxy for a stream. Nothing else here is
caller-driven; RT-CV and RT-Embed receive every source from VIOS.

```bash
# RT-VLM — dense captioning (source-agnostic; captions → mdx-vlm-captions, yes/no
# incidents → mdx-vlm-incidents). SKIP when the build has an Alert Bridge — see
# the carve-out below.
POST http://localhost:<rt-vlm-port>/v1/files          # uploaded (VOD): multipart form only — -F purpose=vision -F media_type=video -F url=<vios-storage-url> -F creation_time=<upload-anchor> → file_id (a JSON body 422s; url is a form field; omit creation_time and captions land in a …-1970-01-01 index)
POST http://localhost:<rt-vlm-port>/v1/streams/add    # RTSP: feed the VIOS live-proxy URL
#   then POST .../v1/generate_captions with the returned file_id/stream_id

# RT-VLM tagging — the search-indexing leg, where no tagging receiver runs it.
# Independent of the Alert Bridge carve-out above; same rtvi-vlm deployment, different
# prompt. Controlled JSON-tag prompt + response_format json_object + temperature 0 +
# 5s chunks. RT-VLM does NOT read the x-stream-id header — carry identity in the body
# (id / sensor_name for VOD, streams[].id for RTSP). See the shared-id + upload-date rules.
#
# ⚠️ This leg is the ONLY one that produces searchable tag documents. The dense-captioning
# leg above uses a free-form prose prompt; its output lands in the SAME default_<streamId>
# index but is REJECTED by the tag reader (it is not the {"tags":[...],"description":"..."}
# JSON contract), so `vss search run tag` returns 0 valid hits (every document malformed).
# Same endpoint, same Kafka topic (mdx-vlm-captions), same index — ONLY the prompt differs.
# For tag search, always use the controlled JSON-tag prompt below, never the dense-captioning
# prompt.
#   Upload (VOD): one finite call, then delete the temporary asset —
TAG_PROMPT='Analyze only this video interval. Return JSON only with exactly two fields: "tags", an array of concise visible concepts, actions, objects, and events; and "description", one concise factual sentence. Do not infer facts that are not visible.'
curl -s -X POST "http://localhost:<rt-vlm-port>/v1/generate_captions" \
  -H "Content-Type: application/json" \
  --data-binary @- <<EOF
{"id":"<sensorId>","model":"<resolved-vlm-model>","url":"<vios-storage-url>","creation_time":"<upload-anchor>","prompt":$(printf '%s' "$TAG_PROMPT" | jq -Rs .),"response_format":{"type":"json_object"},"temperature":0,"chunk_duration":5,"stream":false}
EOF
#   then release the temporary RT-VLM file asset:
curl -s -X DELETE "http://localhost:<rt-vlm-port>/v1/files/<sensorId>"
#   Live (RTSP): register, then confirm admission (HTTP 200) and intentionally close
#   after a short read. A healthy endless SSE stream runs until the caller closes it, so
#   don't rely on `--max-time` alone: under `set -e` a timeout exits 28, and without status
#   validation an HTTP 4xx can still exit 0 — success/failure get inverted. Capture the HTTP
#   status, fail on >=400, and treat the deliberate early close (curl 28 / SIGPIPE 141 after
#   a 200) as admission confirmed. Both RT-VLM `id` and `sensor_name` carry the canonical
#   VIOS sensor ID (`<sensorId>`), never the display/source name.
#   `description` is a required field on AddLiveStream; carry the source name (or a
#   non-empty tag-session label) and validate the registration response before
#   starting the tagging leg.
if reg_code=$(curl -sS -o /dev/null -w '%{http_code}' \
  -X POST "http://localhost:<rt-vlm-port>/v1/streams/add" \
  -H "Content-Type: application/json" \
  -d "{\"streams\":[{\"liveStreamUrl\":\"<vios-url>\",\"description\":\"<source-name>\",\"id\":\"<sensorId>\",\"sensor_name\":\"<sensorId>\"}]}"
); then :; else echo "rt-vlm live stream registration failed (curl exit $?)" >&2; exit 1; fi
case "$reg_code" in 2*|3*) : ;; *) echo "rt-vlm live stream registration failed (http ${reg_code:-unknown})" >&2; exit 1 ;; esac
if http_code=$(curl -sS --connect-timeout 10 --max-time 5 \
  -X POST "http://localhost:<rt-vlm-port>/v1/generate_captions" \
  -H "Content-Type: application/json" -H "Accept: text/event-stream" \
  -o /dev/null -w '%{http_code}' \
  --data-binary @- <<EOF
{"id":"<sensorId>","model":"<resolved-vlm-model>","prompt":$(printf '%s' "$TAG_PROMPT" | jq -Rs .),"response_format":{"type":"json_object"},"temperature":0,"chunk_duration":5,"stream":true}
EOF
); then :; else
  case "$?" in 28|141) : ;; *) echo "rt-vlm tagging admission failed (curl exit $?)" >&2; exit 1 ;; esac
fi
case "$http_code" in 2*|3*) : "admission confirmed" ;; *) echo "rt-vlm tagging admission failed (http ${http_code:-unknown})" >&2; exit 1 ;; esac
#   stop with:  DELETE /v1/generate_captions/<sensorId>?request_id=<returned-id>  then  DELETE /v1/streams/delete/<sensorId>
#   (the request_id is the `id` RT-VLM returned in the admission/first-SSE response; without it the
#   DELETE tears down every subscriber on that stream — see `vss-build-vision-ai`
#   `references/services/rt-vlm.md` § Tearing down a hand-driven captioning session)
```

**Alert-Bridge carve-out — dense captioning only.** On a build carrying an Alert
Bridge, do **not** drive the dense-captioning leg from here: the rule is created
via `vss-manage-alerts` `POST :9080/api/v1/realtime` (per-sensor
`live_stream_url`) and the bridge wires `rtvi-vlm` itself. A direct
`/v1/streams/add` bypasses rule persistence and is a failure even if the stream
goes live. A build that carries a bridge leg does not also run tagging on that
instance, so the tagging block above does not apply there either.

Exact payloads and field lists live in the operating contracts — do not restate
them here: RT-VLM `vss-deploy-dense-captioning` `integrate-rt-vlm.md`;
VIOS/NvStreamer this skill's
[`integrate-vios-service.md`](integrate-vios-service.md) +
[`nvstreamer-api-reference.md`](nvstreamer-api-reference.md). The controlled
tag-prompt contract and the `default_<streamId>` indexing path are specified in
[`docs/designs/vlm-tagging-search.md`](../../../../docs/designs/vlm-tagging-search.md).

**Teardown.** A hand-driven leg is the caller's to stop:
`DELETE /v1/streams/delete/{id}` (a tagging leg first needs its `request_id`,
per the block above), then delete the VIOS sensor. No `camera_remove` item
covers it, so deleting only VIOS leaves RT-VLM holding the stream.

## Troubleshooting — a consumer never got the source

Registration is the caller's contract, so reach for this only when someone
reports missing perception, not as a routine check. Fan-out has no
introspection API: the evidence is a container-log grep, run against **both**
VIOS containers (`vss-vios-sensor`, `vss-vios-streamprocessing`) for these
literal patterns:

- `Webhook .*delivered`
- `Webhook .*giving up`
- `Webhook .*retrying in`
- `skipped, camera_type '`

Each line names its item as `camera_status_change/<event>`, plus ` (<id>)` where
the item has one. Receivers within an item are identified by 1-based position,
not by URL.

| Observed | Meaning | Action |
|---|---|---|
| `Webhook … giving up` (or repeated `retrying in`) for an enabled item | Delivery failed and was dropped — there is no dead-letter and no redelivery | Report the item and the log line. Remediation: fix the receiver, then re-register the source. Do **not** paper over it with a direct consumer call |
| Same, and that item's service is not deployed | The mounted config does not match the build | Report the item against the config as a build defect, not a fan-out failure (`vss-build-vision-ai` `references/services/vios.md`) |
| **No** webhook attempt at all | The item is disabled, or its `camera_type` filter excluded this origin | Report the receiver as unreachable by this origin, not as a delivery failure |
| `400 camera_url is required` | VIOS emits `camera_streaming` for every upload, but its URL generation failed, so the event carried an empty URL — only `camera_add` is tolerated URL-less | Report it against VIOS storage URL generation; re-registering will not help until the upload resolves a URL |

While `retrying in` lines are still appearing, delivery is in flight rather than
lost: the slowest shipped receivers spend ~30 minutes on their attempts before
they give up.

## Sources

- This skill: [`integrate-vios-service.md`](integrate-vios-service.md), [`nvstreamer-api-reference.md`](nvstreamer-api-reference.md), [`api-reference.md`](api-reference.md) (`§ RTSP Proxy` for `/proxy/streams`; `/sensor/streams`)
- `skills/deployment/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
- `skills/operations/vss-manage-alerts/references/always-on.md` (Alert Bridge always-on contract)
- Endpoint contract (read-vs-write resolution split): `skills/vss-build-vision-ai/references/deployment_resolution.md`
- Shipped notification configs: `deploy/docker/developer-profiles/dev-profile-{search,alerts,lvs}/vios/configs/` and the webhooks-disabled default `deploy/docker/services/vios/configs/notification_config.json`
