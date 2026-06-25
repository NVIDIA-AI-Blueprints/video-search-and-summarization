# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import argparse
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


class ValidateFile(argparse.Action):
    """
    Custom argparse action to validate file paths.
    Checks if the file exists and has read access.
    Raises an error if validation fails.

    Examples::
        >>> parser = argparse.ArgumentParser()
        >>> parser.add_argument('--file', action=ValidateFile)
        >>> args = parser.parse_args(['--file', 'valid_file.txt'])
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if not os.path.exists(values) or not os.path.isfile(values):
            parser.error(f"Please enter a valid file path. Got: {values}")
        elif not os.access(values, os.R_OK):
            parser.error(f"File {values} doesn't have read access")
        setattr(namespace, self.dest, values)


def validate_file_path(input_string: str) -> str:
    """
    Validates whether the input string matches a file path pattern.
    Only allows alphanumeric characters, underscores, hyphens, slashes, dots, and hashes.

    :param str input_string: Input string to validate as a file path.
    :return str: Validated file path if it matches the pattern.
    :raises argparse.ArgumentTypeError: If the input string contains invalid characters.

    Examples::
        >>> path = validate_file_path("valid/path/file.txt")
        >>> print(f"Valid path: {path}")
        >>> # Raises ArgumentTypeError
        >>> validate_file_path("invalid*path")
    """
    file_path_pattern = r"^[a-zA-Z0-9_\-\/.#]+$"

    if re.match(file_path_pattern, input_string):
        return input_string
    else:
        raise argparse.ArgumentTypeError(f"Invalid file path: {input_string}")


def load_json_from_file(file_path: str) -> Any:
    """
    Loads and parses JSON data from a file.
    Validates the file path before attempting to read.

    :param str file_path: Path to the JSON file to load.
    :return Any: Parsed JSON data as a Python object.
    :raises FileNotFoundError: If the file doesn't exist.
    :raises json.JSONDecodeError: If the file contains invalid JSON.

    Examples::
        >>> data = load_json_from_file("config.json")
        >>> print(f"Loaded data: {data}")
    """
    valid_file_path = validate_file_path(file_path)
    with open(valid_file_path) as f:
        data = json.load(f)
    logger.info(f"Loaded JSON file: {valid_file_path}")
    return data
