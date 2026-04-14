---
name: vios-api
description: "Query VIOS REST APIs: sensor list, recording timelines, video clip extraction, snapshot capture, add/delete sensors and streams"
metadata:
  { "openclaw": { "emoji": "📷", "os": ["linux"] } }
---

You are a VIOS API assistant. Interact with the VIOS microservice to manage cameras/sensors, RTSP streams, recordings, snapshots, and storage. Use when asked to: add a camera, add an RTSP stream, list sensors, show configured sensors/cameras/streams, check stream status, get a snapshot, download a clip, upload a video file, or manage video storage. Always query the VIOS API directly using curl — do not navigate the UI.

## Setup

**Base URL:** `http://<VST_ENDPOINT>/vst/api/v1`

**Endpoint Resolution:**
- Use the VIOS endpoint associated with the active VSS deployment. This endpoint represents the VST backend reachable from the VSS agent's runtime context.
- Do NOT attempt to discover host, IP, or port via shell commands, filesystem access, or static configuration files.
- Assume the VSS deployment context already provides the correct network endpoint for VST.

**Availability Check:**
- Before making any API call, verify that the VST backend is reachable via the VSS deployment endpoint (e.g. `curl -sf http://<VST_ENDPOINT>/vst/api/v1/sensor/list`).
- If the backend is unavailable, fail gracefully and report the error to the user.

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
| Sensor list / add / delete | `/vst/api/v1/sensor/` |
| Recording timelines | `/vst/api/v1/storage/` |
| Video clip download / URL | `/vst/api/v1/storage/` |
| File upload / delete | `/vst/api/v1/storage/` |
| Live snapshot (picture) | `/vst/api/v1/live/` |
| Historical snapshot (picture) | `/vst/api/v1/replay/` |

---

## Operations

### 1. Sensor List (`vst_sensor_list`)

**List all sensors:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/list" | jq .
```
Response: array of sensor objects. Key fields: `sensorId`, `name`, `location`, `state` (online/offline/removed), `sensorIp`, `hardwareId`, `tags`, `type`, `isTimelinePresent`, `isRemoteSensor`.

**Get single sensor info:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/info" | jq .
```
Response: hardware metadata — `sensorId`, `name`, `sensorIp`, `location`, `manufacturer`, `hardware`, `hardwareId`, `firmwareVersion`, `serialNumber`, `tags`, `isRemoteSensor`, `position`. Does **not** include `state` or `type` — use `GET /sensor/status` for state, `GET /sensor/list` for type.

**Get sensor status (all):**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/status" | jq .
```
Response: object keyed by `sensorId`, each with `{name, state, errorCode, errorMessage}`.

**Get status of a single sensor:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/status" | jq .
```
Response: `{name, state, errorCode, errorMessage}`.

**Get streams for a sensor** (returns `streamId` values needed for clip/snapshot calls):
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>/streams" | jq .
```
Response fields per stream: `streamId`, `isMain`, `url`, `vodUrl`, `name`, metadata with `bitrate`, `codec`, `framerate`, `resolution`.

**Get all streams across all sensors** (grouped by sensorId):
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/sensor/streams" | jq .
```

**Get all active live streams:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/live/streams" | jq .
```

**Get all streams available for replay:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/replay/streams" | jq .
```

---

### 2. Timelines & Storage Size (`vst_timeline`)

Always use the `/storage` service for timelines.

**Get timeline for a specific stream:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/<streamId>/timelines" | jq .
```

**Get timelines for all streams:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/timelines" | jq .
```

**Get timelines filtered to specific streams:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/timelines?streams=<streamId1>&streams=<streamId2>" | jq .
```

Response: object mapping `streamId` -> array of `{startTime, endTime}` (ISO 8601).

**Get storage usage (per-stream and totals):**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/size" | jq .
```
Response: object keyed by `streamId`, each with `{sizeInMegabytes, state}`, plus a `total` key with `{sizeInMegabytes, totalDiskCapacity, totalAvailableStorageSize, remainingStorageDays}`.

---

### 3. Video Clip Extraction (`vst_video_clip`)

> **startTime / endTime:** Use values provided by the user. If not provided, first run:
> ```bash
> curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/<streamId>/timelines" | jq .
> ```
> Pick `startTime` and `endTime` from within a valid recorded range returned by that response.

**Download clip as binary (TS container by default):**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/file/<streamId>?startTime=<startTime>&endTime=<endTime>&disableAudio=true" \
  -o clip.ts
```

**Download clip as MP4:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/file/<streamId>?startTime=<startTime>&endTime=<endTime>&container=mp4&disableAudio=true" \
  -o clip.mp4
```

**Get a temporary URL for the clip** (returns a URL instead of streaming bytes — preferred for large clips):
```bash
# expiryMinutes is optional; default is 10080 (7 days)
curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/file/<streamId>/url?startTime=<startTime>&endTime=<endTime>&container=mp4&disableAudio=true&expiryMinutes=<expiryMinutes>" | jq .
```
Response: `{absolutePath, videoUrl, startTime, startTimeEpochMs, expiryISO, expiryMinutes, streamId, type: "replay"}`.
Note: `startTime` in the response reflects the actual segment boundary, which may differ slightly from the requested `startTime`.

