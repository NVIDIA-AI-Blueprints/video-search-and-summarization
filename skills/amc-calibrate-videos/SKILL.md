---
name: "amc-calibrate-videos"
description: "Calibrate a new dataset from pre-recorded video files via the AutoMagicCalib REST API. Use when the user has local MP4s and says 'calibrate my videos', 'run AMC on these videos', 'calibrate from video files', or similar. Requires a running AMC microservice. For RTSP/live streams, use amc-calibrate-rtsp-streams instead."
owner: "nvidia-metropolis-team"
service: "auto-magic-calib"
version: "1.0.0"
reviewed: "2026-05-11"
data_classification: public
license: "Apache License 2.0"
metadata:
  author: "NVIDIA Metropolis Team"
  tags: [amc, calibration, rest-api, camera, python]
  languages: [bash, python]
  domain: calibration
---

# Skill: Calibrate from Video Files

## Purpose

Run AutoMagicCalib on user-supplied **pre-recorded video files** (MP4) by uploading them and driving calibration through the microservice REST API. No CLI scripts or Docker bind-mounts required — just a running microservice and your files.

For live RTSP camera streams, use [`amc-calibrate-rtsp-streams`](../amc-calibrate-rtsp-streams/SKILL.md) instead.

## Prerequisites

- [ ] AMC microservice **and** UI running (deploy via the [`/deploy`](../deploy/SKILL.md) skill with the `auto-calibration` profile — see [`references/auto-calibration.md`](../deploy/references/auto-calibration.md))
- [ ] You know the microservice URL (e.g. `http://<HOST_IP>:<MS_PORT>`) and UI URL
- [ ] Video files available locally, named `cam_00.mp4`, `cam_01.mp4`, … (time-synchronized, 1920×1080 recommended)
- [ ] Python 3 with `requests` installed

## What to Ask the User

### Required
1. **Videos directory** — a folder containing `cam_00.mp4`, `cam_01.mp4`, … (time-synchronized, 1920×1080 recommended). The skill reads `cam_*.mp4` from here and uploads them sorted alphabetically.
2. **Microservice URL** — e.g. `http://192.168.1.100:8000`
3. **Project name** — short descriptive string

### Auto-Detected (ask only if not found)

The skill scans the **videos directory** and its **parent directory** for these files and uses them silently if exactly one match is found. Ask the user only if missing or ambiguous; if they don't have the file, fall back to the UI:

| File | Candidate filenames | UI fallback |
|---|---|---|
| Calibration settings | `settings.json`, `config.json`, `calibration_config.json` (UI Step 3 Download produces one of these). When provided, this file replaces the entire UI Step 3 Parameters dialog — every parameter the user wants tuned (rectification, bundle-adjustment, evaluation, detector, …) lives in this file, so users without the UI handy can drive everything from the local file. The skill additionally parses the file for `"detector"` / `"detector_type"` (`"resnet"` or `"transformer"`) and passes that value to the calibrate call, since the detector is a separate API parameter on `/calibrate`, not driven by `/config`. | UI Step 3: Parameters — tune manually or leave defaults |
| Alignment JSON | `alignment_data.json` | UI Step 4: Alignment — mark correspondence points |
| Layout PNG | `layout.png` | UI Step 4: Alignment — upload layout image |

### Optional
4. **Ground truth zip** — `GT.zip` with `_World_Cameras_Camera_XX/` folders (enables evaluation metrics)
5. **Focal lengths** — one per camera, e.g. `1269.0, 1099.5, 1099.5`
6. **Detector type** — `resnet` (default, fast) or `transformer` (slower, better under occlusion)
7. **Run VGGT refinement?** — only if VGGT model is loaded (see the `auto-calibration` profile reference)

See root `README.md` "Custom Dataset" section for input-video guidelines and ground-truth format.

---

## API Call Sequence

### Step 1 — Create Project

```
POST /v1/create_project
Content-Type: application/x-www-form-urlencoded

project_name=<your_project_name>
```

Response: `{"project_id": "<id>", ...}` — save `project_id`.

### Step 2 — Upload Videos (required)

```
POST /v1/upload_video_files/<project_id>
Content-Type: multipart/form-data

files: [("files", ("cam_00.mp4", <bytes>, "video/mp4")),
        ("files", ("cam_01.mp4", <bytes>, "video/mp4")), ...]
```

> **Important**: upload sorted alphabetically — the server assigns camera indices by upload order.

### Step 3 — Resolve Local Files (Auto-Scan, Ask, or UI)

