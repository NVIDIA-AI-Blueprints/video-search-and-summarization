# Camera Grouping Tools

This directory contains command-line tools for camera grouping, clustering, and BEV (Bird's Eye View) origin calculation for multi-camera tracking systems.

Camera grouping visualizations use the canonical library module
`spatialai_data_utils.visualization.camera_groups`. The historical
`spatialai_data_utils.core.cameras.visualization` path remains available as a
compatibility shim and emits a deprecation warning.

## 📁 Tools Overview

| Tool | Status | Purpose |
|------|--------|---------|
| **`calculate_origin.py`** | ✅ Production Ready | Calculate/update group origins and dimensions |
| **`create_camera_clusters.py`** | ✅ Production Ready | Partition cameras into spatially compact clusters (deployment / inference) |
| **`create_camera_groups.py`** | ✅ Production Ready | Create camera groups with duplication support (training / finetuning) |
| **`reassign_camera_groups.py`** | ✅ Production Ready | Move specific cameras into existing BEV groups, then recompute origins |
| **`find_suggested_cluster_params.py`** | ✅ Helper | Grid-search clustering thresholds / seed index (also auto-invoked by `create_camera_clusters.py`) |
| **`batch_create_camera_groups.sh`** | ✅ Helper | Interactive batch wrapper that runs `create_camera_groups.py` across many scenes |

The three primary tools each have a detailed section below; the helper tools are summarized in [Other Tools](#other-tools).

---

# ✅ calculate_origin.py

## Overview

The `calculate_origin.py` tool calculates Bird's Eye View (BEV) group origins and dimensions for camera groups. It processes calibration files and updates them with precise group metadata needed for multi-camera tracking systems.

## Key Features

✅ **Single-Group Fallback**: If calibration lacks groups, assigns all sensors to one group (`bev-sensor-1`) via `--n-sensor-groups` — run a clustering/grouping tool first for real multi-group splits
✅ **Flexible FOV Calculation**: Supports both attribute-based and frustum-based FOV generation  
✅ **Sensor Filtering**: Process only specific cameras  
✅ **Visualization**: Generate visual maps of camera groups  
✅ **Multiple Output Options**: Create new file, overwrite, or custom output path

## Quick Start

```bash
# With existing groups - calculate origins
python tools/camera_grouping/calculate_origin.py data/scene/calibration_grouped.json

# Without groups - automatic grouping + origins
python tools/camera_grouping/calculate_origin.py \
    data/scene/calibration.json \
    --map_file data/scene/Top.png
```

## When to Use This Tool

### Scenario 1: Calculate Origins for Existing Groups
You have a calibration file that already contains camera groups, and you need to add/update origin and dimension metadata.

### Scenario 2: Raw Calibration Without Groups
You have a raw calibration file without groups. With `--n-sensor-groups 1`
(the default) the tool will:
1. Assign all sensors to a single group (`bev-sensor-1`)
2. Calculate origins and dimensions
3. Save the complete calibration

For real multi-group splits, run `create_camera_clusters.py` or
`create_camera_groups.py` first.

### Scenario 3: Frustum-Based FOV
Your calibration file doesn't have `fieldOfViewPolygon` attributes. The tool will calculate FOV from camera intrinsic/extrinsic matrices.

## Command-Line Arguments

### Required Arguments

| Argument | Description |
|----------|-------------|
| `input_calibration` | Path to a calibration JSON file **or** a dataset folder containing `calibration.json` (a sibling `Top.png` is auto-detected for visualization) |

### Output Configuration

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `-o, --output` | str | `{input}_with_origins.json` | Output calibration file path |
| `--overwrite` | flag | False | Overwrite the input file (exclusive with `--output`) |

### FOV Calculation

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--prefer-existing-fov` | flag | False | Use FOV from attributes (vs frustum calculation) |
| `--height-range MIN MAX` | float float | 1.0 3.0 | Height range for ground plane intersection (meters) |
| `--dilation` | float | 1.0 | Dilation distance for group bounds (meters) |
| `--scene-bounds MIN_X MIN_Y MAX_X MAX_Y` | float×4 | None | Clip frustum polygons to scene bounds (meters) |
| `--max-camera-distance` | float | 30.0 | Maximum distance from camera to constrain frustum (meters) |

### Automatic Grouping (only when the input has no `group` field)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--n-sensor-groups` | int | 1 | Groups to create when the input is ungrouped. `1` puts every sensor in `bev-sensor-1`; `>1` is a placeholder for a future clustering algorithm (run `create_camera_clusters.py` first for real multi-group splits) |
| `--max-sensors-per-group` | int | None | Upper bound on sensors per group (reserved for future grouping functionality) |

### Filtering & Visualization

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--sensor-names` | str | None | Process only specified sensors (comma-separated) |
| `--map_file` | str | None | Path to map image for visualization |
| `--visualize` | flag | False | Generate group visualization (falls back to a black background if `--map_file` is omitted) |
| `--vis_separate_images` | flag | False | Generate separate images per group instead of combined (default: combined) |

## Usage Examples

### Example 1: Basic Origin Calculation (With Groups)

```bash
# Input: calibration_grouped.json (already has groups)
# Output: calibration_grouped_with_origins.json

python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json
```

**What happens:**
1. ✅ Loads calibration with existing groups
2. ✅ Calculates origin and dimensions for each group
3. ✅ Saves updated calibration

### Example 2: Single-Group Fallback (Without Groups)

```bash
# Input: calibration.json (NO groups)
# Output: calibration_with_origins.json (WITH a single group + origins)

python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration.json \
    --map_file data/mtmc/scene_001/Top.png
```

**What happens:**
1. ✅ Detects missing groups
2. ✅ Assigns all sensors to one group (`bev-sensor-1`, per `--n-sensor-groups 1`)
3. ✅ Calculates origins and dimensions
4. ✅ Saves complete calibration

### Example 3: Use Existing FOV Polygons

```bash
# Prefer FOV polygons from calibration attributes
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --prefer-existing-fov
```

### Example 4: Custom Height Range

```bash
# Use different height range for ground plane intersection
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --height-range 0.5 2.5
```

### Example 5: Process Specific Sensors Only

```bash
# Calculate origins for only Camera_01, Camera_02, Camera_03
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --sensor-names Camera_01,Camera_02,Camera_03
```

### Example 6: Custom Output File

```bash
# Specify exact output path
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --output data/mtmc/scene_001/calibration_final.json
```

### Example 7: Overwrite Original

```bash
# Update the input file in-place
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --overwrite
```

### Example 8: With Visualization

```bash
# Generate visual map showing camera groups
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --map_file data/mtmc/scene_001/Top.png \
    --visualize
```

**Output:**
- `calibration_grouped_with_origins.json` (calibration data)
- `calibration_grouped_with_origins_map.png` (visualization)

### Example 9: Constrain FOV with Scene Bounds

```bash
# Clip frustum polygons to a specific region
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --scene-bounds -30 -40 30 40 \
    --max-camera-distance 25.0
```

**What happens:**
1. ✅ Frustum polygons clipped to 60m×80m rectangle
2. ✅ Each camera's FOV limited to 25m from camera center
3. ✅ More focused and accurate group origins

### Example 10: Long-Range Outdoor Setup

```bash
# Configure for large outdoor area with distant cameras
python tools/camera_grouping/calculate_origin.py \
    data/parking_lot/calibration.json \
    --max-camera-distance 50.0 \
    --scene-bounds -100 -100 100 100 \
    --height-range 1.0 2.0
```

### Example 11: Complete Workflow

```bash
# Combine multiple options for production use
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_grouped.json \
    --output data/mtmc/scene_001/calibration_final.json \
    --dilation 1.5 \
    --height-range 1.0 3.0 \
    --scene-bounds -50 -50 50 50 \
    --max-camera-distance 30.0 \
    --map_file data/mtmc/scene_001/Top.png \
    --visualize
```

## Input/Output Format

### Input: Calibration File

**With Groups:**
```json
{
  "sensors": [
    {
      "id": "Camera_01",
      "group": {
        "name": "bev-sensor-1",
        "origin": [0, 0],           ← Will be updated
        "dimensions": [0, 0, 0, 0]  ← Will be updated
      },
      "intrinsicMatrix": [...],
      "extrinsicMatrix": [...],
      "attributes": [...]
    }
  ]
}
```

**Without Groups:**
```json
{
  "sensors": [
    {
      "id": "Camera_01",
      // No "group" field ← Will run grouping automatically!
      "intrinsicMatrix": [...],
      "extrinsicMatrix": [...],
      "attributes": [...]
    }
  ]
}
```

### Output: Updated Calibration

```json
{
  "sensors": [
    {
      "id": "Camera_01",
      "group": {
        "name": "bev-sensor-1",
        "alias": "area-1",
        "type": "bev",
        "origin": [5.23, -12.45],        ← Calculated!
        "dimensions": [-2.1, -18.5, 12.6, -6.4]  ← Calculated!
      },
      ...
    }
  ]
}
```

**Origin**: `[center_x, center_y]` - Centroid of the group's FOV union  
**Dimensions**: `[x_min, y_min, x_max, y_max]` - Bounding box of the group's FOV union

## Workflow Diagrams

### Workflow 1: With Existing Groups

```text
Input: calibration_grouped.json
  ↓
[Load calibration]
  ↓
[Groups exist?] → YES
  ↓
[Calculate origins from FOV]
  ↓
[Update group metadata]
  ↓
Output: calibration_grouped_with_origins.json
```

### Workflow 2: Without Groups (Single-Group Fallback)

```text
Input: calibration.json (no groups)
  ↓
[Load calibration]
  ↓
[Groups exist?] → NO
  ↓
[--n-sensor-groups == 1? (default)] → YES
  ├─ Assign all sensors to bev-sensor-1
  └─ Calculate origins / dimensions
  ↓
Output: calibration_with_origins.json (single group)
```

For real multi-group assignments, run `create_camera_clusters.py` or
`create_camera_groups.py` before this tool.

## FOV Calculation Methods

### Method 1: Frustum-Based (Default)

Calculates FOV from camera intrinsic/extrinsic matrices by intersecting camera frustum with ground planes.

**Advantages:**
- ✅ Works without `fieldOfViewPolygon` attributes
- ✅ Dynamically adapts to camera parameters
- ✅ Accurate for varying camera heights

### Method 2: Attribute-Based (`--prefer-existing-fov`)

Uses existing `fieldOfViewPolygon` from calibration attributes, falls back to frustum if missing.

**Advantages:**
- ✅ Uses pre-computed FOV polygons
- ✅ Faster processing
- ✅ Consistent with calibration data

## Parameter Details

### `--dilation` (Meters)

Controls how much to expand the FOV polygons when calculating group bounds.

- **Smaller (0.5-1.0)**: Tighter bounds, closer to actual FOV
- **Larger (1.5-3.0)**: More generous bounds, includes buffer zone
- **Default**: 1.0 meters

### `--height-range` (Meters)

Defines the height range for ground plane intersection when using frustum-based FOV.

- **Format**: `MIN MAX` (two float values)
- **Default**: `1.0 3.0` (1 to 3 meters above ground)
- **Use case**: Adjust to typical object heights — e.g. `0.5 2.0` for low objects (packages, small vehicles), `1.0 2.0` for human tracking, `1.0 4.0` for tall objects (trucks, buildings)

### `--prefer-existing-fov`

- **Without the flag (default)**: FOV is computed from each camera's frustum (intrinsic/extrinsic matrices).
- **With the flag**: prefer `fieldOfViewPolygon` from the calibration attributes, falling back to frustum if missing.

### `--scene-bounds` (Meters)

Clips frustum polygons to a rectangular boundary in world coordinates. Useful for constraining FOV calculations to a specific area of interest.

- **Format**: `MIN_X MIN_Y MAX_X MAX_Y` (four float values)
- **Default**: None (no clipping)
- **Use case**: Limit FOV to a specific room, zone, or region

**When to use:**
- 🎯 Focus on a specific region (e.g., warehouse floor, parking lot)
- 🚫 Exclude irrelevant areas (e.g., hallways, external areas)
- ⚡ Speed up computation by reducing polygon complexity
- 🎨 Improve visualization by removing out-of-bounds FOV

### `--max-camera-distance` (Meters)

Constrains frustum polygon generation to a maximum distance from each camera center. Prevents frustums from extending infinitely and focuses on the effective coverage area.

- **Default**: 30.0 meters
- **Range**: Typically 10-100 meters depending on scene scale
- **Use case**: Match camera's effective detection range

**Effect on FOV:**
```text
Camera Position: (0, 0, 5m height)
Without constraint: Frustum extends until scene bounds
With --max-camera-distance 30.0: Frustum limited to 30m radius from camera
```

**When to adjust:**
- 📏 Match your scene scale (small room vs large warehouse)
- 🎯 Match camera specifications (detection range, resolution limits)
- ⚡ Reduce computation time for large scenes
- 🎨 Improve visualization by avoiding overly large polygons

## Output Explanation

### Origin Calculation

The **origin** is the centroid (geometric center) of the group's FOV union:

```python
origin = [center_x, center_y]
```

### Dimensions Calculation

The **dimensions** define the bounding box of the group's FOV union:

```python
dimensions = [x_min, y_min, x_max, y_max]
```

**Example:**
```text
Group FOV Union (top-down view):
  ┌─────────────┐
  │             │
  │      •      │ ← Origin: [5.0, -10.0]
  │             │
  └─────────────┘
  
Dimensions: [-2.0, -18.0, 12.0, -2.0]
           │      │      │      │
           x_min  y_min  x_max  y_max
```

## Real-World Example

### Synthetic Warehouse Scene

```bash
# Process the scene calibration
python tools/camera_grouping/calculate_origin.py \
    data/mtmc/scene_001/calibration_buffer_zone_c4.json \
    --output data/mtmc/scene_001/calibration_with_origins.json \
    --dilation 1.0 \
    --prefer-existing-fov \
    --map_file data/mtmc/scene_001/Top.png \
    --visualize
```

**Output:**
```text
2025-11-17 10:30:15 - INFO - Loading calibration data from: calibration_buffer_zone_c4.json
2025-11-17 10:30:15 - INFO - Loaded 4 sensors
2025-11-17 10:30:15 - INFO - ================================================================================
2025-11-17 10:30:15 - INFO - Calculating BEV Group Origins and Dimensions
2025-11-17 10:30:15 - INFO - ================================================================================
2025-11-17 10:30:15 - INFO - Found 1 groups in calibration file
2025-11-17 10:30:15 - INFO - Calculating origin for bev-sensor-1 (4 cameras)...
2025-11-17 10:30:15 - INFO -   Origin: [-0.42, -12.23]
2025-11-17 10:30:15 - INFO -   Dimensions: [-10.56, -20.56, 9.30, -3.13]
2025-11-17 10:30:15 - INFO - ✓ Successfully saved calibration file
2025-11-17 10:30:15 - INFO - ✓ Successfully generated visualization
2025-11-17 10:30:15 - INFO - ✓ Completed!
```

## Troubleshooting

### Issue 1: Camera center very low warning

```text
WARNING: Camera center is very low (z=-3.33m), frustum may not intersect ground planes
```

**Solutions:**
- Adjust `--height-range` to include lower heights
- Use `--prefer-existing-fov` if FOV polygons are available
- Check camera extrinsic matrix calibration

### Issue 2: No groups found / input has no `group` field

```text
WARNING: No valid groups found for any sensors in the group
```

`calculate_origin.py` does not run an FOV-overlap grouping search — when the
input is ungrouped it falls back to `--n-sensor-groups` (default `1`, i.e. all
sensors in `bev-sensor-1`).

**Solutions:**
- Run `create_camera_clusters.py` or `create_camera_groups.py` first to produce real multi-group assignments
- Verify camera calibration data (intrinsics/extrinsics) is correct
- Increase `--dilation` to expand FOV polygons before the union

### Issue 3: Invalid sensor names

```text
ERROR: The following sensor names do not exist: ['InvalidCamera']
```

**Solution:**
Check available sensors in calibration file and use exact names.

### Issue 4: Slow processing

**Solutions:**
- Use `--prefer-existing-fov` if polygons are available (faster than frustum)
- Process fewer sensors with `--sensor-names`
- Narrow `--scene-bounds` or lower `--max-camera-distance` to shrink frustum polygons

### Issue 5: Visualization looks empty / black

`--visualize` does **not** require `--map_file`; without it the groups are drawn
on a black background. Pass a map image for an overhead reference:

```bash
--map_file data/scene/Top.png --visualize
```

## Technical Details

### Origin Calculation Algorithm

```python
For each group:
  1. polygons = [get_fov_polygon(sensor) for sensor in group]
  2. buffered = [poly.buffer(dilation) for poly in polygons]
  3. union = unary_union(buffered)
  4. origin = [union.centroid.x, union.centroid.y]
  5. dimensions = [union.bounds.minx, union.bounds.miny, 
                   union.bounds.maxx, union.bounds.maxy]
```

### Frustum Calculation

When using frustum-based FOV (default):

1. Get camera intrinsic matrix (K)
2. Get camera extrinsic matrix (RT)
3. Define image corners in pixel space
4. Project rays through camera center
5. Intersect rays with ground planes at `height_range`
6. Constrain to `max_camera_distance` from camera center (default: 30m)
7. Create polygon from intersection points
8. Clip to `scene_bounds` if specified

### FOV Polygon Dilation

The `--dilation` parameter controls boundary expansion:

1. Extract/calculate FOV polygon for each camera
2. Dilate polygon by `dilation` meters: `polygon.buffer(dilation)`
3. Union all dilated polygons in a group
4. Calculate centroid and bounding box

## Performance Notes

| Input Size | Processing Time |
|------------|-----------------|
| 4 cameras (with groups) | ~1 second |
| 12 cameras (with groups) | ~2 seconds |
| 12 cameras (without groups, automatic) | ~10-60 seconds |
| 20+ cameras (without groups) | ~30-180 seconds |

**Note:** Automatic grouping time depends on:
- Number of cameras
- FOV overlap complexity
- Grouping parameters

## Best Practices

1. **Always provide `--map_file`** when using automatic grouping
2. **Start with default dilation** (1.0) and adjust if needed
3. **Use `--visualize`** to verify results visually
4. **Test on subset first** using `--sensor-names` for large datasets
5. **Keep original files** - use `--output` instead of `--overwrite` initially
6. **Choose appropriate height range** based on your objects (humans, vehicles, etc.)

## API Integration

This tool is a command-line wrapper around:

```python
from spatialai_data_utils.core.cameras.bev import calculate_group_origins_from_calibration

output_path = calculate_group_origins_from_calibration(
    input_calibration="calibration.json",  # JSON file or dataset directory
    output="calibration_with_origins.json",
    overwrite=False,
    map_file=None,
    dilation=1.0,
    height_range=(1.0, 3.0),
    prefer_existing_fov=False,
    sensor_names=None,
    visualize=False,
    vis_separate_images=False,
    # FOV constraint parameters
    scene_bounds=(-50, -50, 50, 50),  # Optional: (min_x, min_y, max_x, max_y)
    max_camera_distance=30.0,  # Maximum frustum distance from camera
    # Single-group fallback params (used only if groups are missing)
    n_sensor_groups=1,  # 1 -> all sensors assigned to bev-sensor-1
    max_sensors_per_group=None,
)
```

See `spatialai_data_utils/core/cameras/bev.py` for programmatic usage.

## Output Files

### 1. Calibration File
**Filename**: Specified by `--output`, or `{input_name}_with_origins.json`

**Contains:**
- All original sensor data
- Updated/added group information
- Calculated origins and dimensions
- Region metadata (if map provided)

### 2. Visualization Image (Optional)
**Filename**: `{output_name}_map.png` (if `--visualize` is used)

**Shows:**
- Overhead map
- Camera FOV polygons
- Group boundaries
- Camera positions

---

# ✅ create_camera_clusters.py

## Overview

The `create_camera_clusters.py` tool partitions ALL cameras into exactly N spatially compact clusters based on FOV coverage and spatial proximity. Unlike grouping (which finds overlapping camera sets), clustering assigns every camera to exactly one cluster with minimal spatial scatter.

The positional `input_calibration` argument accepts either a `calibration.json` file path or a directory that contains one, so you can point it straight at a calibration file. When a file is given, outputs are written next to it and a sibling `Top.png` is auto-discovered for visualization.

## Key Features

✅ **Complete Coverage**: Every camera is assigned to exactly one cluster  
✅ **Spatial Compactness**: Minimizes scatter within each cluster  
✅ **Flexible Cluster Count**: Specify exact number or auto-calculate from max cameras per group  
✅ **Two-Phase Algorithm**: Greedy initialization + iterative refinement  
✅ **FOV Polygon Support**: Uses existing FOV or calculates from camera frustum  
✅ **Visualization**: Generate visual maps of camera clusters

## Algorithm Description

The clustering algorithm uses a **greedy initialization** followed by **unassigned-camera handling** (`--mode densify`/`balanced`) to partition cameras into spatially compact clusters. By default it also **auto-tunes** its key parameters first (see [Auto-Tuning Parameters](#auto-tuning-parameters)).

### Phase 1: Greedy Initialization

Builds clusters by iteratively adding cameras based on FOV overlap and spatial proximity:

```text
1. Seed first cluster with the specified start camera
2. For each remaining camera slot:
   a. If current cluster is not empty:
      - Find camera with MAXIMUM FOV overlap with current cluster
      - If no overlap, find camera with SHORTEST distance to cluster centroid
      - Add camera to current cluster
   b. If current cluster reaches target size:
      - Seed new cluster with camera FURTHEST from all existing clusters
3. Repeat until all cameras are assigned
```

**Key Metrics:**
- **FOV Overlap**: Intersection area between camera FOV and cluster's combined FOV
- **Spatial Distance**: Euclidean distance from camera's FOV centroid to cluster centroid
- **Cluster Centroid**: Center of the union of all FOV polygons in the cluster

### Phase 2: Unassigned-Camera Handling (`--mode`)

After greedy initialization, some cameras may remain unassigned (no cluster met
the overlap/distance thresholds, or clusters reached capacity). The `--mode`
flag controls how they are resolved:

```text
densify (default):
  - Prioritize filling clusters to capacity
  - Reassign leftover cameras by cascading them into the nearest cluster
    that still has room (recursing up to --max_cascade_depth)
balanced:
  - Enforce the overlap/distance thresholds more strictly
  - Split overflow into additional clusters instead of overfilling
```

**Spatial Scatter** (lower is better) scores how compact a cluster is:
```python
scatter(cluster) = mean([max_distance(camera, other_cameras) for camera in cluster])
```

### Algorithm Visualization

```text
Initial State (12 cameras, 3 clusters):
┌─────────────────────────────────────────┐
│  ○ ○    ○                               │
│    ○  ○    ○ ○                          │
│  ○       ○    ○                         │
│        ○                                │
└─────────────────────────────────────────┘

After Greedy Initialization:
┌─────────────────────────────────────────┐
│  ● ●    ▲                               │  ● = Cluster 1
│    ●  ▲    ■ ■                          │  ▲ = Cluster 2
│  ●       ▲    ■                         │  ■ = Cluster 3
└─────────────────────────────────────────┘

After densify/balanced handling (unassigned cameras resolved):
┌─────────────────────────────────────────┐
│  ● ●    ●                               │  Cluster 1: compact top-left
│    ●  ▲    ■ ■                          │  Cluster 2: compact center
│  ▲       ▲    ■                         │  Cluster 3: compact right
└─────────────────────────────────────────┘
```

## Quick Start

```bash
# Pass a calibration.json file directly (outputs are written next to it)
python tools/camera_grouping/create_camera_clusters.py data/scene/calibration.json --max_camera_per_group 10

# Or pass a directory that contains calibration.json (and optionally Top.png)
python tools/camera_grouping/create_camera_clusters.py data/scene --max_camera_per_group 10

# Any calibration file name also works (e.g. a raw, ungrouped calibration)
python tools/camera_grouping/create_camera_clusters.py data/scene/calibration_raw.json --max_camera_per_group 10

# Override auto-calculated cluster count
python tools/camera_grouping/create_camera_clusters.py data/scene/calibration.json --max_camera_per_group 10 --n_clusters 5
```

## Command-Line Arguments

### Required Arguments

| Argument | Description |
|----------|-------------|
| `input_calibration` | Path to a `calibration.json` file **or** a directory containing `calibration.json` (and optionally `Top.png`). When a file is given, outputs are written next to it and a sibling `Top.png` is auto-discovered for visualization. |
| `--max_camera_per_group` | Maximum cameras per cluster; auto-calculates `n_clusters` |

### Clustering Parameters

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--n_clusters` | int | None | Optional override for cluster count (auto-calculated from total cameras / max_camera_per_group) |
| `--start_camera_index` | int | 0 | Index of camera to seed the first cluster (auto-tuned by default unless `--disable_param_tuning`) |
| `--mode` | str | "densify" | `densify` fills clusters to capacity via cascade reassignment; `balanced` enforces thresholds and splits overflow |
| `--min_overlap_threshold` | float | 0.2 | Minimum FOV overlap (0-1) for cluster membership (auto-tuned by default) |
| `--max_distance_threshold` | float | 8.0 | Maximum centroid distance (meters) for membership (auto-tuned by default) |
| `--max_cascade_depth` | int | 3 | Max recursion depth for densify-mode cascade reassignment |

### FOV Calculation Parameters

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--prefer_existing_fov` | flag | False | Use pre-computed FOV polygons from the calibration instead of frustum (frustum is the default) |
| `--height_range` | float float | 1.0 3.0 | Height range (min, max) in meters for ground plane intersection |
| `--image_size` | int int | 1920 1080 | Image dimensions (width, height) in pixels for frustum calculation |
| `--max_camera_distance` | float | 30.0 | Maximum effective distance in meters for frustum calculation |
| `--dilation` | float | 8.0 | Buffer distance in meters for cluster bounding boxes |

### Output & Input Options

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--output` | str | None | Output path for the clustered calibration (defaults next to the input) |
| `--overwrite` | flag | False | Overwrite the input calibration file |
| `--output_suffix` | str | "clustered" | Suffix for output files |
| `--map_file` | str | None | Map image for visualization (black background if omitted) |
| `--sensor_names` | str ... | None | Restrict clustering to the listed sensor names (space-separated) |

### Visualization Options

Visualization is opt-in via `--visualize` (off by default); the other `--vis_*`
flags below only take effect when `--visualize` is set.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--visualize` | flag | False | Generate visualization of camera clusters on the map |
| `--vis_no_camera_id_labels` | flag | False | Disable camera ID labels in visualization |
| `--vis_separate_images` | flag | False | Generate separate images per cluster instead of combined (default: combined for clustering) |

### Auto-Tuning Parameters

By default the tool **auto-tunes** `start_camera_index`, `min_overlap_threshold`, and `max_distance_threshold` with a parameter sweep (the same logic as the standalone `find_suggested_cluster_params.py`) and overrides the values you pass. Disable with `--disable_param_tuning`.

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--disable_param_tuning` | flag | False | Skip auto-tuning; use the `start_camera_index` / threshold values as given |
| `--tuning_overlap_grid` | float ... | None | Overlap thresholds (0-1) to search when auto-tuning |
| `--tuning_distance_grid` | float ... | None | Centroid distance thresholds (meters) to search when auto-tuning |
| `--tuning_start_index_grid` | int ... | None | Seed camera indices to try when auto-tuning |
| `--tuning_start_index_seed` | int | None | Random seed for auto-generated start indices (when no grid given) |
| `--tuning_workers` | int | 0 | Parallel workers for the sweep (0=auto cpu_count, 1=serial) |

## Usage Examples

### Example 1: Basic Clustering

```bash
# Specify max cameras per cluster (required)
python tools/camera_grouping/create_camera_clusters.py data/scene --max_camera_per_group 10
```

**Output:**
- `data/scene/calibration_clustered.json`

By default no map is written. Add `--visualize` to also produce
`calibration_clustered_map.png` (a combined map if `Top.png` exists; see
Example 6).

### Example 2: Override Cluster Count

```bash
# Override auto-calculated cluster count
python tools/camera_grouping/create_camera_clusters.py data/scene --max_camera_per_group 10 --n_clusters 5
```

### Example 3: Auto-Calculate Clusters

```bash
# Automatically determine cluster count based on max 10 cameras per cluster
# If you have 45 cameras → ceil(45/10) = 5 clusters
python tools/camera_grouping/create_camera_clusters.py data/scene --max_camera_per_group 10
```

### Example 4: Custom Frustum Parameters

```bash
# Frustum-based FOV is the default; tune its parameters here.
# (Pass --prefer_existing_fov to use pre-computed polygons instead.)
python tools/camera_grouping/create_camera_clusters.py data/scene \
    --max_camera_per_group 10 \
    --height_range 0.5 4.0 \
    --image_size 3840 2160
```

### Example 5: Custom Starting Point

```bash
# Start clustering from camera at index 5
python tools/camera_grouping/create_camera_clusters.py data/scene \
    --max_camera_per_group 10 \
    --start_camera_index 5 \
    --n_clusters 4
```

### Example 6: Separate Visualization Per Cluster

```bash
# Generate separate visualization for each cluster
python tools/camera_grouping/create_camera_clusters.py data/scene \
    --max_camera_per_group 10 \
    --visualize \
    --vis_separate_images
```

**Output:**
- `calibration_clustered.json`
- `calibration_clustered_map_bev-sensor-1.png`
- `calibration_clustered_map_bev-sensor-2.png`
- ... (one image per cluster)

### Example 7: Complete Production Setup

```bash
# Full configuration for production use
python tools/camera_grouping/create_camera_clusters.py data/warehouse \
    --max_camera_per_group 8 \
    --height_range 1.0 3.0 \
    --image_size 1920 1080 \
    --max_camera_distance 25.0 \
    --dilation 10.0 \
    --mode densify \
    --output_suffix production
```

## Parameter Tuning Guide

### `--max_camera_per_group` (Required)

The `--max_camera_per_group` parameter is **required** and controls how cameras are distributed into clusters.

#### Cluster Calculation Logic

The number of clusters (`n_clusters`) is determined by the following logic:

```text
1. If --n_clusters is NOT provided:
   → n_clusters = ceil(num_sensors / max_camera_per_group)
   → Example: 45 sensors / 10 max = 5 clusters

2. If --n_clusters IS provided:
   a. If n_clusters == 1:
      → Validate: num_sensors must be ≤ max_camera_per_group
      → If validation fails: ERROR (increase max_camera_per_group or set n_clusters > 1)
      → If validation passes: All sensors assigned to single cluster (bev-sensor-1)
   
   b. If n_clusters > 1:
      → Calculate: required_clusters = ceil(num_sensors / max_camera_per_group)
      → If required_clusters > n_clusters: WARNING, override to required_clusters
      → Otherwise: use specified n_clusters

3. Final validation: n_clusters must not exceed num_sensors
```

#### Single Cluster Mode Optimization

When `n_clusters == 1` (either explicitly set or auto-calculated), the tool **skips the clustering algorithm entirely** and directly assigns all sensors to `bev-sensor-1`. This is faster and produces the same result as running the algorithm with a single cluster.

```bash
# Auto-calculated single cluster (4 sensors, max 10 per group → 1 cluster)
python create_camera_clusters.py data/small_scene --max_camera_per_group 10

# Explicit single cluster
python create_camera_clusters.py data/small_scene --max_camera_per_group 10 --n_clusters 1
```

#### Examples

```bash
# Required: Specify maximum cameras per cluster
--max_camera_per_group 10
# With 45 cameras → 5 clusters (9 cameras each)
# With 50 cameras → 5 clusters (10 cameras each)
# With 51 cameras → 6 clusters (8-9 cameras each)
# With 4 cameras → 1 cluster (single cluster mode, skips algorithm)

# Optional: Override auto-calculated cluster count
--max_camera_per_group 10 --n_clusters 5
```

### Performance Notes (GTC24, 146 cameras, frustum + visualize, mode=densify)

| max_camera_per_group | Auto n_clusters | Wall time (real) |
|----------------------|-----------------|------------------|
| 50                   | 3               | ~12.6s           |
| 15                   | 10              | ~16.0s           |
| 4                    | 37              | ~17.2s           |

Notes:
- Configuration: `--height_range 1.0 8.0 --max_camera_distance 30 --max_distance_threshold 90.0 --min_overlap_threshold 0.0001 --mode densify --start_camera_index 0 --disable_param_tuning` (frustum FOV is on by default; pass `--visualize` to also render the cluster map).
- Overlap threshold is a ratio (0–1). Distance is in meters.
- Larger `max_camera_per_group` → fewer clusters → slightly faster.
- Times measured on this dataset; expect small variance (±1–2s) by hardware/load.

### `--height_range`

| Range | Use Case |
|-------|----------|
| `0.3 1.5` | Low objects (packages, pets) |
| `1.0 2.0` | Human tracking |
| `1.0 3.0` | General purpose (default) |
| `1.5 4.0` | Tall objects (vehicles, machinery) |

## Troubleshooting

### Issue 1: Unbalanced Clusters

**Symptom:** Some clusters have many more cameras than others.

**Solutions:**
- Try `--mode balanced` (stricter on thresholds, splits overflow).
- Lower `--min_overlap_threshold` or raise `--max_distance_threshold` if too strict (or let auto-tuning pick them).
- Verify sensors have valid intrinsics/extrinsics (frustum FOV is the default).

### Issue 2: Slow Processing

**Symptom:** Clustering takes too long.

**Solutions:**
- Pass `--disable_param_tuning` to skip the auto-tuning parameter sweep.
- Pass `--prefer_existing_fov` if the calibration already has FOV polygons (frustum is slower).
- Reduce image size (`--image_size`), which is only used for frustum-based FOV.

### Issue 3: Missing FOV Polygons

**Symptom:** Warning about missing `fieldOfViewPolygon`.

**Solutions:**
- No action needed: frustum FOV (computed from camera matrices) is the default; only `--prefer_existing_fov` relies on `fieldOfViewPolygon`.
- Verify calibration has valid intrinsic/extrinsic matrices.

## API Integration

This tool is a command-line wrapper around:

```python
from spatialai_data_utils.core.cameras.bev import create_camera_clusters_from_calibration

output_path = create_camera_clusters_from_calibration(
    input_calibration="data/scene/calibration.json",  # calibration.json file or a directory containing it
    max_camera_per_group=10,                  # required
    output="data/scene/calibration_clustered.json",  # optional
    output_suffix="clustered",                # optional, default "clustered"
    overwrite=False,
    n_clusters=5,                             # optional override
    start_camera_index=0,
    dilation=8.0,
    use_frustum=False,
    max_camera_distance=30.0,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    sensor_names=None,
    visualize=True,
    mode="densify",                           # or "balanced"
    overlap_threshold=0.15,                   # overlap ratio 0–1
    distance_threshold=25.0,                  # meters
    max_cascade_depth=3,
)
```

See `spatialai_data_utils/core/cameras/bev.py` for programmatic usage.

## Output Files

### 1. Calibration File
**Filename**: `calibration_{output_suffix}.json`

**Contains:**
- All original sensor data
- Cluster assignments (`group` field for each sensor)
- Calculated origins and dimensions
- Region metadata with cluster polygons
- Tripwire/ROI `groups` fields (automatically filled to match cluster assignments)

### 2. Visualization Image
**Filename**: `map_plotted_{output_suffix}.png`

**Shows:**
- Overhead map
- Camera FOV polygons colored by cluster
- Cluster boundaries
- Camera positions and IDs

## Automatic Tripwire/ROI Group Assignment

The clustering pipeline automatically fills the `"groups"` field on any tripwires and ROIs present in the calibration file. For each tripwire/ROI, `"groups"` is set to the list of BEV-sensor group names that share at least one sensor with its `"sensors"` list.

This step runs after cluster assignments are finalized and before the calibration file is saved. It handles three cases:

- **Missing or empty `"groups"`**: the field is populated with the correct groups
- **Wrong `"groups"`**: the field is corrected and a warning is logged
- **Already correct `"groups"`**: no change

No separate tool or manual step is needed — tripwire/ROI groups are always consistent with the clustering output.

The core logic lives in `spatialai_data_utils/core/cameras/calibration_fields.py`.

---

# ✅ create_camera_groups.py

## Overview

The `create_camera_groups.py` tool creates camera groups with **duplication support** based on overlapping fields of view (FOV). Unlike clustering which partitions cameras (each in exactly one cluster), this grouping algorithm allows cameras to appear in multiple groups.

## Key Features

✅ **Camera Duplication**: Cameras can appear in multiple groups  
✅ **Fixed Group Size**: User specifies exact number of groups AND cameras per group  
✅ **Multiple Size Types**: Create groups with different sizes (e.g., n_groups each of 5, 8, and 6 cameras)  
✅ **Auto Mode**: Automatically generate groups with sizes 1, 2, ..., min(n_sensors, max_sensors_per_group)  
✅ **Max Sensors Per Group**: Limit maximum group size (default: 18) even with many cameras  
✅ **Coverage Guarantee**: Every camera appears in at least one group  
✅ **Farthest-First Seeding**: Groups are started from spatially diverse locations  
✅ **Randomized Selection**: Randomization by default; use `--random_seed` for deterministic results  
✅ **Duplicate Detection**: Automatically detects and regenerates duplicate groups  
✅ **FOV Overlap Constraints**: Groups are built based on FOV overlap and spatial proximity  
✅ **Natural Sorting**: Group IDs and camera IDs are sorted numerically (1, 2, 10 not 1, 10, 2)  
✅ **Visualization**: Generate separate or combined visual maps for camera groups

## Algorithm Description

The grouping algorithm uses **farthest-first seeding** combined with **greedy group building**, **randomization**, and **duplicate detection**:

### Phase 1: Group Seeding

```text
1. First group: Seed with camera at start_camera_index
2. Subsequent groups: 
   a. Prefer UNSELECTED cameras: Pick farthest unselected camera from all previous seeds
   b. Fallback: If all cameras are already selected, pick farthest from ALL cameras
   c. Randomization: Top candidates are shuffled before selection (unless --no_randomize)
   - This ensures spatial diversity across groups
```

### Phase 2: Group Building

For each group after seeding:

```text
1. While group size < cameras_per_group:
   a. Try to add from UNSELECTED cameras (prefer cameras not yet in any group):
      - Find cameras with MAXIMUM FOV overlap with current group
      - Apply randomization among top candidates (unless --no_randomize)
      - Must meet overlap_threshold and distance_threshold
   b. If no unselected cameras available, allow DUPLICATION:
      - Select from ALL cameras (including already-selected ones)
      - Same overlap/distance criteria with randomization
   c. If still no valid camera, RELAX constraints:
      - Accept any camera with any overlap/distance
2. Mark all cameras in group as "selected"
```

### Phase 3: Duplicate Detection

```text
After building each group:
1. Check if the new group is identical to any previously created group
2. If duplicate detected:
   a. Regenerate the group (up to 5 retries by default)
   b. Different randomization ensures different result
3. For group size = total cameras: only ONE group is created
   (since all cameras form the only possible unique combination)
```

### Phase 4: Coverage Verification

```text
After all groups are built:
1. Check for any cameras with assignment_count == 0
2. If any camera is not covered → RAISE ERROR
   - Groups maintain EXACT size (cameras_per_group)
   - Coverage must be achieved through duplication during group building
   - Error message guides user to adjust parameters
```

### Algorithm Visualization

```text
Initial State (6 cameras, 3 groups of 3):
┌─────────────────────────────────────────┐
│  ○ ○    ○                               │
│    ○  ○                                 │
│                  ○                      │
└─────────────────────────────────────────┘

After Group 1 (seed: camera 0, overlap-based growth):
┌─────────────────────────────────────────┐
│  ● ●    ○                               │  ● = Group 1
│    ●  ○                                 │
│                  ○                      │
└─────────────────────────────────────────┘

After Group 2 (seed: farthest unselected camera 5):
┌─────────────────────────────────────────┐
│  ● ●    ▲                               │  ▲ = Group 2 (includes ● as duplicate)
│    ●  ▲                                 │
│                  ▲                      │
└─────────────────────────────────────────┘

After Group 3 (seed: farthest camera, more duplication):
┌─────────────────────────────────────────┐
│  ● ●    ▲                               │  ■ = Group 3
│    ●■ ▲■                                │  Note: cameras can be ●▲■ simultaneously
│                  ▲■                     │
└─────────────────────────────────────────┘

Result: 6 cameras × 3 groups × 3 per group = 9 slots
        → Each camera appears 1-2 times on average
```

## Quick Start

Input can be a `calibration.json` file directly **or** a directory containing one.

```bash
# Auto mode: groups of sizes 1, 2, ..., min(n_sensors, 18) — recommended for training
python tools/camera_grouping/create_camera_groups.py data/scene --auto

# Manual: 5 groups with 8 cameras each (+ visualization)
python tools/camera_grouping/create_camera_groups.py data/scene \
    --n_groups 5 --cameras_per_group 8 --visualize
```

See the **Usage Examples** below for the auto-mode variants (`--max_sensors_per_group`,
`--n_groups`), multiple size types (`--cameras_per_group 5 8 6`), overlap thresholds,
deterministic seeding (`--random_seed` / `--no_randomize`), and combined visualization.

## Command-Line Arguments

### Required Arguments

| Argument | Description |
|----------|-------------|
| `input_calibration` | Path to a `calibration.json` file **or** a directory containing `calibration.json`. When a file is given, outputs are written next to it. |

### Mode Selection (Choose One)

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--auto` | flag | False | **Auto mode**: Automatically set cameras_per_group to [1, 2, ..., min(n_sensors, max_sensors_per_group)]. Overrides --cameras_per_group. |
| `--n_groups` | int | 1 | Number of groups per size type |
| `--cameras_per_group` | int(s) | None | Number of cameras per group. Single value: all groups have that size. Multiple values: creates n_groups for EACH size (total = n_groups × count). Example: `--n_groups 2 --cameras_per_group 5 8 6` creates 6 groups (2×5-cam, 2×8-cam, 2×6-cam). **Required when --auto is not used.** |
| `--max_sensors_per_group` | int | 18 | Maximum number of sensors per group. In auto mode, limits cameras_per_group to [1, 2, ..., min(n_sensors, max_sensors_per_group)]. |

### Output Configuration

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--output` | str | None | Output path for grouped calibration file |
| `--output_suffix` | str | "grouped" | Suffix for output files |
| `--map_file` | str | None | Path to map image for visualization (auto-detects Top.png) |

### Grouping Parameters

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--start_camera_index` | int | 0 | Starting camera index for seeding first group |
| `--min_overlap_threshold` | float | 0.2 | Minimum FOV overlap (0-1) for group membership |
| `--max_distance_threshold` | float | inf | Maximum centroid distance (meters) for membership |
| `--no_randomize` | flag | False | Disable randomization in camera selection (default: randomization enabled) |
| `--random_seed` | int | None | Random seed for deterministic results (default: None for non-deterministic) |
| `--max_duplicate_retries` | int | 5 | Maximum retries when a duplicate group is generated |

### FOV Calculation Parameters

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--prefer_existing_fov` | flag | False | Use existing FOV polygons instead of frustum calculation |
| `--max_camera_distance` | float | 30.0 | Maximum distance (meters) for frustum calculation |
| `--height_range` | float float | 1.0 3.0 | Height range (min, max) for ground plane intersection |
| `--image_size` | int int | 1920 1080 | Image dimensions for frustum calculation |
| `--dilation` | float | 8.0 | Buffer distance (meters) for group bounding boxes |

### Visualization Options

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--visualize` | flag | False | Generate visualization of camera groups |
| `--vis_no_camera_id_labels` | flag | False | Disable camera ID labels in visualization |
| `--vis_combined` | flag | False | Generate single combined image instead of separate images per group (default: separate for grouping) |

## Usage Examples

### Example 1: Auto Mode (Recommended for Training)

```bash
# Auto mode: create groups with sizes 1, 2, ..., min(n_sensors, 18)
# For a scene with 25 cameras, creates groups of sizes 1-18 (18 is default max)
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --auto
```

**Output:**
- `calibration_grouped.json` with 18 groups (sizes 1 through 18)
- Each size has 1 group (default n_groups=1)

### Example 2: Auto Mode with Custom Max Sensors

```bash
# Auto mode with custom max sensors per group (e.g., 12)
# For a scene with 25 cameras, creates groups of sizes 1-12
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --auto \
    --max_sensors_per_group 12
```

### Example 3: Auto Mode with Multiple Groups Per Size

```bash
# Auto mode with 2 groups per size
# Creates 2 groups each of sizes 1, 2, ..., min(n_sensors, 18)
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --auto \
    --n_groups 2
```

**Output:**
- For a scene with 25 cameras: 36 groups (2 groups × 18 sizes)
- Groups are named bev-sensor-1, bev-sensor-2, ..., bev-sensor-36

### Example 4: Basic Grouping (Uniform Size)

```bash
# Create 3 groups with 10 cameras each
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 3 \
    --cameras_per_group 10
```

**Output:**
- `data/mtmc/scene_002/calibration_grouped.json`

### Example 5: Multiple Size Types

```bash
# Create 2 groups × 3 sizes = 6 total groups
# (2 groups with 5 cameras, 2 with 8, 2 with 6)
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 2 \
    --cameras_per_group 5 8 6
```

**Output:**
- `calibration_grouped.json` with 6 groups:
  - Groups 0-1: 5 cameras each
  - Groups 2-3: 8 cameras each
  - Groups 4-5: 6 cameras each

### Example 6: With Visualization (Separate Images)

```bash
# Generate separate visualization for each group (default for grouping)
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 3 \
    --cameras_per_group 10 \
    --visualize
```

**Output:**
- `calibration_grouped.json`
- `calibration_grouped_map_bev-sensor-1.png`
- `calibration_grouped_map_bev-sensor-2.png`
- `calibration_grouped_map_bev-sensor-3.png`

### Example 7: With Combined Visualization

```bash
# Generate single combined image with all groups
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 3 \
    --cameras_per_group 10 \
    --visualize --vis_combined
```

**Output:**
- `calibration_grouped.json`
- `calibration_grouped_map.png` (single image with all groups)

### Example 8: Strict Overlap Requirements

```bash
# Require higher FOV overlap for group membership
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 3 \
    --cameras_per_group 8 \
    --min_overlap_threshold 0.4 \
    --max_distance_threshold 20.0
```

### Example 9: Use Existing FOV Polygons

```bash
# Use pre-computed FOV polygons from calibration attributes
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 4 \
    --cameras_per_group 6 \
    --prefer_existing_fov
```

### Example 10: Custom Frustum Parameters

```bash
# Configure frustum calculation for specific scene
python tools/camera_grouping/create_camera_groups.py \
    data/warehouse \
    --n_groups 5 \
    --cameras_per_group 10 \
    --max_camera_distance 40.0 \
    --height_range 0.5 2.5 \
    --image_size 3840 2160
```

### Example 11: Deterministic Results with Random Seed

```bash
# Use random seed for reproducible results (recommended)
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 3 \
    --cameras_per_group 10 \
    --random_seed 42
```

**Note:** With `--random_seed`, randomization is still applied but produces identical results when using the same seed.

### Example 12: Fully Deterministic (No Randomization)

```bash
# Disable randomization entirely for fully deterministic results
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --n_groups 3 \
    --cameras_per_group 10 \
    --no_randomize
```

**Note:** Without randomization, running the same command will always produce identical results.

### Example 13: Complete Production Setup

```bash
# Full configuration for production use
python tools/camera_grouping/create_camera_groups.py \
    data/mtmc/scene_002 \
    --auto \
    --max_sensors_per_group 15 \
    --output data/mtmc/scene_002/calibration_production.json \
    --min_overlap_threshold 0.2 \
    --max_distance_threshold 30.0 \
    --dilation 10.0 \
    --visualize
```

## Grouping vs Clustering

| Aspect | Grouping (`create_camera_groups.py`) | Clustering (`create_camera_clusters.py`) |
|--------|--------------------------------------|------------------------------------------|
| **Primary Use** | **Model training & finetuning** | **Deployment & inference** |
| **Camera Assignment** | Cameras can be in MULTIPLE groups | Each camera in exactly ONE cluster |
| **Group Size** | Fixed: user specifies exact size(s) | Variable: depends on algorithm |
| **Group Count** | Fixed: user specifies count per size | Auto-calculated or user override |
| **Coverage** | GUARANTEED: all cameras in ≥1 group | GUARANTEED: all cameras in exactly 1 cluster |
| **Duplication** | ✅ Allowed and expected | ❌ Not allowed |
| **Criterion** | FOV overlap (require intersection) | Spatial proximity (minimize scatter) |
| **Algorithm** | Farthest-first seeding + greedy growth | Greedy + refinement |

### When to Use Grouping (Training & Finetuning)

- ✅ **Model training**: Create diverse training batches with overlapping camera views
- ✅ **Finetuning**: Generate multi-view data for re-identification model training
- ✅ Need overlapping camera coverage for multi-view reconstruction
- ✅ Want redundant camera assignments for data augmentation
- ✅ Building re-identification zones with shared cameras
- ✅ Each group needs exactly N cameras regardless of total camera count

### When to Use Clustering (Deployment & Inference)

- ✅ **Deployment**: Partition cameras for distributed inference pipelines
- ✅ **Inference**: Assign cameras to processing nodes for real-time tracking
- ✅ Need to partition camera network into manageable sub-networks
- ✅ Want balanced workload distribution across compute resources
- ✅ Each camera should belong to exactly one processing unit
- ✅ Group sizes can vary based on spatial distribution

## Duplication Calculation

The algorithm automatically calculates the minimum duplication required:

```text
total_slots = n_groups × cameras_per_group
min_duplication = (total_slots - num_cameras) / num_cameras

Example:
- 20 cameras, 5 groups of 8 = 40 slots
- min_duplication = (40 - 20) / 20 = 1.0
- Each camera appears ~2 times on average
```

**Important**: To ensure all cameras can be covered, make sure:
```text
n_groups × cameras_per_group ≥ num_cameras
```

If total slots < num_cameras, some cameras may not be covered and an error will be raised.

## Error Handling

### Coverage Failure Error

If any camera cannot be assigned to any group, the algorithm raises a `RuntimeError`:

```text
RuntimeError: Camera grouping failed: 2 camera(s) could not be assigned to any group: 
['Camera_15', 'Camera_16']. Consider adjusting n_groups, cameras_per_group, or threshold parameters.
```

**Solutions:**
1. Increase `n_groups` or `cameras_per_group` to create more slots
2. Lower `--min_overlap_threshold` to allow more camera combinations
3. Increase `--max_distance_threshold` to allow spatially distant cameras
4. Check if isolated cameras have valid FOV polygons

## Parameter Tuning Guide

### `--auto` Mode

Auto mode is ideal for **training and fine-tuning** where you need diverse group sizes (`--auto` creates groups with sizes 1, 2, ..., min(n_sensors, max_sensors_per_group)).

**Behavior:**
- Reads calibration to count total sensors (n_sensors)
- Sets cameras_per_group = [1, 2, 3, ..., min(n_sensors, max_sensors_per_group)]
- Each size gets `n_groups` groups (default: 1)
- Overrides `--cameras_per_group` (ignored when `--auto` is used)
- Does NOT override `--n_groups` (can be customized)

**Example:**
| Scene | max_sensors_per_group | Result |
|-------|----------------------|--------|
| 25 cameras | 18 (default) | Groups of sizes 1-18 (18 groups total) |
| 25 cameras | 12 | Groups of sizes 1-12 (12 groups total) |
| 10 cameras | 18 | Groups of sizes 1-10 (10 groups total) |

### `--max_sensors_per_group`

Limits the maximum number of cameras in any single group. In auto mode it caps the largest size (e.g. 25 cameras with `--max_sensors_per_group 12` → groups of sizes 1-12).

| Value | Use Case |
|-------|----------|
| 18 (default) | Standard training, balances diversity and computation |
| 8-12 | Faster training, smaller batches |
| 24+ | Large-scale scenes where more cameras per group is beneficial |

### `--random_seed` and `--no_randomize`

Controls randomization behavior during camera selection.

| Option | Effect |
|--------|--------|
| Default (no options) | Randomization ON, different runs may produce different groups |
| `--random_seed 42` | Randomization ON with fixed seed, reproducible results |
| `--no_randomize` | Randomization OFF, fully deterministic results |

**When to use `--random_seed`:**
- Reproducible experiments while maintaining variety in selection
- CI/CD pipelines requiring consistent outputs
- Comparing results across different parameter configurations

**When to use `--no_randomize`:**
- Debugging and testing with completely deterministic behavior
- When you want the "best" candidate always selected (no random tie-breaking)
- Comparing algorithm behavior with no randomness

### `--min_overlap_threshold`

Controls how much FOV overlap is required for a camera to join a group.

| Value | Effect |
|-------|--------|
| 0.0 | Accept any camera (no overlap required) |
| 0.1-0.2 | Light overlap (default, recommended) |
| 0.3-0.5 | Moderate overlap |
| 0.5+ | Strict overlap (may cause coverage issues) |

### `--max_distance_threshold`

Controls maximum allowed distance from camera to group centroid.

| Value | Effect |
|-------|--------|
| `inf` | No distance limit (default) |
| 30-50 | Moderate constraint |
| 10-20 | Strict constraint (cameras must be close) |

### Threshold Relaxation

If constraints are too strict, the algorithm automatically relaxes them:
1. First tries with user-specified thresholds
2. Falls back to overlap=0.0, distance=inf if no valid camera found

## Output Format

### Calibration File

```json
{
  "sensors": [
    {
      "id": "Camera_01",
      "group": {
        "name": "bev-sensor-1",
        "alias": "area-1",
        "type": "bev",
        "origin": [5.23, -12.45],
        "dimensions": [-2.1, -18.5, 12.6, -6.4]
      },
      ...
    },
    {
      "id": "Camera_02",
      "group": {
        "name": "bev-sensor-1",  // Same group as Camera_01 (duplication!)
        ...
      },
      ...
    }
  ]
}
```

### Visualization Files

When `--visualize` is enabled, separate images are generated for each group:
- `calibration_grouped_map_bev-sensor-1.png`
- `calibration_grouped_map_bev-sensor-2.png`
- etc.

Each visualization shows:
- Map background (or black background if no map)
- FOV polygons for cameras in that group
- Camera positions and IDs
- Group name and camera count in title

## API Integration

This tool is a command-line wrapper around:

```python
from spatialai_data_utils.core.cameras.bev import create_camera_groups_from_calibration

output_path = create_camera_groups_from_calibration(
    input_calibration="data/scene",
    n_groups=5,
    cameras_per_group=8,  # Can be int or list of ints (e.g., [1, 2, 3, ..., 18] for auto mode)
    map_file=None,  # Auto-detects Top.png
    output="data/scene/calibration_grouped.json",
    output_suffix="grouped",
    start_camera_index=0,
    dilation=8.0,
    use_frustum=True,
    max_camera_distance=30.0,
    height_range=(1.0, 3.0),
    image_size=(1920, 1080),
    visualize=True,
    label_camera_ids=True,
    overlap_threshold=0.2,
    distance_threshold=float("inf"),
    randomize=True,  # Set to False for fully deterministic results
    max_duplicate_retries=5,  # Max retries for duplicate groups
    random_seed=42,  # Set to int for reproducible results, None for non-deterministic
)
```

**Auto mode equivalent in code:**
```python
import json
from spatialai_data_utils.core.cameras.bev import create_camera_groups_from_calibration

# Load calibration to count sensors
with open("data/scene/calibration.json") as f:
    n_sensors = len(json.load(f).get("sensors", []))

# Auto mode: create groups with sizes 1, 2, ..., min(n_sensors, max_sensors)
max_sensors_per_group = 18
cameras_per_group = list(range(1, min(n_sensors, max_sensors_per_group) + 1))

output_path = create_camera_groups_from_calibration(
    input_calibration="data/scene",
    n_groups=1,
    cameras_per_group=cameras_per_group,  # max_sensors_per_group only shapes this list; it is NOT a function parameter
    # ... other parameters
)
```

See `spatialai_data_utils/core/cameras/bev.py` for programmatic usage.

## Troubleshooting

### Issue 1: Coverage Failure Error

**Symptom:** `RuntimeError: Camera grouping failed: X camera(s) could not be assigned`

**Solutions:**
- Lower `--min_overlap_threshold`
- Increase `--max_distance_threshold`
- Increase total slots (`n_groups × cameras_per_group`)
- Check if cameras have valid FOV polygons

### Issue 2: Highly Duplicated Cameras

**Symptom:** Some cameras appear in many groups while others appear in few.

**Solutions:**
- Increase `n_groups` to spread cameras more evenly
- Lower `cameras_per_group` to reduce total slots
- Adjust thresholds to allow more diverse camera selection

### Issue 3: Duplicate Groups Detected

**Symptom:** Warning about duplicate groups being regenerated.

**Explanation:** The algorithm automatically detects and regenerates groups that are identical to previously created groups. This is expected behavior with randomization.

**Solutions:**
- This is usually not a problem - the algorithm handles it automatically
- If you see many retry warnings, consider:
  - Reducing `n_groups` (fewer groups means less chance of duplicates)
  - Ensuring `cameras_per_group < n_sensors` (groups with all cameras are always identical)

**Note:** When `cameras_per_group == n_sensors`, only ONE group of that size is created since all cameras form the only possible unique combination.

### Issue 4: Missing FOV Polygons

**Symptom:** Warning about missing `fieldOfViewPolygon`.

**Solutions:**
- Don't use `--prefer_existing_fov` (let it calculate from frustum)
- Verify calibration has valid intrinsic/extrinsic matrices

### Issue 5: Slow Processing

**Solutions:**
- Use `--prefer_existing_fov` if FOV polygons exist
- Disable `--visualize` when not needed
- Reduce `--max_camera_distance` for faster frustum calculation
- In auto mode, reduce `--max_sensors_per_group` to limit group sizes

### Issue 6: Non-Deterministic Results

**Symptom:** Running the same command produces different group assignments.

**Explanation:** By default, randomization is enabled to create diverse groups.

**Solutions:**
- Use `--random_seed 42` for reproducible results with randomization (recommended)
- Use `--no_randomize` for fully deterministic results without any randomization

---

# Other Tools

These helpers complement the three primary tools above. Run any of them with `--help` for the full option list.

## `reassign_camera_groups.py`

Move specific cameras into **existing** BEV groups in a clustered/grouped `calibration.json`, then recompute the affected group origins. Useful for hand-correcting a few misassigned cameras without re-running the whole clustering.

```bash
python tools/camera_grouping/reassign_camera_groups.py \
    data/scene/calibration_clustered.json \
    --move Camera_01:bev-sensor-2 Camera_05:bev-sensor-3 \
    --output data/scene/calibration_reassigned.json
```

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `input_calibration` | str | — | Calibration with existing groups (e.g. from `create_camera_clusters.py`) |
| `--move` (required) | str ... | — | `camera_id:group_name` pairs (space-separated) to reassign |
| `--output` | str | `<input>_reassigned.json` | Output calibration path |
| `--overwrite` | flag | False | Overwrite the input file in-place |
| `--strict` | flag | False | Fail (vs. warn + skip) if a camera or target group is missing |
| `--prefer_existing_fov` | flag | False | Use existing FOV polygons instead of frustum when recomputing origins |
| `--dilation` | float | 1.0 | Dilation (meters) when recomputing group bounds |
| `--height_range` | float float | 1.0 3.0 | Height range for ground-plane intersection |
| `--image_size` | int int | 1920 1080 | Image size for frustum FOV |
| `--max_camera_distance` | float | 30.0 | Max frustum distance from camera |
| `--map_file` | str | None | Map image (auto-detects sibling `Top.png`) |
| `--visualize` | flag | False | Generate visualization of the reassigned groups on the map |
| `--vis_no_camera_id_labels` | flag | False | Toggle camera-ID labels on the visualization |
| `--output_suffix` | str | "reassigned" | Suffix for output files |

Wraps `spatialai_data_utils.core.cameras.group_utils.reassign_camera_groups_from_calibration`.

## `find_suggested_cluster_params.py`

Grid-search `overlap_threshold`, `distance_threshold`, and `start_camera_index` for `create_camera_clusters.py`, printing the best-scoring combinations (lower score = more compact, capacity-respecting clusters). `create_camera_clusters.py` runs this automatically unless you pass `--disable_param_tuning`.

```bash
python tools/camera_grouping/find_suggested_cluster_params.py \
    data/scene --max_camera_per_group 10 --top_k 5
```

Key options: `--max_camera_per_group` (required), `--mode {densify,balanced}`, `--prefer_existing_fov`, `--height_range` (default `1.0 3.0`), `--image_size`, `--max_camera_distance`, `--max_cascade_depth`, `--overlap_grid`, `--distance_grid`, `--start_index_grid`, `--start_index_seed`, `--workers`, `--top_k`, `--verbose`.

Wraps `spatialai_data_utils.core.cameras.clustering.find_suggested_cluster_params`.

## `batch_create_camera_groups.sh`

Interactive helper that scans a data directory for scene folders containing `calibration.json`, lets you pick scenes and shared parameters, then runs `create_camera_groups.py` on each.

```bash
bash tools/camera_grouping/batch_create_camera_groups.sh data/mtmc
```

Defaults to `data/mtmc` when no directory is given; prompts for auto mode, `n_groups`, thresholds, visualization, etc., and prints a per-scene success/failure summary at the end.

---

## Related Resources

### Core Modules
- **`spatialai_data_utils/core/cameras/bev.py`** - BEV camera functions (public API)
- **`spatialai_data_utils/core/cameras/origin.py`** - Core origin calculations
- **`spatialai_data_utils/core/cameras/grouping.py`** - Grouping algorithms
- **`spatialai_data_utils/core/cameras/clustering.py`** - Clustering + parameter auto-tuning
- **`spatialai_data_utils/core/cameras/group_utils.py`** - Group reassignment helpers

### Test Suites
- **`tests/core/cameras/test_origin_calculation.py`** - Origin calculation tests
- **`tests/core/cameras/test_camera_clustering.py`** - Camera clustering tests
- **`tests/core/cameras/test_camera_grouping.py`** - Camera grouping tests

## Getting Help

```bash
# Show all available options
python tools/camera_grouping/calculate_origin.py --help

# Show usage examples
python tools/camera_grouping/calculate_origin.py --help | grep -A 20 "Examples:"
```

---

**Last Updated**: 2026-06-02
**Tools Location**: `tools/camera_grouping/`  
**Core Module**: `spatialai_data_utils.core.cameras.bev`  
**Status**: ✅ Production Ready (all tools)
