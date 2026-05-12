---
name: "amc-calibrate-sample-dataset"
description: "Run end-to-end calibration on the shipped sample dataset (sdg_08_2_sample_data_010926.zip) against a running AMC microservice. Use when user says 'test sample dataset', 'run sample calibration', 'verify AMC install', or 'launch and test'."
owner: "nvidia-metropolis-team"
service: "auto-magic-calib"
version: "1.0.0"
reviewed: "2026-05-11"
data_classification: public
license: "Apache License 2.0"
metadata:
  author: "NVIDIA Metropolis Team"
  tags: [amc, calibration, sample, rest-api, validation, python]
  languages: [python, bash]
  domain: calibration
---

# Skill: Calibrate Sample Dataset

## Purpose

Run a full calibration on the bundled sample dataset (`sdg_08_2_sample_data_010926.zip`, 4 synthetic warehouse cameras with ground truth) against a running AutoMagicCalib microservice. Useful for verifying that a freshly-launched stack works end-to-end before throwing real data at it.

The sample includes GT, so the run produces evaluation metrics (L2 distance, reprojection error) — no calibration parameter tuning needed.

## Prerequisites

- [ ] AMC microservice running (deploy via the [`/deploy`](../deploy/SKILL.md) skill with the `auto-calibration` profile if not — see [`references/auto-calibration.md`](../deploy/references/auto-calibration.md))
- [ ] Sample zip present at `assets/sdg_08_2_sample_data_010926.zip` — **the VSS repo does not ship this file.** See [Obtain the sample zip](#obtain-the-sample-zip) below.
- [ ] Python 3 with `requests` available — or use the Swagger UI path below
  - The inline run block self-heals: if `requests` is missing it creates a throwaway venv under `${TMPDIR:-/tmp}/amc-sample-test-venv` (nothing written to the repo)
  - If `python3 -m venv` itself fails with `ensurepip not available`: `sudo apt install -y python3-venv python3-pip`

## Quick Start for Agents

**"launch AMC and test sample dataset" (or similar):**

1. Run the [`/deploy`](../deploy/SKILL.md) skill with the `auto-calibration` profile first.
2. Wait for `/v1/ready` to return OK.
3. Extract sample data (snippet below) — idempotent, safe to re-run.
4. Run the inline block in [Run Inline (No File Written)](#run-inline-no-file-written). Do **not** save it as a `.py` file — pipe via heredoc so the user's repo stays clean.
5. Report final metrics + UI URL for manual inspection.

**"test sample dataset" (MS already running):**

1. Detect backend: scan ports 8000–8009 for a `/v1/ready` response.
2. If none → point to the [`/deploy`](../deploy/SKILL.md) skill with the `auto-calibration` profile.
3. Extract sample data if not already cached.
4. Run the inline block (heredoc-piped Python — no file written).
5. Report metrics.

### Detect Running Backend

```bash
MS_PORT=""
for port in {8000..8009}; do
  if curl -s "http://localhost:$port/v1/ready" | grep -q '"code":0'; then
    MS_PORT=$port; break
  fi
done
if [ -z "$MS_PORT" ] && curl -s "http://localhost:8010/v1/ready" | grep -q '"code":0'; then
  MS_PORT=8010
fi
[ -z "$MS_PORT" ] && { echo "No running backend. Run the deploy skill with the auto-calibration profile first."; exit 1; }
echo "Backend on port $MS_PORT"
```

### Obtain the sample zip

The zip is **not** committed to the VSS repo. It lives in the standalone AMC repo on GitHub, where it ships via git-lfs:

- Canonical source: <https://github.com/NVIDIA-AI-IOT/auto-magic-calib/blob/main/assets/sdg_08_2_sample_data_010926.zip>
- Raw LFS download: <https://github.com/NVIDIA-AI-IOT/auto-magic-calib/raw/main/assets/sdg_08_2_sample_data_010926.zip>
- File size: ~154 MB

Pick the path that fits your setup:

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)
mkdir -p "$REPO_ROOT/assets"
TARGET="$REPO_ROOT/assets/sdg_08_2_sample_data_010926.zip"

# (a) Reuse an existing AMC checkout on the same host (cheapest, no network)
if [ -f "$HOME/auto-magic-calib/assets/sdg_08_2_sample_data_010926.zip" ]; then
  ln -sf "$HOME/auto-magic-calib/assets/sdg_08_2_sample_data_010926.zip" "$TARGET"

# (b) Pull from GitHub LFS directly (no AMC checkout needed)
else
  curl -L -o "$TARGET" \
    https://github.com/NVIDIA-AI-IOT/auto-magic-calib/raw/main/assets/sdg_08_2_sample_data_010926.zip
fi

# (c) Or: clone the AMC repo with LFS into a sibling dir and symlink — useful if you
# also want the AMC scripts/docs:
#   git lfs install
#   git clone https://github.com/NVIDIA-AI-IOT/auto-magic-calib.git ../auto-magic-calib
#   ln -sf "$PWD/../auto-magic-calib/assets/sdg_08_2_sample_data_010926.zip" "$TARGET"

# Verify (~154 MB)
ls -lh "$TARGET"
```

> The VSS repo deliberately doesn't bundle the zip (size + version-skew across AMC releases). Don't commit it here — `assets/sdg_08_2_sample_data_010926.zip` should stay gitignored if you copy it in.

### Locate + Extract Sample Data (idempotent)

```bash
export REPO_ROOT=$(git rev-parse --show-toplevel)

SAMPLE_ZIP="$REPO_ROOT/assets/sdg_08_2_sample_data_010926.zip"
[ -f "$SAMPLE_ZIP" ] || { echo "Sample zip not found at $SAMPLE_ZIP"; exit 1; }

# Cache directory next to the zip.
SAMPLE_DIR="$(dirname "$SAMPLE_ZIP")/.cache/sdg_08_2_sample_data_010926"

if [ ! -d "$SAMPLE_DIR" ]; then
  mkdir -p "$SAMPLE_DIR"
  unzip -q "$SAMPLE_ZIP" -d "$SAMPLE_DIR"
fi
ls "$SAMPLE_DIR"
# Expected (possibly inside a wrapper folder): alignment_data/  GT.zip  videos/
```

## Run Inline (No File Written)

Run the test on the fly — pipe Python into `python3` via heredoc so nothing is saved into the user's repo. The block below is fully self-contained: it resolves `REPO_ROOT` via `git rev-parse`, reads `MS_PORT` from `.env`, picks (or creates) a Python with `requests` installed, and then pipes the inline script. It is safe to copy/paste verbatim into any shell where the AMC backend is reachable on `localhost`. To re-test, just run it again; each invocation creates a fresh project.

```bash
# Resolve env
export REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
ENV_FILE="$REPO_ROOT/deploy/docker/industry-profiles/warehouse-operations/.env"
export MS_PORT="$(grep ^VSS_AUTO_CALIBRATION_PORT "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
export MS_PORT="${MS_PORT:-8010}"
export BASE_URL="http://localhost:${MS_PORT}/v1"
# Optional: export SAMPLE_DIR=/abs/path/to/extracted/sample to override autodetection

# Pick a python3 that has `requests`; create a throwaway venv if needed (no repo files written)
PY=python3
"$PY" -c 'import requests' 2>/dev/null || {
  VENV="${TMPDIR:-/tmp}/amc-sample-test-venv"
  python3 -m venv "$VENV" 2>/dev/null \
    || { sudo apt install -y python3-venv python3-pip && python3 -m venv "$VENV"; }
  "$VENV/bin/pip" install --quiet requests
  PY="$VENV/bin/python3"
}

"$PY" - <<'PY'
import os
import sys
import time
from pathlib import Path

import requests

# REPO_ROOT comes from the surrounding shell; fall back to cwd when missing
# (no `__file__` to lean on when fed via stdin).
REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path.cwd())
MS_PORT = os.environ.get("MS_PORT", "8010")
BASE_URL = os.environ.get("BASE_URL", f"http://localhost:{MS_PORT}/v1")

