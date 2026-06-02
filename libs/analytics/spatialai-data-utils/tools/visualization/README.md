# Visualization Tools

This directory contains CLI tools for two workflows:
- projecting and drawing 3D bounding boxes on camera images, and
- rendering 3D camera placement/frustums from `calibration.json`.

The rendering logic lives in `spatialai_data_utils.visualization` and can be
used both from the command line and programmatically.
Camera group/map visualization helpers are exposed from
`spatialai_data_utils.visualization.camera_groups`; the older
`spatialai_data_utils.core.cameras.visualization` import path is kept only as a
compatibility shim.

## Tools Overview

| Tool | Purpose |
|------|---------|
| **`draw_camera_placement.py`** | Render dual-view camera placement from `calibration.json` (3D frustums + BEV coverage), with sequence PNG outputs |
| **`draw_3dbbox.py`** | Focused customer wrapper: render NVSchema model results (+ calibration JSON + image directory) to annotated images |
| **`draw_3dbbox_batch.py`** | General dispatcher: render NVSchema results, ground-truth JSON, **or** a sparse4d-style pkl |

Use `draw_3dbbox.py` for the common case of "I have NVSchema
results and want annotated images". Use `draw_3dbbox_batch.py` when you need
to pick between the three input formats (or are rendering GT).
Use `draw_camera_placement.py` when you need a clearer calibration sanity-check:
camera orientation in 3D plus BEV coverage/overlap in one artifact.

---

## draw_camera_placement.py

### Overview

Renders camera placement as a **dual-view** figure:
- left panel: 3D camera frustums (pose/orientation),
- right panel: BEV camera coverage polygons.

By default the CLI writes a **sequence of PNGs**:
- `all_cameras.png` for the full camera set,
- one image per calibration group under `groups/`.

This CLI intentionally reuses the package calibration loader
(`load_calib_into_dict` / `load_calib_into_dict_with_group_memberships`) and
existing FOV utilities. By default, the BEV panel uses frustum-derived
footprints; `--bev_source auto` can prefer `fieldOfViewPolygon` attributes.
The renderer writes the calibration path as a small figure footer and picks a
scene-aware 3D view angle unless `--elev` / `--azim` are specified.

### Quick Start

```bash
python tools/visualization/draw_camera_placement.py \
    --calib_path data/mtmc/Scene/calibration.json \
    --output_path output/camera_placement_seq

# Render a subset of cameras with map-backed BEV
python tools/visualization/draw_camera_placement.py \
    --calib_path data/mtmc/Scene/calibration.json \
    --sensor_ids Camera_01 Camera_02 Camera_03 \
    --map_file data/mtmc/Scene/map.png \
    --group_names bev-sensor-1 bev-sensor-2 \
    --draw_local_axes \
    --frustum_depth 4.0 \
    --output_path output/camera_placement_subset_seq

# Optional: prefer fieldOfViewPolygon attributes when present
python tools/visualization/draw_camera_placement.py \
    --calib_path data/mtmc/Scene/calibration.json \
    --bev_source auto \
    --output_path output/camera_placement_auto_seq

# Single-output mode (one dual-view image)
python tools/visualization/draw_camera_placement.py \
    --calib_path data/mtmc/Scene/calibration.json \
    --single_output \
    --output_path output/camera_placement.png

# Optional: open a matplotlib window for rotating/zooming the 3D panel
python tools/visualization/draw_camera_placement.py \
    --calib_path data/mtmc/Scene/calibration.json \
    --output_path output/camera_placement_seq \
    --interactive_3d
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--calib_path` | Yes | — | Path to calibration JSON file |
| `--output_path` | No | `camera_placement` | Output directory in sequence mode; output image path in `--single_output` mode |
| `--single_output` | No | `false` | Disable sequence mode and write one image |
| `--sensor_ids` | No | all cameras | Optional camera ids to render |
| `--group_names` | No | all groups | Optional calibration groups to include in sequence mode |
| `--frustum_depth` | No | `3.0` | Frustum depth in world units |
| `--draw_local_axes` | No | `false` | Draw local XYZ axes per camera |
| `--hide_labels` | No | `false` | Hide camera id labels |
| `--map_file` | No | auto | Optional map image used as BEV background. If omitted, the CLI auto-detects `<calibration_dir>/map.png` when available |
| `--map_mask_alpha` | No | `0.35` | Dark-mask opacity applied on top of the map background to improve BEV overlay readability (`0` disables mask) |
| `--bev_source` | No | `frustum` | BEV polygon source: `frustum` (generated default), `attributes`, or `auto` (attributes then frustum fallback) |
| `--height_range` | No | `1.0 3.0` | Height range for frustum-ground intersection fallback |
| `--max_camera_distance` | No | `20.0` | Max frustum distance for BEV fallback |
| `--recentering` | No | `false` | Apply group-origin recentering when loading calibration |
| `--title` | No | `Camera 3D Placement` | Plot title |
| `--elev` | No | auto | Elevation angle in degrees. If omitted, the renderer selects a scene-aware 3D view |
| `--azim` | No | auto | Azimuth angle in degrees. If omitted, the renderer selects a scene-aware 3D view |
| `--dpi` | No | `180` | Output image DPI |
| `--show` | No | `false` | Open interactive window while also saving image |
| `--interactive_3d` | No | `false` | Open a matplotlib GUI window so the 3D panel can be rotated/zoomed. Uses existing matplotlib only; requires a display-capable backend |