For each of calibration-settings, alignment, and layout, run this resolution:

1. **Auto-scan** `VIDEO_DIR` and `VIDEO_DIR.parent` for the candidate filenames (table above).
2. If **exactly one match**, use it silently and print what was found.
3. If **zero or multiple matches**, ask the user for an explicit path via `AskUserQuestion`. If they don't have the file, mark it for UI fallback.
4. **UI fallback**: tell the user to complete the corresponding UI step; wait for confirmation; for alignment/layout also verify files landed in `projects/project_<id>/manual_adjustment/`.

### Step 4 — Upload Resolved Files

For each file that was resolved locally:

**Calibration settings** (resolved via scan or user path) — posting this file replaces what the user would otherwise tune in UI Step 3 (rectification, bundle-adjustment, evaluation knobs, detector, …):
```
POST /v1/config/<project_id>
Content-Type: application/json

<file contents, posted as-is>
```
Non-2xx is surfaced — do not silently fall back. Skip this call if the user chose the UI-fallback path.

After a successful POST, also parse the file for `"detector"` / `"detector_type"` — if it's `"resnet"` or `"transformer"`, use that value for the `/calibrate` call in Step 7 (detector is a separate API parameter, not consumed by `/config`).

**Alignment JSON**:
```
POST /v1/upload_alignment/<project_id>
alignment_file: ("alignment_data.json", <bytes>, "application/json")
```

**Layout PNG**:
```
POST /v1/upload_layout/<project_id>
layout_file: ("layout.png", <bytes>, "image/png")
```

**Ground truth** (optional, enables evaluation):
```
POST /v1/upload_gt_file/<project_id>
gt_file: ("GT.zip", <bytes>, "application/zip")
```

**Focal lengths** (optional, overrides GeoCalib estimates):
```
POST /v1/upload_focal_length/<project_id>
focal_length=1269.0&focal_length=1099.5&...
```

### Step 5 — UI Fallback (only for files the user doesn't have locally)

If any of settings / alignment / layout was not resolved in Step 3, direct the user to the appropriate UI step:

- **Settings missing** → "Open UI project `<project_id>`, go to **Step 3: Parameters**, tune via the settings dialog (or accept defaults), click Save."
- **Alignment or layout missing** → "Open UI project `<project_id>`, go to **Step 4: Alignment**, upload layout, mark correspondence points, click Save."

Wait for user confirmation. For alignment/layout, verify on disk before continuing:

```bash
# Project state lives under $VSS_APPS_DIR/services/auto-calibration/projects (the path
# bind-mounted into the MS container in deploy/docker/services/auto-calibration/ms/compose.yml).
HOST_PROJECTS="${VSS_APPS_DIR}/services/auto-calibration/projects"

ls "$HOST_PROJECTS/project_<project_id>/manual_adjustment/"
# Expected: alignment_data.json, layout.png
```

### Step 6 — Verify Project

```
POST /v1/verify_project/<project_id>
```

Response: `{"project_state": "READY"}` — must be `READY` before calibrating.

### Step 7 — Start Calibration

```
POST /v1/calibrate/<project_id>
Content-Type: application/json

{"detector_type": "resnet"}
```

### Step 8 — Poll for Completion

```
GET /v1/get_project_info/<project_id>
```

Poll every 10 s. `project_info.project_state`:

| State | Meaning |
|---|---|
| `RUNNING` | Calibration in progress |
| `COMPLETED` | Finished |
| `ERROR` | Failed — check log |

Typical time: **10–60 min** depending on video length and detector.

### Step 9 — Get Results

```
GET /v1/get_project_info/<project_id>                     # project state
GET /v1/result/<project_id>/evaluation_statistics         # only if GT was uploaded
GET /v1/amc/calibrate/<project_id>/log                    # calibration log
```

Evaluation response includes `Average L2 distance(m)` and `Average reprojection error 0(px)`.

### Step 10 — (Optional) VGGT Refinement

Only if `vggt_state == "READY"` in project info (VGGT model must be loaded — see the `auto-calibration` profile reference):

```
POST /v1/vggt/calibrate/<project_id>
GET  /v1/get_project_info/<project_id>                    # poll vggt_state
GET  /v1/vggt_results/<project_id>/evaluation_statistics  # VGGT metrics
```

---

## Complete Python Script