# Sample zip lives in assets/.
def _find_sample_dir() -> Path:
    candidate = REPO_ROOT / "assets" / ".cache" / "sdg_08_2_sample_data_010926"
    if candidate.exists():
        return candidate
    sys.exit(
        "Sample data not extracted. Run the extraction snippet from this skill first, "
        "or pass SAMPLE_DIR= explicitly."
    )

# NOTE: do NOT write `Path(os.environ.get("SAMPLE_DIR", "")) or _find_sample_dir()`
# — Path("") evaluates to Path('.') which is truthy, so the `or` never falls
# through and the script silently picks `.` (typically the repo root). Rglobbing
# `cam_*.mp4` from there can sweep dozens of stale videos from prior test runs.
_env_sample = os.environ.get("SAMPLE_DIR")
SAMPLE_DIR = Path(_env_sample).resolve() if _env_sample else _find_sample_dir()

# Locate sample files (handle an optional wrapper folder from unzip)
def _find(path: Path, name: str) -> Path:
    hits = list(path.rglob(name))
    if not hits:
        sys.exit(f"Could not find {name} under {path}")
    return hits[0]

# Anchor video discovery on the canonical `videos/` directory if present
# (non-recursive). Only fall back to rglob if no `videos/` folder exists,
# and assert a sane upper bound so a misconfigured SAMPLE_DIR fails loud
# instead of uploading every cam_*.mp4 in the tree.
videos_dirs = list(SAMPLE_DIR.rglob("videos"))
videos_dir = next((d for d in videos_dirs if d.is_dir()), None)
if videos_dir is not None:
    videos = sorted(videos_dir.glob("cam_*.mp4"))
