---
name: vss-generate-video-calibration
description: Calibrate a multi-camera dataset with AutoMagicCalib — from local MP4s, live RTSP streams, or the bundled sample dataset. Also handles deploying the AMC microservice (`vss-auto-calibration`) when the user hasn't set it up yet. Use when the user says "calibrate my videos", "run AMC on these videos", "calibrate from video files", "calibrate RTSP streams", "calibrate from live cameras", "run AMC on RTSP", "test sample dataset", "run sample calibration", "verify AMC install", "launch and test", "launch AMC", "deploy AMC", "deploy auto-calibration", "set up auto-magic-calib", "start AMC microservice", or provides RTSP URLs / local cam_*.mp4 files. Routes to the deploy / videos / rtsp / sample-dataset workflow based on user intent.
license: Apache-2.0
metadata:
  version: "3.2.0"
  github-url: "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization"
  tags: "nvidia blueprint operational"
---

# VSS Generate Video Calibration

Run AutoMagicCalib over one of three input sources and drive the calibration through the microservice REST API. The input-resolution work differs per source; everything from `verify_project` onward is identical and lives in this file. Pick the right input-mode reference and pair it with the [Shared Calibration Tail](#shared-calibration-tail) below.

## Input Routing

Match the user's request to a mode, then load that mode's reference for input collection, mode-specific API calls, and the full Python script.

| User says / has | Mode | Reference |
|---|---|---|
| "launch AMC" / "deploy auto-calibration" / "set up auto-magic-calib" / "start AMC microservice" | `deploy` | [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) |
| "calibrate my videos" / "calibrate from video files" / local `cam_*.mp4` files | `videos` | [`references/videos.md`](references/videos.md) |
| "calibrate RTSP streams" / "calibrate from live cameras" / live RTSP URLs | `rtsp` | [`references/rtsp.md`](references/rtsp.md) |
| "test sample dataset" / "verify AMC install" / "launch and test" | `sample-dataset` | [`references/sample-dataset.md`](references/sample-dataset.md) |

**Disambiguation rule:** if the user is asking to launch / deploy / set up AMC (no calibration verb) → `deploy`. If they provide RTSP URLs → `rtsp`. If they mention local files / a videos directory → `videos`. If they ask to verify install or test the bundled sample → `sample-dataset`. Combined intents (e.g. "launch AMC and calibrate my videos") → walk `deploy` first, then the calibration mode. When ambiguous, ask via `AskUserQuestion`.

## Prerequisites (shared across calibration modes)

- AMC microservice + UI running. If not, walk [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) first.
- Microservice reachable at `http://<HOST_IP>:${VSS_AUTO_CALIBRATION_PORT:-8010}/v1/ready` → `{"code":0,...}`.
- Python 3 with `requests` installed (each input-mode reference includes a self-healing venv fallback for direct runs).

Mode-specific prerequisites (VIOS for `rtsp`, sample zip for `sample-dataset`) live in the respective references.

## Shared Calibration Tail

The verify → calibrate → poll → results sequence is identical regardless of input mode. After the mode-specific reference has uploaded videos / ingested RTSP clips / uploaded the bundled sample, run this tail.

### Step A — Verify Project

```
POST /v1/verify_project/<project_id>
```

Response: `{"project_state": "READY"}` — must be `READY` before calibrating. If not READY, re-check that videos + alignment + layout are present (either via API or via UI manual alignment).

### Step B — Start Calibration

```
POST /v1/calibrate/<project_id>
Content-Type: application/json

{"detector_type": "resnet"}   # or "transformer"
```

`detector_type` is a separate `/calibrate` parameter — **not** consumed by `/v1/config/<id>`. If the user provided a calibration settings file, parse it for `"detector"` / `"detector_type"` and use that value. If no settings file, ask the user via `AskUserQuestion`:

- `resnet` — default, fast.
- `transformer` — slower, better under heavy occlusion.

UI Step 3 (Parameters) does NOT cover detector choice; never assume the user picked one in the UI.

### Step C — Poll for Completion

```
GET /v1/get_project_info/<project_id>
```

Poll every 10 s. `project_info.project_state`:

| State | Meaning |
|---|---|
| `RUNNING` | Calibration in progress |
| `COMPLETED` | Finished |
| `ERROR` | Failed — pull log via `GET /v1/amc/calibrate/<id>/log` |

Typical time: **10–60 min** (your-own videos), **10–30 min** (bundled sample).

### Step D — Results

```
GET /v1/get_project_info/<project_id>                    # project state
GET /v1/result/<project_id>/evaluation_statistics        # only if GT uploaded
GET /v1/amc/calibrate/<project_id>/log                   # calibration log
```

Evaluation response includes `Average L2 distance(m)` and `Average reprojection error 0(px)`.

### Step E — (Optional) VGGT Refinement

Only if `vggt_state == "READY"` in project info (VGGT model must be staged — see [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) Step 2):

```
POST /v1/vggt/calibrate/<project_id>
GET  /v1/get_project_info/<project_id>                    # poll vggt_state
GET  /v1/vggt_results/<project_id>/evaluation_statistics  # VGGT metrics
```

## Settings File + Detector Pattern

Optional across all three modes. When the user provides a JSON settings file (typically exported from UI Step 3 Download), POST it verbatim:

```
POST /v1/config/<project_id>
Content-Type: application/json

<file contents, posted as-is>
```

The file replaces what the user would otherwise tune in UI Step 3 (rectification, bundle-adjustment, evaluation knobs, detector, …). After a successful POST, **also** parse the file for `"detector"` / `"detector_type"` — if it's `"resnet"` or `"transformer"`, use that value for the `/calibrate` call in Step B (detector is a separate API parameter, not consumed by `/config`).

Non-2xx is surfaced — do not silently fall back. Skip this call entirely if the user chose the UI-fallback path.

## UI Fallback Pattern

When alignment / layout files aren't on disk, direct the user to the appropriate AMC UI step:

- **Settings missing** → "Open UI project `<project_id>`, go to **Step 3: Parameters**, tune via the settings dialog (or accept defaults), click Save." **Also**: before the `/calibrate` call, ask the user via `AskUserQuestion` whether to use the `resnet` or `transformer` detector — Step 3 doesn't cover detector choice.
- **Layout missing** → "Open UI project `<project_id>`, go to **Step 2: Video Configuration**, upload `layout.png` only (do NOT re-upload videos — they're already attached via API/RTSP), click Save."
- **Alignment missing** → "Open UI project `<project_id>`, go to **Step 4: Alignment**, either upload `alignment_data.json` or mark correspondence points on the layout, click Save."

Wait for user confirmation. For alignment/layout, verify on disk before continuing:

```bash
# Project state lives under $VSS_APPS_DIR/services/auto-calibration/projects
# (the path bind-mounted into the MS container in
#  deploy/docker/services/auto-calibration/ms/compose.yml).
HOST_PROJECTS="${VSS_APPS_DIR}/services/auto-calibration/projects"

ls "$HOST_PROJECTS/project_<project_id>/manual_adjustment/"
# Expected: alignment_data.json, layout.png
```

## Success Criteria

- `project_state == "COMPLETED"` after polling.
- If manual alignment was used: `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/manual_adjustment/` contains `alignment_data.json` + `layout.png`.
- If GT was uploaded: evaluation returns typical thresholds (`Average L2 distance(m)` < 1.5, `Average reprojection error 0(px)` < 5 for your data; < 10 for the bundled sample).
- No `ERROR` state.

## Key Output Files

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

## Cross-cutting Troubleshooting

Mode-specific issues live in each reference's own troubleshooting table.

| Issue | Fix |
|---|---|
| `verify_project` state not `READY` | Confirm videos uploaded/ingested and alignment + layout are present (either via API or via UI manual alignment). Mode-specific upload steps in the reference. |
| Manual alignment files missing after UI step | User didn't click Save; also verify `${VSS_APPS_DIR}/services/auto-calibration/projects/project_<id>/manual_adjustment/` exists. |
| Calibration stuck `RUNNING` > 90 min | `GET /v1/amc/calibrate/<id>/log` — usually insufficient tracklets (scene too static). See "Custom Dataset" guidelines in root `README.md`. |
| Immediate `ERROR` state | Check video naming: must be `cam_00.mp4`, `cam_01.mp4`, … contiguous (videos mode) / camera_name labels (RTSP mode). |
| Low L2 but high reprojection | Provide explicit `focal_length` override during input upload (see videos / rtsp references). |
| VGGT `INIT`, never `READY` | VGGT model not loaded — see [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) Step 2. |
| Upload timeout | Large videos — bump `timeout=300` to e.g. `600` in the per-mode Python script. |
| Port scan finds no backend | Backend not running — walk [`references/deploy-auto-calibration-service.md`](references/deploy-auto-calibration-service.md) first. |

## For Downstream Skills — MV3DT Export

Downstream consumers (e.g. a Multi-View 3D Tracking skill owned by another team) fetch the MV3DT-format calibration output directly from the microservice. This skill returns the `project_id`; the downstream skill calls:

```
GET /v1/result/{project_id}/mv3dt_result?result_type=amc
# Response: application/zip — mv3dt_output.zip containing transforms.yml
```

For VGGT-refined output (only available if VGGT ran to `COMPLETED`, see Step E):

```
GET /v1/result/{project_id}/mv3dt_result?result_type=vggt
# Response: application/zip — vggt_mv3dt_output.zip
```

Downstream skill flow:
1. Call this skill with the user's inputs; capture the printed `project_id`.
2. Wait for the skill to return (it polls until `COMPLETED` internally).
3. `GET /v1/result/{project_id}/mv3dt_result?result_type=amc` — save the ZIP locally.
4. If VGGT also ran, optionally fetch `?result_type=vggt` for the refined MV3DT.

## Related Skills

- [`vios`](../vios/SKILL.md) — VIOS API skill; only the `rtsp` calibration mode depends on VIOS being reachable.

Root `README.md` "Custom Dataset" and "Calibration Workflow (UI)" sections document input-video guidelines and the UI-driven alternative to this API flow.
