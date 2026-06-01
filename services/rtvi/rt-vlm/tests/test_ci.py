# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import subprocess


def test_nvidia_smi_runs():  # Not picked up by -k rtvi_embed or -k rtvi_vlm
    """Test that nvidia-smi command executes successfully."""
    result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, f"nvidia-smi failed with error: {result.stderr}"


def test_nvidia_smi_runs_rtvi_embed():  # Picked up by -k rtvi_embed, not by -k rtvi_vlm
    """Test that nvidia-smi command executes successfully."""
    result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, f"nvidia-smi in rtvi-embed failed with error: {result.stderr}"


def test_nvidia_smi_runs_rtvi_vlm():  # Picked up by -k rtvi_vlm, not by -k rtvi_embed
    """Test that nvidia-smi command executes successfully."""
    result = subprocess.run(["nvidia-smi"], capture_output=True, text=True, timeout=10)

    assert result.returncode == 0, f"nvidia-smi in rtvi-vlm failed with error: {result.stderr}"
