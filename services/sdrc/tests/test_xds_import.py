# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

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
