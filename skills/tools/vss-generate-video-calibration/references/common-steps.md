# Common Calibration Steps

Shared snippets used by all three input-mode references (videos, RTSP,
sample-dataset). Each mode reference points here for the common create_project,
upload_videos, and handoff steps to avoid duplication.

## Create project

```
POST /v1/create_project
Content-Type: application/x-www-form-urlencoded

project_name=<your_project_name>
```

Save the returned `project_id` — every subsequent endpoint takes it.

Validate the project name before sending it: 3–50 characters using only ASCII letters, digits, `_`, or `-` (`[A-Za-z0-9_-]{3,50}`).

Python equivalent:

```python
import re

if not re.fullmatch(r"[A-Za-z0-9_-]{3,50}", PROJECT_NAME):
    raise ValueError("PROJECT_NAME must be 3-50 characters using only letters, digits, '_' or '-'")
r = s.post(f"{BASE_URL}/create_project", data={"project_name": PROJECT_NAME})
r.raise_for_status()
project_id = r.json()["project_id"]
```

## Upload videos

Videos may use any readable MP4 filename. Upload them in the intended camera order; that order defines camera indices.

```
POST /v1/upload_video_files/<project_id>
Content-Type: multipart/form-data

files=@camera-a.mp4
files=@camera-b.mp4
...
```

For the sample-dataset mode the bundled zip already contains the cameras in
the correct order; the mode reference just feeds them into this endpoint.

## Hand off to the shared calibration tail

Once the mode-specific reference has uploaded videos, alignment, and layout
(plus any optional GT zip / focal lengths), continue with the **Shared
Calibration Tail** — see [SKILL.md Step A onward](../SKILL.md#step-a--stage-linear-media)
for the REST flow and [`calibration-tail.md`](calibration-tail.md) for the
shared Python snippet (stage linear media → verify → VGGT/post-process when ready → AMC/post-process → results).
