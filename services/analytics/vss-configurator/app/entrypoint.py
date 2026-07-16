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

"""
Entrypoint for VSS Configurator (distroless-friendly).

Distroless-friendly startup: optionally runs the
profile configurator, then starts gunicorn. This is used because the image has
no shell (e.g. distroless / Chainguard).
"""
import logging
import os
import subprocess
import sys

# Configure logging to match app format (timestamp, level, name, message)
_level = os.environ.get("LOG_LEVEL", "INFO").upper()
_numeric_level = getattr(logging, _level, logging.INFO)
logging.basicConfig(
    level=_numeric_level,
    format="%(asctime)s - %(levelname)s - %(name)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger(__name__)


def get_env_bool(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip().lower()
    return "true" if value == "true" else ("false" if value == "false" else default)


def main() -> None:
    enable_profile = get_env_bool("ENABLE_PROFILE_CONFIGURATOR", "false")
    enable_sensor_mapping = get_env_bool("ENABLE_SENSOR_CONFIGURATOR", "true")
    port = os.environ.get("PORT", "5000")

    log.info("Starting VSS Configurator...")
    log.info("ENABLE_PROFILE_CONFIGURATOR=%s", enable_profile)

    if enable_profile == "true":
        log.info("Profile configurator enabled - running GPU configurator")
        code = subprocess.run(
            [sys.executable, "profile_configurator/profile_config_manager.py"],
            cwd="/usr/src/app",
        ).returncode
        if code != 0:
            log.error("Profile configurator exited with code %s", code)
            sys.exit(code)

    if enable_sensor_mapping == "true":
        log.info("Starting gunicorn server on port %s", port)
        # Replace current process with gunicorn (no shell required)
        os.execv(
            sys.executable,
            [
                sys.executable,
                "-m",
                "gunicorn",
                "--config",
                "gunicorn_config.py",
                "-b",
                f"0.0.0.0:{port}",
                "sensor_config_manager:app",
            ],
        )
    log.info("Exiting VSS Configurator...")
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.error("Error: %s", e)
        sys.exit(1)
