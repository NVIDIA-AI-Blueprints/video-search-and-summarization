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

"""Evaluation V2: several models, the whole application (methodology v2.0).

Layers (imports point down only; ``tests/v2/test_v2_boundaries.py`` enforces it):

- ``evalv2.core``: hashing, content-addressed artifacts, manifests, budgets, retries, instruments,
  checkpoints, config, the network guard and the frozen-V1 guard. Imports nothing above it.
- ``evalv2.adapters``: model and pipeline adapters (provider specifics only, never scoring).
- ``evalv2.tasks``: one package per dimension, each a runner plus scorers. Scorers never touch the network.
- ``evalv2.cli``: ``python -m evalv2 plan | preflight | run --dry-run | audit``.

V1 (the flat modules beside this package) is frozen and untouched.
"""

EVALV2_VERSION = "0.1.0"
