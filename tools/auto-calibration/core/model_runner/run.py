#!/usr/bin/env python3
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

"""Standalone entry point for the model runner.

This script bootstraps the Python path so that the model_runner package
can be imported with its relative imports intact.  It exists because AMC
launch scripts invoke Python files directly (``python path/to/script.py``)
rather than as modules (``python -m core.model_runner.cli``).

Usage (from any working directory):
    python  core/model_runner/run.py  run-amc-compatible -v video.mp4 -o out/
    python  core/model_runner/run.py  benchmark --image img.jpg -o out/ --n-runs 10
"""
import sys
import os

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from core.model_runner.cli import main

if __name__ == "__main__":
    main()