else:
    videos = sorted(SAMPLE_DIR.rglob("cam_*.mp4"))

alignment = _find(SAMPLE_DIR, "alignment_data.json")
layout = _find(SAMPLE_DIR, "layout.png")
gt_zip = _find(SAMPLE_DIR, "GT.zip")

assert len(videos) >= 2, f"Need >=2 cam_XX.mp4 under {SAMPLE_DIR}, found {len(videos)}"
# Sample dataset has 4 cameras — bail if SAMPLE_DIR is so wide we'd upload
# unrelated videos. Override SAMPLE_DIR explicitly if you need a different one.
assert len(videos) <= 16, (
    f"Found {len(videos)} cam_*.mp4 under {SAMPLE_DIR} — looks like SAMPLE_DIR "
    "is too broad (probably picked up stale test caches). Set SAMPLE_DIR to the "
    "extracted sample folder explicitly and re-run."
)
print(f"Base URL:   {BASE_URL}")
print(f"Sample dir: {SAMPLE_DIR}")
print(f"Videos:     {[v.name for v in videos]}")

s = requests.Session()

# Step 1 — Create project
project_name = f"sample_test_{int(time.time())}"
r = s.post(f"{BASE_URL}/create_project", data={"project_name": project_name})
r.raise_for_status()
project_id = r.json()["project_id"]
print(f"[1] Created project {project_name} → {project_id}")

# Step 2 — Upload videos (sorted alphabetically; upload order defines camera indices)
files, handles = [], []
for v in videos:
    f = open(v, "rb"); handles.append(f)
    files.append(("files", (v.name, f, "video/mp4")))
r = s.post(f"{BASE_URL}/upload_video_files/{project_id}", files=files, timeout=300)
for f in handles: f.close()
r.raise_for_status()
print(f"[2] Uploaded {len(videos)} videos")

# Step 3 — Upload alignment JSON
with open(alignment, "rb") as f:
    r = s.post(f"{BASE_URL}/upload_alignment/{project_id}",
               files={"alignment_file": (alignment.name, f, "application/json")})
    r.raise_for_status()
print(f"[3] Uploaded alignment JSON")

# Step 4 — Upload layout PNG
with open(layout, "rb") as f:
    r = s.post(f"{BASE_URL}/upload_layout/{project_id}",
               files={"layout_file": (layout.name, f, "image/png")})
    r.raise_for_status()
