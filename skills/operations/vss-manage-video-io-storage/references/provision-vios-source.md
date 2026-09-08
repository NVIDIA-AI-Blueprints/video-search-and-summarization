# Provision a source and verify fan-out (headless)

Registering a source brings **no** perception with it: a bare VIOS add stores or
publishes the media, but nothing detects, embeds, or captions it until the
source reaches the consumers a build deployed. A stock full-stack profile does
this through the agent in one transaction. When **no agent tier is present** —
e.g. a `vss-build-vision-ai` headless `_builds/<name>` deployment — this file is
that recipe: register one source with VIOS, then confirm the deployment's
configured fan-out delivered it.

Where the mounted notification config enables webhooks (the current Docker
search and alerts profiles, and any build selecting such a config), the fan-out
is **webhook-driven**: VIOS posts the sensor lifecycle events to the receivers
that config declares, so the caller registers and verifies, never provisions a
consumer. Without webhook coverage, use the direct-REST fallback appendix.
Step 2 decides which case you are in — never the profile name.

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

Register the source once with VIOS (Step 1). What happens next is decided by the
deployment, not the caller:

| Deployment state | Provisioning path |
|---|---|
| Agent tier present | Agent-owned ingest (`vss-search-archive` / `vss-manage-alerts`) — this recipe stops at the guard above |
| Headless, webhook coverage for this source's origin (Step 2) | Register with VIOS → **verify fan-out** (Step 3) |
| Headless, no webhook coverage | Register with VIOS → **direct-REST fan-out** (fallback appendix) |

On SDRC-routed deployments (warehouse and LVS Docker profiles, all Helm
profiles) the single-registration rule still holds: do **not** also register the
source as an SDRC workload — SDRC auto-fan-in plus a second provisioning path
provisions the same stream twice. Keep SDRC for VST recording/playback only.
Webhook-driven builds run VIOS in direct mode, with no SDRC chain.

The consumer set follows the build's resolved capabilities, not any profile.
**VIOS is the mandatory base** — every build registers exactly one source. On a
webhook-covered build the authoritative expected set is the mounted config
(Step 2), not this table; the table maps each consumer to the owner contract for
its payload and read-back API:

| Consumer | Expected when the build resolves… | Owner |
| --- | --- | --- |
| **RT-CV** | detection / tracking / attribute perception (also the base a CV-verification alert runs off) | `vss-deploy-detection-tracking-2d` |
| **RT-Embed** | chunk/video embeddings (retrieval / search) | `vss-deploy-video-embedding` |
| **RT-VLM** dense captioning (free-form prompt) | real-time VLM dense captioning (captions/incidents), and only where the build has **no Alert Bridge** — see the leg split below | `vss-deploy-dense-captioning` |
| **RT-VLM** tagging (controlled JSON-tag prompt) | BM25 tag-search indexing (captions → `mdx-vlm-captions` → Logstash → `default_<streamId>`); **independent of the Alert Bridge** | `vss-deploy-dense-captioning` + `vss-search-archive` |
| **Alert Bridge** | real-time alerts (`2d_vlm`) — the bridge, not the caller, drives RT-VLM's verification leg | `vss-manage-alerts` |

**Behavior-Analytics is never provisioned here**: it
consumes Kafka (`mdx-raw` / `mdx-embed`), so enabling its workers is config, not a
source call.

Both feed origins — upload and live — reach every receiver; the origin changes
which source URL the event carries (Step 1) and which `camera_type` filter
applies (Step 2).

**RT-VLM carries two independent legs** on one deployment, differing only in the
`generate_captions` prompt — no second service:

- **Dense captioning** — a free-form prompt for captions/incidents. **Skipped
  where the build carries an Alert Bridge** (alerts `2d_vlm` realtime or `2d_cv`
  verification): the rule is created via `vss-manage-alerts`
  `POST :9080/api/v1/realtime` (per-sensor `live_stream_url`) and the bridge
  wires `rtvi-vlm` itself. That orchestration is owned by `vss-manage-alerts`,
  not this recipe, on either path.
- **Tagging** — a controlled JSON-tag prompt (`response_format`
  `json_object`, `temperature=0`, 5s chunks) feeding BM25 tag search.
  **Independent of the Alert Bridge**: it owns search indexing, not alert
  verification, and coexists with a bridge — but only at a matching decode
  signature, since a second caption request differing in chunk duration, frame
  sampling, input size, or audio settings is rejected `400 BadParameters`.

