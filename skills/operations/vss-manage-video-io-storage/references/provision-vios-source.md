# Provision and fan-out (headless, direct REST)

Registering a source brings **no** perception with it: a bare VIOS add stores or
publishes the media, but nothing detects, embeds, or captions it until the
source is fanned into the consumers a build deployed. A stock full-stack profile
does this through the agent in one transaction. When
**no agent tier is present** — e.g. a `vss-build-vision-ai` headless
`_builds/<name>` deployment — the operator or a runtime eval must do it by
**direct REST**. This file is that recipe: register one source, then fan it out to
only the resolved consumers.

## Headless-only — first line of defense

This recipe is the **agent-free** path. If an agent tier is present, **STOP** —
provisioning is agent-owned and a direct-REST fan-out double-provisions. The
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

## One mechanism, off one VIOS sensor

Register the source once with VIOS, then call each **resolved** consumer directly
with the resulting source URL. Do **not** also register it as an SDRC workload:
SDRC auto-fan-in plus a direct `/stream/add` would provision the same stream
twice. Keep SDRC for VST recording/playback only. (RT-Embed is never SDRC-routed,
so a direct call is the one mechanism that covers every consumer uniformly.)

The consumer set follows the build's resolved capabilities, not any profile. One
invariant: **VIOS is the mandatory base** — every build registers exactly one
source — and each consumer below is provisioned **only if the build resolved it**.
Read the owner contracts for the authoritative keys:

| Consumer | Provision when the build resolves… | Owner |
| --- | --- | --- |
| **VIOS source** (`/sensor/add` or `/storage/file`) | always — the mandatory base | this skill (`api-reference.md`, `nvstreamer-api-reference.md`) |
| **RT-CV** `/stream/add` | detection / tracking / attribute perception (also the base a CV-verification alert runs off) | `vss-deploy-detection-tracking-2d` |
| **RT-Embed** `generate_video_embeddings` | chunk/video embeddings (retrieval / search) | `vss-deploy-video-embedding` |
| **RT-VLM** dense captioning (`/v1/files` or `/v1/streams/add` + `/v1/generate_captions`, free-form prompt) | real-time VLM **dense captioning** (captions/incidents), and only when the build has **no Alert Bridge** — see the bridge carve-out below | `vss-deploy-dense-captioning` |
| **RT-VLM** tagging (`/v1/generate_captions` with a controlled JSON-tag prompt) | BM25 tag-search indexing for **search** builds (captions → `mdx-vlm-captions` → Logstash → `default_<streamId>`); **independent of the Alert Bridge** — see the tagging leg below | `vss-deploy-dense-captioning` + `vss-search-archive` |

Provision **any subset** off the single VIOS source — RT-CV for detection,
RT-Embed for embeddings, RT-VLM for dense captioning and/or tagging — in any
combination, including **RT-VLM alone** (VIOS + RT-VLM, no CV or embeddings). No
consumer is a prerequisite for another. **Behavior-Analytics is never provisioned
here**: it consumes Kafka (`mdx-raw` / `mdx-embed`), so enabling its workers is
config, not a source call. The fan-out set is exactly `{RT-CV, RT-Embed, RT-VLM
(dense captioning and/or tagging)}`.

Both feed origins — upload and live — reach every consumer; the origin only
changes which source URL a consumer gets (Step 1) and, for RT-VLM, which endpoint
you call (Step 2). RT-VLM has **two independent direct fan-out legs** here:

- **Dense captioning** — a free-form `generate_captions` prompt for
  captions/incidents. **SKIP this leg when the build carries an Alert Bridge**
  (alerts `2d_vlm` realtime or `2d_cv` verification): RT-VLM is then bridge-driven,
  the rule is created via `vss-manage-alerts` `POST :9080/api/v1/realtime`
  (per-sensor `live_stream_url`), and the bridge wires `rtvi-vlm` itself. Do **not**
  also call `rtvi-vlm` directly for dense captioning on those builds — a direct
  `/v1/streams/add` bypasses rule persistence and is a failure even if the stream
  goes live. That orchestration is owned by `vss-manage-alerts`, not this recipe.
- **Tagging** — a *controlled* `generate_captions` request (JSON-tag prompt,
  `response_format={"type":"json_object"}`, `temperature=0`, 5s chunks) that feeds
  BM25 tag search. This leg is **independent of the Alert Bridge**: it owns search
  indexing, not alert verification, and coexists with a bridge. Provision it when
  the build resolves **search** (RT-CV + RT-Embed + RT-VLM). The same single
  `rtvi-vlm` deployment serves both legs; tagging is just a different prompt on
  the same `POST /v1/generate_captions` endpoint, so it needs no second service.

## Endpoints are injected by the caller — never hard-code ports

This recipe takes the consumer endpoints as **inputs** — `VST_API_BASE`,
`RTVI_CV_URL`, `RTVI_EMBED_URL`, `RTVI_VLM_URL` — resolved and passed in by the
caller; it reads no build manifest itself. A `vss-build-vision-ai` build reads
them from its resolved service ports and hands them in; a human operator
supplies them directly. A build can remap ports (RT-CV's is
`${RTVI_CV_HOST_PORT:-9000}`, not a fixed `9000`), so never hard-code. Address
each endpoint by the vantage that uses it:

- **Calls you make from the deploy host** — VIOS/VST, NvStreamer, RT-CV, RT-Embed,
  RT-VLM — use `http://localhost:<resolved-port>`, the same loopback the readiness
  checks use. On a build that fronts RT-VLM at `/rtvi-vlm` for the tagging leg, the
  origin URL (`http://<origin>/rtvi-vlm`) is also valid — use it to drive the tagging
  leg from a remote host; loopback (`http://localhost:${RTVI_VLM_PORT:-8018}`) remains
  the lower-latency choice from the deploy host. On a non-tagging build, RT-VLM has no
  ingress route, so loopback is its only form — identical to every other consumer here.
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
the "read it from VIOS" rule is common to both.

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
`camera_url` (Step 2); RT-VLM takes it directly — no pre-upload — or registers it via
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
published asynchronously. Search and alerts builds run VIOS in **SDRC mode**, where
the republish is gated on the SDRC Envoy, so the per-sensor
`GET /sensor/<sensorId>/streams` → `.url` can stay empty well past the brief
post-add race. **Docker search** runs VIOS in **direct** mode; the same
aggregate/proxy read + backoff still applies for the brief post-add race.
Resolve the handle from the endpoint that carries it — the aggregate
`GET /sensor/streams` (match your `sensorId`) or `GET /proxy/streams` (`.proxyUrl`
for that `sensorId`) — and **retry with backoff until it is non-empty** before
passing it to the consumers. Read it, never construct it; do **not** fall back to
the raw NvStreamer/camera URL — that bypasses the VIOS-managed handle and is the
failure mode the runtime evals guard against. This applies to the live/RTSP origin
only; the uploaded-file origin above has no live proxy.

## Step 2 — fan out to the consumers (direct REST)

Call each resolved consumer with the Step-1 source URL (storage URL for an upload,
live proxy for a live stream); RT-VLM on an upload takes its `file_id` instead:

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
# incidents → mdx-vlm-incidents). SKIP when the build has an Alert Bridge: RT-VLM is
# then bridge-driven via vss-manage-alerts POST :9080/api/v1/realtime, not from here.
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

Exact payloads and field lists live in the operating contracts — do not restate
them here: RT-CV `vss-deploy-detection-tracking-2d` `api-reference.md`; RT-Embed
`vss-deploy-video-embedding` `rest-api.md`; VIOS/NvStreamer this skill's
[`integrate-vios-service.md`](integrate-vios-service.md) +
[`nvstreamer-api-reference.md`](nvstreamer-api-reference.md); RT-VLM
`vss-deploy-dense-captioning` `integrate-rt-vlm.md`. The controlled tag-prompt
contract and the `default_<streamId>` indexing path are specified in
[`../../../docs/designs/vlm-tagging-search.md`](../../../docs/designs/vlm-tagging-search.md).

## The shared-id rule

Every consumer must key on the **one VST `sensorId` returned at Step 1**, VOD and RTSP
alike. Thread that `sensorId`
verbatim as RT-CV `camera_id`, RT-Embed `id`, and the `x-stream-id` header on **both**
calls (`x-stream-id` pins the stream to a worker under an SDR-fronted RTVI deployment).
Set RT-CV `camera_name` to the canonical **source name** (distinct from the id): embed
keys on the `sensorId`, behavior and raw on the name, and the search read path resolves
each accordingly. Never let a consumer mint its own id — given no `id`,
RT-Embed generates its own asset UUID and its embeddings land under a `sensor.id` no name-
or sensor-scoped query can reach.

## The upload-date rule

`creation_time`/`timestamp` is **upload-only**:

- VIOS anchors an untimed upload at `2025-01-01T00:00:00.000Z`; pin
  `timestamp=2025-01-01T00:00:00.000Z` to state that anchor;
- pass that anchor as `creation_time` on **every** upload consumer — RT-CV
  `/stream/add` (`value.creation_time` + `headers.created_at`), RT-Embed
  `generate_video_embeddings`, and RT-VLM `/v1/files` (dense-captioning builds).
  Omit it and frame times are file-relative (epoch 0), landing records in a
  `…-1970-01-01` index a date-pinned read can't see — RT-Embed and RT-VLM are
  **not** exempt;
- **RTSP carries none** on any — chunks are stamped from live NTP time.

## Idempotency and teardown

Make add idempotent: list first (`GET /sensor/list`, RT-CV
`GET /api/v1/stream/get-stream-info`, RT-Embed `GET /v1/streams/get-stream-info`,
RT-VLM `GET /v1/streams/get-stream-info`) and skip or delete-then-add on a match — a
duplicate RT-VLM `streams/add` returns `409 DuplicateStreamId`. To remove a source,
reverse the fan-out — RT-CV `change:"camera_remove"`, RT-Embed
`DELETE /v1/streams/delete/{id}` (or `DELETE /v1/generate_video_embeddings/{id}`),
RT-VLM `DELETE /v1/streams/delete/{id}` — then delete the VIOS sensor. Deleting only
VIOS leaves the consumers provisioned.

## Sources

- This skill: [`integrate-vios-service.md`](integrate-vios-service.md), [`nvstreamer-api-reference.md`](nvstreamer-api-reference.md), [`api-reference.md`](api-reference.md) (`§ RTSP Proxy` for `/proxy/streams`; `/sensor/streams`)
- `skills/deployment/vss-deploy-detection-tracking-2d/references/api-reference.md`
- `skills/deployment/vss-deploy-video-embedding/references/rest-api.md`
- `skills/deployment/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
- Endpoint contract (read-vs-write resolution split): `skills/vss-build-vision-ai/references/deployment_resolution.md`
