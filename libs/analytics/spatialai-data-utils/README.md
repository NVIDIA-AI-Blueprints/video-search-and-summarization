# `spatialai_data_utils`: Utilities for SpatialAI Datasets

`spatialai_data_utils` (SDU) is a Python utility package for working with
NVIDIA SpatialAI / MTMC (multi-target, multi-camera) datasets in warehouse,
retail, and hospital environments. It provides:

- **Loaders** for NVSchema, ground-truth, calibration, and Sparse4D pkl
  formats.
- **Calibration + camera grouping** helpers, including BEV (Bird's Eye
  View) group origin / dimensions calculation and per-group fan-out.
- **Pure-numpy 3D ↔ 2D geometry** (`box3d_to_corners`,
  `project_boxes_3d_to_2d`, frustum / FOV helpers) with no `mmdet3d`
  dependency.
- **Multi-camera 3D bounding-box visualization** on camera images and BEV.
- **Evaluation** for detection (nuScenes-style mAP + TP errors) and
  tracking (HOTA, CLEAR, identity, count), including a reproduction of
  the AICity Challenge MTMC Track-1 protocol (2025 + 2026 editions).
- **Result-format converters** (e.g. nuScenes-style results → NVSchema).
- **Video ↔ frame** utilities for single-cam and full-scene multi-cam
  decoding/encoding.