On a webhook-covered build the tagging leg needs no call from here: the config's
`rtvi-vlm` receiver carries the tag prompt in `user_defined_metadata`, and
RT-VLM starts captioning automatically on any `stream/add` bearing a `prompt`,
so Step 3 verifies it and the `camera_remove` webhook tears it down. Drive it by
hand only in the fallback, where the teardown caveat below applies.

## Endpoints are injected by the caller — never hard-code ports

This recipe takes the consumer endpoints as **inputs** — `VST_API_BASE`,
`RTVI_CV_URL`, `RTVI_EMBED_URL`, `RTVI_VLM_URL`, and `ALERT_BRIDGE_URL` on
real-time-alerts builds — resolved and passed in by the caller; it reads no
build manifest itself. On the webhook path they serve the Step-3 read-back
checks; only the fallback writes to them. A `vss-build-vision-ai` build reads
them from its resolved service ports and hands them in; a human operator
supplies them directly. A build can remap ports (RT-CV's is
`${RTVI_CV_HOST_PORT:-9000}`, not a fixed `9000`), so never hard-code. Address
each endpoint by the vantage that uses it:

- **Calls you make from the deploy host** — VIOS/VST, NvStreamer, RT-CV, RT-Embed,
  RT-VLM, Alert Bridge — use `http://localhost:<resolved-port>`, the same loopback the readiness
  checks use. On a build that fronts RT-VLM at `/rtvi-vlm` for the tagging leg, the
  origin URL (`http://<origin>/rtvi-vlm`) is also valid — use it to reach the tagging
  leg from a remote host; loopback (`http://localhost:${RTVI_VLM_PORT:-8018}`) remains
  the lower-latency choice from the deploy host. Otherwise RT-VLM has no ingress
  route, so loopback is its only form — identical to every other consumer here.
- **URLs a service consumes** — the synthetic RTSP from NvStreamer and the VIOS
  live proxy handed to the consumers — are host-reachable `$HOST_IP` URLs produced
  by those services: **read** them, don't build them. VIOS assigns the RTSP port
  from its pool (`30554–30564`) at registration, so only VIOS knows the exact value
(Step 1). The upload equivalent is the uploaded stream's VIOS clip URL —
likewise consumer-reachable via `vst-ingress` / `$HOST_IP:<vst-ingress-port>`,
  **not** loopback.

## Step 1 — register the source, then resolve its consumer URL

Register one source in VIOS, then **read back** the URL the consumers will use —
never construct it. The origin decides the register call and the URL's shape, but
the "read it from VIOS" rule is common to both. On a webhook-covered build the
caller never hands this URL to a consumer — VIOS dispatches it — but the
read-back remains the registration's readiness gate, and the fallback appendix
needs it.

**Stored file (upload).** Store the bytes (synchronous) and pin the timeline:

```bash
PUT http://localhost:<vios-port>/vst/api/v1/storage/file/<filename>?timestamp=2025-01-01T00:00:00.000Z
#   octet-stream, Content-Length required → {sensorId, streamId, filePath}
```

`timestamp` anchors the storage timeline (see the date rule). A bare upload stores
bytes only — no detections or embeddings. All three consumers (RT-CV, RT-Embed,
RT-VLM) take the timeline-resolved VIOS clip URL: `GET /vst/api/v1/storage/<streamId>/timelines` for
`{startTime, endTime}`, then the self-contained
`/vst/api/v1/storage/file/<streamId>?startTime=<t0>&endTime=<t1>&container=mp4&disableAudio=true` **HTTP** URL
(binary-direct — the same clip the `/url` envelope wraps, minus its upstream
double-`http://` bug; see `integrate-vios-service.md`). RT-Embed and RT-VLM accept
`http`/`https`/`file` but gate `file://` behind `FILE_URL_ALLOWED_DIRS` (unset by
default); RT-CV's `camera_url` accepts `http(s)://`, `rtsp://`, and `file://`, but a
`file://` resolves *inside the RT-CV container*, where the stored bytes are not mounted.
So the VIOS **HTTP** URL is the reliable path for every consumer — RT-CV consumes it as
`camera_url`; RT-VLM takes it directly — no pre-upload — or registers it via
`/v1/files`. There is no live proxy on this path.

**Live (RTSP).** Register the RTSP URL — an external camera as-is, or a local file
served as synthetic RTSP by NvStreamer (stage it into
`${VSS_DATA_DIR}/videos/<build>/`, then **read** the generated URL from
`GET http://localhost:<nvstreamer-port>/api/v1/sensor/<stem>/streams` `.[0].url`,
never construct it):