---

## draw_3dbbox.py

### Overview

Minimal NVSchema-only CLI that drives the stage-1 / stage-2 pipeline
one input row at a time.  Each line of the NVSchema JSON-lines file
carries its own `sensorId`, `timestamp`, and `id` (frame id); the CLI
streams the file, maps the row's `sensorId` to one or more concrete
target cameras (see below), resolves an image per target under
`<image_dir>/<cam_name>/`, projects that row's 3D boxes onto the
target camera(s), draws them, and writes annotated images directly
under `<output_data_path>/<cam_name>/` (no per-scene subdirectory).
Rows whose `sensorId` is the wrong kind for the chosen mode, and rows
whose image cannot be resolved, are skipped quietly (counts reported
at the end).  The batch-mode equivalent is
`draw_3dbbox_batch.py --nvschema_path …` (or the library-API
`spatialai_data_utils.visualization.visualize_nvschema`), which
takes a scene-oriented view: all cameras in the calibration are
rendered for every frame, and outputs are nested under
`<scene_name>/` above the camera folders.

#### `sensorId` resolution

The CLI runs in one of two modes — selected by `--ground_truth` —
and each mode interprets the row's `sensorId` in exactly one way (no
auto-detection / fallback chain):

* **Default / model-output mode** (`--ground_truth` *not* set):
  `sensorId` is expected to be a **BEV sensor group** name (e.g.
  `bev-sensor-1` from a grouped calibration's
  `sensors[*].group.name`).  The row is fanned out over every
  member camera of the group — one annotated image per member.
* **Ground-truth mode** (`--ground_truth`): `sensorId` is expected
  to be a **concrete camera** name (a key in the flattened
  `{cam: calib}` dict).  The row is projected onto that single
  camera.  Use this for ground-truth NVSchema exports where every
  annotation is already attributed to its observing camera.

Rows whose `sensorId` doesn't match the expected kind for the chosen
mode (or whose calibration entry is missing) are skipped quietly and
counted as `skipped_sensor_not_in_calib` in the end-of-run report.

### Quick Start