print(f"[4] Uploaded layout PNG")

# Step 5 — Upload GT zip (enables evaluation metrics)
with open(gt_zip, "rb") as f:
    r = s.post(f"{BASE_URL}/upload_gt_file/{project_id}",
               files={"gt_file": (gt_zip.name, f, "application/zip")}, timeout=120)
    r.raise_for_status()
print(f"[5] Uploaded GT zip")

# Step 6 — Verify project
r = s.post(f"{BASE_URL}/verify_project/{project_id}")
r.raise_for_status()
state = r.json()["project_state"]
print(f"[6] verify_project → {state}")
assert state == "READY", f"Expected READY, got {state}"

# Step 7 — Start calibration (defaults work for this dataset)
r = s.post(f"{BASE_URL}/calibrate/{project_id}", json={"detector_type": "resnet"})
r.raise_for_status()
print(f"[7] Calibration started (detector=resnet)")

# Step 8 — Poll for completion (~10–30 min for sample)
print(f"[8] Polling (expect 10–30 min)...")
start = time.time()
last_state = ""
while time.time() - start < 3600:
    r = s.get(f"{BASE_URL}/get_project_info/{project_id}")
    r.raise_for_status()
    st = r.json()["project_info"]["project_state"]
    elapsed = int(time.time() - start)
    if st != last_state:
        print(f"    [{elapsed:>4}s] {st}", flush=True)
        last_state = st
    if st == "COMPLETED":
        print(f"[8] Completed in {elapsed}s")
        break
    if st == "ERROR":
        sys.exit(f"Calibration failed. Pull log: GET {BASE_URL}/amc/calibrate/{project_id}/log")
    time.sleep(10)
else:
    sys.exit("Timed out after 60 min")

# Step 9 — Evaluation statistics (GT was uploaded, so this should return metrics)
r = s.get(f"{BASE_URL}/result/{project_id}/evaluation_statistics")
if r.status_code == 200:
    stats = r.json().get("statistics", r.json())
    print(f"\n[9] Evaluation statistics:")
    for k, v in stats.items():
        print(f"    {k}: {v}")
else:
    print(f"\n[9] evaluation_statistics returned {r.status_code}: {r.text[:200]}")

print(f"\nProject ID: {project_id}")
print("Inspect in UI: open the project in the web UI to view results and overlay videos")
PY
```

> **Why heredoc, not a `.py` file?** The skill is meant to run on demand against any user's checkout — writing `run_sample_test.py` into the repo would dirty their working tree. The `<<'PY'` quoting prevents shell expansion inside the script. Re-run the same block any time; each run creates a fresh project.

## Alternative: Swagger UI Walkthrough

The microservice exposes an interactive OpenAPI UI at **`http://<HOST_IP>:<MS_PORT>/docs`**. If you prefer clicking through the API by hand:

1. Open `http://<HOST_IP>:<MS_PORT>/docs` in a browser.
2. Unzip `sdg_08_2_sample_data_010926.zip` into a cache directory next to it.
3. Execute these endpoints **in order**, copying the `project_id` from step 1 into subsequent paths:

   | # | Endpoint | Body / Files |
   |---|---|---|
   | 1 | `POST /v1/create_project` | `project_name`: any string |
   | 2 | `POST /v1/upload_video_files/{project_id}` | `files`: upload all 4 `videos/cam_0*.mp4` **sorted by name** |
   | 3 | `POST /v1/upload_alignment/{project_id}` | `alignment_file`: `alignment_data/alignment_data.json` |
   | 4 | `POST /v1/upload_layout/{project_id}` | `layout_file`: `alignment_data/layout.png` |
   | 5 | `POST /v1/upload_gt_file/{project_id}` | `gt_file`: `GT.zip` |
   | 6 | `POST /v1/verify_project/{project_id}` | — (expect `project_state: READY`) |
   | 7 | `POST /v1/calibrate/{project_id}` | JSON: `{"detector_type": "resnet"}` |
   | 8 | `GET /v1/get_project_info/{project_id}` | Refresh every ~10 s until `project_state` = `COMPLETED` |
   | 9 | `GET /v1/result/{project_id}/evaluation_statistics` | Read L2 distance + reprojection error |