```bash
POST http://localhost:<vios-port>/vst/api/v1/sensor/add
#   {"sensorUrl":"rtsp://…","name":"…","username":"","password":""} → {sensorId}
#   the field is `sensorUrl`, not `url`.
```

Then resolve the **live proxy** the consumers must target, and treat this read as a
**readiness gate**. VST re-publishes the stream under a stable, VIOS-managed,
`sensorId`-keyed RTSP handle (e.g. `rtsp://<vios-host>:<pool-port>/live/<sensorId>`),
published asynchronously. SDRC-routed builds (warehouse, LVS, Helm) gate the
republish on the SDRC Envoy, so the per-sensor
`GET /sensor/<sensorId>/streams` → `.url` can stay empty well past the brief
post-add race. Docker search and alerts run VIOS in **direct** mode; the same
aggregate/proxy read + backoff still applies for the brief post-add race.
Resolve the handle from the endpoint that carries it — the aggregate
`GET /sensor/streams` (match your `sensorId`) or `GET /proxy/streams` (`.proxyUrl`
for that `sensorId`) — and **retry with backoff until it is non-empty** before
passing it to the consumers. Read it, never construct it; do **not** fall back to
the raw NvStreamer/camera URL — that bypasses the VIOS-managed handle and is the
failure mode the runtime evals guard against. This applies to the live/RTSP origin
only; the uploaded-file origin above has no live proxy.

## Step 2 — resolve webhook coverage

The fan-out policy is the notification config mounted at the fixed container
path `/home/vst/vst_release/configs/notification_config.json` in **both** VIOS
containers (`vss-vios-sensor` and `vss-vios-streamprocessing`). VIOS reads only
that fixed path, once, at process start — there is no reload API.
`VST_NOTIFICATION_CONFIG_PATH` exists only at the Compose mount layer and is
interpolated away in a resolved build; never read it off a deployed build.
Resolve the effective config in this precedence order, stopping at the first
that answers:

1. **Caller-supplied** — the normal case. A `vss-build-vision-ai` caller knows
   which notification config its `override.env` selected and passes the path in.
2. **Read the bind-mount source out of the running deployment** — the
   authoritative, interpolation-proof source:

   ```bash
   # Host path of the effective config, from each VIOS container's mount spec.
   for c in vss-vios-sensor vss-vios-streamprocessing; do
     docker inspect "$c" --format '{{range .Mounts}}{{if eq .Destination "/home/vst/vst_release/configs/notification_config.json"}}{{.Source}}{{"\n"}}{{end}}{{end}}'
   done
   ```

   Both containers must print the **same** host path. A mismatch is a
   split-brain fan-out — stop and report it.
3. **`VST_NOTIFICATION_CONFIG_PATH`** — a convenience only, valid when reading a
   profile's `overrides.env` directly.

**Coverage is per-event and per-origin, not per-deployment.** The expected
receiver set for this source is precisely:

```
webhooks.enabled == true
AND items[] where enabled == true AND camera_status_change == "camera_streaming"
  AND within those, request[] entries whose camera_type[] is empty
      or contains this source's origin ("file" for uploads, "rtsp" for live)
```

A global `webhooks.enabled: false` overrides every per-item `enabled: true`; a
request filtered out by `camera_type` never fires. Do not count a disabled
placeholder item — some shipped configs carry one (`dummy-camera-add`,
`enabled: false`, empty `request[]`). No matching entries → the fallback
appendix.

**Then intersect with the build's deployed services.** An inherited config can
name a receiver for a service this build did not deploy. Verify only the
intersection — receivers with a running service behind them — and give each one
post-check in Step 3. An orphaned receiver's `giving up` line is not a delivery
fault, but it is a **build defect**, not a tolerable state: the mounted config's
receiver set should equal the deployed consumer set (`vss-build-vision-ai`
`references/services/vios.md`). Name the orphans in the report so the build gets
a matching config — with no introspection API, a genuine failure to a deployed
service is log-identical to this noise.

## Step 3 — verify the fan-out

One bounded, read-only post-check per expected `request[]` entry:

| Expected `request[]` entry | Post-check (read-only) |
|---|---|
| RT-CV `stream/add` | `GET ${RTVI_CV_URL}/api/v1/stream/get-stream-info` lists the `sensorId`; detections land in `mdx-raw-*` |
| RT-Embed `stream/add` | `GET ${RTVI_EMBED_URL}/v1/streams/get-stream-info`; embeddings in `mdx-embed` → `mdx-embed-filtered-*` |
| RT-VLM `stream/add` (tagging) | `GET ${RTVI_VLM_URL}/v1/streams/get-stream-info`; captions on `mdx-vlm-captions`, which Logstash writes to `default_<streamId>` (there is no `mdx-vlm-tags` or `mdx-vlm-captions-*` index). Incidents (`mdx-vlm-incidents-*`) appear only when the model emits a trigger token — not a valid liveness check for tagging |
| Alert Bridge `always-on` | `GET ${ALERT_BRIDGE_URL}/api/v1/realtime/incidents` scoped to the camera; the bridge log shows the always-on POST (see `vss-manage-alerts` `always-on.md`). A repeat delivery returns `STREAM_ADD_ALREADY_ACTIVE` — success, not failure |
| ES `_delete_by_query` (teardown) | Counts drain to zero **for upload-anchored data only** (`*-2025-01-01`). Live-stream documents in `*-<today>` are not cleaned by these webhooks — report the residue as a known limitation, not a failure |

### Timing — compute the bound, do not quote one

Delivery is asynchronous per receiver; final failure is logged and dropped — no
dead-letter, no redelivery. Poll each post-check until it passes or the bound
for **that entry** elapses:

```
worst_case ~ max_attempts x timeout_ms
           + sum(i = 0 .. max_attempts-2) backoff_ms[min(i, len-1)]
```

`backoff_ms` clamps to its last element when shorter than `max_attempts`. Every
shipped config uses `backoff_ms: [1000, 5000, 15000]`; computed from the shipped
values:

| `request[]` entry | `max_attempts` | `timeout_ms` | Worst case |
|---|---|---|---|
| search → RT-CV | 3 | 60000 | ~3 min 6 s |
| search → RT-Embed | 3 | 600000 | ~30 min 6 s |
| search → RT-VLM | 3 | 5000 | ~21 s |
| alerts → RT-CV / Alert Bridge | 60 | 10000 | ~24 min 21 s |
| search → ES cleanup | 1 (no retry) | 5000 | 5 s, then dropped |

The slowest shipped entry is RT-Embed at ~30 min — an impatient bound abandons
exactly the receiver that needed waiting. The ES cleanup entries get a single 5 s
attempt, so run their post-check promptly and treat any residue as likely
permanent rather than pending.

### Failure taxonomy

| Observed | Meaning | Action |
|---|---|---|
| Post-check passes within the computed bound | Fan-out delivered | Done |
| Consumer reports the stream already present, or `409 DuplicateStreamId` / `STREAM_ADD_ALREADY_ACTIVE` | Already provisioned — expected on RTSP reconnect, since VIOS re-fires `camera_streaming` with no dedup | **Treat as success.** Never as a failure or a reason to re-add |
| Bound elapses; VIOS logs show `Webhook … giving up` (or repeated `retrying in`) for a receiver **in the intersection** | Delivery genuinely failed and was dropped | Report the consumer and the log line. Remediation: fix the receiver, then re-register the source (delete the VIOS sensor, re-add). Do **not** paper over it with a direct consumer call |
| Same, for a receiver whose service the build did not deploy | Orphaned receiver in an inherited config — not a delivery fault | Report it as the config/build mismatch it is (Step 2), not as a fan-out failure |
| Bound elapses; **no** webhook attempt in the VIOS logs | The `camera_type` filter excluded the receiver (Step 2), or `camera_streaming` was skipped because no URL could be generated (upload path) | Distinguish the two from the logs, then report |

There is no webhook introspection API: every "VIOS logs show…" check above is a
container-log grep, run against **both** VIOS containers (`vss-vios-sensor`,
`vss-vios-streamprocessing`) for these literal patterns:

- `Webhook .*delivered`
- `Webhook .*giving up`
- `Webhook .*retrying in`
- `skipped, camera_type '`

## The shared-id rule

Every consumer must key on the **one VST `sensorId` returned at Step 1**, VOD and
RTSP alike. The id-vs-name distinction is load-bearing on both paths: embed
records key on the `sensorId`, behavior and raw records on the **source name**,
and both the search read path and the `camera_remove` ES cleanup resolve each
accordingly. An id/name mismatch deletes nothing, silently.

**On the webhook path** VIOS threads both: `{{event.camera_id}}` is the
`sensorId`, `event.camera_name` the `name` given at registration. Nothing to
thread — register under the canonical source name, then confirm each consumer
keyed on that `sensorId`.

