## Shared Calibration Tail (Python)

The stage-linear-media → verify → VGGT/post-process when available → AMC/post-process → results sequence is identical across all
three input modes (videos, RTSP, sample-dataset). The mode-specific
references stop after their last upload step and reference this snippet.

Assumes `s`, `BASE_URL`, `project_id`, and `DETECTOR_TYPE` are already
bound from the preceding mode-specific Python.

```python
import os
import time
from urllib.parse import urlparse

# Stage source media before verification. Set MEDIA_MODE=linear only after
# confirming every input is already linear/pinhole. For distorted media, set
# MEDIA_MODE=rectified, complete/review/commit AMC UI Rectification, then
# continue only when rectification_state is COMPLETED.
media_mode = os.environ.get("MEDIA_MODE", globals().get("MEDIA_MODE", "")).strip().lower()
if not media_mode:
    try:
        choice = input("Are all source videos already linear/pinhole? [y/N] ").strip().lower()
    except EOFError as exc:
        raise RuntimeError(
            "Choose MEDIA_MODE=linear only for confirmed linear media, or "
            "MEDIA_MODE=rectified after AMC UI Rectification is committed."
        ) from exc
    media_mode = "linear" if choice in {"y", "yes"} else "rectified"
info = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
if info.get("rectification_state") != "COMPLETED":
    if media_mode == "linear":
        staged = s.post(f"{BASE_URL}/linear_media/{project_id}")
        staged.raise_for_status()
        if staged.json().get("rectification_state") != "COMPLETED":
            raise RuntimeError("Linear-media staging did not complete")
    elif media_mode == "rectified":
        raise RuntimeError("Complete and commit AMC UI Rectification, then re-run after rectification_state is COMPLETED")

# Verify after linear media is ready. Do not re-verify a project with an
# active job: the API rejects verification while work is running. A persisted
# VGGT job is safe to resume by polling below.
project = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
running_keys = [
    key for key in ("rectification_state", "amc_state", "vggt_state", "postprocess_state")
    if project.get(key) == "RUNNING"
]
if running_keys == ["vggt_state"]:
    print("[B] Persisted VGGT job is running; resume its poll without re-verifying")
elif running_keys:
    raise RuntimeError(
        f"Project already has a running job ({', '.join(running_keys)}); "
        "wait for its terminal state before continuing"
    )
else:
    s.post(f"{BASE_URL}/verify_project/{project_id}").raise_for_status()

def postprocess_if_multicam():
    project = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
    video_count = int(project.get("video_files_count") or len(project.get("video_files", [])))
    if video_count <= 1:
        return
    s.post(f"{BASE_URL}/postprocess/{project_id}").raise_for_status()
    post_start = time.time()
    while time.time() - post_start < 1800:
        post_state = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {}).get("postprocess_state")
        if post_state == "COMPLETED":
            return
        if post_state == "ERROR":
            raise RuntimeError(f"Layout post-processing failed for project {project_id}")
        time.sleep(10)
    raise RuntimeError("Layout post-processing still running after 30 min")

# Step C — VGGT is independent of AMC. Start or resume it first by default, then
# post-process every completed run before AMC resets the shared state.
def wait_for_vggt():
    started = time.time()
    while time.time() - started < 900:
        state = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {}).get("vggt_state")
        if state == "COMPLETED":
            return True
        if state == "ERROR":
            print("Independent VGGT calibration failed; continuing with AMC calibration")
            return False
        time.sleep(10)
    raise RuntimeError("Independent VGGT calibration still running after 15 min")

vggt_completed = False
vggt_state = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {}).get("vggt_state", "INIT")
if vggt_state == "READY":
    s.post(f"{BASE_URL}/vggt/calibrate/{project_id}").raise_for_status()
    vggt_completed = wait_for_vggt()
elif vggt_state == "RUNNING":
    print("Independent VGGT calibration is already running; waiting for it")
    vggt_completed = wait_for_vggt()
elif vggt_state == "COMPLETED":
    vggt_completed = True
elif vggt_state == "MODEL_MISSING":
    print("VGGT model is unavailable; continuing with AMC-only calibration")
elif vggt_state == "ERROR":
    print("Independent VGGT calibration previously failed; continuing with AMC calibration")

vggt_stats = None
if vggt_completed:
    postprocess_if_multicam()
    response = s.get(f"{BASE_URL}/vggt_results/{project_id}/evaluation_statistics")
    vggt_stats = response.json().get("statistics") if response.status_code == 200 else None
    if vggt_stats:
        print("VGGT evaluation metrics:")
        for key, value in vggt_stats.items():
            print(f"    {key}: {value}")

# Step D — Start AMC calibration (detector_type is a /calibrate argument; not consumed by /v1/config)
s.post(f"{BASE_URL}/calibrate/{project_id}",
       json={"detector_type": DETECTOR_TYPE}).raise_for_status()

# Surface where to watch progress before the long poll begins.
_host = urlparse(BASE_URL).hostname or "<HOST_IP>"
_ui_port = os.environ.get("VSS_AUTO_CALIBRATION_UI_HOST_PORT") or os.environ.get("VSS_AUTO_CALIBRATION_UI_PORT", "5000")
_root = BASE_URL.rsplit("/v1", 1)[0]
print("[B] Calibration started")
print(f"    Project:  {project_id}")
print(f"    Detector: {DETECTOR_TYPE}")
print(f"    UI:       http://{_host}:{_ui_port}")
print(f"    Logs:     GET {BASE_URL}/amc/calibrate/{project_id}/log   (Swagger UI: {_root}/docs)")

# Step E — Poll until COMPLETED (10–60 min typical). Poll every 10s, and print a
# heartbeat at least once a minute so a long RUNNING state still shows progress.
start, last_state, last_beat = time.time(), "", 0.0
while time.time() - start < 5400:
    info = s.get(f"{BASE_URL}/get_project_info/{project_id}").json()
    st = info["project_info"]["amc_state"]
    mins, secs = divmod(int(time.time() - start), 60)
    if st != last_state or time.time() - last_beat >= 60:
        print(f"    [{mins:>3}m {secs:02d}s] {st}", flush=True)
        last_state, last_beat = st, time.time()
    if st == "COMPLETED":
        print(f"[C] Completed in {mins}m {secs:02d}s"); break
    if st == "ERROR":
        # Surface the tail of the calibration log so the failure is actionable.
        try:
            log_lines = s.get(f"{BASE_URL}/amc/calibrate/{project_id}/log").text.splitlines()
            print("    --- last calibration log lines ---")
            for line in log_lines[-20:]:
                print(f"    {line}")
        except Exception:
            pass
        raise RuntimeError(f"Calibration ERROR — full log: GET {BASE_URL}/amc/calibrate/{project_id}/log")
    time.sleep(10)
else:
    raise RuntimeError(
        f"Calibration still running after {int((time.time() - start) // 60)} min — "
        f"inspect GET {BASE_URL}/amc/calibrate/{project_id}/log or the UI at http://{_host}:{_ui_port}"
    )

# Step F — AMC resets postprocess_state, so post-process again after AMC.
postprocess_if_multicam()

# Results + review
print("\n=== Calibration complete ===")
print(f"Project:  {project_id}")
print(f"Detector: {DETECTOR_TYPE}")

# Evaluation metrics are only produced when a ground-truth GT.zip was uploaded.
# A missing result here is normal (no GT) — it is not the end of result reporting.
r = s.get(f"{BASE_URL}/result/{project_id}/evaluation_statistics")
amc_stats = r.json().get("statistics") if r.status_code == 200 else None
if amc_stats:
    print("AMC evaluation metrics:")
    for k, v in amc_stats.items():
        print(f"    {k}: {v}")
else:
    print("AMC evaluation metrics: not available — upload a ground-truth GT.zip before calibrating to get L2 / reprojection metrics.")

if vggt_completed:
    print("Compare VGGT and AMC metrics plus Results-page overlays, then select the more accurate calibration for export.")

# Always point to the visual overlay so the user can validate calibration quality.
_projects_dir = os.environ.get(
    "PROJECTS_DIR",
    f"{os.environ.get('VSS_APPS_DIR', '<VSS_APPS_DIR>')}/services/auto-calibration/projects",
)
_proj_path = f"{_projects_dir}/project_{project_id}"
print("\nReview the calibration:")
print(f"    UI:            http://{_host}:{_ui_port}  — open project {project_id}, then the Results page to view the overlay")
print(f"    Overlay image: {_proj_path}/output/multi_view_results/BA_output/results_ba_scaled_world/overlay_img_*.png")
print(f"    Project files: {_proj_path}")
```

See [SKILL.md Shared Calibration Tail](../SKILL.md#shared-calibration-tail) for
the REST equivalents and the meaning of each project state.
