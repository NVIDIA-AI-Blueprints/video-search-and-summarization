---
name: "amc-calibrate-rtsp-streams"
description: "Calibrate a new dataset from live RTSP streams via the AutoMagicCalib REST API. The MS records streams through VIOS, ingests the recorded clips, then runs the normal AMC calibration. Use when the user says 'calibrate RTSP streams', 'calibrate from live cameras', 'run AMC on RTSP', or provides RTSP URLs. Requires a running AMC microservice AND a reachable VIOS instance."
owner: "nvidia-metropolis-team"
service: "auto-magic-calib"
version: "1.0.0"
reviewed: "2026-05-11"
data_classification: public
license: "Apache License 2.0"
metadata:
  author: "NVIDIA Metropolis Team"
  tags: [amc, calibration, rtsp, vios, rest-api, camera, python]
  languages: [bash, python]
  domain: calibration
---

# Skill: Calibrate from RTSP Streams

## Purpose

Run AutoMagicCalib on **live RTSP camera streams** instead of pre-recorded MP4s. The microservice uses VIOS to record a fixed-duration clip from each stream, ingests the clips into the project, and then runs the same calibration pipeline as the file-upload flow.

If your data is already in MP4 files on disk, use [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) instead — it skips the VIOS step entirely.

## Prerequisites

- [ ] AMC microservice **and** UI running (deploy via the [`/deploy`](../deploy/SKILL.md) skill with the `auto-calibration` profile — see [`references/auto-calibration.md`](../deploy/references/auto-calibration.md))
- [ ] **VIOS is running and reachable** — Step 1 probes the default port `30888` first, then falls back to `VIOS_BASE_URL` from the MS container env / compose files. If none work, point the user at the [`vios`](../vios/SKILL.md) skill, else ask them to deploy VIOS.
- [ ] MS knows where VIOS is — `VIOS_BASE_URL` is set in the MS container's environment (auto-wired from `${VST_INTERNAL_URL}` under `bp_wh_*` blueprints; otherwise set explicitly in [`deploy/docker/industry-profiles/warehouse-operations/.env`](../../deploy/docker/industry-profiles/warehouse-operations/.env)). Required at runtime; Step 1 only uses the 30888 probe to detect whether VIOS is up locally.
- [ ] RTSP URLs for each camera, reachable from the VIOS host.
- [ ] Python 3 with `requests` installed.

## Step 1 — Verify VIOS Is Reachable

Confirm VIOS is up before doing anything else. Probe in this order — stop at the first hit:

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)
VIOS_BASE_URL=""

# 1a. Default port probe — standard VIOS one-click deployment listens on 30888.
if curl -sf http://localhost:30888/vst/api/v1/sensor/list >/dev/null 2>&1; then
  # Use HOST_IP from the warehouse-operations env (not `localhost` — the MS container can't reach host `localhost`)
  ENV_FILE="$REPO_ROOT/deploy/docker/industry-profiles/warehouse-operations/.env"
  HOST_IP=$(grep ^HOST_IP "$ENV_FILE" 2>/dev/null | cut -d= -f2)
  VIOS_BASE_URL="http://${HOST_IP:-localhost}:30888"
  echo "VIOS detected at default port: $VIOS_BASE_URL"
fi

# 1b. Fallback — VIOS_BASE_URL from the running MS container env (authoritative if set).
if [ -z "$VIOS_BASE_URL" ]; then
  VIOS_BASE_URL=$(docker exec vss-auto-calibration printenv VIOS_BASE_URL 2>/dev/null)
fi

# 1c. Fallback — grep compose files (useful when MS isn't running yet).
if [ -z "$VIOS_BASE_URL" ]; then
  VIOS_BASE_URL=$(grep -hR '^\s*-\?\s*VIOS_BASE_URL' "$REPO_ROOT/deploy/docker/services/auto-calibration" 2>/dev/null \
    | sed -E 's/.*VIOS_BASE_URL[=:]\s*//' | head -1)
fi

# 1d. Confirm VIOS actually responds at whatever URL we resolved.
if [ -n "$VIOS_BASE_URL" ]; then
  curl -sf "${VIOS_BASE_URL}/vst/api/v1/sensor/list" >/dev/null \
    && echo "VIOS up at $VIOS_BASE_URL" \
    || { echo "VIOS_BASE_URL=$VIOS_BASE_URL is set but not responding"; VIOS_BASE_URL=""; }
