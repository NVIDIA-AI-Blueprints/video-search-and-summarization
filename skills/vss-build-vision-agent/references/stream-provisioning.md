# Stream provisioning

Deployment brings the services **up** but registers **no** source. Provisioning is
the runtime step that registers one video source and fans it into the consumers a
build deployed. A stock full-stack profile does this through the agent; a build
from this skill is **headless** (no agent tier), so the operator or a runtime eval
must do it by **direct REST**. This file is that recipe — agent-free by
construction; do not route it through the agent's ingest tools.

## One mechanism, off one VIOS sensor

Register the source once with VIOS, then call each **resolved** consumer directly
with the resulting source URL. Do **not** also register it as an SDRC workload:
SDRC auto-fan-in plus a direct `/stream/add` would provision the same stream
twice. Keep SDRC for VST recording/playback only. (RT-Embed is never SDRC-routed,
so a direct call is the one mechanism that covers every consumer uniformly.)

The consumer set follows the build's resolved capabilities, not any profile. One
invariant: **VIOS is the mandatory base** — every build registers exactly one
source — and each consumer below is provisioned **only if the build resolved it**.
Read the owner contracts under `services/` for the authoritative keys:

| Consumer | Provision when the build resolves… | Owner |
|---|---|---|
| **VIOS source** (`/sensor/add` or `/storage/file`) | always — the mandatory base | `services/vios.md` |
| **RT-CV** `/stream/add` | detection / tracking / attribute perception (also the base a CV-verification alert runs off) | `services/rt-cv.md` |
| **RT-Embed** `generate_video_embeddings` | chunk/video embeddings (retrieval / search) | `services/rt-embed.md` |
| **RT-VLM** (`/v1/files` or `/v1/streams/add`) | real-time VLM captioning / alerts | `services/rt-vlm.md` |

Provision **any subset** off the single VIOS source — RT-CV for detection,
RT-Embed for embeddings, RT-VLM for real-time alerts — in any combination,
including **RT-VLM alone** (VIOS + RT-VLM, no CV or embeddings). No consumer is a
prerequisite for another. **Behavior-Analytics is never provisioned here**: it
consumes Kafka (`mdx-raw` / `mdx-embed`), so enabling its workers is config, not a
source call. The fan-out set is exactly `{RT-CV, RT-Embed, RT-VLM}`.

Both feed origins — upload and live — reach every consumer; the origin only
changes which source URL a consumer gets (Step 1) and, for RT-VLM, which endpoint
you call (Step 2). The one genuinely live-stream-bound path is the Alert Bridge
realtime-rule orchestration (`POST :9080/api/v1/realtime`, per-sensor
`live_stream_url`), owned by the alerts operating skill, not this recipe.

## Resolve endpoints from the build — never hard-code ports

