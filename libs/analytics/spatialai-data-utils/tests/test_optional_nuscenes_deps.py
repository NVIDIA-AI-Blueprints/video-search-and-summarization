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
Tests verifying that nuScenes (the ``eval`` extra) is an *optional* dependency.

``nuscenes-devkit`` pulls OpenCV transitively (ffmpeg, flagged by OSRB), so it
is NOT a declared install-require — it is the opt-in ``eval`` extra. The
package and every non-eval code path must import and work without it.

Unlike ``torch`` / ``cv2`` (used inside function bodies, so deferrable), the
eval subpackage and ``core.boxes.aicity_box`` *subclass* nuScenes classes at
module-import time, so they cannot degrade lazily: they must raise a clear
``ImportError`` (pointing at the ``eval`` extra) at **import** time when
nuScenes is missing — not a bare ``ModuleNotFoundError``.

These tests spawn a fresh subprocess with an ``sys.meta_path`` blocker that
simulates ``nuscenes`` being absent, so they run even where nuScenes *is*
installed.
"""

import importlib.util
import subprocess
import sys
import textwrap

import pytest


# Script prelude installing a meta-path blocker that simulates an absent
# ``nuscenes`` package. Any ``import nuscenes`` (or submodule) raises ImportError.
_BLOCKER_PRELUDE = textwrap.dedent(
    """
    import sys

    for _mod in list(sys.modules):
        if _mod == 'nuscenes' or _mod.startswith('nuscenes.'):
            del sys.modules[_mod]

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == 'nuscenes' or name.startswith('nuscenes.'):
                # Mirror real import machinery: a missing module raises
                # ModuleNotFoundError with ``name`` set (the guards key off it).
                raise ModuleNotFoundError(f'simulated missing: {name}', name=name)
            return None

    sys.meta_path.insert(0, _Blocker())
    """
).strip()


def _run_without_nuscenes(body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a fresh Python subprocess with ``nuscenes`` blocked."""
    script = _BLOCKER_PRELUDE + "\n" + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )


# Eval / nuScenes-dependent modules that must raise a clear ImportError.
_EVAL_MODULES = [
    "spatialai_data_utils.core.boxes.aicity_box",
    "spatialai_data_utils.eval.common.utils",
    "spatialai_data_utils.eval.common.loaders",
    "spatialai_data_utils.eval.detection.evaluate",
    "spatialai_data_utils.eval.detection.loaders",
    "spatialai_data_utils.eval.detection.data_classes",
    "spatialai_data_utils.eval.tracking.data_classes",
    "spatialai_data_utils.eval.tracking.loaders",
    "spatialai_data_utils.eval.tracking.algo",
    "spatialai_data_utils.eval.tracking.aic24_eval",
    "spatialai_data_utils.eval.tracking.hota.hota_eval",
]


# ===================================================================
# Package / non-eval imports work without nuScenes
# ===================================================================
class TestImportsWithoutNuscenes:
    def test_top_level_package_imports(self):
        result = _run_without_nuscenes(
            """
            import spatialai_data_utils
            import sys
            assert 'nuscenes' not in sys.modules, 'nuscenes should not be imported'
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_non_eval_subpackages_import(self):
        """Loaders / visualization / datasets / core geometry must import
        without nuScenes (they do not touch the eval stack)."""
        result = _run_without_nuscenes(
            """
            import importlib
            mods = [
                'spatialai_data_utils.loaders.calibration',
                'spatialai_data_utils.loaders.nvschema',
                'spatialai_data_utils.visualization',
                'spatialai_data_utils.datasets.scenes',
                'spatialai_data_utils.core.geometry.projection',
                'spatialai_data_utils.core.boxes.box_3d',
            ]
            failures = []
            for name in mods:
                try:
                    importlib.import_module(name)
                except Exception as exc:
                    failures.append((name, f'{type(exc).__name__}: {exc}'))
            assert not failures, failures
            import sys
            assert 'nuscenes' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


# ===================================================================
# Eval modules raise a clear, actionable ImportError at import time
# ===================================================================
class TestEvalModulesRaise:
    @pytest.mark.parametrize("module", _EVAL_MODULES)
    def test_module_raises_friendly_import_error(self, module):
        result = _run_without_nuscenes(
            f"""
            import importlib
            try:
                importlib.import_module({module!r})
            except ImportError as exc:
                msg = str(exc)
                # Must be the friendly error pointing at the eval extra,
                # NOT a bare 'No module named nuscenes'.
                assert "spatialai-data-utils[eval]" in msg, msg
                assert "nuscenes-devkit" in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError({module!r} + ' did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout


# ===================================================================
# Sanity: when nuScenes IS available, the eval modules import in-process.
# ===================================================================
# ``_HAS_NUSCENES`` is import-based: it is True only when nuscenes is both
# installed AND importable (importing it pulls cv2). Use it only for the
# sanity class below that actually imports the eval modules end-to-end.
_HAS_NUSCENES = True
try:
    import nuscenes  # noqa: F401
except ImportError:
    _HAS_NUSCENES = False

# ``_NUSCENES_INSTALLED`` is installed-based: find_spec locates the package
# without executing its ``__init__`` (so it does NOT pull cv2). The cv2-missing
# test below must gate on this — otherwise it would be skipped in exactly the
# "nuscenes installed but cv2 missing" environment it is meant to cover.
_NUSCENES_INSTALLED = importlib.util.find_spec("nuscenes") is not None


@pytest.mark.skipif(
    not _HAS_NUSCENES,
    reason="nuscenes-devkit not installed; skipping 'with-nuscenes' sanity checks.",
)
class TestWithNuscenesAvailable:
    @pytest.mark.parametrize("module", _EVAL_MODULES)
    def test_eval_module_imports(self, module):
        import importlib

        importlib.import_module(module)  # must not raise


# ===================================================================
# A non-nuscenes import failure must NOT be masked as "install nuscenes".
# ===================================================================
@pytest.mark.skipif(
    not _NUSCENES_INSTALLED,
    reason="needs nuscenes installed to exercise the 'nuscenes present, cv2 missing' path.",
)
class TestNonNuscenesFailureNotMasked:
    """nuScenes' top-level ``__init__`` does ``import cv2``. If nuScenes is
    installed but cv2 is missing, the guard must re-raise the real cv2 error
    rather than rewrite it to the 'install the eval extra' message."""

    def test_missing_cv2_is_not_masked(self):
        script = textwrap.dedent(
            """
            import sys
            for _m in list(sys.modules):
                if _m == 'cv2' or _m.startswith('cv2.'):
                    del sys.modules[_m]

            class _Blocker:
                def find_spec(self, name, path=None, target=None):
                    if name == 'cv2' or name.startswith('cv2.'):
                        raise ModuleNotFoundError(f'No module named {name!r}', name=name)
                    return None

            sys.meta_path.insert(0, _Blocker())

            import importlib
            try:
                importlib.import_module('spatialai_data_utils.eval.common.utils')
            except ImportError as exc:
                msg = str(exc)
                # Must NOT be rewritten to the nuscenes 'eval extra' message, and
                # should point at the real culprit (cv2).
                assert 'spatialai-data-utils[eval]' not in msg, 'cv2 failure masked: ' + msg
                assert getattr(exc, 'name', None) == 'cv2' or 'cv2' in msg, msg
                print('RERAISED:', getattr(exc, 'name', None))
            else:
                raise AssertionError('expected ImportError when cv2 is missing')
            """
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stderr
        assert "RERAISED:" in result.stdout
