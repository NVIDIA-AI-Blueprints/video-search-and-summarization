---
name: vios
description: "Query VIOS REST APIs: sensor list, recording timelines, video clip extraction, snapshot capture, add/delete sensors and streams"
license: Apache-2.0
metadata:
  version: "3.1.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

You are a VIOS API assistant. Interact with the VIOS microservice to manage cameras/sensors, RTSP streams, recordings, snapshots, and storage. Use when asked to: add a camera, add an RTSP stream, list sensors, show configured sensors/cameras/streams, check stream status, get a snapshot, download a clip, upload a video file, or manage video storage. Always query the VIOS API directly using curl — do not navigate the UI.

## Deployment prerequisite

This skill requires any VSS profile that brings up VIOS / VST — **base** (recommended), or any of `lvs` / `search` / `alerts`. Before any request:

1. Probe VIOS:
   ```bash
   curl -sf --max-time 5 "http://${HOST_IP}:30888/vst/api/v1/sensor/list" >/dev/null
   ```

2. **If the probe fails**, ask the user:
   > *"No VSS profile appears to be running on `$HOST_IP` (VIOS unreachable). Shall I deploy `base` using the `/deploy` skill? If you'd like a different profile, say which."*

   - If yes → hand off to `/deploy -p base` (or the profile the user names). Return here once it succeeds.
   - If no → stop. VIOS operations require the VST backend to be up.

   (If your caller has granted explicit pre-authorization to deploy
   autonomously — e.g. the request says "pre-authorized to deploy
   prerequisites", or you are running in a non-interactive evaluation
   harness with that permission — skip the confirmation and invoke
   `/deploy -p base` directly. Prefer `base` unless the request names
   another profile.)

3. If the probe passes, proceed.

---

## Known limitation — leftover containers from prior deploys

The following VIOS API paths can return **HTTP 502 Bad Gateway** or
stale results when the host has leftover containers from an earlier
deploy:

- `GET /vst/api/v1/sensor/list`
- `GET /vst/api/v1/sensor/<sensorId>/streams`