fi
```

**If VIOS still can't be reached** (all four checks failed):
1. Look for a VIOS setup skill in this repo: `ls skills/ | grep -i vios`. If one is found (e.g. `vios`), invoke it.
2. Otherwise, ask the user to deploy VIOS and share the base URL via `AskUserQuestion`. Do **not** proceed until `${VIOS_BASE_URL}/vst/api/v1/sensor/list` returns 200.

**If VIOS was detected on 30888 but the MS container env is unset**, the capture endpoint will still return 503 until `VIOS_BASE_URL` is set. The cleanest fix is to deploy alongside a `bp_wh_*` blueprint (which auto-wires it from `${VST_INTERNAL_URL}`). Otherwise set `VIOS_BASE_URL=http://<HOST_IP>:30888` in [`deploy/docker/industry-profiles/warehouse-operations/.env`](../../deploy/docker/industry-profiles/warehouse-operations/.env) and re-run `docker compose --env-file ... up -d` from `deploy/docker/`.

## Step 2 — Collect Inputs From User

### Required
1. **RTSP URLs** — one per camera. Example: `rtsp://<nvstreamer-host>:31556/stream/cam_00.mp4` or `rtsp://user:pass@<cam-ip>:554/stream`.
2. **Camera names** — short label per stream (used as `camera_name` in the capture request), e.g. `cam_00`, `cam_01`, …
3. **Duration seconds** — recording window (minimum `60`). Pick at least 2–3 min of moving objects for decent calibration.
4. **Microservice URL** — e.g. `http://<HOST_IP>:<MS_PORT>`.
5. **Project name** — short descriptive string.

### Anchor-File Pattern (ask config first, then auto-scan its dir for alignment)

Because there's no videos directory to anchor the scan, ask the user for the **calibration settings file** first. Then look in its directory for alignment/layout:

| File | Order | UI fallback |
|---|---|---|
| Calibration settings | Ask the user for a path (e.g. one exported via UI Step 3 Download). When provided, this file replaces the entire UI Step 3 Parameters dialog — every parameter the user wants tuned (rectification, bundle-adjustment, evaluation, detector, …) lives in this file, so users without the UI handy can drive everything from the local file. The skill additionally parses the file for `"detector"` / `"detector_type"` (`"resnet"` or `"transformer"`) and passes that value to the calibrate call, since the detector is a separate API parameter on `/calibrate`, not driven by `/config`. If they don't have a file, skip to UI Step 3. | UI Step 3: Parameters — tune or accept defaults |
| Alignment JSON | If a config path was given, scan the **same directory** for `alignment_data.json`. If exactly one match, use it; zero or multiple → ask the user; no answer → UI fallback. | UI Step 4: Alignment — mark correspondence points |
| Layout PNG | Same scan rule, filename `layout.png`. | UI Step 4: Alignment — upload layout |

### Optional
6. **`sensor_id`** per stream — if VIOS already has the sensor registered, pass the ID to skip re-registration. Leave null and the MS auto-registers via VIOS.
7. **Ground truth zip** (`GT.zip`), **focal lengths**, **VGGT flag** — same options as the video-file flow.

For nvstreamer setup details and sensor pre-registration, see your VIOS deployment docs; from this skill's perspective a valid RTSP URL is all that's needed.

## Step 3 — Create Project

```
POST /v1/create_project
Content-Type: application/x-www-form-urlencoded

project_name=<your_project_name>
```

Save the returned `project_id`.

## Step 4 — Start RTSP Capture

```
POST /v1/rtsp/capture/<project_id>
Content-Type: application/json

{
  "streams": [
    {"rtsp_url": "rtsp://.../cam_00", "camera_name": "cam_00", "sensor_id": null},
    {"rtsp_url": "rtsp://.../cam_01", "camera_name": "cam_01", "sensor_id": null}
  ],
  "duration_seconds": 180,
  "vios_token": null,
  "ssl_verify": false
}
```

Response includes `session_id`. Save it.

**Session lifecycle:**
```
STARTING → RECORDING → COMPLETED → INGESTING → INGESTED
                                ↘ ERROR
RECORDING → CANCELLED (via /stop)
```

## Step 5 — Poll Capture Status, Then Ingest

Poll every ~10 s until session state is `COMPLETED`:

```
GET /v1/rtsp/capture/<project_id>/<session_id>
```

Then ingest the recorded clips as the project's video files:

```
POST /v1/rtsp/capture/<project_id>/<session_id>/ingest
```

When this returns successfully, the project has the clips attached — same state as if you'd called `/v1/upload_video_files/<project_id>` with local MP4s.

**Need to stop early?** `POST /v1/rtsp/capture/<project_id>/<session_id>/stop` — the partial clip can still be ingested.