```bash
# Bare minimum
python tools/visualization/draw_3dbbox.py \
    --input_data_path  results/scene_001.jsonl \
    --output_data_path output/viz \
    --calib_path       data/mtmc/Scene/calibration.json \
    --image_dir        data/mtmc/Scene/images

# Recentered calibration + custom styling + warehouse class remapping
python tools/visualization/draw_3dbbox.py \
    --input_data_path  results/scene_001.jsonl \
    --output_data_path output/viz \
    --calib_path       data/mtmc/Scene/calibration_clustered.json \
    --image_dir        data/mtmc/Scene/images \
    --recentering \
    --line_thickness   3 \
    --object_class_tag   warehouse

# Ground-truth NVSchema (sensorId rows are concrete cameras, not BEV groups)
python tools/visualization/draw_3dbbox.py \
    --input_data_path  data/mtmc/Scene/ground_truth.jsonl \
    --output_data_path output/viz_gt \
    --calib_path       data/mtmc/Scene/calibration.json \
    --image_dir        data/mtmc/Scene/images \
    --ground_truth
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--input_data_path`  | Yes | — | Path to the NVSchema JSON-lines model-results file |
| `--output_data_path` | Yes | — | Directory where annotated images are written |
| `--calib_path`       | Yes | — | Path to the scene's calibration JSON file |
| `--image_dir`        | Yes | — | Directory with one sub-folder per **concrete** camera; per-row lookup is timestamp-first and then 9 canonical filesystem layouts shared with `draw_3dbbox_batch.py` (see below) |
| `--recentering`      | No  | `false` | Apply group-origin recentering to the calibration (use when predictions are in recentered coordinates) |
| `--line_thickness`   | No  | `2` | Wireframe line thickness in pixels |
| `--no_shade_heading` | No  | `false` | Disable the semi-transparent heading-face shading |
| `--object_class_tag`   | No  | `warehouse` | Object-class config name (built-in, e.g. `warehouse`, `default`, `scout`) or path to a `.py` config. Drives **two** behaviours: (1) **class filtering** — boxes whose `type` isn't recognised by the config are dropped before drawing, so reviewers see only classes the active taxonomy cares about; (2) **display-name remap** — kept boxes' `type` strings are translated to human-readable display names. Pass `none` to disable both (every box drawn, raw `type` shown) |
| `--color_by`         | No  | `class` | Auto-coloring mode: `class` (this CLI's default) walks `COLOR_MAP` in FIFO order — the first `type` seen in a frame claims slot 0, the next distinct `type` claims slot 1, etc. — so every Person/Pallet/Transporter renders uniformly and 2–5 classes get maximally-separated colours. `track_id` assigns one colour per NVSchema `id` (each tracked object gets its own wireframe colour — matching `draw_3dbbox_batch.py`'s default). Ignored when an explicit `color=` is passed to the library API |
| `--ground_truth`     | No  | `false` | Treat each row's `sensorId` as a **concrete camera** (single-target projection) instead of a **BEV sensor group** (the default, fan-out projection). Required for ground-truth NVSchema exports where every annotation is recorded against its observing camera |

#### `--image_dir` layout & per-(row, target) lookup

`--image_dir` is keyed by **concrete** camera — BEV-sensor-group
rows do not correspond to a filesystem folder; instead the CLI
resolves each member camera independently.  For every
`(row, target_camera)` pair the CLI calls
`spatialai_data_utils.datasets.frame_paths.resolve_frame_path`,
which both NVSchema CLIs share.  Lookup order:

1. **Timestamp substring match** (only when the row has a non-empty
   `timestamp`).  `<image_dir>/<target_camera>/` is scanned in
   sorted order and the first `.png` / `.jpg` / `.jpeg` file whose
   basename contains the row's timestamp string is returned — this
   lets datasets bake the timestamp into the filename without
   dictating the prefix:

   ```text
   <image_dir>/Camera_08/
     42_2025-04-14T00-36-45.009Z.jpg     # matches "2025-04-14T00-36-45.009Z"
     2025-04-14T00-36-45.009Z.png          # also matches (prefix form)
   ```

2. **Canonical-layout fallback** — the 9 historical filesystem
   patterns supported by `draw_3dbbox_batch.py`'s
   `get_frame_paths_of_multi_cameras`, tried in order:

   | # | Pattern | Example |
   |---|---|---|
   | 1 | `<cam>/images/<09d>.jpg`                | `Camera_08/images/000000006.jpg` |
   | 2 | `<cam>/rgb/rgb_<05d>.png`               | `Camera_08/rgb/rgb_00006.png`    |
   | 3 | `<cam>/rgb/rgb_<05d>.jpg`               | `Camera_08/rgb/rgb_00006.jpg`    |
   | 4 | `<cam>/rgb/<09d>.jpg`                   | `Camera_08/rgb/000000006.jpg`    |
   | 5 | `<cam>/image_<frame_id>.jpg` (scout)    | `Camera_08/image_6.jpg`          |
   | 6 | `frames/<cam>/images/<09d>.jpg`         | `frames/Camera_08/images/000000006.jpg` |
   | 7 | `<cam>/<frame_id>.jpg`                  | `Camera_08/6.jpg`                |
   | 8 | `<cam>/<frame_id>.png`                  | `Camera_08/6.png`                |
   | 9 | `<cam>/<frame_id>.jpeg`                 | `Camera_08/6.jpeg`               |

