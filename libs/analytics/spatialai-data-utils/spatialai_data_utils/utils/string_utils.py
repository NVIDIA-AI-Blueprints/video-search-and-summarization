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
Generic string helpers shared by dataset and evaluation tools.

This module hosts language-level primitives that contain no camera,
scene, or dataset knowledge and can be applied to any string.

Anything that knows about cameras (sorting cam names, extracting cam
IDs, listing cam dirs / ``.h5`` files inside a scene) lives in
:mod:`spatialai_data_utils.datasets.scenes`. Frame-path resolution
helpers live in :mod:`spatialai_data_utils.datasets.frame_paths`.

Main Components:
- natural_sort_key: Build a natural-sort key for mixed text and numbers.
- extract_numbers: Extract every digit run from a string as integers.
- sanitize_string: Replace unsafe path/name characters with underscores.

String Sanitization:
- Preserve alphanumeric characters, dot, underscore, slash, #, and dash.
- Replace other characters with underscores for safer generated names.
"""

import re


def natural_sort_key(s):
    """
    Generate a key for natural sorting of strings containing numbers.

    Ensures that ``'Camera_2'`` comes before ``'Camera_10'`` and
    ``'bev-sensor-2'`` comes before ``'bev-sensor-10'``, instead of
    alphabetical sorting where ``'10'`` comes before ``'2'``. The
    input is coerced to ``str`` so non-string inputs (ints, paths,
    etc.) sort cleanly without an upstream cast.

    :param s: Value to generate a sort key for. Coerced to ``str``.
    :type s: Any
    :return: A list of alternating text and integer parts for comparison.
    :rtype: list
    """
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]


def extract_numbers(text):
    """
    Extract all sequences of digits from a string and return them as integers.

    :param text: The input string.
    :type text: str
    :return: A list of integers found in the string.
    :rtype: list[int]
    """
    return [int(num) for num in re.findall(r"\d+", text)]


def sanitize_string(input_string: str) -> str:
    """
    Sanitizes an input string.

    :param input_string: Input string.
    :type input_string: str
    :return: Sanitized string.
    :rtype: str
    """
    return re.sub(r"[^a-zA-Z0-9\._/#-]", "_", input_string)


__all__ = [
    "extract_numbers",
    "natural_sort_key",
    "sanitize_string",
]