**Other session endpoints:**
- `GET /v1/rtsp/sessions/<project_id>` — list all sessions for a project.
- `DELETE /v1/rtsp/session/<project_id>/<session_id>` — delete a session record.

## Step 6 — Apply Config, Upload Alignment / Layout (or UI Fallback)

Resolve the config path (asked in Step 2) and use it as the anchor to scan for alignment + layout.

**Calibration settings** (if user provided a path) — posting this file replaces what the user would otherwise tune in UI Step 3 (rectification, bundle-adjustment, evaluation knobs, detector, …):
```
POST /v1/config/<project_id>
Content-Type: application/json

<file contents, posted as-is>
```
After a successful POST, also parse the file for `"detector"` / `"detector_type"` — if it's `"resnet"` or `"transformer"`, use that value for the `/calibrate` call in Step 7 (detector is a separate API parameter, not consumed by `/config`).

**Alignment + layout** (resolved via same-dir scan of the config path, or user-provided, or UI fallback):
```
POST /v1/upload_alignment/<project_id>    alignment_file=<alignment_data.json>
POST /v1/upload_layout/<project_id>       layout_file=<layout.png>
```

**Other optional uploads** (same as the video-file flow):
```
POST /v1/upload_gt_file/<project_id>      gt_file=<GT.zip>                 # optional
POST /v1/upload_focal_length/<project_id> focal_length=<f0>&focal_length=<f1>...  # optional
```

**UI fallback** — for any file the user doesn't have:
- Settings missing → UI Step 3 (Parameters), accept defaults or tune, Save.
- Alignment/layout missing → UI Step 4 (Alignment), mark correspondence points, Save. Verify `projects/project_<id>/manual_adjustment/` contains `alignment_data.json` + `layout.png` before continuing. See [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) Step 5 for the verification shell snippet.

## Step 7 — Verify, Calibrate, Poll, Results

Identical to the video-file flow:

```
POST /v1/verify_project/<project_id>             # expect {"project_state": "READY"}
POST /v1/calibrate/<project_id>                  {"detector_type": "resnet"}
GET  /v1/get_project_info/<project_id>           # poll until state == COMPLETED
GET  /v1/result/<project_id>/evaluation_statistics   # only if GT uploaded
GET  /v1/amc/calibrate/<project_id>/log              # debug log
```

See [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) for state table, timing expectations, and the optional VGGT refinement (Step 10).

---

## Complete Python Script

