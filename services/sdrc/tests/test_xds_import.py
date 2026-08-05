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

import importlib
from pathlib import Path
import sys

def _drop_modules(*prefixes):
    for name in tuple(sys.modules):
        if name in prefixes or any(name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(name, None)


def test_envoyxds_imports_without_class_body_side_effects():
    _drop_modules("betterproto", "envoy_data_plane", "grpclib", "lib.xDS")

    module = importlib.import_module("lib.xDS.envoyxDS")

    assert module.envoyxDS is not None
    assert Path(module.__file__).name == "envoyxDS.py"
