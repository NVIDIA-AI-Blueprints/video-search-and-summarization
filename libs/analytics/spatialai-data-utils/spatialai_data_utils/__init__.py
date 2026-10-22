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

"""SpatialAI Data Utils Package.

Most of this package only requires numpy/scipy-style dependencies and can be
used without ``torch`` or ``pytorch3d`` installed. A small number of functions
(notably 3D IoU helpers in :mod:`spatialai_data_utils.eval.common.utils` and
the 3D IoU path in the MTMC tracking datasets) do require ``torch`` and
``pytorch3d``; those functions will raise a clear :class:`ImportError` at
call time if the optional dependencies are missing.

To enable the torch/pytorch3d-dependent functionality, install:

  # CPU-only torch
  pip install torch>=2.10.0 --index-url https://download.pytorch.org/whl/cpu

  # CUDA (GPU) torch — pick ONE variant; do not install alongside the CPU build
  pip install torch>=2.10.0

  # pytorch3d (requires torch first)
  pip install 'pytorch3d @ git+https://github.com/facebookresearch/pytorch3d.git@33824be' --no-build-isolation
"""
