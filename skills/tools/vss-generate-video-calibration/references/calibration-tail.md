## Shared Calibration Tail (Python)

The linear-media → verify → VGGT/post-process (when available) → AMC/post-process → results sequence is identical across all
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

# Verify after linear media is ready. A restored VGGT RUNNING project was
# already verified; re-verification is rejected while its job is active.
project = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
running_keys = [key for key in ("rectification_state", "amc_state", "vggt_state", "postprocess_state") if project.get(key) == "RUNNING"]
if running_keys == ["vggt_state"]:
    print("[B] Persisted VGGT job is running; resume its poll without re-verifying")
elif running_keys:
    raise RuntimeError(f"Project already has a running job ({', '.join(running_keys)}); wait for its terminal state before continuing")
else:
    verified = s.post(f"{BASE_URL}/verify_project/{project_id}")
    verified.raise_for_status()
    if verified.json().get("project_state") not in {"READY", "COMPLETED", "ERROR"}:
        raise RuntimeError(f"Project verification returned an unexpected state: {verified.text[:500]}")

# v3.3.0: VGGT is independent from AMC and is the first/default method when
# ready because it is faster. Do not invoke it if its model is absent.
run_vggt = globals().get("RUN_VGGT_IF_READY", True)
project = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
vggt_state = project.get("vggt_state", "INIT")
is_multi_camera = project.get("video_files_count", len(project.get("video_files", []))) > 1
vggt_stats = None
vggt_postprocessed = False
vggt_completed = False
if is_multi_camera and (vggt_state == "RUNNING" or (run_vggt and vggt_state in {"READY", "COMPLETED"})):
    if vggt_state == "READY":
        s.post(f"{BASE_URL}/vggt/calibrate/{project_id}").raise_for_status()
        print("[C] VGGT calibration started first")
        vggt_state = "RUNNING"
    elif vggt_state == "RUNNING":
        print("[C] Resuming persisted VGGT calibration poll")
    else:
        print("[C] Reusing completed VGGT calibration")
        vggt_completed = True

    if vggt_state == "RUNNING":
        vggt_start = time.time()
        while time.time() - vggt_start < 900:
            project = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
            vggt_state = project.get("vggt_state", "INIT")
            if vggt_state == "COMPLETED":
                print("    VGGT completed")
                vggt_completed = True
                break
            if vggt_state == "ERROR":
                print("    VGGT failed; continuing with independent AMC calibration")
                break
            time.sleep(10)
        else:
            raise RuntimeError("VGGT still running after 15 min; wait for a terminal state before starting AMC")

    # Required after VGGT and before AMC. A persisted completed run may already
    # have export artifacts; otherwise run its post-process now. AMC below must
    # still run a separate post-process pass.
    if vggt_completed and not project.get("vggt_export_ready", False):
        s.post(f"{BASE_URL}/postprocess/{project_id}").raise_for_status()
        post_start = time.time()
        while time.time() - post_start < 1800:
            post_state = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {}).get("postprocess_state")
            if post_state == "COMPLETED":
                print("    VGGT layout post-processing completed")
                vggt_postprocessed = True
                break
            if post_state == "ERROR":
                print("    VGGT layout post-processing failed; continuing with independent AMC calibration")
                break
            time.sleep(10)
        else:
            raise RuntimeError("VGGT layout post-processing still running after 30 min; wait for a terminal state before starting AMC")

    elif vggt_completed:
        print("    VGGT post-processed export artifacts already available")
        vggt_postprocessed = True

    if vggt_postprocessed:
        vggt_metrics = s.get(f"{BASE_URL}/vggt_results/{project_id}/evaluation_statistics")
        if vggt_metrics.status_code == 200:
            vggt_stats = vggt_metrics.json().get("statistics", vggt_metrics.json())
elif not is_multi_camera:
    print("[C] Single-camera project; VGGT is not supported, continuing AMC")
elif vggt_state == "MODEL_MISSING":
    print("[C] VGGT model missing; skipping VGGT and continuing AMC")
elif vggt_state == "READY":
    print("[C] VGGT ready but disabled by RUN_VGGT_IF_READY=False; continuing AMC")
else:
    print(f"[C] VGGT unavailable (state={vggt_state}); continuing AMC")

# Step D — Start AMC calibration (detector_type is a /calibrate argument; not consumed by /v1/config)
s.post(f"{BASE_URL}/calibrate/{project_id}",
       json={"detector_type": DETECTOR_TYPE}).raise_for_status()

# Surface where to watch progress before the long poll begins.
_host = urlparse(BASE_URL).hostname or "<HOST_IP>"
_ui_port = os.environ.get("VSS_AUTO_CALIBRATION_UI_HOST_PORT") or os.environ.get("VSS_AUTO_CALIBRATION_UI_PORT", "5000")
_root = BASE_URL.rsplit("/v1", 1)[0]
print("[D] AMC calibration started")
print(f"    Project:  {project_id}")
print(f"    Detector: {DETECTOR_TYPE}")
print(f"    UI:       http://{_host}:{_ui_port}")
print(f"    Logs:     GET {BASE_URL}/amc/calibrate/{project_id}/log   (Swagger UI: {_root}/docs)")

# Step E — Poll until COMPLETED (10–60 min typical). Poll every 10s, and print a
# heartbeat at least once a minute so a long RUNNING state still shows progress.
start, last_state, last_beat = time.time(), "", 0.0
while time.time() - start < 5400:
    info = s.get(f"{BASE_URL}/get_project_info/{project_id}").json()
    st = info["project_info"].get("amc_state")
    if st is None:
        raise RuntimeError("Project info did not include required amc_state")
    mins, secs = divmod(int(time.time() - start), 60)
    if st != last_state or time.time() - last_beat >= 60:
        print(f"    [{mins:>3}m {secs:02d}s] {st}", flush=True)
        last_state, last_beat = st, time.time()
    if st == "COMPLETED":
        print(f"[E] AMC completed in {mins}m {secs:02d}s"); break
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

# Step F — Layout post-processing (required after AMC; do not reuse VGGT pass)
project = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {})
if project.get("video_files_count", len(project.get("video_files", []))) > 1:
    post = s.post(f"{BASE_URL}/postprocess/{project_id}")
    post.raise_for_status()
    post_start = time.time()
    while time.time() - post_start < 1800:
        post_state = s.get(f"{BASE_URL}/get_project_info/{project_id}").json().get("project_info", {}).get("postprocess_state")
        if post_state == "COMPLETED":
            break
        if post_state == "ERROR":
            raise RuntimeError(f"Layout post-processing failed for project {project_id}")
        time.sleep(10)
    else:
        raise RuntimeError("Layout post-processing still running after 30 min")

# Results + review
print("\n=== Calibration complete ===")
print(f"Project:  {project_id}")
print(f"Detector: {DETECTOR_TYPE}")
if vggt_stats:
    print("VGGT evaluation metrics:")
    for k, v in vggt_stats.items():
        print(f"    {k}: {v}")

# Evaluation metrics are only produced when a ground-truth GT.zip was uploaded.
# A missing result here is normal (no GT) — it is not the end of result reporting.
r = s.get(f"{BASE_URL}/result/{project_id}/evaluation_statistics")
_stats = r.json().get("statistics") if r.status_code == 200 else None
if _stats:
    print("Evaluation metrics:")
    for k, v in _stats.items():
        print(f"    {k}: {v}")
else:
    print("Evaluation metrics: not available — upload a ground-truth GT.zip before calibrating to get L2 / reprojection metrics.")

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