```python
import os
import time
from pathlib import Path

import requests

# --- Edit these ---
BASE_URL       = "http://<HOST_IP>:<MS_PORT>/v1"
PROJECT_NAME   = "my_calibration_run"
VIDEO_DIR      = Path("/path/to/videos")
# Optional explicit overrides (leave as None to trigger auto-scan, then ask-user, then UI fallback)
CONFIG_FILE    = None                                   # e.g. Path("/path/to/settings.json")
                                                        # Full settings override — replaces UI Step 3 (rectification, BA, eval, detector, ...).
                                                        # If the file pins a detector, it's also extracted for the calibrate call below.
ALIGNMENT_JSON = None                                   # e.g. Path("/path/to/alignment_data.json")
LAYOUT_PNG     = None                                   # e.g. Path("/path/to/layout.png")
GT_ZIP         = None                                   # optional: Path("/path/to/GT.zip")
FOCAL_LENGTHS  = None                                   # optional: [1269.0, 1099.5]
DETECTOR_TYPE  = "resnet"                               # "resnet" or "transformer" (overridden if CONFIG_FILE pins it)
RUN_VGGT       = False

# Projects dir on the host (for verifying manual alignment output).
# Bind-mounted into the MS container from $VSS_APPS_DIR/services/auto-calibration/projects
# (see deploy/docker/services/auto-calibration/ms/compose.yml). Override via PROJECTS_DIR if needed.
VSS_APPS_DIR = Path(os.environ.get("VSS_APPS_DIR", Path.cwd()))
PROJECTS_DIR = Path(os.environ.get("PROJECTS_DIR", VSS_APPS_DIR / "services" / "auto-calibration" / "projects"))

VIDEO_FILES = sorted(VIDEO_DIR.glob("cam_*.mp4"))
assert VIDEO_FILES, f"No cam_*.mp4 files under {VIDEO_DIR}"

# --- Auto-scan helper ---
def _resolve_local(override, candidate_names, scan_dirs, label):
    """Return a Path if found locally (via override or scan), else None (→ ask user / UI fallback).
    If the scan finds multiple matches, return None so the caller asks the user explicitly."""
    if override and Path(override).exists():
        return Path(override)
    hits = []
    for d in scan_dirs:
        for name in candidate_names:
            p = d / name
            if p.exists():
                hits.append(p)
    if len(hits) == 1:
        print(f"    auto-detected {label}: {hits[0]}")
        return hits[0]
    if len(hits) > 1:
        print(f"    multiple {label} candidates in {scan_dirs}: {hits} — skipping auto-detect")
    return None

_scan_dirs = [VIDEO_DIR, VIDEO_DIR.parent]
CONFIG_FILE    = _resolve_local(CONFIG_FILE,    ["settings.json", "config.json", "calibration_config.json"], _scan_dirs, "config")
ALIGNMENT_JSON = _resolve_local(ALIGNMENT_JSON, ["alignment_data.json"],                                       _scan_dirs, "alignment")
LAYOUT_PNG     = _resolve_local(LAYOUT_PNG,     ["layout.png"],                                                _scan_dirs, "layout")

s = requests.Session()

# Step 1 — Create project
r = s.post(f"{BASE_URL}/create_project", data={"project_name": PROJECT_NAME})
r.raise_for_status()
project_id = r.json()["project_id"]
print(f"[1] Created project: {project_id}")

# Step 2 — Upload videos (sorted)
files, handles = [], []
for v in VIDEO_FILES:
    f = open(v, "rb"); handles.append(f)
    files.append(("files", (v.name, f, "video/mp4")))
r = s.post(f"{BASE_URL}/upload_video_files/{project_id}", files=files, timeout=300)
for f in handles: f.close()
r.raise_for_status()
print(f"[2] Uploaded {len(VIDEO_FILES)} videos")

# Step 3/4 — Upload resolved files
if CONFIG_FILE and CONFIG_FILE.exists():
    r = s.post(f"{BASE_URL}/config/{project_id}",
               data=CONFIG_FILE.read_bytes(),
               headers={"Content-Type": "application/json"})
    r.raise_for_status()
    print(f"[3] Applied calibration config from {CONFIG_FILE.name} (full settings override; replaces UI Step 3)")
    # Detector lives in the same file but is consumed via the separate /calibrate parameter,
    # so additionally extract it here and use it in Step 7.
    try:
        import json as _json
        _cfg = _json.loads(CONFIG_FILE.read_text())
        _det = _cfg.get("detector") or _cfg.get("detector_type")
        if _det in ("resnet", "transformer"):
            DETECTOR_TYPE = _det
            print(f"    Detector overridden from config: {DETECTOR_TYPE}")
    except Exception:
        pass  # non-JSON config or no detector field — keep DETECTOR_TYPE as-is

if ALIGNMENT_JSON and ALIGNMENT_JSON.exists():
    with open(ALIGNMENT_JSON, "rb") as f:
        s.post(f"{BASE_URL}/upload_alignment/{project_id}",
               files={"alignment_file": (ALIGNMENT_JSON.name, f, "application/json")}).raise_for_status()
    print(f"[3] Uploaded alignment: {ALIGNMENT_JSON.name}")

if LAYOUT_PNG and LAYOUT_PNG.exists():
    with open(LAYOUT_PNG, "rb") as f:
        s.post(f"{BASE_URL}/upload_layout/{project_id}",
               files={"layout_file": (LAYOUT_PNG.name, f, "image/png")}).raise_for_status()
    print(f"[3] Uploaded layout: {LAYOUT_PNG.name}")

if GT_ZIP and GT_ZIP.exists():
    with open(GT_ZIP, "rb") as f:
        s.post(f"{BASE_URL}/upload_gt_file/{project_id}",
               files={"gt_file": (GT_ZIP.name, f, "application/zip")}, timeout=120).raise_for_status()
    print(f"[3] Uploaded GT zip")

if FOCAL_LENGTHS:
    s.post(f"{BASE_URL}/upload_focal_length/{project_id}",
           data={"focal_length": FOCAL_LENGTHS}).raise_for_status()
    print(f"[3] Uploaded focal lengths: {FOCAL_LENGTHS}")

# Step 5 — UI fallback for anything not resolved
ui_tasks = []
if not CONFIG_FILE:
    ui_tasks.append("Step 3 (Parameters): tune settings or accept defaults, then Save.")
if not ALIGNMENT_JSON or not LAYOUT_PNG:
    ui_tasks.append("Step 4 (Alignment): upload layout, mark correspondence points, then Save.")
if ui_tasks:
    print(f"\n[5] UI action required for project {project_id}:")
    for t in ui_tasks:
        print(f"    - {t}")
    input("    Press Enter when done...")
    # Verify alignment files if the UI fallback was used for alignment
    if not ALIGNMENT_JSON or not LAYOUT_PNG:
        manual_dir = PROJECTS_DIR / f"project_{project_id}" / "manual_adjustment"
        assert (manual_dir / "alignment_data.json").exists() and (manual_dir / "layout.png").exists(), (
            f"Alignment files missing under {manual_dir}. Re-check UI Step 4 and click Save."
        )
        print(f"    Alignment files verified at {manual_dir}")

# Step 6 — Verify
r = s.post(f"{BASE_URL}/verify_project/{project_id}")
r.raise_for_status()
state = r.json()["project_state"]
print(f"[6] Project state: {state}")
assert state == "READY", f"Expected READY, got {state}"

# Step 7 — Calibrate
s.post(f"{BASE_URL}/calibrate/{project_id}",
       json={"detector_type": DETECTOR_TYPE}).raise_for_status()
print(f"[7] Calibration started (detector={DETECTOR_TYPE})")

# Step 8 — Poll
print(f"[8] Polling (10–60 min)...")
start = time.time(); last = ""
while time.time() - start < 3600:
    info = s.get(f"{BASE_URL}/get_project_info/{project_id}").json()
    st = info["project_info"]["project_state"]
    elapsed = int(time.time() - start)
    if st != last:
        print(f"    [{elapsed:>4}s] {st}", flush=True); last = st
    if st == "COMPLETED":
        print(f"[8] Done in {elapsed}s"); break
    if st == "ERROR":
        raise RuntimeError(f"ERROR state — see log: GET {BASE_URL}/amc/calibrate/{project_id}/log")
    time.sleep(10)

# Step 9 — Results
print(f"\n[9] Results:")
r = s.get(f"{BASE_URL}/result/{project_id}/evaluation_statistics")
if r.status_code == 200:
    for k, v in (r.json().get("statistics") or r.json()).items():
        print(f"    {k}: {v}")
else:
    print("    No GT provided — skipping evaluation_statistics")

# Step 10 — VGGT (optional)
if RUN_VGGT:
    info = s.get(f"{BASE_URL}/get_project_info/{project_id}").json()
    vggt_state = info.get("project_info", {}).get("vggt_state", "INIT")
    if vggt_state == "READY":
        s.post(f"{BASE_URL}/vggt/calibrate/{project_id}").raise_for_status()
        print("\n[10] VGGT started")
        t0 = time.time()
        while time.time() - t0 < 900:
            vs = s.get(f"{BASE_URL}/get_project_info/{project_id}").json() \
                .get("project_info", {}).get("vggt_state", "INIT")
            if vs == "COMPLETED":
                print("     VGGT done"); break
            if vs == "ERROR":
                raise RuntimeError("VGGT failed")
            time.sleep(10)
    else:
        print(f"\n[10] VGGT not ready (state={vggt_state}) — skipping")

print(f"\nProject: {project_id}")
print(f"Final camera parameters: projects/project_{project_id}/output/multi_view_results/BA_output/results_ba/refined/camInfo_XX.yaml")
```

