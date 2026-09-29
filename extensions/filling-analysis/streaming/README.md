# Live RF-DETR filling worker

This separate service analyzes arriving allowlisted RTSP frames. It imports the
frozen RF-DETR model/height helpers without changing the recorded worker, its
pipeline hash, recording selection, or saved results. It mounts no recorded
media and never loads the completed-recording analysis.

Build with `bash streaming/build.sh` from the extension root. This verifies the
immutable local base image before assigning a dedicated BuildKit alias; it never
changes a runtime tag. Parent deployment supplies GPU access and mounts:

- `/config/streams.json`, read-only; `LIVE_STREAMS_CONFIG` can override it.
- `/state`, writable SQLite session/event persistence.
- Checkpoints read-only. Default `BOTTLE_CHECKPOINT=/models/bottle.pth` and
  `LIQUID_CHECKPOINT=/models/liquid.pth`. Directory mounts are supported through
  these explicit environment settings.

No live input starts at container startup. Startup only loads/validates the model
pair; `POST /live/start` explicitly starts one stream. API port: **8092**.

## Allowlist

```json
{
  "schema_version": 1,
  "streams": {
    "REGISTERED-VIOS-UUID": {
      "name": "Sammy live replay",
      "url": "rtsp://SERVER-RESOLVED-INPUT",
      "width": 1280,
      "height": 720,
      "fps": 24,
      "profile_id": "single-station-rfdetr-demo-v2",
      "reference_level": 0.72803,
      "tolerance": 0.05,
      "calibration_basis": "Reviewed fixed-camera Sammy replay; visible bottle height"
    }
  }
}
```

The receiver uses RTSP/TCP and retains decoded PTS/time base, connection epoch,
and receiver timestamps. UTC is **receiver-estimated**, not verified camera
capture time. Until the VSS API verifies the mapping, evidence remains pending.
The fixed reference and exterior regions are explicit camera calibration, not
event times/counts. Arbitrary cameras require calibration and model validation.

## API

The implementation follows the shared `LIVE-CONTRACT.md`. Endpoints:
`POST /live/start`, `GET /live/status`, `POST /live/stop`,
`GET /live/events`, `GET /live/frame`, and `GET /health`.
The VSS API handles source discovery, public UI transport, deterministic queries,
and verified evidence links. This internal service never constructs VIOS URLs.

A frame response contains the JPEG and geometry from the same immutable inferred
frame. JPEG resizing retains the original aspect ratio and normalized geometry.
Queue dropping is explicit and bounded; it never renumbers PTS or invents missing
observations. A missing model mask yields an unknown height. Closed tracks are
evaluated only from frames already received. Final underfill requires observed
rise, stability and physical departure. Decoder loss is not departure.

Overflow uses a separate calibrated exterior-color signal on **every decoded
frame before the GPU queue**, retaining actual PTS/epoch independently of dropped
model frames. Its persistence test allows at most one actual PTS clock tick for
endpoint quantization; it never joins evidence across decoder gaps or reconnects.
The model's observed stationary interval still gates the completed-cycle verdict.
Qualified bursts preserve actual diagnostic JPEGs and timestamp metadata under
`/state/diagnostics`; the in-memory preview cache is bounded. This is
not an RF-DETR spill classifier. Cycle/event counters come from actual inference,
never the known anomaly schedule. Interrupted/unclosed tracks are incomplete and
do not increment completed-cycle counters. SQLite preserves event IDs for
idempotent retrieval and marks active sessions interrupted on worker restart.

The live code uses a separate pipeline hash/algorithm from recorded measurement
v2. Model calibration/thresholds retain their source-specific weak-supervision
limitations. Actual sustained throughput and source/evidence behavior must be
validated on the real arriving stream; unit tests do not establish those claims.
