# Common Calibration Steps

Shared snippets used by all three input-mode references (videos, RTSP,
sample-dataset). Each mode reference points here for the common create_project,
upload_videos, and handoff steps to avoid duplication.

## Create project

Use a project name 3–50 characters long containing only letters, numbers, hyphens, and underscores (`[A-Za-z0-9_-]{3,50}`). Surface a 4xx validation response; do not sanitize a rejected name silently.

```
POST /v1/create_project
Content-Type: application/x-www-form-urlencoded

project_name=<your_project_name>
```

Save the returned `project_id` — every subsequent endpoint takes it.

Python equivalent:

```python
r = s.post(f"{BASE_URL}/create_project", data={"project_name": PROJECT_NAME})
r.raise_for_status()
project_id = r.json()["project_id"]
```

## Validate and upload videos

Before upload, require every source to be a readable, non-empty, valid `1920x1080` MP4 with a codec/pixel-format combination accepted by DeepStream. Multi-camera clips must cover the same time window, be ordered by overlapping FOV, and contain enough moving people/objects for tracklet-based AMC. VGGT does not remove the AMC input-quality requirements when AMC will also run.

Use `ffprobe` when available and stop before creating an expensive calibration run if any input fails:

```bash
for video in "$VIDEO_DIR"/cam_*.mp4; do
  test -s "$video" || { echo "Empty or missing video: $video" >&2; exit 1; }
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,pix_fmt,width,height,duration \
    -of default=noprint_wrappers=1 "$video" || exit 1
done
```

Videos must be named `cam_00.mp4`, `cam_01.mp4`, … contiguous, with no gaps. Upload order defines camera indices.

```
POST /v1/upload_video_files/<project_id>
Content-Type: multipart/form-data

files=@cam_00.mp4
files=@cam_01.mp4
...
```

For the sample-dataset mode the bundled zip already contains the cameras in
the correct order; the mode reference just feeds them into this endpoint.

## Hand off to the shared calibration tail

Once the mode-specific reference has uploaded videos, alignment, and layout
(plus any optional GT zip / focal lengths), continue with the **Shared
Calibration Tail** — see [SKILL.md Step A onward](../SKILL.md#step-a--stage-linear-media)
for the REST flow and [`calibration-tail.md`](calibration-tail.md) for the
shared Python snippet (linear media → verify → VGGT/post-process when available → AMC/post-process → results).