Rows whose row-specific image cannot be resolved (neither rule
fires, or the sensor sub-folder is missing) are skipped quietly and
counted in the final `skipped_image_not_found` tally.  Timestamps
are matched as **literal substrings** of the filename; if your
dataset uses a normalised form that differs from the NVSchema
`timestamp` field, rely on the canonical-layout fallback instead.

### Scope

One input row × one target camera → one rendered image.  In default
mode each row's BEV-group `sensorId` produces one output per member
camera; with `--ground_truth` each row's concrete-camera `sensorId`
produces exactly one output.  Rows that resolve to the same output
path (same concrete target camera + same image filename) overwrite
each other.  For scene-oriented batch jobs (every camera in the
calibration rendered for every frame), confidence filtering
(`--conf_thresh`), frame caps (`--n_frames`), H5-backed image sources
(`--h5_file`), or the gt_json_aicity / pkl input modes, use
`draw_3dbbox_batch.py --nvschema_path …` instead (or call
`spatialai_data_utils.visualization.visualize_nvschema` directly).

---

## draw_3dbbox_batch.py

### Overview

Projects 3D bounding boxes from world coordinates onto one or more camera
images using calibration data. Supports three input formats:

- **NVSchema** — model results as JSON-lines (`--nvschema_path`).
- **Ground truth JSON** — scene `ground_truth.json` (`--gt_json_aicity_path`).
- **Data pkl** — sparse4d-style pkl bundling calibration, per-frame image
  paths, and GT annotations (`--data_pkl`).

Exactly one of the three source arguments must be provided.

### Quick Start

