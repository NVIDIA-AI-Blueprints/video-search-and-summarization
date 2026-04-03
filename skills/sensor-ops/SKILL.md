---
name: sensor-ops
description: Interact with the VIOS (Video IO & Storage) microservice in a running VSS profile — manage cameras/sensors, RTSP streams, recordings, snapshots, and storage. Use when asked to add a camera, add an RTSP stream, list sensors, show configured sensors/cameras/streams, what sources are available, check stream status, start/stop recording, get a snapshot, or manage video storage. Always query the VIOS API directly — do not navigate the UI to answer these questions.
metadata:
  { "openclaw": { "emoji": "📷", "os": ["linux"] } }
---

# Sensor Operations (VIOS)

VIOS manages cameras, live/replay streams, recordings, and storage for all VSS profiles.

> **Run all commands yourself. Do NOT give the user instructions to run them.**

## Base URL

```
http://<HOST_IP>:30888/vst/api/v1
```

Get `HOST_IP` from `TOOLS.md`, or detect live:
```bash
ip route get 1.1.1.1 | awk '{print $7; exit}'
```

Auth is optional (Bearer token) for writes — most deployments run without auth.

---

## Sensor Management

**List all sensors:**
```bash
curl -s http://localhost:30888/vst/api/v1/sensor/list | jq .
```

**Add sensor by RTSP URL:**
```bash
curl -s -X POST http://localhost:30888/vst/api/v1/sensor/add \
  -H "Content-Type: application/json" \
  -d '{
    "sensorUrl": "rtsp://<url>",
    "username": "",
    "password": "",
    "name": "<friendly-name>"
  }' | jq .
```
Response includes `sensorId` — use this for all subsequent operations.

**Add sensor by IP (ONVIF discovery):**
```bash
curl -s -X POST http://localhost:30888/vst/api/v1/sensor/add \
  -H "Content-Type: application/json" \
  -d '{"sensorIp": "<ip>", "username": "<user>", "password": "<pass>", "name": "<name>"}' | jq .
```

**Check sensor status (all):**
```bash
curl -s http://localhost:30888/vst/api/v1/sensor/status | jq .
```

**Check sensor status (specific):**
```bash
curl -s http://localhost:30888/vst/api/v1/sensor/<sensorId>/status | jq .
```

**Get streams for a sensor:**
```bash
curl -s http://localhost:30888/vst/api/v1/sensor/<sensorId>/streams | jq .
```

**Get all sensor streams:**
```bash
curl -s http://localhost:30888/vst/api/v1/sensor/streams | jq .
```

**Remove a sensor:**
```bash
curl -s -X DELETE http://localhost:30888/vst/api/v1/sensor/<sensorId> | jq .
```

**Scan for sensors on the network:**
```bash
curl -s -X POST http://localhost:30888/vst/api/v1/sensor/scan | jq .
```

---

## Live Streams

**List available live streams:**
```bash
curl -s http://localhost:30888/vst/api/v1/live/streams | jq .
```

**Get a snapshot (picture) from a live stream:**
```bash
curl -s "http://localhost:30888/vst/api/v1/live/<streamId>/picture" --output /tmp/snapshot.jpg
# Then send /tmp/snapshot.jpg to the user via Slack/current channel
```

**Get a temporary URL for a live snapshot:**
```bash
curl -s "http://localhost:30888/vst/api/v1/live/<streamId>/picture/url" | jq .
```

---

## Replay / VOD Streams

**List available recorded streams:**
```bash
curl -s http://localhost:30888/vst/api/v1/replay/streams | jq .
```

**Get a snapshot from a recorded stream:**
```bash
curl -s "http://localhost:30888/vst/api/v1/replay/<streamId>/picture" --output /tmp/replay-snapshot.jpg
```

**Get temporary URL for recorded video segment:**
```bash
curl -s "http://localhost:30888/vst/api/v1/storage/file/<streamId>/url" | jq .
```

---

## Recording

**List recorded streams:**
```bash
curl -s http://localhost:30888/vst/api/v1/record/streams | jq .
```

**Start recording a stream:**
```bash
curl -s -X POST http://localhost:30888/vst/api/v1/record/<streamId>/start | jq .
```

**Stop recording:**
```bash
curl -s -X POST http://localhost:30888/vst/api/v1/record/<streamId>/stop | jq .
```

**Get recording timeline for a stream:**
```bash
curl -s http://localhost:30888/vst/api/v1/record/<streamId>/timelines | jq .
```

**Get recording status:**
```bash
curl -s http://localhost:30888/vst/api/v1/record/<streamId>/status | jq .
```

---

## RTSP Proxy

**List proxy streams:**
```bash
curl -s http://localhost:30888/vst/api/v1/proxy/streams | jq .
```

**Add an RTSP proxy stream:**
```bash
curl -s -X POST http://localhost:30888/vst/api/v1/proxy/stream/add \
  -H "Content-Type: application/json" \
  -d '{"rtspUrl": "rtsp://<url>"}' | jq .
```

**Remove an RTSP proxy stream:**
```bash
curl -s -X DELETE http://localhost:30888/vst/api/v1/proxy/stream/<streamId> | jq .
```

---

## Storage

**List all media files:**
```bash
curl -s http://localhost:30888/vst/api/v1/storage/file/list | jq .
```

**List media files for a sensor:**
```bash
curl -s http://localhost:30888/vst/api/v1/storage/file/<sensorId>/list | jq .
```

**Get storage usage info:**
```bash
curl -s http://localhost:30888/vst/api/v1/storage/info | jq .
```

**Get temporary URL for a stored video file (default 7-day expiry):**
```bash
curl -s "http://localhost:30888/vst/api/v1/storage/file/<streamId>/url" | jq .
```

**Upload a video file:**
```bash
curl -s -X PUT http://localhost:30888/vst/api/v1/storage/file/<filename> \
  --upload-file /path/to/video.mp4 | jq .
```

**Delete a media file:**
```bash
curl -s -X DELETE http://localhost:30888/vst/api/v1/storage/file \
  -H "Content-Type: application/json" \
  -d '{"fileId": "<id>"}' | jq .
```

---

## Quick Reference

| Service | Base path |
|---|---|
| Sensor management | `/vst/api/v1/sensor/` |
| Live streams | `/vst/api/v1/live/` |
| Replay / VOD | `/vst/api/v1/replay/` |
| Recording | `/vst/api/v1/record/` |
| RTSP proxy | `/vst/api/v1/proxy/` |
| Storage | `/vst/api/v1/storage/` |

Web UI: `http://<HOST_IP>:30888/vst/`