## Success Criteria

- `project_state == "COMPLETED"` after polling.
- If manual alignment was used: `projects/project_<id>/manual_adjustment/` contains `alignment_data.json` + `layout.png`.
- If GT was uploaded: evaluation returns typical thresholds:
  - `Average L2 distance(m)` < 1.5
  - `Average reprojection error 0(px)` < 5
- No `ERROR` state.

## Key Output Files (on server)

Under `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<project_id>/`:

```
project_<project_id>/
├── manual_adjustment/
│   ├── alignment_data.json
│   └── layout.png
├── output/
│   ├── single_view_results/cam_XX/
│   │   ├── camInfo_hyper_XX.yaml
│   │   └── trajDump_Stream_0_3d.txt
│   └── multi_view_results/BA_output/results_ba/
│       ├── initial/camInfo_XX.yaml
│       └── refined/camInfo_XX.yaml          # ← final calibration
└── calibration.log
```

## Troubleshooting

| Issue | Fix |
|---|---|
| `verify_project` state not `READY` | Confirm videos uploaded and alignment + layout are present (either via API or via UI manual alignment) |
| Manual alignment files missing after UI step | User didn't click Save; also verify `projects/project_<id>/manual_adjustment/` exists |
| Calibration stuck `RUNNING` > 90 min | `GET /v1/amc/calibrate/<id>/log` — usually insufficient tracklets (scene too static). See "Custom Dataset" guidelines in root README. |
| Immediate `ERROR` state | Check video naming: must be `cam_00.mp4`, `cam_01.mp4`, … contiguous |
| Low L2 but high reprojection | Provide explicit `focal_length` override via Step 3 |
| VGGT `INIT`, never `READY` | VGGT model not loaded — see the `auto-calibration` profile reference, Step 2 |
| Upload timeout | Large videos — bump `timeout=300` to e.g. `600` in the script |

