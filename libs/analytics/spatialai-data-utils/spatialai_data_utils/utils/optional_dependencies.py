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