Take every port from the deployed build's `resolved.yml` (`ports:` mappings); a
build can remap them (RT-CV's is `${RTVI_CV_HOST_PORT:-9000}`, not a fixed `9000`).
Address each endpoint by the vantage that uses it:

- **Calls you make from the deploy host** — VIOS/VST, NvStreamer, RT-CV, RT-Embed,
  RT-VLM — use `http://localhost:<resolved-port>`, the same loopback the readiness
  checks use.
- **URLs a service consumes** — the synthetic RTSP from NvStreamer and the VIOS
  live proxy handed to the consumers — are host-reachable `$HOST_IP` URLs produced
  by those services: **read** them, don't build them. VIOS assigns the RTSP port
  from its pool (`30554–30564`) at registration, so only VIOS knows the exact value
  (Step 1).

This mirrors the split the read path already uses (`query.md`: `localhost` for
backend calls, `$HOST_IP` for the URLs that must outlive the call).

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
bytes only — no detections or embeddings. URL-taking consumers use the VIOS storage
URL for `<sensorId>`; RT-VLM instead takes the file via `/v1/files` (Step 2), so it
needs no URL. There is no live proxy on this path.

**Live (RTSP).** Register the RTSP URL — an external camera as-is, or a local file
served as synthetic RTSP by NvStreamer (stage it into
`${VSS_DATA_DIR}/videos/<build>/`, then **read** the generated URL from
`GET http://localhost:<nvstreamer-port>/vst/api/v1/sensor/<stem>/streams` `.[0].url`,
never construct it):

```bash
POST http://localhost:<vios-port>/vst/api/v1/sensor/add
#   {"sensorUrl":"rtsp://…","name":"…","username":"","password":""} → {sensorId}
#   the field is `sensorUrl`, not `url`.
```

Then resolve the **live proxy** the consumers must target, and treat this read as a
**readiness gate**. VST re-publishes the stream under a stable, VIOS-managed,
`sensorId`-keyed RTSP handle (e.g. `rtsp://$HOST_IP:30554/live/<sensorId>`),
published asynchronously. Search and alerts builds run VIOS in **SDRC mode**, where
the republish is gated on the SDRC Envoy, so the per-sensor
`GET /sensor/<sensorId>/streams` → `.url` can stay empty well past the brief
post-add race. Resolve the handle from the endpoint that carries it — the aggregate
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
# RT-CV — detection/tracking (sensor envelope):
POST http://localhost:<rtvi-cv-port>/api/v1/stream/add
#   {"key":"sensor","value":{"camera_id":"<id>","camera_url":"<vios-url>",
#     "change":"camera_add","creation_time":"<upload-anchor>"},
#    "headers":{"source":"vst","created_at":"<upload-anchor>"}}
#   Upload: creation_time + created_at REQUIRED; RTSP: omit both (see upload-date rule).

# RT-Embed — chunk embeddings (only when embeddings/retrieval is resolved).
# The two origins drive the endpoint differently:
#   Upload (VOD): one synchronous call, no register, no teardown —
POST http://localhost:<rt-embed-port>/v1/generate_video_embeddings   # {"url":"<vios-storage-url>","id":"<id>","model":"<resolved>","creation_time":"<upload-anchor>","chunk_duration":<n>}
#     accept: application/json; blocks until the bounded clip finishes
#     (read usage.total_chunks_processed). creation_time REQUIRED (see upload-date
#     rule). No stream:true, no /v1/streams/add.
#   Live (RTSP): register, then fire-and-verify — do NOT hold the SSE open —
POST http://localhost:<rt-embed-port>/v1/streams/add                 # register the live proxy
POST http://localhost:<rt-embed-port>/v1/generate_video_embeddings   # {"id":"<id>","model":"<resolved>","stream":true,"chunk_duration":<n>}
#     open, confirm HTTP 200, then CLOSE. The server keeps embedding and publishing
#     to Kafka after you disconnect (closing the SSE does not stop it, and Kafka
#     publishing does not require it held open); stop with
#     DELETE /v1/generate_video_embeddings/{id} then DELETE /v1/streams/delete/{id}.

# RT-VLM — real-time alerts (source-agnostic; incidents land on mdx-vlm-incidents):
POST http://localhost:<rt-vlm-port>/v1/files          # uploaded (VOD): multipart form only — -F purpose=vision -F media_type=video -F url=<vios-storage-url> → file_id (a JSON body 422s; url is a form field)
POST http://localhost:<rt-vlm-port>/v1/streams/add    # RTSP: feed the VIOS live-proxy URL
#   then POST .../v1/generate_captions with the returned file_id/stream_id
```

Exact payloads and field lists live in the operating contracts — do not restate
them here: RT-CV `vss-deploy-detection-tracking-2d` `api-reference.md`; RT-Embed
`vss-deploy-video-embedding` `rest-api.md`; VIOS/NvStreamer
`vss-manage-video-io-storage` `integrate-vios-service.md` +
`nvstreamer-api-reference.md`; RT-VLM `vss-deploy-dense-captioning`
`integrate-rt-vlm.md`.

## The upload-date rule

`creation_time`/`timestamp` is **upload-only**; upload mirrors the agent's
`video_ingest`, RTSP mirrors `rtsp_ingest`:
- VIOS anchors an untimed upload at `2025-01-01T00:00:00.000Z`; pin
  `timestamp=2025-01-01T00:00:00.000Z` to state that anchor;
- pass that anchor as `creation_time` on **both** upload consumers — RT-CV
  `/stream/add` (`value.creation_time` + `headers.created_at`) and RT-Embed
  `generate_video_embeddings`. Omit it and frame times are file-relative (epoch 0),
  landing records in a `…-1970-01-01` index a date-pinned read can't see — RT-Embed
  is **not** exempt;
- **RTSP carries none** on either — chunks are stamped from live NTP time.

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

- `skills/vss-manage-video-io-storage/references/integrate-vios-service.md`, `references/nvstreamer-api-reference.md`, `references/api-reference.md` (`§ RTSP Proxy` for `/proxy/streams`; `/sensor/streams`)
- `skills/vss-deploy-detection-tracking-2d/references/api-reference.md`
- `skills/vss-deploy-video-embedding/references/rest-api.md`
- `skills/vss-deploy-dense-captioning/references/integrate-rt-vlm.md`
