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
Tests verifying that `torch` and `pytorch3d` are *optional* dependencies.

The package must import and non-torch code paths must work even when the
optional dependencies are missing. Torch/pytorch3d-dependent functions must
raise a clear ``ImportError`` at call time (not at import time).

These tests spawn a fresh subprocess with an ``sys.meta_path`` blocker that
simulates ``torch`` and ``pytorch3d`` being absent. This lets the tests run
on machines where the deps *are* installed, while still validating the
optional-dependency behavior.

NOTE: This module intentionally does NOT import ``torch`` or any submodule
that transitively imports ``torch`` at top level, so the test module itself
remains importable without those optional deps.
"""

import subprocess
import sys
import textwrap

import pytest


# Script prelude that installs a meta-path blocker simulating absent
# torch/pytorch3d packages. Any ``import torch`` (or any submodule) and
# any ``import pytorch3d`` (or any submodule) will raise ImportError.
_BLOCKER_PRELUDE = textwrap.dedent(
    """
    import sys

    for _mod in list(sys.modules):
        if (
            _mod == 'torch'
            or _mod.startswith('torch.')
            or _mod == 'pytorch3d'
            or _mod.startswith('pytorch3d.')
        ):
            del sys.modules[_mod]

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if (
                name == 'torch'
                or name.startswith('torch.')
                or name == 'pytorch3d'
                or name.startswith('pytorch3d.')
            ):
                raise ImportError(f'simulated missing: {name}')
            return None

    sys.meta_path.insert(0, _Blocker())
    """
).strip()


def _run_without_torch(body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a fresh Python subprocess with torch/pytorch3d blocked.

    Returns the completed process so tests can assert on return code, stdout,
    and stderr.
    """
    script = _BLOCKER_PRELUDE + "\n" + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )


# ===================================================================
# Package-level import behavior
# ===================================================================
class TestPackageImportsWithoutTorch:
    """The top-level package must import cleanly without torch/pytorch3d."""

    def test_top_level_package_imports(self):
        result = _run_without_torch(
            """
            import spatialai_data_utils
            print('OK')
            # And neither dep should have sneaked in.
            import sys
            assert 'torch' not in sys.modules, 'torch should not be imported'
            assert 'pytorch3d' not in sys.modules, 'pytorch3d should not be imported'
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_eval_common_utils_imports(self):
        """`eval.common.utils` must import even though it contains torch-dependent funcs."""
        result = _run_without_torch(
            """
            from spatialai_data_utils.eval.common import utils
            assert hasattr(utils, 'iou_3d')
            assert hasattr(utils, 'iou_3d_matrix')
            assert hasattr(utils, '_boxes_to_corners')
            import sys
            assert 'torch' not in sys.modules
            assert 'pytorch3d' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_eval_data_classes_import(self):
        """`eval.tracking.data_classes` and `eval.detection.data_classes` must import
        without torch, because they only use ``iou_3d`` lazily at call time."""
        result = _run_without_torch(
            """
            from spatialai_data_utils.eval.tracking import data_classes as tdc  # noqa
            from spatialai_data_utils.eval.detection import data_classes as ddc  # noqa
            import sys
            assert 'torch' not in sys.modules
            assert 'pytorch3d' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_eval_tracking_algo_imports(self):
        # `eval.tracking.algo` does ``import pandas`` at module level (guarded
        # by a ``raise unittest.SkipTest`` for backwards-compat), so pandas
        # must be installed in the test environment for this import path.
        result = _run_without_torch(
            """
            from spatialai_data_utils.eval.tracking import algo  # noqa
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_core_modules_import(self):
        """Every submodule of ``spatialai_data_utils.core`` (except pre-existing
        non-torch-related issues) must import without torch/pytorch3d."""
        result = _run_without_torch(
            """
            import importlib
            import pkgutil
            import spatialai_data_utils.core as core

            failures = []
            for _, name, _ in pkgutil.walk_packages(core.__path__, prefix='spatialai_data_utils.core.'):
                # Skip projection.py: it imports mmdet3d (separate optional dep,
                # unrelated to the torch/pytorch3d decoupling exercised here).
                if name == 'spatialai_data_utils.core.geometry.projection':
                    continue
                try:
                    importlib.import_module(name)
                except Exception as exc:
                    failures.append((name, f'{type(exc).__name__}: {exc}'))

            assert not failures, failures
            import sys
            assert 'torch' not in sys.modules
            assert 'pytorch3d' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


# ===================================================================
# Non-torch code paths keep working
# ===================================================================
class TestNonTorchCodePathsWork:
    def test_boxes_to_corners_runs_without_torch(self):
        """``_boxes_to_corners`` is pure numpy and must run without torch."""
        result = _run_without_torch(
            """
            import numpy as np
            from spatialai_data_utils.eval.common.utils import _boxes_to_corners

            corners = _boxes_to_corners(
                translations=[(0, 0, 0)],
                sizes=[(1, 2, 3)],
                rotations=[(1, 0, 0, 0)],
            )
            assert corners.shape == (1, 8, 3)
            # Identity rotation: min and max should match half-extents.
            np.testing.assert_allclose(
                corners.min(axis=1), [[-0.5, -1.0, -1.5]], atol=1e-12
            )
            np.testing.assert_allclose(
                corners.max(axis=1), [[0.5, 1.0, 1.5]], atol=1e-12
            )
            import sys
            assert 'torch' not in sys.modules
            assert 'pytorch3d' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_iou_3d_matrix_empty_case_runs_without_torch(self):
        """Empty-list shortcut in ``iou_3d_matrix`` must not require torch."""
        result = _run_without_torch(
            """
            from spatialai_data_utils.eval.common.utils import iou_3d_matrix

            out_mn = iou_3d_matrix([], [])
            assert out_mn.shape == (0, 0)

            # We can't construct concrete EvalBoxes without pulling nuscenes
            # here, but the empty-empty path alone covers the non-torch branch.
            import sys
            assert 'torch' not in sys.modules
            assert 'pytorch3d' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


# ===================================================================
# Torch-dependent functions raise clear ImportError at call time
# ===================================================================
class TestTorchDependentFunctionsRaise:
    def test_iou_3d_raises_import_error(self):
        result = _run_without_torch(
            """
            from nuscenes.eval.detection.data_classes import DetectionBox
            from spatialai_data_utils.eval.common.utils import iou_3d

            b1 = DetectionBox(
                sample_token='s',
                translation=(0, 0, 0),
                size=(1, 1, 1),
                rotation=(1, 0, 0, 0),
            )
            b2 = DetectionBox(
                sample_token='s',
                translation=(0, 0, 0),
                size=(1, 1, 1),
                rotation=(1, 0, 0, 0),
            )
            try:
                iou_3d(b1, b2)
            except ImportError as exc:
                msg = str(exc)
                assert 'torch' in msg or 'pytorch3d' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('iou_3d did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_iou_3d_matrix_raises_import_error(self):
        result = _run_without_torch(
            """
            from nuscenes.eval.detection.data_classes import DetectionBox
            from spatialai_data_utils.eval.common.utils import iou_3d_matrix

            b = DetectionBox(
                sample_token='s',
                translation=(0, 0, 0),
                size=(1, 1, 1),
                rotation=(1, 0, 0, 0),
            )
            try:
                iou_3d_matrix([b], [b])
            except ImportError as exc:
                msg = str(exc)
                assert 'torch' in msg or 'pytorch3d' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('iou_3d_matrix did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_base_dataset_3d_iou_raises_import_error(self):
        """``_BaseDataset._calculate_3DBBox_ious`` must raise a clear ImportError
        (not a bare ModuleNotFoundError) when torch/pytorch3d are missing."""
        result = _run_without_torch(
            """
            import numpy as np
            from spatialai_data_utils.eval.tracking.hota.datasets._base_dataset import _BaseDataset

            # Non-empty inputs so we actually reach the pytorch3d-backed path.
            # Format used inside _calculate_3DBBox_ious: [x, y, z, w, l, h, pitch, roll, yaw]
            bboxes = np.array([[0, 0, 0, 1, 1, 1, 0, 0, 0]], dtype=np.float32)
            try:
                _BaseDataset._calculate_3DBBox_ious(bboxes, bboxes)
            except ImportError as exc:
                msg = str(exc)
                assert 'torch' in msg or 'pytorch3d' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('_calculate_3DBBox_ious did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout


# ===================================================================
# Sanity: when torch/pytorch3d ARE available, the functions still work
# (this is the "in-process" path that uses the real deps).
# ===================================================================
# Detect availability WITHOUT calling ``pytest.importorskip`` at module level,
# because that would skip the entire file (including the "without-torch" tests
# above) on CI environments where these optional deps are absent.
_HAS_TORCH_AND_PYTORCH3D = True
try:
    import torch  # noqa: F401
    import pytorch3d  # noqa: F401
except ImportError:
    _HAS_TORCH_AND_PYTORCH3D = False


def _make_detection_box(translation, size, rotation=(1, 0, 0, 0)):
    from nuscenes.eval.detection.data_classes import DetectionBox

    return DetectionBox(
        sample_token="test",
        translation=tuple(translation),
        size=tuple(size),
        rotation=tuple(rotation),
    )


@pytest.mark.skipif(
    not _HAS_TORCH_AND_PYTORCH3D,
    reason="torch/pytorch3d not installed; skipping 'with-torch' sanity checks.",
)
class TestWithTorchAvailable:
    def test_iou_3d_identical_boxes(self):
        from spatialai_data_utils.eval.common.utils import iou_3d

        b = _make_detection_box((0, 0, 0), (1, 1, 1))
        d = iou_3d(b, b)
        assert d == pytest.approx(0.0, abs=1e-5)

    def test_iou_3d_disjoint_boxes(self):
        from spatialai_data_utils.eval.common.utils import iou_3d

        b1 = _make_detection_box((0, 0, 0), (1, 1, 1))
        b2 = _make_detection_box((10, 0, 0), (1, 1, 1))
        d = iou_3d(b1, b2)
        assert d == pytest.approx(1.0, abs=1e-5)

    def test_iou_3d_matrix_shape_and_values(self):
        from spatialai_data_utils.eval.common.utils import iou_3d_matrix

        b1 = _make_detection_box((0, 0, 0), (1, 1, 1))
        b2 = _make_detection_box((10, 0, 0), (1, 1, 1))
        mat = iou_3d_matrix([b1, b2], [b1, b2])
        assert mat.shape == (2, 2)
        assert mat[0, 0] == pytest.approx(0.0, abs=1e-5)
        assert mat[1, 1] == pytest.approx(0.0, abs=1e-5)
        assert mat[0, 1] == pytest.approx(1.0, abs=1e-5)
        assert mat[1, 0] == pytest.approx(1.0, abs=1e-5)
