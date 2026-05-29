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

import os
import sys

from importlib import import_module


def load_configs_from_file(config_path):
    """
    Load configuration variables by importing a Python file as a module.

    Temporarily adds the file's directory to ``sys.path``, imports the module,
    extracts all attributes that don't start with ``"__"`` into a dictionary,
    and then removes the directory from ``sys.path`` (even if the import
    raises).

    :param config_path: The absolute or relative path to the Python
        configuration file. The filename (without extension) must not
        contain dots.
    :type config_path: str
    :return: A dictionary containing the configuration variables defined in
        the file.
    :rtype: dict
    :raises ValueError: If the module name derived from ``config_path``
        contains a dot.
    """
    config_path = os.path.abspath(config_path)
    # Strip the extension via ``os.path.splitext`` so this works for
    # arbitrary extensions (``.py``, ``.pyx``, ``.cfg``) and for
    # extension-less filenames.  The pre-fix slice ``[:-3]`` chopped a
    # fixed 3 chars off the basename, which silently mangled
    # non-3-char extensions (``config.pyx`` -> ``"config.p"``) and
    # extension-less names (``config`` -> ``"co"``).
    module_name = os.path.splitext(os.path.basename(config_path))[0]
    if "." in module_name:
        raise ValueError(
            f"Dots are not allowed in config file name: {module_name!r}"
        )
    config_dir = os.path.dirname(config_path)
    sys.path.insert(0, config_dir)
    try:
        mod = import_module(module_name)
    finally:
        sys.path.pop(0)
    return {
        name: value
        for name, value in mod.__dict__.items()
        if not name.startswith("__")
    }
