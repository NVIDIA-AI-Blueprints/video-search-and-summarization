# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Helpers for optional runtime dependencies."""

from typing import Callable


_TORCH_PYTORCH3D_INSTALL_HINT = (
    "Pick ONE torch variant (CPU or CUDA), then install pytorch3d:\n"
    "  # CPU-only torch\n"
    "  pip install torch>=2.10.0 --index-url https://download.pytorch.org/whl/cpu\n"
    "  # or CUDA (GPU) torch\n"
    "  pip install torch>=2.10.0\n"
    "  # pytorch3d (requires torch first)\n"
    "  pip install 'pytorch3d @ git+https://github.com/facebookresearch/pytorch3d.git@33824be' "
    "--no-build-isolation"
)


# OpenCV (`cv2`) is an optional runtime dependency: it is NOT declared as an
# install-require so the published wheel / locked dependency set does not ship
# the OpenCV distribution (which bundles ffmpeg). Callers that need the
# visualization / video code paths install it themselves.
_OPENCV_INSTALL_HINT = (
    "Install the `viz` extra (brings a headless OpenCV build):\n"
    "  pip install 'spatialai-data-utils[viz]'\n"
    "Note: OpenCV ships with bundled ffmpeg libraries and codecs -- review "
    "their licenses and terms of distribution and use before installing."
)


# nuscenes-devkit is an optional dependency (the `eval` extra): it is NOT
# declared as an install-require so the default install / locked dependency
# set stays free of OpenCV, which nuscenes-devkit pulls transitively (and which
# bundles ffmpeg, flagged by OSRB). The eval subpackage and core.boxes.aicity_box
# subclass nuscenes classes at import time, so it cannot be deferred lazily.
_NUSCENES_INSTALL_HINT = (
    "Install the `eval` extra (brings nuscenes-devkit and, transitively, OpenCV):\n"
    "  pip install 'spatialai-data-utils[eval]'\n"
    "  # or directly: pip install nuscenes-devkit==1.2.0"
)


def import_torch(context: str):
    """Import torch for torch-dependent code paths."""
    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            f"{context} requires `torch` to be installed. {_TORCH_PYTORCH3D_INSTALL_HINT}"
        ) from exc
    return torch


def import_box3d_overlap(context: str) -> Callable:
    """Import pytorch3d's box3d overlap op for torch-dependent code paths."""
    try:
        from pytorch3d.ops import box3d_overlap
    except ImportError as exc:
        raise ImportError(
            f"{context} requires `pytorch3d` to be installed. {_TORCH_PYTORCH3D_INSTALL_HINT}"
        ) from exc
    return box3d_overlap


def import_cv2(context: str):
    """Import OpenCV (`cv2`) for visualization / video code paths.

    OpenCV is an optional dependency (see :data:`_OPENCV_INSTALL_HINT`); call
    this at the top of any function that needs ``cv2`` so the package keeps
    importing — and all non-OpenCV code paths keep working — when it is not
    installed. Raises a clear :class:`ImportError` with install instructions
    at call time instead of failing at import time.
    """
    try:
        import cv2
    except ImportError as exc:
        raise ImportError(
            f"{context} requires OpenCV (`cv2`) to be installed. {_OPENCV_INSTALL_HINT}"
        ) from exc
    return cv2


def nuscenes_import_error(context: str) -> ImportError:
    """Build a clear :class:`ImportError` for the optional nuscenes-devkit dep.

    Unlike torch / cv2 (used inside function bodies, so importable lazily), the
    eval subpackage and ``core.boxes.aicity_box`` subclass nuscenes classes at
    module-import time, so nuscenes cannot be deferred — these modules must
    raise at import when it is missing. Wrap the module-level
    ``from nuscenes... import ...`` so this is raised ONLY when nuscenes itself
    is absent, letting any other import failure (e.g. nuscenes installed but
    cv2 missing, or a broken nuscenes submodule) propagate unmasked::

        try:
            from nuscenes... import ...
        except ModuleNotFoundError as exc:
            if exc.name == "nuscenes":
                raise nuscenes_import_error(__name__) from exc
            raise
    """
    return ImportError(
        f"{context} requires the optional evaluation dependency `nuscenes-devkit`. "
        f"{_NUSCENES_INSTALL_HINT}"
    )
