# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VIOS Sensor Management microservice (Python control-plane reimplementation)."""

import os

# Package version. The SERVICE/release version reported by /sensor/version is sourced from the build
# (SENSOR_MS_VERSION, injected from the Makefile VST_VERSION at image build), matching the C++
# service which compiles in VST_VERSION/MMS_VERSION. Falls back to the package version for local runs.
__version__ = "0.1.0"
SERVICE_VERSION = os.environ.get("SENSOR_MS_VERSION", "").strip() or __version__