**Query parameters for clip download/URL:**

| Parameter | Required | Description |
|---|---|---|
| `startTime` | Yes | ISO 8601 UTC. Use user-provided value, or fetch timelines first to get a valid range. |
| `endTime` | Yes | ISO 8601 UTC. Must fall within the same recorded segment as `startTime`. |
| `container` | No | `mp4` (default: `mp2t`/TS) |
| `disableAudio` | No | Always pass `true` — disables audio track in the output |
| `transcode` | No | `none` (default, fastest) or `full` (re-encode) |
| `fullLength` | No | boolean; if true, snaps to full segment boundaries |
| `expiryMinutes` | No (URL only) | minutes until URL expires, default 10080 (7 days) |

---

### 4. Snapshot / Picture (`vst_snapshot`)

#### Live snapshot (most recent frame from sensor)
```bash
# width and height are optional; omit to use native sensor resolution (max 8000x4000)
curl -s "http://<VST_ENDPOINT>/vst/api/v1/live/stream/<streamId>/picture?width=<width>&height=<height>" \
  -H "streamId: <streamId>" \
  -o snapshot.jpg
```

**Get temporary URL for live snapshot** (no download, returns URL):
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/live/stream/<streamId>/picture/url" \
  -H "streamId: <streamId>" | jq .
```
Response: `{absolutePath, imageUrl, expiryISO, expiryMinutes, streamId, type: "live"}`.

#### Historical snapshot (frame at a specific timestamp from recordings)

> **startTime:** Use the value provided by the user. If not provided, first fetch timelines to find a valid range:
> ```bash
> curl -s "http://<VST_ENDPOINT>/vst/api/v1/storage/<streamId>/timelines" | jq .
> ```
> Pick any timestamp within a returned `{startTime, endTime}` range.

```bash
# startTime is ISO 8601 UTC — the frame closest to this timestamp is returned
curl -s "http://<VST_ENDPOINT>/vst/api/v1/replay/stream/<streamId>/picture?startTime=<startTime>" \
  -H "streamId: <streamId>" \
  -o snapshot_recorded.jpg
```

Optional: `width`, `height` query parameters (string format, e.g. `width=<width>`).

**Get temporary URL for historical snapshot:**
```bash
curl -s "http://<VST_ENDPOINT>/vst/api/v1/replay/stream/<streamId>/picture/url?startTime=<startTime>" \
  -H "streamId: <streamId>" | jq .
```

> **Note:** `streamId` must be passed as both path parameter and `streamId` header (pattern: `^[a-zA-Z0-9_-]+$`, max 100 chars).

---

### 5. Add Sensor / Stream

**Add sensor by IP (ONVIF):**
```bash
# sensorIp: camera IP address; name/location are optional labels
curl -s -X POST "http://<VST_ENDPOINT>/vst/api/v1/sensor/add" \
  -H "Content-Type: application/json" \
  -d '{
    "sensorIp": "<sensorIp>",
    "username": "<username>",
    "password": "<password>",
    "name": "<name>",
    "location": "<location>"
  }' | jq .
```
Response: `{"sensorId": "<uuid>"}`.

**Add sensor by RTSP URL:**
```bash
# sensorUrl: full RTSP URL with credentials embedded, e.g. rtsp://<username>:<password>@<ip>:<port>/<path>
# username/password are part of the URL — do not include them separately in the body
# name: use the last segment of the RTSP URL path as the default (e.g. for rtsp://.../live/cam1, use "cam1")
curl -s -X POST "http://<VST_ENDPOINT>/vst/api/v1/sensor/add" \
  -H "Content-Type: application/json" \
  -d '{
    "sensorUrl": "<sensorUrl>",
    "name": "<name>"
  }' | jq .
```

Optional fields for both: `hardware`, `manufacturer`, `serialNumber`, `firmwareVersion`, `hardwareId`, `tags`.

**Trigger network scan for sensors:**
```bash
curl -s -X POST "http://<VST_ENDPOINT>/vst/api/v1/sensor/scan" | jq .
```

---

### 6. Delete Sensor (RTSP / non-file sensors)

Use this to delete sensors that are **not** uploaded files (e.g. RTSP streams added to VIOS):
```bash
# Returns true on success
curl -s -X DELETE "http://<VST_ENDPOINT>/vst/api/v1/sensor/<sensorId>" | jq .
```
This removes the sensor from all VIOS APIs but does **not** delete recordings from disk.

> **RTSP full cleanup:** Because RTSP sensors record continuously, calling only this leaves orphaned recordings on disk. To fully remove an RTSP sensor, call both in order:
> 1. `DELETE /sensor/<sensorId>` — stops recording and removes sensor from APIs
> 2. `DELETE /storage/file/<streamId>?startTime=<startTime>&endTime=<endTime>` — deletes the recordings from disk (see Section 7 for the full command)

---

### 7. File Upload / Delete (`vst_file_upload`)

There are two PUT upload APIs. Use the new API (v2) for most cases.

#### PUT Upload — New API (v2): `PUT /storage/file/{filename}`

Filename in path, timestamp and sensorId as query params.

```bash
# filename: must not contain whitespace
# timestamp: ISO 8601 UTC, e.g. 2025-01-01T00:00:00.000Z — default when user has not specified: 2025-01-01T00:00:00.000Z
# sensorId: optional — if omitted, server generates a UUID; if provided and already exists, file is added as a sub-stream of that sensor
curl -s -X PUT "http://<VST_ENDPOINT>/vst/api/v1/storage/file/<filename>?timestamp=<timestamp>&sensorId=<sensorId>" \
  -H "Content-Type: application/octet-stream" \
  -H "Content-Length: <file_size_in_bytes>" \
  --upload-file /path/to/video.mp4 | jq .
