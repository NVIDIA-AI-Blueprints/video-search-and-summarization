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
Tests for ``spatialai_data_utils.converters.config_loader.load_configs_from_file``.

Pins two pre-fix bugs that the helper now avoids:

1. ``sys.path`` leakage on import failure — the legacy version called
   ``sys.path.pop(0)`` *outside* a ``try/finally``, so any
   ``ImportError`` left the user-provided directory on ``sys.path``
   for the rest of the session (and could shadow other packages).
2. Fixed-length extension stripping (``[:-3]``) silently mangled
   non-3-char extensions (e.g. ``"config.pyx"`` -> ``"config.p"``)
   and extension-less filenames (``"config"`` -> ``"co"``).  The
   helper now uses ``os.path.splitext``.
"""

import os
import sys

import pytest

from spatialai_data_utils.converters.config_loader import load_configs_from_file


def _write(tmp_path, name: str, body: str) -> str:
    """Write ``body`` to ``tmp_path/name`` and return the path."""
    path = os.path.join(str(tmp_path), name)
    with open(path, "w") as f:
        f.write(body)
    return path


class TestLoadConfigsFromFile:
    """Public behaviour of ``load_configs_from_file``."""

    def test_loads_top_level_attributes(self, tmp_path):
        path = _write(
            tmp_path, "good_config.py",
            "FOO = 1\nBAR = 'baz'\n_HIDDEN = 'kept'\n__DUNDER__ = 'dropped'\n",
        )
        cfg = load_configs_from_file(path)
        # Single-underscore names are kept (only ``__``-prefixed ones drop).
        assert cfg == {"FOO": 1, "BAR": "baz", "_HIDDEN": "kept"}

    def test_dotted_module_name_raises_value_error(self, tmp_path):
        """``weird.name.py`` -> stem ``weird.name`` is invalid as a module name."""
        path = _write(tmp_path, "weird.name.py", "X = 1\n")
        with pytest.raises(ValueError, match="weird.name"):
            load_configs_from_file(path)


class TestLoadConfigsFromFileSysPathHygiene:
    """The helper must not leave the config directory on ``sys.path``."""

    def test_sys_path_unchanged_on_success(self, tmp_path):
        before = list(sys.path)
        _write(tmp_path, "ok_config.py", "X = 1\n")
        load_configs_from_file(os.path.join(str(tmp_path), "ok_config.py"))
        assert sys.path == before, (
            "load_configs_from_file leaked the config directory onto "
            "sys.path on success."
        )

    def test_sys_path_unchanged_on_import_failure(self, tmp_path):
        """``try/finally`` keeps ``sys.path`` clean even when ``import_module`` raises.

        Pre-fix the ``sys.path.pop(0)`` line ran only on the happy path,
        so a missing module or broken config silently polluted
        ``sys.path`` for the rest of the process.
        """
        before = list(sys.path)
        missing = os.path.join(str(tmp_path), "nonexistent.py")
        with pytest.raises(ModuleNotFoundError):
            load_configs_from_file(missing)
        assert sys.path == before, (
            "load_configs_from_file leaked the config directory onto "
            "sys.path when import_module raised."
        )


class TestLoadConfigsFromFileExtensionHandling:
    """``os.path.splitext`` correctly strips non-3-char extensions."""

    def test_pyx_extension_stripped_cleanly(self, tmp_path):
        """A ``.pyx`` file's stem is ``myconfig``, not ``myconfig.``.

        The legacy ``[:-3]`` slice would have produced ``"myconfig."``
        (trailing dot), tripping the dot-name guard with a confusing
        message; ``os.path.splitext`` produces a clean ``"myconfig"``.
        ``import_module`` still raises ``ModuleNotFoundError`` because
        ``.pyx`` files aren't natively importable, but the *error* is
        unambiguous about which name it tried.
        """
        path = _write(tmp_path, "myconfig.pyx", "")
        with pytest.raises(ModuleNotFoundError, match=r"'myconfig'"):
            load_configs_from_file(path)

    def test_extension_less_filename_does_not_silently_truncate(self, tmp_path):
        """``"config"`` (no extension) must not be sliced to ``"co"``.

        The legacy ``[:-3]`` slice would have looked up a ``co``
        module — silently importing whatever happens to be on
        ``sys.path`` under that name, instead of raising.  With
        ``os.path.splitext`` the stem is ``"config"`` and we get a
        clean ``ModuleNotFoundError``.
        """
        path = _write(tmp_path, "config", "")
        with pytest.raises(ModuleNotFoundError, match=r"'config'"):
            load_configs_from_file(path)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