CLI wrappers live under [`tools/`](tools); the same entry points are
importable as a library (see [Library API](#library-api)).

## Package installation

`spatialai_data_utils` supports **Python 3.11+** (tested and released on
3.13). Create a clean conda env for development:

```bash
conda create -n spatialai_data_utils python=3.13 -y
conda activate spatialai_data_utils
```

> **Why `torch` and `pytorch3d` are not declared as install requires:**
> `torch` needs a CUDA variant chosen at install time, and `pytorch3d`
> must be built from source against a matching `torch`
> (`--no-build-isolation`). They are documented in [`Pipfile`](Pipfile) but
> installed manually for both source and wheel flows below. SDU itself
> works with either CPU-only or CUDA torch — the library uses torch (with
> `pytorch3d`) only for the **3D-IoU computation in the `eval` subpackage**
> (`eval.common.utils.iou_3d` / `iou_3d_matrix` and the HOTA
> `_calculate_3DBBox_ious`); those functions raise a clear `ImportError`
> at call time when the deps are missing, and the rest of the library —
> including the rest of `eval` — runs without them. `torch` + `pytorch3d`
> ship in the `full` extra (`full` = `viz` + `eval` + torch + pytorch3d).
> Because `pytorch3d` must build against an already-installed `torch` (it
> can't install under build isolation), `full` must be installed with the
> manual build flow, e.g.
> `pip install --no-build-isolation 'spatialai-data-utils[full]'` (with
> torch already present); `viz` and `eval` stay plain `pip install`-able.
> `fvcore` and `iopath` (pytorch3d build deps) are bundled in the wheel.
>
> **Why OpenCV (`cv2`) is not declared as an install require:**
> OpenCV bundles libraries and codecs that carry license and distribution
> restrictions; read the license and the terms of distribution and use before
> installing it. It is only needed by the visualization / video code paths
> (`spatialai_data_utils.visualization`, `tools/video_utils`), which raise a
> clear `ImportError` at call time when it is missing. Install the `viz` extra
> only if you need those features:
>
> ```bash
> pip install 'spatialai-data-utils[viz]'
> ```
>
> **Why `nuscenes-devkit` is not declared as an install require:**
> The evaluation stack — `spatialai_data_utils.eval` (detection mAP,
> tracking, HOTA, AICity MTMC) and `core.boxes.aicity_box` — builds on the
> nuScenes dev-kit, which pulls OpenCV transitively (hence `ffmpeg`, per
> above). To keep the default install / [`Pipfile.lock`](Pipfile.lock) free
> of OpenCV, nuScenes is an **opt-in `eval` extra**. These modules subclass
> nuScenes classes at import time, so — unlike the visualization paths —
> they cannot degrade gracefully: they raise a clear `ImportError` at
> **import** time when nuScenes is missing. Install the extra (which also
> brings OpenCV) if you need evaluation:
>
> ```bash
> pip install 'spatialai-data-utils[eval]'
> ```

### Option A: Install from source (recommended for development)

```bash
# 1. Pick ONE torch variant
pip install 'torch>=2.10.0' --index-url https://download.pytorch.org/whl/cpu  # CPU-only
# or
pip install 'torch>=2.10.0'                                                    # CUDA

# 2. Build pytorch3d against that torch
pip install 'pytorch3d @ git+https://github.com/facebookresearch/pytorch3d.git@33824be' \
    --no-build-isolation

# 3. Install SDU (editable)
pip install --no-cache-dir -e ./release
# ...or with optional extras, e.g. the evaluation stack (nuScenes, also
# pulls OpenCV): pip install --no-cache-dir -e './release[eval]'
```

Pipenv variant — installs every required runtime dep (everything in
[`Pipfile`](Pipfile)) but still leaves `torch` / `pytorch3d` to you (and,
being optional extras, the `eval` / `viz` features too):

```bash
pip install pipenv
pipenv install
# then steps 1 + 2 above inside the pipenv shell
```

### Option B: Install from prebuilt wheel

Wheels are published to NVIDIA's customer-facing edge artifactory mirror.
Point `pip` at the mirror's PyPI-compatible simple index as an extra
index:

```bash
pip install 'torch>=2.10.0' --index-url https://download.pytorch.org/whl/cpu   # or CUDA build
pip install 'pytorch3d @ git+https://github.com/facebookresearch/pytorch3d.git@33824be' \
    --no-build-isolation
pip install spatialai-data-utils==2.0.1 \
    --extra-index-url=https://edge.urm.nvidia.com/artifactory/api/pypi/sw-metropolis-pypi/simple
# add optional extras as needed, e.g. the evaluation stack:
#   pip install 'spatialai-data-utils[eval]==2.0.1' --extra-index-url=...
```

If you already have a CUDA `torch` in your environment (e.g. from
sparse4d), skip step 1. Bump `==2.0.1` to the version you want to
install; available versions can be browsed at
<https://edge.urm.nvidia.com/artifactory/sw-metropolis-pypi/spatialai-data-utils/>.

### Option C: Docker

A self-contained CPU image is provided in [`docker/Dockerfile`](docker/Dockerfile)
(builds `pytorch3d` from source in a builder stage and ships only the
runtime deps in the final image):

```bash
docker build -f docker/Dockerfile -t spatialai_data_utils .
```

The image uses a custom OpenCV build compiled without FFmpeg, GStreamer, or
their codecs. Evaluation and image-based visualization are supported, but
OpenCV video reading and writing are not. For video visualization, use a
clean environment with `pip install "spatialai-data-utils[viz]"`, which
pulls OpenCV with bundled FFmpeg libraries and codecs; review their licenses
and terms of distribution and use before proceeding.

Tool-specific Docker run examples live in the validation_and_evaluation README, such as
[`tools/validation_and_evaluation/README.md`](tools/validation_and_evaluation/README.md).

### Removing the environment

```bash
conda deactivate
conda remove -n spatialai_data_utils --all
```

## Package layout

The library lives under `spatialai_data_utils/`. The top-level
`__init__.py` stays deliberately bare so callers that only need, say,
`loaders.calibration` don't pay for transitively pulling the
visualization stack (`tqdm`, the drawing helpers) via
`visualization.render`. OpenCV (`cv2`) goes a step further — it is
imported lazily inside the functions that use it (see
`utils.optional_dependencies.import_cv2`), so even importing
`visualization.render` never requires it.

| Sub-package | What's in it |
|---|---|
| [`loaders/`](spatialai_data_utils/loaders) | NVSchema, ground-truth, calibration, Sparse4D pkl, and object-class loaders. |
| [`core/`](spatialai_data_utils/core) | Pure-numpy primitives: 3D box ↔ corner conversions, projection, FOV / frustum helpers, camera utilities. |
| [`datasets/`](spatialai_data_utils/datasets) | Scene/split metadata, frame-path resolvers, AICity'24 / '25 / '26 dataset hooks, cloud-utils. |
| [`converters/`](spatialai_data_utils/converters) | Result-format converters (e.g. nuScenes-style results → NVSchema). |
| [`visualization/`](spatialai_data_utils/visualization) | 3D-bbox rendering on camera images and BEV; camera-group / map visualizations; video ↔ frame helpers. |
| [`eval/`](spatialai_data_utils/eval) | Detection (mAP + TP errors) and tracking (HOTA, CLEAR, identity, count) evaluators; AICity Challenge MTMC reproduction (2025 + 2026 editions). |
| [`validation/`](spatialai_data_utils/validation) | Schema / dataset structural validators. |
| [`utils/`](spatialai_data_utils/utils) | Cross-cutting helpers (dataset splits, etc.). |
| [`schemas/`](spatialai_data_utils/schemas) | JSON schemas for input validation (e.g. `calibration.json`). |
| [`constants.py`](spatialai_data_utils/constants.py) | Package-wide constants. |

## Tools

CLI tools live under [`tools/`](tools); each subdirectory ships its own
README with full usage, arguments, and examples.

| Directory | Purpose |
|-----------|---------|
| [`tools/camera_grouping/`](tools/camera_grouping/README.md) | Camera grouping, clustering, and BEV group-origin / dimensions calculation for multi-camera tracking systems. |
| [`tools/visualization/`](tools/visualization/README.md) | 3D-bbox rendering (`draw_3dbbox.py`, `draw_3dbbox_batch.py`) and dual-view camera placement from calibration (`draw_camera_placement.py`: 3D frustums + BEV coverage). |
| [`tools/projection/`](tools/projection/README.md) | Project NVSchema 3D bounding boxes to 2D image-space corners for a target camera (`project_bbox3d_to_2d.py`). Pure-numpy, no `mmdet3d` dependency. |
| [`tools/video_utils/`](tools/video_utils/README.md) | Video ↔ per-frame-image conversion: single-video decode (`video2frame.py`) and encode (`frame2video.py`), plus multi-camera scene-wide parallel decode (`video2frame_scene.py`) and stacked-grid encode (`frame2video_scene.py`). |
| [`tools/evaluation/`](tools/evaluation/README.md) | Standalone metric runners on already-produced results, e.g. `evaluate_aicity_mtmc.py` (reproduces the official AICity Challenge MTMC HOTA protocol; supports the 2025 + 2026 editions via `--edition`, default 2026). |
| [`tools/validation_and_evaluation/`](tools/validation_and_evaluation/README.md) | End-to-end validation + Sparse4D BEV-detection evaluation on MTMC data pulled from S3 (`run_validation_and_evaluation.py`). |

## Library API

The library entry points the CLIs wrap are importable from their
defining sub-modules:

```python
from spatialai_data_utils.visualization.render import (
    visualize_nvschema,
    visualize_3dbbox,
    draw_bev_objects_bbox_in_image,
)
from spatialai_data_utils.core.geometry.projection import (
    project_bev_objects_bbox_in_image,
    project_boxes_3d_to_2d,
)
from spatialai_data_utils.core.boxes.box_3d import (
    box3d_to_corners,
    check_nvschema_coords_len,
)
from spatialai_data_utils.loaders.calibration import (
    load_calib_into_dict,                            # flat {cam: calib}
    load_calib_into_dict_with_group_memberships,     # flat + {group_name: [cams]} for BEV fan-out
    load_calib_into_dict_from_pkl,
)
from spatialai_data_utils.loaders.nvschema import load_nvschema
from spatialai_data_utils.datasets.frame_paths import (
    resolve_frame_path,                              # single-camera image-path resolver
    get_frame_paths_of_multi_cameras,                # scene-wide image-path lookup
)
```

Each function is documented in its module docstring. See the per-tool
READMEs under `tools/` for the wrapping CLI usage.

## Tests and benchmarks

```bash
pytest -q tests/                  # unit tests; mirror the library layout under tests/
python benchmarks/benchmark_frustum.py   # see benchmarks/README.md
```

Tests are organised to mirror the library tree (`tests/core/`,
`tests/eval/`, `tests/loaders/`, `tests/visualization/`, ...). A handful
of tests exercise the optional `torch` / `pytorch3d` path
(`tests/test_optional_torch_deps.py`); they skip cleanly if the optional
deps are not installed. The optional OpenCV (`cv2`) path has its own
suite (`tests/test_optional_opencv_deps.py`): it verifies the package and
the `visualization` sub-package import without `cv2`, and that the
visualization / video functions raise a clear `ImportError` at call time
when it is absent. The optional nuScenes (`eval` extra) path is covered by
`tests/test_optional_nuscenes_deps.py`: it verifies the package and the
non-eval sub-packages import without `nuscenes`, and that the `eval`
modules / `core.boxes.aicity_box` raise a clear `ImportError` pointing at
the `eval` extra when it is absent.

## Contributing

Contributions are accepted under Apache-2.0 with a DCO sign-off. See
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md) for full details, including
file-level license-header conventions for new files and for changes to
third-party-derived files.

## License

`spatialai_data_utils` is released under the Apache License, Version 2.0
(see the root [`LICENSE`](../../../LICENSE) file). Third-party attributions and the
full upstream license texts for adapted/vendored code are collected in the
root [`NOTICE`](NOTICE) file, and per-dependency licenses for everything
installed at runtime (plus optional extras) are listed in
[`3rdParty_Licenses.md`](3rdParty_Licenses.md).

## Acknowledgements

This project would not exist without the following upstream open-source
projects. The full attribution (file lists, upstream URLs, copyright lines,
and license texts) is in the root [`NOTICE`](NOTICE) file; this section is a
short pointer for users skimming the README.

### nuScenes dev-kit

Parts of `spatialai_data_utils/eval/` (and, where present, the vendored
`spatialai_data_utils/nuscenes/` namespace) are adapted from the
[nuScenes dev-kit](https://github.com/nutonomy/nuscenes-devkit/tree/1.2.0),
Copyright 2021 Motional, licensed under the Apache License, Version 2.0.
The following files preserve the original Motional copyright alongside
the NVIDIA modifications:

- `spatialai_data_utils/eval/common/loaders.py`
- `spatialai_data_utils/eval/detection/data_classes.py`
- `spatialai_data_utils/eval/detection/evaluate.py`
- `spatialai_data_utils/eval/tracking/aic24_eval.py`
- `spatialai_data_utils/eval/tracking/algo.py`
- `spatialai_data_utils/eval/tracking/data_classes.py`
- `spatialai_data_utils/eval/tracking/loaders.py`

### TrackEval

The HOTA evaluator under `spatialai_data_utils/eval/tracking/hota/` is
adapted from [TrackEval](https://github.com/kovalp/TrackEval/tree/1.3.0),
Copyright (c) 2020 Jonathon Luiten, licensed under the MIT License. Each
file in that subtree is dual-licensed `MIT AND Apache-2.0`: the MIT terms
cover the upstream TrackEval portions; the NVIDIA modifications are
released under Apache-2.0.