```python
import os
import time
from pathlib import Path

import requests

# --- Edit these ---
BASE_URL       = "http://<HOST_IP>:<MS_PORT>/v1"
PROJECT_NAME   = "rtsp_calibration_run"

# One entry per camera
STREAMS = [
    {"rtsp_url": "rtsp://<host>:31556/.../cam_00.mp4", "camera_name": "cam_00", "sensor_id": None},
    {"rtsp_url": "rtsp://<host>:31557/.../cam_01.mp4", "camera_name": "cam_01", "sensor_id": None},
]
DURATION_SECONDS = 180                 # >= 60

# Anchor file — ask user for this path. Leave None if they don't have one (→ UI Step 3 fallback).
CONFIG_FILE    = None                                   # e.g. Path("/path/to/settings.json")
                                                        # Full settings override — replaces UI Step 3 (rectification, BA, eval, detector, ...).
                                                        # If the file pins a detector, it's also extracted for the calibrate call below.
# If CONFIG_FILE is set, the skill scans its parent directory for alignment + layout.
# These can also be set explicitly to override the scan.
ALIGNMENT_JSON = None
LAYOUT_PNG     = None
GT_ZIP         = None                                   # optional
FOCAL_LENGTHS  = None                                   # optional: [1269.0, 1099.5]
DETECTOR_TYPE  = "resnet"                               # overridden below if CONFIG_FILE pins it

REPO_ROOT    = Path(os.environ.get("REPO_ROOT", Path.cwd()))
PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", REPO_ROOT / "projects"))

# Auto-scan alignment+layout from the same dir as CONFIG_FILE
def _resolve_local(override, candidate_names, scan_dir, label):
    if override and Path(override).exists():
        return Path(override)
    if scan_dir is None:
        return None
    hits = [scan_dir / n for n in candidate_names if (scan_dir / n).exists()]
    if len(hits) == 1:
        print(f"    auto-detected {label}: {hits[0]}")
        return hits[0]
    if len(hits) > 1:
        print(f"    multiple {label} candidates in {scan_dir}: {hits} — skipping auto-detect")
    return None

_scan_dir = CONFIG_FILE.parent if (CONFIG_FILE and Path(CONFIG_FILE).exists()) else None
ALIGNMENT_JSON = _resolve_local(ALIGNMENT_JSON, ["alignment_data.json"], _scan_dir, "alignment")
LAYOUT_PNG     = _resolve_local(LAYOUT_PNG,     ["layout.png"],           _scan_dir, "layout")

s = requests.Session()

# Step 3 — Create project
r = s.post(f"{BASE_URL}/create_project", data={"project_name": PROJECT_NAME})
r.raise_for_status()
project_id = r.json()["project_id"]
print(f"[3] Created project {project_id}")

# Step 4 — Start RTSP capture
r = s.post(f"{BASE_URL}/rtsp/capture/{project_id}", json={
    "streams": STREAMS,
    "duration_seconds": DURATION_SECONDS,
    "vios_token": None,
    "ssl_verify": False,
})
r.raise_for_status()
session_id = r.json()["session_id"]
print(f"[4] Capture session {session_id} — duration {DURATION_SECONDS}s")

# Step 5a — Poll capture status
print(f"[5] Polling capture status (~{DURATION_SECONDS + 60}s)...")
start = time.time(); last = ""
while time.time() - start < DURATION_SECONDS + 600:
    info = s.get(f"{BASE_URL}/rtsp/capture/{project_id}/{session_id}").json()
    state = info.get("status") or info.get("state")
    elapsed = int(time.time() - start)
    if state != last:
        print(f"    [{elapsed:>4}s] {state}", flush=True); last = state
    if state == "COMPLETED":
        break
    if state in {"ERROR", "CANCELLED"}:
        raise RuntimeError(f"Capture {state}: {info}")
    time.sleep(10)
else:
    raise RuntimeError("Capture poll timed out")

# Step 5b — Ingest clips into project
r = s.post(f"{BASE_URL}/rtsp/capture/{project_id}/{session_id}/ingest")
r.raise_for_status()
print(f"[5] Ingested clips: {r.json()}")

# Step 6 — Config + alignment + layout + optional extras
if CONFIG_FILE and Path(CONFIG_FILE).exists():
    r = s.post(f"{BASE_URL}/config/{project_id}",
               data=Path(CONFIG_FILE).read_bytes(),
               headers={"Content-Type": "application/json"})
    r.raise_for_status()
    print(f"[6] Applied calibration config from {Path(CONFIG_FILE).name} (full settings override; replaces UI Step 3)")
    # Detector lives in the same file but is consumed via the separate /calibrate parameter,
    # so additionally extract it here and use it in Step 7.
    try:
        import json as _json
        _cfg = _json.loads(Path(CONFIG_FILE).read_text())
        _det = _cfg.get("detector") or _cfg.get("detector_type")
        if _det in ("resnet", "transformer"):
            DETECTOR_TYPE = _det
            print(f"    Detector overridden from config: {DETECTOR_TYPE}")
    except Exception:
        pass

if ALIGNMENT_JSON and ALIGNMENT_JSON.exists():
    with open(ALIGNMENT_JSON, "rb") as f:
        s.post(f"{BASE_URL}/upload_alignment/{project_id}",
               files={"alignment_file": (ALIGNMENT_JSON.name, f, "application/json")}).raise_for_status()
if LAYOUT_PNG and LAYOUT_PNG.exists():
    with open(LAYOUT_PNG, "rb") as f:
        s.post(f"{BASE_URL}/upload_layout/{project_id}",
               files={"layout_file": (LAYOUT_PNG.name, f, "image/png")}).raise_for_status()
if GT_ZIP and Path(GT_ZIP).exists():
    with open(GT_ZIP, "rb") as f:
        s.post(f"{BASE_URL}/upload_gt_file/{project_id}",
               files={"gt_file": (Path(GT_ZIP).name, f, "application/zip")}, timeout=120).raise_for_status()
if FOCAL_LENGTHS:
    s.post(f"{BASE_URL}/upload_focal_length/{project_id}",
           data={"focal_length": FOCAL_LENGTHS}).raise_for_status()

# UI fallback for anything not resolved
ui_tasks = []
if not CONFIG_FILE:
    ui_tasks.append("Step 3 (Parameters): tune settings or accept defaults, then Save.")
if not ALIGNMENT_JSON or not LAYOUT_PNG:
    ui_tasks.append("Step 4 (Alignment): upload layout, mark correspondence points, then Save.")
if ui_tasks:
    print(f"\n[6] UI action required for project {project_id}:")
    for t in ui_tasks:
        print(f"    - {t}")
    input("    Press Enter when done...")
    if not ALIGNMENT_JSON or not LAYOUT_PNG:
        manual_dir = PROJECTS_DIR / f"project_{project_id}" / "manual_adjustment"
        assert (manual_dir / "alignment_data.json").exists() and (manual_dir / "layout.png").exists(), (
            f"Alignment files missing under {manual_dir}."
        )

# Step 7 — Verify + calibrate + poll
s.post(f"{BASE_URL}/verify_project/{project_id}").raise_for_status()
s.post(f"{BASE_URL}/calibrate/{project_id}",
       json={"detector_type": DETECTOR_TYPE}).raise_for_status()
print(f"[7] Calibration started (detector={DETECTOR_TYPE})")

start = time.time(); last = ""
while time.time() - start < 3600:
    info = s.get(f"{BASE_URL}/get_project_info/{project_id}").json()
    st = info["project_info"]["project_state"]
    elapsed = int(time.time() - start)
    if st != last:
        print(f"    [{elapsed:>4}s] {st}", flush=True); last = st
    if st == "COMPLETED":
        print(f"[7] Done in {elapsed}s"); break
    if st == "ERROR":
        raise RuntimeError(f"Calibration ERROR — see GET {BASE_URL}/amc/calibrate/{project_id}/log")
    time.sleep(10)

r = s.get(f"{BASE_URL}/result/{project_id}/evaluation_statistics")
if r.status_code == 200:
    for k, v in (r.json().get("statistics") or r.json()).items():
        print(f"    {k}: {v}")

print(f"\nProject: {project_id}")
```