**In the fallback** the caller threads them: `sensorId` verbatim as RT-CV
`camera_id`, RT-Embed `id`, and the `x-stream-id` header on **both** calls
(`x-stream-id` pins the stream to a worker under an SDR-fronted RTVI
deployment), with RT-CV `camera_name` the source name. Never let a consumer mint
its own id — given no `id`, RT-Embed generates an asset UUID and its embeddings
land under a `sensor.id` no name- or sensor-scoped query can reach.

## The upload-date rule

`creation_time`/`timestamp` is **upload-only**:
- VIOS anchors an untimed upload at `2025-01-01T00:00:00.000Z` itself; pin
  `timestamp=2025-01-01T00:00:00.000Z` to state that anchor rather than inherit
  it silently;
- on the webhook path the anchor rides the event as
  `metadata.file_start_time`, which RT-Embed and RT-VLM map to `creation_time`,
  so records land in the `*-2025-01-01` indices the `camera_remove` cleanup
  targets. VIOS emits that field on every upload, pinned or not — the consumers'
  `created_at` fallback never applies here, and omitting the pin does **not**
  move the records off the anchor;
- in the fallback the caller passes the anchor as `creation_time` on **every**
  upload consumer itself — RT-CV
  `/stream/add` (`value.creation_time` + `headers.created_at`), RT-Embed
  `generate_video_embeddings`, and RT-VLM `/v1/files` (dense-captioning builds).
  Omit it and frame times are file-relative (epoch 0), landing records in a
  `…-1970-01-01` index a date-pinned read can't see — RT-Embed and RT-VLM are
  **not** exempt;
- **RTSP carries none** on any path — chunks are stamped from live NTP time.

## Idempotency and teardown

Add = register once, then verify (Step 3). Duplicates are benign and expected on
RTSP reconnect: RT-VLM answers `409 DuplicateStreamId`, the Alert Bridge
`STREAM_ADD_ALREADY_ACTIVE` — treat both as success.

Teardown on a webhook-covered build = delete the VIOS sensor; the
`camera_remove` webhooks perform consumer removal and the upload-anchor ES
cleanup. Verify absence scoped: consumer stream state drains
(`get-stream-info` no longer lists the `sensorId`); ES counts drain for the
`*-2025-01-01` anchor indices only — live-dated index residue is a known
limitation, not a failure. On a webhook-less build, reverse the fan-out by hand
per the fallback appendix **before** deleting the sensor.

## Fallback — direct REST on builds without webhook coverage

Use this only when Step 2 finds no webhook coverage (shared default config, or
every receiver filtered out by `camera_type`). Call each resolved consumer with
the Step-1 source URL (storage URL for an upload, live proxy for a live
stream); RT-VLM on an upload takes its `file_id` instead.

**Contract note:** this path's RT-Embed VOD call is the synchronous
`POST /v1/generate_video_embeddings` — no register, no teardown. The webhook
path uses `POST /v1/stream/add` with `user_defined_metadata` auto-start. These
are different contracts — do not mix them.

