# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""DEFT backend (VSS-AOI).

The DEFT backend is the client side of the AOI flywheel loop: it drives the
external DEFT fine-tuning service and the AOI Storage Backend, and exposes them
through the neutral ``BackendProvider`` seam so the AOI pipeline stays
backend-agnostic. Selected at build/runtime by ``backend.kind == "deft"``.

**Skeleton (Plan 1):** every method is a typed stub raising ``NotImplementedError``
with a ``TODO`` marking where the implementation lands. Nothing here is imported
unless the DEFT backend is selected (lazy import in ``src/backends/factory.py``).
"""
