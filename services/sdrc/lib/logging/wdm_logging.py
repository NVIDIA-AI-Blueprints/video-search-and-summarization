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
import logging
import os
import sys

# Use importlib so PyInstaller bundles stdlib logging.handlers (not confused with lib.logging).
_handlers = importlib.import_module("logging.handlers")
RotatingFileHandler = _handlers.RotatingFileHandler


class WlObjectNameFilter(logging.Filter):
    """Inject workload object name into every log record."""

    def __init__(self, wl_name):
        super().__init__()
        self.wl_name = wl_name

    def filter(self, record):
        record.wl_object_name = self.wl_name
        return True


def wdm_log_formatter():
    return logging.Formatter(
        "%(asctime)s [%(wl_object_name)s] %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def configure_root_logging(wl_log_prefix: str, repo_root: str, max_bytes: int = 200000, backup_count: int = 2) -> None:
    """Root logger: rotating file under repo logs/ + stdout (matches app.py)."""
    log_dir = os.path.join(repo_root, "logs")
    log_file = os.path.join(log_dir, f"{wl_log_prefix}-wdm-services.log")
    os.makedirs(log_dir, exist_ok=True)

    formatter = wdm_log_formatter()
    wl_name_filter = WlObjectNameFilter(wl_log_prefix)
    file_handler = RotatingFileHandler(
        filename=log_file, maxBytes=max_bytes, backupCount=backup_count
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(wl_name_filter)

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(wl_name_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stdout_handler)
