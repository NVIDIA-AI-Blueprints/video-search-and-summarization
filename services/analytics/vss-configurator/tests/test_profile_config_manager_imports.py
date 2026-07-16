# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Import-mode regression tests for profile_config_manager."""

import os
import subprocess
import sys


def test_profile_config_manager_imports_as_package_from_app_root():
    """Container-style package import should not require profile_configurator on sys.path."""
    env = os.environ.copy()
    env["CALIBRATION_DIR_MOUNT_PATH"] = "runtime/calibration"
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'app'); import profile_configurator.profile_config_manager",
        ],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