Root cause: the alerts compose profile (`bp_developer_alerts_2d_cv` /
`bp_developer_alerts_2d_vlm`) brings up the `*-smc` set of VST
microservices alongside the `*-dev` set, both with `network_mode: host`
binding the same host ports (30000 for `sensor-ms`, 30888 for
`vst-ingress`). When a subsequent base/lvs/search deploy runs, those
`*-smc` containers can survive past the `/deploy` skill's Step 0
teardown if the teardown grep doesn't catch them — and one
sensor-ms loses the port-bind race, returning 502 to anything that
proxies through `vst-ingress`. See
[issue #151](https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization/issues/151).

The `/deploy` skill's Step 0 teardown grep was extended to cover the
full set (`sensor-ms-*`, `vst-ingress-*`, `centralizedb-*`,
`storage-ms-*`, `sdr-*`, `envoy-*`, `rtspserver-ms-*`, etc.), so
fresh deploys via `/deploy` should not hit this. If you inherit a
host without re-deploying and see 502s, re-run `/deploy` to clean.

Other VIOS paths (`storage/file/*` upload, `replay/stream/*/picture/url`
snapshot, `storage/file/*/url` clip extraction) are unaffected.

---

## Setup

**Base URL:** `http://<VST_ENDPOINT>/vst/api/v1`

**Endpoint Resolution:**
- Use the VIOS endpoint associated with the active VSS deployment. This endpoint represents the VST backend reachable from the VSS agent's runtime context.
- Do NOT attempt to discover host, IP, or port via shell commands, filesystem access, or static configuration files.
- Assume the VSS deployment context already provides the correct network endpoint for VST.

**Availability Check:**
- Before making any API call, verify that the VST backend is reachable via the VSS deployment endpoint:
  ```bash
  curl -sf --connect-timeout 5 http://<VST_ENDPOINT>/vst/api/v1/sensor/version
  ```
- If the backend is unavailable (non-zero exit code or connection error), fail gracefully and report the error to the user.

**Fallback:**
- If endpoint information is not available from context, explicitly ask the user to provide the VST endpoint (host/IP and port).

**Run all curl commands yourself** — never instruct the user to run commands manually.

**Auth:** Optional. Most deployments run without auth. If a `401` is returned, retry with `-H "Authorization: Bearer <token>"` and ask the user for the token.

**Start/end time handling:** Any API that requires `startTime`/`endTime`:
- If the user provides them, use those values directly.
- If the user does not provide them, first fetch the timelines for the relevant stream to find valid recorded ranges, then pick appropriate values from the response before calling the API. Never fabricate timestamps.

**Resolving sensorId / streamId:** If the user has not provided a sensorId or streamId, look it up automatically using one of:
- `GET /sensor/list` — lists all sensors with their `sensorId`
- `GET /sensor/{sensorId}/streams` — lists streams for a specific sensor with their `streamId`
- `GET /sensor/streams` — lists all streams across all sensors
- `GET /live/streams` — lists all active live streams
- `GET /replay/streams` — lists all available replay streams

If a sensor has only one stream, `sensorId` and `streamId` are equal and can be used interchangeably.

---

## Service Map

| Capability | URL prefix |
|---|---|
| Version / health check | `/vst/api/v1/sensor/version` |
| Sensor list / info / status / add / delete | `/vst/api/v1/sensor/` |
| Sensor streams | `/vst/api/v1/sensor/streams`, `/vst/api/v1/sensor/{id}/streams` |
| Network scan | `/vst/api/v1/sensor/scan` |
| Recording timelines | `/vst/api/v1/storage/` |
| Video clip download / URL | `/vst/api/v1/storage/` |
| File upload / delete | `/vst/api/v1/storage/` |
| Live streams / snapshot (picture) | `/vst/api/v1/live/` |
| Replay streams / historical snapshot | `/vst/api/v1/replay/` |

---

## Operations

The full VIOS REST API reference — sensor management, storage, snapshots, clip extraction, WebRTC live/replay, RTSP proxy, recorder, service configuration, and service discovery — lives in [`references/api-reference.md`](references/api-reference.md). Read that file when invoking any operation.

---

## Workflow: sensor name/IP -> clip or snapshot

When the user has a sensor name or IP but needs a clip or snapshot:

0. Verify VST is reachable (see Setup — Availability Check):
   ```bash
   curl -sf --connect-timeout 5 "http://<VST_ENDPOINT>/vst/api/v1/sensor/version"
   ```
1. List sensors to find `sensorId`:
   ```bash
   curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/list" | jq .
   ```
2. Get streams for that sensor to find `streamId` (prefer `isMain: true`):
   ```bash
   curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/streams" | jq .
   ```
3. Check timelines to confirm a recording exists in the requested range:
   ```bash
   curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/<streamId>/timelines" | jq .
   ```
4. Download clip or snapshot using the `streamId`.

---

## Responses

**Success with data:** JSON object or array.

**Success with no data:** `null` — a `null` response means the API call succeeded but there is no data to return (e.g. no schedule configured, scan returned no results). It is not an error.

**Success with boolean:** Some endpoints return `true` on success (e.g. `DELETE /sensor/{sensorId}`).

**Error:** JSON object with `error_code` and `error_message`:
```json
{
  "error_code": "VMSInternalError",
  "error_message": "VMS internal processing error"
}
```

Common codes: `VMSInternalError`, `VMSNotFound`, `VMSInvalidParameter`.

---

## Tips

- **jq:** All JSON responses are piped through `jq .` for readability. Binary responses (clip download, snapshot) are not — they use `-o <file>` instead.
- **Time format:** Always ISO 8601 UTC, e.g. `2026-04-10T10:30:00Z` or `2026-04-10T10:30:00.000Z`.
- **streamId header:** Live/replay/recorder endpoints require `streamId` as BOTH a path parameter AND a request header — include both.
- **Large clips:** Use the `/url` variant to get a temporary download link rather than streaming bytes through curl.
- **Sensor vs stream ID:** `sensorId` identifies a camera; `streamId` identifies a specific video stream from that camera (a sensor can have a main stream and sub-streams).
- **Identifying sensor type (RTSP vs uploaded file):** Call `GET /sensor/<sensorId>/streams` and inspect the `url` field of each stream. If `url` starts with `rtsp://` it is a live RTSP/IP camera stream. If `url` is a file path (e.g. `"/home/vst/vst_release/streamer_videos/TruckAccident.mp4"`) it is an uploaded file sensor. This determines which delete flow to use — see Section 8.
- **Endpoint resolution:** The VST endpoint is provided by the VSS deployment context. Do not attempt manual IP/port discovery. If unavailable, ask the user. All curl examples use `<VST_ENDPOINT>` as a placeholder — substitute the resolved endpoint before executing.
