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
Filesystem helpers used across dataset and evaluation tools.

Main Components:
- validate_file_path: Validate file-path format (pattern check only).
- validate_readable_file: Pattern + existence + readability check; usable
  as an ``argparse`` ``type=`` callable.
- load_json_from_file: Load JSON data from files.
- make_dir: Validate and create directories.
- all_files_valid: Check that output files are non-empty.

File Validation:
- Validate path format (alphanumeric, dash, underscore, slash, dot, #, plus).
- Check file existence and readability when used through argparse.
- Reject symlink directory targets before creating directories.
- Integrate with argparse for CLI tools.
"""

import argparse
import json
import logging
import os
import re
from typing import Any

_FILE_PATH_PATTERN = r"^[a-zA-Z0-9_\-\/.#+]+$"


def validate_file_path(input_string: str) -> str:
    """
    Validates whether the input string matches a file path pattern.

    Uses :func:`re.fullmatch` so the entire ``input_string`` must match
    ``_FILE_PATH_PATTERN``; inputs with a trailing ``\\n`` (or any
    characters after the pattern) are rejected. ``re.match`` would
    silently accept ``"foo.json\\n"`` because ``$`` matches before a
    trailing newline by default.
    """
    if re.fullmatch(_FILE_PATH_PATTERN, input_string):
        return input_string
    raise ValueError(f"Invalid file path: {input_string}")


def validate_readable_file(input_string: str) -> str:
    """Validate that *input_string* is a readable file path.

    Performs four checks in order:

    1. The path matches the package-wide allowed-character regex
       (alphanumeric, dash, underscore, slash, dot, ``#``, ``+``).
    2. The path exists on disk.
    3. The path is a regular file (rejects directories, broken
       symlinks, sockets, etc.).
    4. The current process has read permission on it.

    Designed to be plugged into ``argparse`` as a ``type=`` callable
    (e.g. ``parser.add_argument(..., type=validate_readable_file)``);
    failures are raised as :class:`argparse.ArgumentTypeError`, which
    argparse converts to a clean ``parser.error`` message at parse time.
    Direct (non-argparse) callers should catch
    :class:`argparse.ArgumentTypeError` (or its parent :class:`Exception`)
    rather than :class:`ValueError`.

    Replaces the older ``ValidateFile`` ``argparse.Action`` subclass so
    the validation surface in this module is uniformly function-based.
    """
    if not re.fullmatch(_FILE_PATH_PATTERN, input_string):
        raise argparse.ArgumentTypeError(f"Invalid file path: {input_string}")
    if not os.path.exists(input_string):
        raise argparse.ArgumentTypeError(f"File {input_string} does NOT exist.")
    if not os.path.isfile(input_string):
        raise argparse.ArgumentTypeError(
            f"Path {input_string} is not a regular file."
        )
    if not os.access(input_string, os.R_OK):
        raise argparse.ArgumentTypeError(f"File {input_string} is NOT readable.")
    return input_string


def load_json_from_file(file_path: str) -> Any:
    """
    Safely loads JSON data from a file.
    """
    valid_file_path = validate_file_path(file_path)
    try:
        with open(valid_file_path, "r") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON format in file {file_path}: {e}")
    except Exception as e:
        raise ValueError(f"An error occurred while loading file {file_path}: {e}")


def make_dir(dir_path: str) -> None:
    """
    Safely create a directory.
    """
    valid_dir_path = validate_file_path(dir_path)
    if os.path.islink(valid_dir_path):
        raise ValueError(f"Directory path {dir_path} must not be a symbolic link.")

    try:
        os.makedirs(valid_dir_path, exist_ok=True)
    except OSError as e:
        raise ValueError(f"Failed to create directory {dir_path}: {e}")


def all_files_valid(*file_paths):
    """Check if all given files exist and are non-empty."""
    all_valid = True
    for fpath in file_paths:
        if not os.path.isfile(fpath):
            logging.info(f"File does not exist: {fpath}")
            all_valid = False
            continue

        size = os.path.getsize(fpath)
        if size == 0:
            logging.info(f"File: {fpath} is of size {size} bytes")
            all_valid = False
    return all_valid
