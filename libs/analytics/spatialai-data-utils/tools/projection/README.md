# 3D to 2D Projection Tools

CLI tools that project 3D bounding boxes from world coordinates to 2D
image-space coordinates for a specific camera, writing the result back
out as structured data. The actual projection math lives in the
`spatialai_data_utils.core.geometry.projection` package — these tools
are thin wrappers that do I/O and argument parsing.

Unlike the scripts under `tools/visualization/`, nothing here *draws*
anything. These tools compute and persist 2D corner coordinates so
they can be consumed downstream (2D IoU, evaluation, external
visualizers, etc.) without re-running projection each time.

## Tools Overview

| Tool | Purpose |
|------|---------|
| **`project_bbox3d_to_2d.py`** | Stream an NVSchema JSON-lines file through stage-1 projection onto a target camera; emit an NVSchema file where each visible object's native `bbox3d.info` map (proto `map<string, string>`) carries `sensorId` + `vertices` (the 8 projected corners, JSON-encoded). Invisible boxes are dropped. |

---

## project_bbox3d_to_2d.py

### Overview

Line-by-line transform of an NVSchema JSON-lines file. For every input
frame:

1. Read the world-space objects (each with `bbox3d.coordinates`).
2. Project the 8 cuboid corners onto a target camera's image plane.
3. Keep only boxes whose corners are in front of the camera **and**
   have at least one corner inside the image.
4. Populate each surviving object's native `bbox3d.info` map (the
   `map<string, string>` field on the `Bbox3d` proto) with the target
   `sensorId` and a `json.dumps`-encoded list of the 8 projected
   corners under key `vertices`. Any pre-existing `info` entries are
   preserved.
5. Write the frame back out with the same top-level fields but the
   filtered/enriched `objects` list.

The tool is a streaming I/O wrapper around
`spatialai_data_utils.core.geometry.projection.project_bev_objects_bbox_in_image`
— it uses no extra projection logic of its own.

### Quick Start

```bash
# Project every input line onto Camera_01
python tools/projection/project_bbox3d_to_2d.py \
    --sensor_id     Camera_01 \
    --calib_path    data/mtmc/Scene/calibration.json \
    --nvschema_path data/mtmc/Scene/ground_truth_nvschema.json \
    --output_path   /tmp/projected_Camera_01.jsonl \
    --image_size   1920 1080
```

### Arguments

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `--sensor_id` | Yes | — | Target camera to project onto; must exist in the calibration JSON |
| `--calib_path` | Yes | — | Path to the scene's calibration JSON file |
| `--nvschema_path` | Yes | — | Input NVSchema JSON-lines file |
| `--output_path` | Yes | — | Output NVSchema JSON-lines file (created / overwritten) |
| `--image_size` | No | `(1920, 1080)` | `WIDTH HEIGHT` used for the in-image visibility check |
| `--origin` | No | `0.5 0.5 0.5` | Box origin in `(w, l, h)` fractions (geometric centre by default) |
| `--recentering` | No | `false` | Apply group-origin recentering to the calibration before projecting |

### Input / Output

**Input** — any NVSchema JSON-lines file with the standard shape:

```json
{"id": "0", "sensorId": "Camera", "objects": [
  {
    "id": "528", "type": "Transporter", "confidence": 1.0,
    "bbox3d": {"coordinates": [x, y, z, w, l, h, pitch, roll, yaw], ...},
    ...
  },
  ...
]}
```

Each object must carry `bbox3d.coordinates` as the 9-value
`[x, y, z, w, l, h, pitch, roll, yaw]` form prescribed by the
NVSchema `Bbox3d` proto. Missing or wrong-length coordinates raise
a clear `ValueError` rather than silently dropping the object.
Legacy 7-value inputs `[x, y, z, w, l, h, yaw]` are no longer
accepted — such datasets must be re-exported to the 9-value form
before consumption.

**Output** — the same JSON-lines shape, with:

- Invisible objects dropped.
- Each visible object's existing `bbox3d` block augmented with a
  populated `info` map (the proto-native `Bbox3d.info`
  `map<string, string>`):

  ```json
  "bbox3d": {
    "coordinates": [x, y, z, w, l, h, pitch, roll, yaw],
    "embedding":   [...],
    "confidence":  1.0,
    "info": {
      "sensorId": "Camera_01",
      "vertices": "[[x0, y0], [x1, y1], [x2, y2], [x3, y3], [x4, y4], [x5, y5], [x6, y6], [x7, y7]]"
    }
  }
  ```

`info` is a `map<string, string>` per the NVSchema proto. `sensorId`
mirrors the top-level NVSchema frame's camelCase key and is a plain
string; `vertices` is a `json.dumps`-encoded `(8, 2)` corner array
(the 2D projection of the 3D cuboid's corners; no `bbox3d_` prefix
needed since it's already inside `bbox3d.info`). Consumers must
`json.loads` `info.vertices` before numeric use.

Any keys already present in `bbox3d.info` on the input are preserved —
the tool only writes/overwrites the two keys listed above.  Each input
row already carries a single observing camera and the tool projects
onto a single target camera, so every enriched object ends up with
exactly one projection. See
`spatialai_data_utils.core.geometry.projection.project_bev_objects_bbox_in_image`
for details.

### Output shape mirrors the input

The tool is a pure line-by-line transform: for every input line it
writes exactly one output line with the same `"id"` and `"sensorId"`
fields and the filtered + enriched `"objects"` list.

NVSchema files typically record the same world-space objects once
per observing camera in a BEV sensor group — so a given frame has one
input line per observing camera, and objects visible from several
cameras appear in multiple of those lines with **identical world-space
`bbox3d.coordinates`**. The tool projects them onto the target camera
in every line they appear in, which means the same visible box can be
written to several output lines of the same frame. Downstream
consumers that don't want that cross-row redundancy can deduplicate
by object `"id"` within each frame — all duplicates carry the same
world-space coordinates, so picking any one row per object is safe.

### Streaming behavior

The tool reads and writes one line at a time. A 108 000-line /
~250 MB input processes in ~1 minute with ~constant memory.

### Library API

If you just want the projection step from Python (no JSON-lines I/O),
call the underlying helper directly:

```python
from spatialai_data_utils.core.geometry.projection import project_bev_objects_bbox_in_image

enriched = project_bev_objects_bbox_in_image(
    sensor_id="Camera_01",
    calib_dict=calib_dict,                # {cam_name: {"intrinsic_matrix", "w2c_matrix"}}
    bev_objects=nvschema_object_list,       # list of raw NVSchema object dicts
    image_size=(1920, 1080),              # optional
    origin=(0.5, 0.5, 0.5),               # optional
)
```

`enriched` is a filtered copy of the input list where each object is
decorated with the `bbox3d.info` projection metadata exactly as the
CLI writes it.

---

## Module Structure

```text
spatialai_data_utils/core/geometry/
    projection.py     # project_bev_objects_bbox_in_image, project_boxes_3d_to_2d,
                      # project_points_3d_to_image, vertices_to_aabb

tools/projection/
    project_bbox3d_to_2d.py   # This CLI (thin wrapper)
```

## Related Tools

Once you have projected corners, you might want to **draw** them on
the actual camera images — that's what the scripts under
`tools/visualization/` are for:

- `tools/visualization/draw_3dbbox.py` — customer wrapper
  for NVSchema → annotated images.
- `tools/visualization/draw_3dbbox_batch.py` — general dispatcher
  (NVSchema / gt_json_aicity / sparse4d pkl → annotated images).