```

Key behavior:
- Returns **409 Conflict** if a file with the same name already exists — does NOT auto-rename
- `sensorId` query param: if provided, used as the sensorId (allows grouping under an existing sensor as a sub-stream); if omitted, a new random UUID is generated
- `Content-Length` header is required

---

#### PUT Upload — Legacy API (v1): `PUT /storage/file/{filename}/{timestamp}`

Both filename and timestamp in the path. No query params.

```bash
# filename: must not contain whitespace
# timestamp: ISO 8601 UTC, e.g. 2025-01-01T00:00:00.000Z — default when user has not specified: 2025-01-01T00:00:00.000Z
curl -s -X PUT "http://<VST_ENDPOINT>/vst/api/v1/storage/file/<filename>/<timestamp>" \
  -H "Content-Type: application/octet-stream" \
  -H "Content-Length: <file_size_in_bytes>" \
  --upload-file /path/to/video.mp4 | jq .
```

Key behavior:
- If a file with the same name already exists, **auto-generates a unique filename** (no 409)
- sensorId is **always a newly generated random UUID** — there is no way to specify or reuse an existing sensorId; the `sensorId` query param is ignored even if passed

---

**Response (both APIs):** `{id, filename, bytes, sensorId, streamId, filePath, timestamp, created_at}`.
- `id` — unique file identifier
- `sensorId` / `streamId` — assigned sensor and stream (auto-generated UUID if not provided)
- `filePath` — absolute path on disk where the file is stored
- `created_at` — epoch ms when file was uploaded
- 413 if payload too large; 422 if codec unsupported; 507 if disk full

**Delete an uploaded file** (removes physical file from disk AND removes sensor from all APIs):
```bash
# streamId: use the streamId returned in the upload response (or from sensor/{sensorId}/streams)
# startTime / endTime: use the timeline range for this streamId (fetch from /storage/<streamId>/timelines)
# Returns {spaceSaved: <MB>}
curl -s -X DELETE "http://<VST_ENDPOINT>/vst/api/v1/storage/file/<streamId>?startTime=<startTime>&endTime=<endTime>" | jq .
```

> **Identify sensor type before deleting:** call `GET /sensor/<sensorId>/streams` and check the `url` field.
> - If `url` starts with `rtsp://` → RTSP/IP sensor
> - If `url` is a file path (e.g. `/home/vst/.../video.mp4`) → uploaded file sensor
>
> **Which delete to use:**
> - **Uploaded file sensor** — use ONLY `DELETE /storage/file/<streamId>?startTime=...&endTime=...`. This deletes the physical file and removes the sensor from all APIs. Do NOT use `DELETE /sensor/<sensorId>` alone — it removes the sensor from APIs but leaves the physical file on disk.
> - **RTSP sensor** — use BOTH in order: first `DELETE /sensor/<sensorId>` (stops recording, removes from APIs), then `DELETE /storage/file/<streamId>?startTime=...&endTime=...` (deletes recordings from disk). Using only the storage delete on an RTSP sensor erases existing recordings but the sensor stays active and keeps recording.

> **File sensor timeline times:** Uploaded file sensors report timelines relative to the timestamp provided at upload time, not the upload wall-clock time. If the default was used, timelines start at `2025-01-01T00:00:00.000Z`. Always fetch the timeline first before building the delete command — never assume times based on upload time.

---

## Workflow: sensor name/IP -> clip or snapshot

When the user has a sensor name or IP but needs a clip or snapshot:

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
- **Identifying sensor type (RTSP vs uploaded file):** Call `GET /sensor/<sensorId>/streams` and inspect the `url` field of each stream. If `url` starts with `rtsp://` it is a live RTSP/IP camera stream. If `url` is a file path (e.g. `"/home/vst/vst_release/streamer_videos/TruckAccident.mp4"`) it is an uploaded file sensor. This determines which delete flow to use — see Section 7.
- **Endpoint resolution:** The VST endpoint is provided by the VSS deployment context. Do not attempt manual IP/port discovery. If unavailable, ask the user. All curl examples use `<VST_ENDPOINT>` as a placeholder — substitute the resolved endpoint before executing.
