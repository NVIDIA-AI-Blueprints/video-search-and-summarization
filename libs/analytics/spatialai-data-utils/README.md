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
  the AICity'25 Track-1 protocol.
- **Result-format converters** (e.g. nuScenes-style results → NVSchema).
- **Video ↔ frame** utilities for single-cam and full-scene multi-cam
  decoding/encoding.

CLI wrappers live under [`tools/`](tools); the same entry points are
importable as a library (see [Library API](#library-api)).

## Package installation

`spatialai_data_utils` supports **Python 3.10+** (tested and released on
3.13). Create a clean conda env for development:

```bash
conda create -n spatialai_data_utils python=3.13 -y
conda activate spatialai_data_utils
```

> **Why `torch` and `pytorch3d` are not declared as install requires:**
> `torch` needs a CUDA variant chosen at install time, and `pytorch3d`
> must be built from source against a matching `torch`
> (`--no-build-isolation`). They are pinned in [`Pipfile`](Pipfile) but
> installed manually for both source and wheel flows below. SDU itself
> works with either CPU-only or CUDA torch — the library uses torch only
> for tensor ops and `torch.utils.data.Dataset`. `fvcore` and `iopath`
> (pytorch3d build deps) are bundled in the wheel.

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
```

Pipenv variant — installs every required runtime dep (everything in
[`Pipfile`](Pipfile)) but still leaves `torch` / `pytorch3d` to you:

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
pip install spatialai-data-utils==2.0.0 \
    --extra-index-url=https://edge.urm.nvidia.com/artifactory/api/pypi/sw-metropolis-pypi/simple
```

If you already have a CUDA `torch` in your environment (e.g. from
sparse4d), skip step 1. Bump `==2.0.0` to the version you want to
install; available versions can be browsed at
<https://edge.urm.nvidia.com/artifactory/sw-metropolis-pypi/spatialai-data-utils/>.

### Option C: Docker

A self-contained CPU image is provided in [`docker/Dockerfile`](docker/Dockerfile)
(builds `pytorch3d` from source in a builder stage and ships only the
runtime deps in the final image):

```bash
docker build -f docker/Dockerfile -t spatialai_data_utils .
docker run --rm -it -v "$PWD":/workspace spatialai_data_utils python
```

### Removing the environment

```bash
conda deactivate
conda remove -n spatialai_data_utils --all
```

## Package layout

The library lives under `spatialai_data_utils/`. The top-level
`__init__.py` stays deliberately bare so callers that only need, say,
`loaders.calibration` don't pay for transitively pulling `cv2` / `tqdm`
via `visualization.render`.

| Sub-package | What's in it |
|---|---|
| [`loaders/`](spatialai_data_utils/loaders) | NVSchema, ground-truth, calibration, Sparse4D pkl, and object-class loaders. |
| [`core/`](spatialai_data_utils/core) | Pure-numpy primitives: 3D box ↔ corner conversions, projection, FOV / frustum helpers, camera utilities. |
| [`datasets/`](spatialai_data_utils/datasets) | Scene/split metadata, frame-path resolvers, AICity'24 / AICity'25 dataset hooks, cloud-utils. |
| [`converters/`](spatialai_data_utils/converters) | Result-format converters (e.g. nuScenes-style results → NVSchema). |
| [`visualization/`](spatialai_data_utils/visualization) | 3D-bbox rendering on camera images and BEV; camera-group / map visualizations; video ↔ frame helpers. |
| [`eval/`](spatialai_data_utils/eval) | Detection (mAP + TP errors) and tracking (HOTA, CLEAR, identity, count) evaluators; AICity'25 Track-1 reproduction. |
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
| [`tools/evaluation/`](tools/evaluation/README.md) | Standalone metric runners on already-produced results, e.g. `evaluate_aicity_mtmc.py` (reproduces the official AICity Challenge MTMC HOTA protocol; year-agnostic, ships the 2025-edition class table + scene mapping by default). |
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
deps are not installed.

## Contributing

Contributions are accepted under Apache-2.0 with a DCO sign-off. See
[`CONTRIBUTING.md`](CONTRIBUTING.md) for full details, including
file-level license-header conventions for new files and for changes to
third-party-derived files.

## License

`spatialai_data_utils` is released under the Apache License, Version 2.0
(see the root [`LICENSE`](LICENSE) file). Third-party attributions and the
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