This is the same sequence the Python script runs, just executed manually.

## Success Criteria

- Project reaches `project_state == "COMPLETED"` within ~30 min.
- `/v1/result/{id}/evaluation_statistics` returns non-empty `statistics` (GT was uploaded).
- No `ERROR` state encountered.

Representative metrics for the sample (yours should be similar):

```
Average L2 distance(m)               : < 1.5
Average reprojection error 0(px)     : < 10
```

## Key Output Files (on the server)

Results persist under `$REPO_ROOT/projects/project_<project_id>/`:

```
projects/project_<project_id>/
├── output/
│   ├── single_view_results/cam_XX/
│   │   ├── camInfo_hyper_XX.yaml
│   │   └── trajDump_Stream_0_3d.txt
│   └── multi_view_results/BA_output/results_ba/refined/
│       └── camInfo_XX.yaml          # ← final calibration (use this)
└── calibration.log
```

## Monitoring Progress

```bash
PROJECT_ID=<id_from_step_1>
REPO_ROOT=$(git rev-parse --show-toplevel)
tail -F --retry "$REPO_ROOT/projects/project_${PROJECT_ID}/calibration.log"
```

Or stream MS logs:

```bash
docker logs -f vss-auto-calibration
```

## Troubleshooting

| Issue | Fix |
|---|---|
| `requests` not installed | Inside a venv: `python3 -m venv venv && ./venv/bin/pip install requests`. If `python3 -m venv` fails: `sudo apt install -y python3-venv python3-pip` first |
| `[2] Uploaded N videos` where N >> 4 | `SAMPLE_DIR` resolved to the repo root (or another over-broad path) and `rglob("cam_*.mp4")` swept stale videos from `.cache/`, `projects/`, etc. Stop the run (`POST /v1/stop_calibration/{id}`), delete the project (`DELETE /v1/delete_project/{id}`), set `SAMPLE_DIR` explicitly to the extracted sample dir, re-run. The script anchors on `videos/` and asserts `len(videos) <= 16` to fail loud |
| `verify_project` returns state `!= READY` | Confirm all 4 videos + alignment + layout + GT uploaded; inspect `GET /v1/get_project_info/{id}` response |
| Sample zip not present at `assets/sdg_08_2_sample_data_010926.zip` | The VSS repo does not bundle it. Pull from GitHub LFS or a sibling AMC checkout — see [Obtain the sample zip](#obtain-the-sample-zip). |
| Sample not extracted | `unzip <repo_root>/assets/sdg_08_2_sample_data_010926.zip -d <repo_root>/assets/.cache/sdg_08_2_sample_data_010926/` |
| `cam_*.mp4` glob finds 0 files | Check wrapper-folder depth: `find <sample_dir> -name "cam_*.mp4"` |
| Calibration times out (>60 min) | Check `calibration.log` for "insufficient tracklets"; see root `README.md` guidelines on input videos |
| Upload returns 413 | Raise server upload limit, or split files (sample files are <200 MB total so this is unusual) |
| Port scan finds no backend | Backend not running — run the [`/deploy`](../deploy/SKILL.md) skill with the `auto-calibration` profile |

## Related Skills

- [`/deploy`](../deploy/SKILL.md) with the `auto-calibration` profile — launch MS + UI (prerequisite — see [reference](../deploy/references/auto-calibration.md)).
- [`amc-calibrate-videos`](../amc-calibrate-videos/SKILL.md) — run calibration on your own pre-recorded MP4s.
- [`amc-calibrate-rtsp-streams`](../amc-calibrate-rtsp-streams/SKILL.md) — run calibration on live RTSP streams via VIOS.

Root `README.md` "Sample Data Setup" and "Calibration Workflow (UI)" sections cover the human-oriented path through the same sample.