```bash
# RT-CV — detection/tracking (sensor envelope). Header x-stream-id: <sensorId>.
POST http://localhost:<rtvi-cv-port>/api/v1/stream/add
#   {"key":"sensor","value":{"camera_id":"<sensorId>","camera_name":"<source-name>",
#     "camera_url":"<vios-url>","change":"camera_add","creation_time":"<upload-anchor>"},
#    "headers":{"source":"vst","created_at":"<upload-anchor>"}}
#   camera_id = the Step-1 sensorId, camera_name = the source name (see the shared-id rule).
#   Upload: creation_time + created_at REQUIRED; RTSP: omit both (see upload-date rule).

# RT-Embed — chunk embeddings (only when embeddings/retrieval is resolved).
# The two origins drive the endpoint differently:
#   Upload (VOD): one synchronous call, no register, no teardown —
POST http://localhost:<rt-embed-port>/v1/generate_video_embeddings   # header x-stream-id: <sensorId>; body {"url":"<vios-storage-url>","id":"<sensorId>","model":"<resolved>","creation_time":"<upload-anchor>","chunk_duration":<n>}
#     accept: application/json; blocks until the bounded clip finishes
#     (read usage.total_chunks_processed). id = the Step-1 sensorId (see the shared-id
#     rule). creation_time REQUIRED (see upload-date rule). No stream:true, no /v1/streams/add.
#   Live (RTSP): register, then fire-and-verify — do NOT hold the SSE open —
POST http://localhost:<rt-embed-port>/v1/streams/add                 # register the live proxy (header x-stream-id: <sensorId>; body {"streams":[{"id":"<sensorId>","liveStreamUrl":"<vios-url>"}]} — carry the id here so streams/add keys on the sensorId instead of minting its own UUID)
POST http://localhost:<rt-embed-port>/v1/generate_video_embeddings   # header x-stream-id: <sensorId>; body {"id":"<sensorId>","model":"<resolved>","stream":true,"chunk_duration":<n>}
#     open, confirm HTTP 200, then CLOSE. The server keeps embedding and publishing
#     to Kafka after you disconnect (closing the SSE does not stop it, and Kafka
#     publishing does not require it held open); stop with
#     DELETE /v1/generate_video_embeddings/{id} then DELETE /v1/streams/delete/{id}.

# RT-VLM — dense captioning (source-agnostic; captions → mdx-vlm-captions, yes/no
# incidents → mdx-vlm-incidents). SKIP when the build has an Alert Bridge — see
# the carve-out below.
POST http://localhost:<rt-vlm-port>/v1/files          # uploaded (VOD): multipart form only — -F purpose=vision -F media_type=video -F url=<vios-storage-url> -F creation_time=<upload-anchor> → file_id (a JSON body 422s; url is a form field; omit creation_time and captions land in a …-1970-01-01 index)
POST http://localhost:<rt-vlm-port>/v1/streams/add    # RTSP: feed the VIOS live-proxy URL
#   then POST .../v1/generate_captions with the returned file_id/stream_id

# RT-VLM tagging — the search-indexing leg (provision when the build resolves search).
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
#   DELETE tears down every subscriber on the shared stream, including the Alert Bridge — see
#   `vss-build-vision-ai` `references/services/rt-vlm.md` § Lifecycle independence of the tagging leg)
```

**Alert-Bridge carve-out — dense captioning only.** On a build carrying an Alert
Bridge, do **not** drive the dense-captioning leg from here: the rule is created
via `vss-manage-alerts` `POST :9080/api/v1/realtime` (per-sensor
`live_stream_url`) and the bridge wires `rtvi-vlm` itself. A direct
`/v1/streams/add` bypasses rule persistence and is a failure even if the stream
goes live. The tagging leg is unaffected — it coexists with the bridge, subject
to the matching decode signature.

Exact payloads and field lists live in the operating contracts — do not restate
them here: RT-CV `vss-deploy-detection-tracking-2d` `api-reference.md`; RT-Embed
`vss-deploy-video-embedding` `rest-api.md`; VIOS/NvStreamer this skill's
[`integrate-vios-service.md`](integrate-vios-service.md) +
[`nvstreamer-api-reference.md`](nvstreamer-api-reference.md); RT-VLM
`vss-deploy-dense-captioning` `integrate-rt-vlm.md`. The controlled tag-prompt
contract and the `default_<streamId>` indexing path are specified in
[`../../../docs/designs/vlm-tagging-search.md`](../../../docs/designs/vlm-tagging-search.md).

**Teardown without webhooks.** Reverse the fan-out — RT-CV
`change:"camera_remove"`, RT-Embed
`DELETE /v1/streams/delete/{id}` (or `DELETE /v1/generate_video_embeddings/{id}`),
RT-VLM `DELETE /v1/streams/delete/{id}` (a tagging leg first needs its
`request_id`, per the block above) — then delete the VIOS sensor. Deleting only
VIOS leaves the consumers provisioned.

## Sources

- This skill: [`integrate-vios-service.md`](integrate-vios-service.md), [`nvstreamer-api-reference.md`](nvstreamer-api-reference.md), [`api-reference.md`](api-reference.md) (`§ RTSP Proxy` for `/proxy/streams`; `/sensor/streams`)
- `skills/deployment/vss-deploy-detection-tracking-2d/references/api-reference.md`
- `skills/deployment/vss-deploy-video-embedding/references/rest-api.md`
- `skills/deployment/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
- `skills/operations/vss-manage-alerts/references/always-on.md` (Alert Bridge always-on contract)
- Endpoint contract (read-vs-write resolution split): `skills/vss-build-vision-ai/references/deployment_resolution.md`
- Shipped notification configs: `deploy/docker/developer-profiles/dev-profile-{search,alerts}/vios/configs/` and the joint `deploy/docker/services/vios/configs/notification_config_search_alerts_2d_{cv,vlm}.json`
