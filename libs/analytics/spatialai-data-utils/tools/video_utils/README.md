# video_utils CLI tools

Four command-line wrappers over
`spatialai_data_utils.visualization.video_utils`:

| Tool | What it does |
|---|---|
| `video2frame.py` | Decode **one video file** into a directory of per-frame images |
| `video2frame_scene.py` | Decode **every per-camera video** under a multi-camera scene directory in parallel |
| `frame2video.py` | Encode **one directory** of per-frame images into one video file |
| `frame2video_scene.py` | Stack **every per-camera frame directory** into a grid layout and encode as one video |

All four take their input(s) and output as **positional arguments**
and exit non-zero if the underlying library helper returns a status
other than `completed` / `skipped`.

For other multi-job patterns (e.g. parallel `frames → video` across
many directories, or programmatic batching with per-job overrides),
drive the library helpers directly — see [Programmatic API](#programmatic-api)
below.

---

## `video2frame.py` — decode a video → frame directory

```bash
# Default: every frame as 9-digit zero-padded JPG
python tools/video_utils/video2frame.py video.mp4 output/frames/

# Keep every 5th frame, capped at the first 200 source frames
python tools/video_utils/video2frame.py video.mp4 output/frames/ \
    --frame_skip 5 --end_frame 200

# Custom filename pattern (extension drives the image encoder)
python tools/video_utils/video2frame.py video.mp4 output/frames/ \
    --frame_pattern 'frame_{:05d}.png'
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `VIDEO` (positional) | Yes | — | Input video file path |
| `OUTPUT_DIR` (positional) | Yes | — | Output frame directory (created if missing) |
| `--frame_pattern` | No | `{:09d}.jpg` | `str.format`-style filename pattern; extension drives the image encoder |
| `--frame_skip` | No | `1` | Keep every Nth decoded frame |
| `--start_frame` | No | `0` | First source-video frame to keep |
| `--end_frame` | No | end | One-past-last source-video frame |
| `--overwrite` | No | `false` | Re-extract even when the output dir already looks complete |

### Status strings

The library returns one of these; the CLI prints it on the final line
and uses it to decide the exit code:

| Status | Meaning |
|---|---|
| `completed` | Frames written successfully |
| `skipped` | Output dir already had ≥ expected frame count (use `--overwrite` to force) |
| `file_not_found` | Input video doesn't exist |
| `empty_file` | Input video is 0 bytes (often the moov-atom-corruption case) |
| `cannot_open` | OpenCV refused to open the file |
| `invalid_properties` | Frame count / fps / dimensions came back ≤ 0 |
| `no_frames_extracted` | Decoded loop ran but no frames written |
| `incomplete_extraction` | < 80% of expected frames written (decoder bailed early) |
| `write_error` | `cv2.imwrite` returned `False` (disk full / permissions) |

---

## `video2frame_scene.py` — decode every video in a multi-camera scene (parallel)

Two input layouts are supported.  In both cases, output goes to
`<scene_dir>/<cam>/<frames_subdir>/<frame_pattern>` per camera, so
downstream tools don't need to know which input layout was used.

### Layout B — central videos folder (default)

This matches the canonical `data/mtmc/synthetic/<scene>/` layout
(Isaac-mirror convention) used elsewhere in the toolkit:

```text
<scene_dir>/
    videos/                 # default --videos_dir
        Camera_01.mp4       # camera name = file basename (sans ext)
        Camera_02.mp4
        ...
    Camera_01/
        rgb/                # default --frames_subdir
            rgb_00000.jpg   # default --frame_pattern: rgb_{:05d}.jpg
            rgb_00001.jpg
            ...
    Camera_02/
        rgb/...
```

```bash
# Default invocation (matches data/mtmc/synthetic/scene_001/, etc.)
python tools/video_utils/video2frame_scene.py \
    data/mtmc/synthetic/scene_001/

# Same scene, 8 workers
python tools/video_utils/video2frame_scene.py \
    data/mtmc/synthetic/scene_001/ --workers 8

# Keep every 5th frame, capped at the first 200 source frames per camera
python tools/video_utils/video2frame_scene.py \
    data/mtmc/synthetic/scene_001/ --frame_skip 5 --end_frame 200
```

Cameras are discovered from filenames inside `<scene_dir>/<videos_dir>/`.
Any file with a common video extension (`.mp4`, `.avi`, `.mov`,
`.mkv`, `.m4v`, `.webm`; case-insensitive) qualifies, and the camera
name is the file basename without the extension.  Names are
natural-sorted (`Camera_2` before `Camera_10`).

The default frame layout (`<cam>/rgb/rgb_{:05d}.jpg`) is the
`isaac_jpg` entry from the library's
[`FRAME_NAME_PATTERN_PRESETS`](../../spatialai_data_utils/visualization/video_utils/video2frame.py),
so the resulting paths are auto-discovered by `draw_3dbbox.py` /
`get_frame_paths_of_multi_cameras` without a custom resolver.

### Layout A — per-camera subdir holds the video

For datasets that store videos inside per-camera subdirectories
instead of a central folder.  Pass an empty `--videos_dir ''` to
select this layout, and override `--frames_subdir` /
`--frame_pattern` if you want a non-Isaac output convention:

```text
<scene_dir>/
    Camera_01/
        video.mp4           # name controlled by --video_filename
    Camera_02/
        video.mp4
    ...
```

```bash
# Layout A: <scene>/Camera_*/video.mp4 → <scene>/Camera_*/images/{:09d}.jpg
python tools/video_utils/video2frame_scene.py data/scene/ \
    --videos_dir '' \
    --frames_subdir images --frame_pattern '{:09d}.jpg' \
    --workers 8
```

Cameras are discovered by listing subdirectories of `<scene_dir>` via
[`get_cam_names_in_scene`](../../spatialai_data_utils/utils/camera_name_utils.py),
with the same natural-sort ordering.

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `SCENE_DIR` (positional) | Yes | — | Scene root |
| `--videos_dir` | No | `videos` | **Layout B** (default): central videos folder name (videos at `<scene_dir>/<videos_dir>/<cam>.<ext>`).  Pass `--videos_dir ''` (empty) to switch to **Layout A**. |
| `--video_filename` | No | `video.mp4` | **Layout A only**: video filename inside each per-camera subdir.  Ignored when `--videos_dir` is set (the default). |
| `--frames_subdir` | No | `rgb` | Per-camera output subdir name (`<scene_dir>/<cam>/<frames_subdir>/`); applies to both layouts |
| `--workers` | No | auto | Number of parallel worker processes (`ProcessPoolExecutor`) |
| `--frame_pattern` | No | `rgb_{:05d}.jpg` | `str.format`-style filename pattern; extension drives the image encoder.  Default matches the Isaac-mirror layout |
| `--frame_skip` | No | `1` | Keep every Nth decoded frame |
| `--start_frame` | No | `0` | First source-video frame to keep |
| `--end_frame` | No | end | One-past-last source-video frame |
| `--overwrite` | No | `false` | Re-extract even when a per-camera output dir already looks complete |

### Status strings

Same set as [`video2frame.py`](#status-strings) — one status per camera.
The CLI logs `[i/N] <status> <video_path>` as workers finish, then a
batch summary line, and exits non-zero if any camera ends in a status
other than `completed` / `skipped`.

---

## `frame2video.py` — encode a frame directory → video

```bash
# Default: all JPG/PNG frames at the package default fps
python tools/video_utils/frame2video.py output/frames/ output.mp4

# 60 fps with a fixed text overlay
python tools/video_utils/frame2video.py output/frames/ output.mp4 \
    --fps 60 --label 'Run #42'

# PNG frames only, half-resolution output
python tools/video_utils/frame2video.py output/frames/ output.mp4 \
    --filename_pattern '*.png' --down_sample 2
```

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `FRAME_DIR` (positional) | Yes | — | Input directory containing per-frame images |
| `OUTPUT` (positional) | Yes | — | Output video file path |
| `--fps` | No | `30.0` | Output frame rate (defaults to package `FPS`) |
| `--codec` | No | `mp4v` | FourCC codec passed to `cv2.VideoWriter_fourcc` |
| `--filename_pattern` (repeatable) | No | `*.jpg`, `*.jpeg`, `*.png` | Filename pattern(s) (shell glob matched against filenames in `FRAME_DIR`) selecting which frames to include |
| `--start_frame` | No | `0` | Index (in sorted order) of first frame to include |
| `--end_frame` | No | end | One-past-last frame index |
| `--down_sample` | No | `1` | Divide output resolution by this integer (1 = full-res) |
| `--label` | No | — | Fixed text overlay (use `\n` for multi-line) |
| `--overwrite` | No | `false` | Re-encode even when the output file already exists |

### Frame requirements

- Filenames sort lexicographically into playback order (the canonical
  `000000001.jpg`, `000000002.jpg`, … format produced by
  `video2frame.py` works out of the box).
- All input frames must have the **same resolution** — the first
  frame's `(W, H)` (after `--down_sample`) is fixed for the whole
  output video.

### Status strings

| Status | Meaning |
|---|---|
| `completed` | Video encoded successfully |
| `skipped` | Output file already exists (use `--overwrite` to force) |
| `no_frames_found` | `--filename_pattern`(s) matched nothing in `FRAME_DIR` |
| `read_error` | `cv2.imread` returned `None` for one of the input frames |
| `write_error` | `cv2.VideoWriter` failed to open or write |

---

## `frame2video_scene.py` — stack multi-camera frames → grid video

Read every per-camera frame directory under a scene, stack each
frame index into a grid layout (auto-selected), and encode the
result as one video file.  Inverse of `video2frame_scene.py`'s
multi-camera split — but consolidates back into a single video for
review/presentation.

### Layout

The defaults match the canonical `data/mtmc/synthetic/<scene>/`
layout (Isaac-mirror convention):

```text
<scene_dir>/
    Camera_01/
        rgb/                # ← per-camera frame dir (--frames_subdir)
            rgb_00000.jpg
            rgb_00001.jpg
            ...
    Camera_02/
        rgb/...
    ...
```

```bash
# Default: <scene>/Camera_*/rgb/rgb_<NNNNN>.jpg → 1080p grid video
# (auto-threads the per-cam decode work)
python tools/video_utils/frame2video_scene.py \
    data/mtmc/synthetic/scene_001/ \
    output/scene_001_grid.mp4

# Force 6 columns, 720p output, 12 worker threads
python tools/video_utils/frame2video_scene.py \
    data/mtmc/synthetic/scene_001/ \
    output/scene_001_grid.mp4 \
    --cols 6 --target_height 720 --workers 12

# First 200 frames at 60 fps with a fixed run-label overlay
python tools/video_utils/frame2video_scene.py \
    data/mtmc/synthetic/scene_001/ \
    output/preview.mp4 \
    --end_frame 200 --fps 60 --label 'scene_001 preview'
```

### Threading speedup

`--workers` parallelises the per-master-frame `cv2.imread` + `cv2.resize` + label work across cameras using a `ThreadPoolExecutor` (cv2 releases the GIL during these operations, so threads actually run in parallel).  Measured on a 12-cam × 100-frame 1080p synthetic scene:

| `--workers` | Wall time | Throughput | Speedup |
|---|---|---|---|
| `1` (sequential) | 20.1s | 5.0 fps | 1.0× |
| `4` | 6.9s | 14.5 fps | 2.9× |
| `12` | 3.9s | 25.8 fps | 5.2× |
| `auto` (default) | 3.8s | 26.0 fps | 5.3× |

The video writer itself stays single-threaded (frames must land in order), so beyond `--workers ≈ N_cameras` you hit diminishing returns.

### Auto grid layout

When `--cols` is omitted, the column count is `ceil(sqrt(N))` where
N is the camera count — gives a near-square layout that lines up
well with 16:9 source frames:

| N cameras | Auto cols × rows |
|---|---|
| 2 | 2 × 1 |
| 4 | 2 × 2 |
| 6 | 3 × 2 |
| 9 | 3 × 3 |
| 12 | 4 × 3 |
| 16 | 4 × 4 |

Empty cells (when N isn't a perfect rectangle) are padded with
black tiles so the output has consistent dimensions.  Master frame
sequence is taken from the **first camera**'s sorted-name file
list; missing frames in other cameras at the same name → black tile
(synchronization preserved).

### Output sizing

`--target_height` (default 1080) sets the total grid height in
pixels.  Tile height = `target_height / n_rows`; tile width
preserves the source aspect ratio.  Output resolution =
`(n_cols × tile_w, n_rows × tile_h)`.

For 12 cameras of 1920×1080 in a 4×3 grid at `target_height=1080`:
each tile is 640×360, output is 2560×1080.  Pass `--target_height 0`
to keep tiles at full source resolution (output may be huge — e.g.
7680×3240 for the same scene).

### Arguments

| Argument | Required | Default | Description |
|---|---|---|---|
| `SCENE_DIR` (positional) | Yes | — | Scene root with per-camera subdirs |
| `OUTPUT` (positional) | Yes | — | Output video file path |
| `--frames_subdir` | No | `rgb` | Per-camera frame directory name (`<scene_dir>/<cam>/<frames_subdir>/`).  Pass `''` (empty) to look directly inside each `<cam>/` |
| `--cols` | No | auto | Grid columns; default = `ceil(sqrt(N))` |
| `--target_height` | No | `1080` | Output height in pixels.  `0` keeps source resolution per tile |
| `--fps` | No | `30.0` | Output frame rate (defaults to package `FPS`) |
| `--codec` | No | `mp4v` | FourCC codec |
| `--filename_pattern` (repeatable) | No | `*.jpg`, `*.jpeg`, `*.png` | Filename glob(s) for which frames to include |
| `--start_frame` | No | `0` | First master-list frame index to include |
| `--end_frame` | No | end | One-past-last master-list frame index |
| `--label` | No | — | Fixed text overlay on the FINAL composed video frame (top-left, large) |
| `--no_per_cam_label` | No | `false` | Suppress per-tile camera-name labels (default: each tile gets its camera name as a small top-left label) |
| `--overwrite` | No | `false` | Re-encode even when the output file already exists |
| `--workers` | No | auto | **Thread**-pool size for parallel per-camera tile decode + resize + label.  `cv2.imread` / `cv2.resize` release the GIL so threading actually accelerates the work.  Pass `1` to disable the pool entirely.  Auto ≈ Python's `ThreadPoolExecutor` default ≈ CPU count. |

### Status strings

Same set as [`frame2video.py`](#status-strings-2):

| Status | Meaning |
|---|---|
| `completed` | Grid video encoded successfully |
| `skipped` | Output file already exists (use `--overwrite` to force) |
| `no_frames_found` | First camera's directory has no matching frames |
| `read_error` | First camera's first frame couldn't be decoded |
| `write_error` | `cv2.VideoWriter` failed to open or write |

---

## Programmatic API

The CLIs cover the common single-input / single-output case.  For
multi-camera / parallel workflows, import the library helpers directly:

```python
from spatialai_data_utils.visualization.video_utils.video2frame import (
    video_to_frames, video_to_frames_batch, diagnose_video_file,
)
from spatialai_data_utils.visualization.video_utils.frame2video import (
    frames_to_video, frames_to_video_batch,
)
from spatialai_data_utils.visualization.video_utils.frame2video_grid import (
    frames_to_video_grid, auto_grid_cols,
)

# One video → frames, every 5th frame, capped at 200 frames
status = video_to_frames(
    "video.mp4", "frames/",
    frame_skip=5, end_frame=200, frame_pattern="frame_{:05d}.png",
)
print(status)   # 'completed' / 'skipped' / ...

# One frame dir → video at 60 fps
frames_to_video("frames/", "out.mp4", fps=60.0)

# Many cameras in parallel (process pool)
results = video_to_frames_batch(
    [("data/scene/Camera_01/video.mp4", "data/scene/Camera_01/images"),
     ("data/scene/Camera_02/video.mp4", "data/scene/Camera_02/images")],
    max_workers=4, frame_skip=1,
)

# Probe a problematic video without extracting any frames
print(diagnose_video_file("suspicious.mp4"))

# Multi-camera grid video (auto-grid layout, 1080p output, threaded)
status = frames_to_video_grid(
    [
        "data/scene/Camera_01/rgb",
        "data/scene/Camera_02/rgb",
        "data/scene/Camera_03/rgb",
        "data/scene/Camera_04/rgb",
    ],
    "data/scene/grid.mp4",
    cam_labels=["Camera_01", "Camera_02", "Camera_03", "Camera_04"],
    fps=30.0,
    target_height=1080,
    max_workers=4,                # parallel per-cam decode + resize + label
)
print(status)                    # 'completed' / 'skipped' / ...
print(auto_grid_cols(12))        # 4
```