## Success Criteria

- Capture session reaches `COMPLETED`, ingest returns success, and `GET /v1/get_project_info/{id}` shows videos attached.
- `verify_project` returns `READY`, calibration transitions `RUNNING → COMPLETED`.
- Evaluation metrics within thresholds (if GT uploaded); see [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) success-criteria section.

## Troubleshooting

| Issue | Fix |
|---|---|
| VIOS `/vst/api/v1/sensor/list` returns connection refused | VIOS isn't running. Look for the [`vios`](../vios/SKILL.md) skill; if none, ask user to deploy VIOS and retry. |
| Capture endpoint returns 503 / "VIOS not configured" | `VIOS_BASE_URL` not set in MS container env. Either deploy alongside a `bp_wh_*` blueprint (which auto-wires it), or set it in `deploy/docker/industry-profiles/warehouse-operations/.env` and re-run `docker compose --env-file ... up -d` from `deploy/docker/`. |
| Session stuck in `STARTING` | VIOS received the request but sensors aren't online. Check `curl ${VIOS_BASE_URL}/vst/api/v1/sensor/list` — look for `status: "online"`. Wait 20–30 s after any `sensor-ms` restart. |
| Session stuck in `RECORDING` past `duration_seconds` | VIOS timer still running; call `POST /v1/rtsp/capture/<pid>/<sid>/stop` to end early. |
| Ingest fails: `No clip available` | Recording window didn't overlap the VIOS timeline — sensors likely came online after capture started. Wait 30–60 s after bringing sensors online before starting a capture. |
| 400 "empty streams" | Pass at least one entry in `streams`. |
| 400 "duration too short" | Minimum is 60 s. |
| 404 on `/v1/rtsp/capture/{project_id}` | Project doesn't exist — create it first via `/v1/create_project`. |
| `verify_project` not `READY` after ingest | Ingest may have partially failed; re-check `GET /v1/get_project_info/<project_id>` — ensure all expected `video_files` are listed. |
| Calibration troubleshooting | Same as [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) — insufficient tracklets, focal length override, VGGT not ready, etc. |

## For Downstream Skills — MV3DT Export

Same pattern as [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md): this skill returns the `project_id`; the downstream skill fetches the MV3DT archive directly from the microservice.

```
GET /v1/result/{project_id}/mv3dt_result?result_type=amc
# Response: application/zip — mv3dt_output.zip containing transforms.yml
```

For VGGT-refined output (only if VGGT ran to `COMPLETED`):

```
GET /v1/result/{project_id}/mv3dt_result?result_type=vggt
# Response: application/zip — vggt_mv3dt_output.zip
```

Downstream flow: invoke this skill → capture `project_id` from its output → after the skill returns (calibration COMPLETED) → `GET` the MV3DT zip at whichever `result_type` is needed.

## Related Skills

- [`/deploy`](../deploy/SKILL.md) with the `auto-calibration` profile — start MS + UI first ([reference](../deploy/references/auto-calibration.md)).
- [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) — same calibration tail, but from local MP4s instead of RTSP.
- [`amc-calibrate-sample-dataset`](../amc-calibrate-sample-dataset/SKILL.md) — sanity-check the stack end-to-end on the bundled sample (video path).