```bash
# NVSchema model results with a calibration JSON
python tools/visualization/draw_3dbbox_batch.py \
    --nvschema_path results/scene_001.json \
    --calib_path    data/mtmc/Scene/calibration_clustered.json \
    --data_path     data/mtmc/Scene \
    --output_dir    output/viz \
    --recentering --h5_file

# Ground-truth JSON (calibration auto-detected from scene dir)
python tools/visualization/draw_3dbbox_batch.py \
    --gt_json_aicity_path data/mtmc/Scene/ground_truth.json \
    --data_path    data/mtmc/Scene \
    --output_dir   output/viz \
    --n_frames 50

# sparse4d-style pkl (calib + image paths + GT all bundled)
# No --calib_path, no --data_path, no --h5_file needed — the pkl
# carries all of that and H5 tuple paths are auto-detected.
python tools/visualization/draw_3dbbox_batch.py \
    --data_pkl   data/mtmc/anno_pkls/.../scene_infos_test.pkl \
    --output_dir output/viz

# Single camera
python tools/visualization/draw_3dbbox_batch.py \
    --nvschema_path results/scene_001.json \
    --calib_path    calibration.json \
    --data_path     data/mtmc/Scene \
    --output_dir    output/viz \
    --sensor_ids Camera_01
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--nvschema_path` | one of 3 | — | NVSchema JSON-lines model results |
| `--gt_json_aicity_path`  | one of 3 | — | Scene `ground_truth.json` file |
| `--data_pkl`      | one of 3 | — | sparse4d-style pkl (calib + image paths + GT) |
| `--output_dir`    | Yes | — | Directory where annotated images are written |
| `--data_path`     | for nvschema/gt_json_aicity | — | Scene root directory with per-camera images |
| `--calib_path`    | for nvschema (optional for gt_json_aicity) | — | Calibration JSON; must be omitted with `--data_pkl` |
| `--sensor_ids`    | No | all | Camera names to render (space-separated) |
| `--conf_thresh`   | No | `0.1` | Confidence threshold for filtering detections |
| `--n_frames`      | No | `-1` | Max frames to render (`-1` = all) |
| `--h5_file`       | No | `false` | Load images from `.h5` instead of loose JPGs/PNGs |
| `--recentering`   | No | `false` | Apply group-origin recentering to the calibration |
| `--calib_mode`    | No | `aic25` | Calibration format: `aic24` or `aic25` (gt_json_aicity mode) |
| `--object_class_tag` | No | `warehouse` | Object-class config name (built-in, e.g. `warehouse`, `default`, `scout`) or path to a `.py` config. Drives both **class filtering** (drops boxes whose `type` isn't in the config) and **display-name remap** for the kept boxes' labels. Pass `none` to disable both — every box drawn with its raw `type` |
| `--color_by`      | No | `track_id` | Auto-coloring mode: `track_id` (this CLI's default) assigns one colour per object `id` — matches the per-track convention used by gt_json_aicity / pkl workflows; `class` walks `COLOR_MAP` in FIFO order (first class seen → slot 0, next → slot 1, …) giving well-separated colours per type (matching the focused `draw_3dbbox.py`'s default) |

### Output Structure

Output images are organized by camera name under the visualization directory:

```text
output_dir/
  <scene_name>/
    Camera_01/
      <frame_basename_1>.jpg
      <frame_basename_2>.jpg
      ...
    Camera_02/
      ...
```

How `scene_name` is derived:

| Mode | Scene name |
|---|---|
| `--nvschema_path` | Stem of the nvschema file (e.g. `scene_001.json` → `scene_001`) |
| `--gt_json_aicity_path`  | Basename of `--data_path` (the scene root directory) |
| `--data_pkl`      | Stem of the pkl file |

Frame filenames match the underlying image layout:
- Loose JPG/PNG under a scene tree → typically `000000000.jpg`, `000000001.jpg`, ...
- H5 datasets addressed as `(h5_path, "rgb/rgb_00000.jpg")` → `rgb_00000.jpg`, ...

The renderer uses `os.path.basename(path[1])` for H5 tuple paths and
`os.path.basename(path)` for string paths, so the filenames follow the
input naming convention without remapping.

### Recentering

When a model is trained with recentered coordinates (group origin at 0, 0),
predictions live in a shifted coordinate frame. Pass `--recentering` with
`--calib_path` to shift the extrinsics so the group origin maps to (0, 0).

| Input mode                              | Use `--recentering`?                       |
|-----------------------------------------|--------------------------------------------|
| `--nvschema_path` (model results)       | **Yes** — when model was trained recentered |
| `draw_3dbbox.py` default mode (model)   | **Yes** — same rule as above                |
| `draw_3dbbox.py --ground_truth`         | **No** — GT NVSchema rows are world-frame   |
| `--gt_json_aicity_path` (scene `ground_truth.json`) | **No** — GT is world-frame             |
| `--data_pkl` (sparse4d pkl)             | **No** — pkl extrinsics already have recentering baked in |

The failure mode for the wrong choice is silent and visual: wireframes
project to the **wrong screen-space region** (typically drifting up off
the floor where scene objects actually sit) because the calibration
ends up in a frame the box coordinates don't live in.  Always
double-check the `recentering` flag matches the source's frame
convention before reading anything into the rendered output.

### Security: `--data_pkl` uses pickle

> **Warning**: The `--data_pkl` mode loads the file via Python's
> [`pickle.load`](https://docs.python.org/3/library/pickle.html#pickle.load),
> which can execute arbitrary code on malicious input
> ([CWE-502](https://cwe.mitre.org/data/definitions/502.html)).
> **Only pass `.pkl` files from trusted sources.**  If you're consuming pkl
> files produced elsewhere (e.g. a shared training pipeline), verify the
> SHA-256 / provenance of the file before running this script.  A future
> release will migrate this format off pickle and onto a safer container
> (JSON / HDF5 / protobuf) — the migration TODOs are noted next to the
> `pickle.load` calls in
> [`spatialai_data_utils/loaders/calibration.py`](../../spatialai_data_utils/loaders/calibration.py)
> and
> [`spatialai_data_utils/loaders/ground_truth.py`](../../spatialai_data_utils/loaders/ground_truth.py).

---

## Library API

All rendering functions are available for programmatic use.

### High-level: `visualize_nvschema` (customer-facing one-shot)

```python
from spatialai_data_utils.visualization import visualize_nvschema

visualize_nvschema(
    nvschema_path="results/scene_001.json",
    calib_path="data/mtmc/scene_001/calibration.json",
    data_path="data/mtmc/scene_001",
    output_dir="output/viz",
    sensor_ids=["Camera_01", "Camera_02"],   # None = all cameras
    conf_thresh=0.1,
    n_frames=50,
    h5_file=False,
    recentering=False,
    object_class_tag="warehouse",
)
```

### High-level: `visualize_3dbbox` (general dispatcher)

```python
from spatialai_data_utils.visualization import visualize_3dbbox

# NVSchema model results
visualize_3dbbox(
    output_dir="output/viz",
    nvschema_path="results/scene_001.json",
    calib_path="data/mtmc/Scene/calibration.json",
    data_path="data/mtmc/Scene",
    sensor_ids=["Camera_01"],
)

# Ground truth
visualize_3dbbox(
    output_dir="output/viz",
    gt_json_aicity_path="data/mtmc/Scene/ground_truth.json",
    data_path="data/mtmc/Scene",
)

# sparse4d-style pkl (calib + image paths + GT all in one file)
visualize_3dbbox(
    output_dir="output/viz",
    data_pkl="data/mtmc/anno_pkls/scene_infos_test.pkl",
)
```

### Mid-level: `process_scene` / `process_frame_*`

For finer control over the rendering loop (custom result iteration, custom
calibration loading, etc.).

```python
from spatialai_data_utils.visualization.render import (
    process_scene,
    process_frame_nvschema,
    process_frame_gt_json_aicity,
)
```

### Stage-1 / Stage-2 helpers

Split the projection step and the drawing step so you can inspect or persist
the projected 2D corners:

```python
from spatialai_data_utils.core.geometry.projection import (
    project_bev_objects_bbox_in_image,
)
from spatialai_data_utils.visualization.render import draw_bev_objects_bbox_in_image

# Stage 1: project BEV boxes to image space (populates bbox3d.info on each det).
enriched = project_bev_objects_bbox_in_image(
    sensor_id="Camera_01",
    calib_dict=calib_dict,            # {cam_name: {"intrinsic_matrix", "w2c_matrix"}}
    bev_objects=nvschema_objects,       # list of raw NVSchema object dicts
)

# Stage 2: draw the enriched BEV objects on an image.
annotated = draw_bev_objects_bbox_in_image(
    bev_objects=enriched,
    image=img_path_or_array,
    sensor_id="Camera_01",            # filter which projection entry to draw
    thickness=2,
    shade_heading=True,
    draw_text_labels=True,
    object_class_tag="warehouse",
)
```

### Low-level: drawing primitives

Direct drawing primitives that take a numpy image, 3D boxes, and a projection
matrix, and return the annotated image.

```python
from spatialai_data_utils.visualization.box_3d import (
    draw_bbox3d_on_img,
    draw_bbox3d_on_bev,
    draw_bbox3d_multicam,
    box3d_to_corners,
)
import numpy as np

boxes = np.array([[x, y, z, w, l, h, yaw]])
image = cv2.imread("frame.jpg")

# Option A: pass a 4x4 world-to-image matrix
result = draw_bbox3d_on_img(boxes, image, world2img=world2img_matrix)

# Option B: pass a single-camera calib_info dict
result = draw_bbox3d_on_img(boxes, image, calib_info={
    "intrinsic_matrix": intrinsic_3x3,
    "w2c_matrix": extrinsic_4x4,
})

# BEV visualization
bev = draw_bbox3d_on_bev(boxes, bev_size=800, bev_range=100)
```

### Utility functions

```python
from spatialai_data_utils.visualization.draw_utils import (
    build_world2img_from_calib,  # calib_dict + cam_name -> 4x4 matrix
    draw_camera_tag,             # stamp camera name on image
    generate_bbox_text,          # build per-box label strings
    load_image,                  # load JPEG/PNG or H5
    save_viz,                    # save annotated image to disk
)
```

---

## Module Structure

```text
spatialai_data_utils/visualization/
    __init__.py       # COLOR_MAP + re-exports
    box_3d.py         # Low-level 3D box drawing (corners, projection, BEV)
    draw_utils.py     # Shared I/O and drawing helpers
    render.py         # High-level pipeline (visualize_nvschema, visualize_3dbbox, ...)
    box_2d.py         # 2D bounding box drawing
    points.py         # 3D vertex / keypoint visualization

spatialai_data_utils/core/geometry/
    projection.py     # Pure-numpy stage-1 projection (project_bev_objects_bbox_in_image)

spatialai_data_utils/loaders/
    nvschema.py       # load_nvschema (JSON-lines model results)
    ground_truth.py   # load_det_3d_from_gt_scene, load_gt_from_pkl
    calibration.py    # load_calib, load_calib_into_dict_from_pkl, load_calib_into_dict

tools/visualization/
    draw_3dbbox.py          # NVSchema-only CLI; inlines stage-1 + stage-2 in main
    draw_3dbbox_batch.py    # CLI wrapper around visualize_3dbbox (3-mode dispatcher)
```

## 3D Box Format

`box3d_to_corners` and `project_boxes_3d_to_2d` require boxes in the
canonical 9-DoF NVSchema `bbox3d.coordinates` layout (shape `(N, 9+)`):

- **9-DoF** `[x, y, z, w, l, h, pitch, roll, yaw]` — full
  `R = R_z(yaw) · R_y(roll) · R_x(pitch)` rotation; ZYX-intrinsic
  convention, matching `euler_from_quaternion`.  Extra trailing
  columns beyond index 8 (e.g. velocity) are ignored.  Names follow
  the body-frame convention used elsewhere in this codebase
  (heading along world `-Y`), so `pitch` rotates about world X (the
  lateral axis), `roll` about world Y (the longitudinal axis), and
  `yaw` about world Z.

| Field | Description |
|-------|-------------|
| `x, y, z` | Center position in world coordinates |
| `w` | Width (X-axis extent) |
| `l` | Length (Y-axis extent) |
| `h` | Height (Z-axis extent) |
| `pitch` | Rotation around X-axis in radians (lateral axis) |
| `roll`  | Rotation around Y-axis in radians (longitudinal / heading axis) |
| `yaw`   | Rotation around Z-axis in radians (vertical axis) |

Legacy 7-DoF `[x, y, z, w, l, h, yaw]` arrays are no longer accepted:
`box3d_to_corners` raises `ValueError` if `shape[-1] < 9`.  Callers
that still hold yaw-only data must pad pitch and roll with zeros
before the call, e.g. `np.insert(box7, 6, 0.0, axis=-1)` twice (the
sparse4d pkl loader already does this internally via
`_gt_box_to_nvschema_obj`).

Raw NVSchema's `bbox3d.coordinates` is validated by
`check_nvschema_coords_len` (length must be `>= 9`; trailing extras
beyond index 8, e.g. velocity components, are permitted and ignored
downstream) and passed through unchanged.

Corner ordering (from `box3d_to_corners`, indices into the `(N, 8, 3)` output):
- Bottom face (z = center - h/2): indices 0, 3, 4, 7
- Top face    (z = center + h/2): indices 1, 2, 5, 6

## Calibration Format

The calibration dictionary (per camera) uses these keys:

| Key | Shape | Description |
|-----|-------|-------------|
| `"intrinsic_matrix"` | 3x3 | Camera intrinsic matrix K |
| `"w2c_matrix"` | 4x4 | World-to-camera extrinsic matrix |
| `"w2p_matrix"` | 4x4 | World-to-pixel (K @ w2c), optional |

Loaded via `spatialai_data_utils.loaders.calibration.load_calib()`.