## For Downstream Skills — MV3DT Export

Downstream consumers (e.g. a Multi-View 3D Tracking skill owned by another team) can fetch the MV3DT-format calibration output directly from the microservice. This skill intentionally does **not** download the archive itself; it returns the `project_id`, and the downstream skill calls:

```
GET /v1/result/{project_id}/mv3dt_result?result_type=amc
# Response: application/zip — mv3dt_output.zip containing transforms.yml
```

For VGGT-refined output (only available if VGGT ran to `COMPLETED`, see Step 10):

```
GET /v1/result/{project_id}/mv3dt_result?result_type=vggt
# Response: application/zip — vggt_mv3dt_output.zip
```

Downstream skill flow:
1. Call this skill with the user's inputs; capture the printed `project_id`.
2. Poll `GET /v1/get_project_info/{project_id}` until `project_info.project_state == "COMPLETED"` (this skill already does this, so the downstream skill just needs to wait for the skill to return).
3. `GET /v1/result/{project_id}/mv3dt_result?result_type=amc` — save the ZIP locally.
4. If VGGT also ran, optionally fetch `?result_type=vggt` for the refined MV3DT.

## Related Skills

- [`/deploy`](../deploy/SKILL.md) with the `auto-calibration` profile — start MS + UI first ([reference](../deploy/references/auto-calibration.md)).
- [`amc-calibrate-sample-dataset`](../amc-calibrate-sample-dataset/SKILL.md) — verify the stack with the bundled sample before trying your own.
- [`amc-calibrate-rtsp-streams`](../amc-calibrate-rtsp-streams/SKILL.md) — same calibration, but sourcing footage from live RTSP streams via VIOS instead of local MP4s.

Root `README.md` "Custom Dataset" and "Calibration Workflow (UI)" sections document input-video guidelines and the UI-driven alternative to this API flow.
