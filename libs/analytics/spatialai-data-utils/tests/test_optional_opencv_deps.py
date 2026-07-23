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
Tests verifying that OpenCV (``cv2``) is an *optional* dependency.

OpenCV bundles ffmpeg (flagged by OSRB), so it is intentionally NOT declared
as an install-require and is removed from ``Pipfile.lock``. The package must
import — and every non-OpenCV code path must work — even when ``cv2`` is
missing. The visualization / video functions that need it must raise a clear
``ImportError`` at call time (not at import time).

These tests spawn a fresh subprocess with an ``sys.meta_path`` blocker that
simulates ``cv2`` being absent. This lets the tests run on machines where
OpenCV *is* installed, while still validating the optional-dependency
behavior.
"""

import subprocess
import sys
import textwrap

import pytest


# Script prelude that installs a meta-path blocker simulating an absent
# ``cv2`` package. Any ``import cv2`` (or any submodule) raises ImportError.
_BLOCKER_PRELUDE = textwrap.dedent(
    """
    import sys

    for _mod in list(sys.modules):
        if _mod == 'cv2' or _mod.startswith('cv2.'):
            del sys.modules[_mod]

    class _Blocker:
        def find_spec(self, name, path=None, target=None):
            if name == 'cv2' or name.startswith('cv2.'):
                raise ImportError(f'simulated missing: {name}')
            return None

    sys.meta_path.insert(0, _Blocker())
    """
).strip()


def _run_without_cv2(body: str) -> subprocess.CompletedProcess:
    """Run ``body`` in a fresh Python subprocess with ``cv2`` blocked.

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
# Package / module import behavior
# ===================================================================
class TestImportsWithoutCv2:
    """Importing the package and visualization modules must not need cv2."""

    def test_top_level_package_imports(self):
        result = _run_without_cv2(
            """
            import spatialai_data_utils
            import sys
            assert 'cv2' not in sys.modules, 'cv2 should not be imported'
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_visualization_package_imports(self):
        """``spatialai_data_utils.visualization`` (whose ``__init__`` eagerly
        imports box_3d / draw_utils / camera_groups / render) must import
        without cv2."""
        result = _run_without_cv2(
            """
            import spatialai_data_utils.visualization  # noqa: F401
            import sys
            assert 'cv2' not in sys.modules, 'cv2 leaked into visualization import'
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout

    def test_all_cv2_using_modules_import(self):
        """Every module that uses cv2 must import without it (lazy import)."""
        result = _run_without_cv2(
            """
            import importlib
            mods = [
                'spatialai_data_utils.visualization.box_2d',
                'spatialai_data_utils.visualization.box_3d',
                'spatialai_data_utils.visualization.camera_groups',
                'spatialai_data_utils.visualization.draw_utils',
                'spatialai_data_utils.visualization.points',
                'spatialai_data_utils.visualization.render',
                'spatialai_data_utils.visualization.video_utils.frame2video',
                'spatialai_data_utils.visualization.video_utils.frame2video_grid',
                'spatialai_data_utils.visualization.video_utils.text_writer',
                'spatialai_data_utils.visualization.video_utils.video2frame',
            ]
            failures = []
            for name in mods:
                try:
                    importlib.import_module(name)
                except Exception as exc:
                    failures.append((name, f'{type(exc).__name__}: {exc}'))
            assert not failures, failures
            import sys
            assert 'cv2' not in sys.modules
            print('OK')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "OK" in result.stdout


# ===================================================================
# cv2-dependent functions raise a clear ImportError at call time
# ===================================================================
class TestVisualizationFunctionsRaise:
    def test_draw_box_2d_raises(self):
        result = _run_without_cv2(
            """
            import numpy as np
            from spatialai_data_utils.visualization.box_2d import draw_box_2d
            try:
                draw_box_2d(np.zeros((10, 10, 3), np.uint8), [1, 1, 5, 5])
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('draw_box_2d did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_draw_camera_tag_raises(self):
        """``draw_camera_tag`` is the case where the OpenCV font constant used
        to be resolved at module import time (``DEFAULT_FONT``)."""
        result = _run_without_cv2(
            """
            import numpy as np
            from spatialai_data_utils.visualization.draw_utils import draw_camera_tag
            try:
                draw_camera_tag(np.zeros((100, 100, 3), np.uint8), 'Camera_01')
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('draw_camera_tag did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_load_image_raises(self):
        result = _run_without_cv2(
            """
            from spatialai_data_utils.visualization.draw_utils import load_image
            try:
                load_image('does_not_exist.jpg')
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('load_image did not raise ImportError')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_draw_box3d_corners_on_img_raises(self):
        result = _run_without_cv2(
            """
            import numpy as np
            from spatialai_data_utils.visualization.box_3d import (
                draw_box3d_corners_on_img,
            )
            try:
                draw_box3d_corners_on_img(
                    np.zeros((100, 100, 3), np.uint8), 1, np.zeros((1, 8, 2)),
                )
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('draw_box3d_corners_on_img did not raise')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_draw_bbox3d_on_bev_raises(self):
        result = _run_without_cv2(
            """
            import numpy as np
            from spatialai_data_utils.visualization.box_3d import draw_bbox3d_on_bev
            try:
                draw_bbox3d_on_bev(np.zeros((0, 9)), 200)
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('draw_bbox3d_on_bev did not raise')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_draw_bev_objects_bbox_in_image_raises(self):
        """The ``cv2.imread`` path triggers when ``image`` is a string path."""
        result = _run_without_cv2(
            """
            from spatialai_data_utils.visualization.render import (
                draw_bev_objects_bbox_in_image,
            )
            try:
                draw_bev_objects_bbox_in_image([], 'does_not_exist.jpg')
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('draw_bev_objects_bbox_in_image did not raise')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_plot_frame_label_raises(self):
        result = _run_without_cv2(
            """
            import numpy as np
            from spatialai_data_utils.visualization.video_utils.text_writer import (
                plot_frame_label,
            )
            try:
                plot_frame_label(np.zeros((100, 100, 3), np.uint8), 'label')
            except ImportError as exc:
                msg = str(exc)
                assert 'cv2' in msg or 'OpenCV' in msg, msg
                print('RAISED:', msg.splitlines()[0])
            else:
                raise AssertionError('plot_frame_label did not raise')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_video_to_frames_raises(self):
        """A real (non-empty) file gets past the existence/size guards and
        reaches the ``cv2.VideoCapture`` call, so the missing-cv2 error
        surfaces as a clean ImportError rather than a status string."""
        result = _run_without_cv2(
            """
            import tempfile, os
            from spatialai_data_utils.visualization.video_utils.video2frame import (
                video_to_frames,
            )
            with tempfile.TemporaryDirectory() as d:
                vid = os.path.join(d, 'fake.mp4')
                with open(vid, 'wb') as f:
                    f.write(b'not a real video but non-empty')
                try:
                    video_to_frames(vid, os.path.join(d, 'out'))
                except ImportError as exc:
                    msg = str(exc)
                    assert 'cv2' in msg or 'OpenCV' in msg, msg
                    print('RAISED:', msg.splitlines()[0])
                else:
                    raise AssertionError('video_to_frames did not raise')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout

    def test_frames_to_video_raises(self):
        """A directory holding at least one matching frame file gets past the
        no-frames guard and reaches ``cv2.imread``."""
        result = _run_without_cv2(
            """
            import tempfile, os
            from spatialai_data_utils.visualization.video_utils.frame2video import (
                frames_to_video,
            )
            with tempfile.TemporaryDirectory() as d:
                # Non-image content is fine: the lazy cv2 import fires before
                # cv2.imread actually decodes the bytes.
                with open(os.path.join(d, '0.jpg'), 'wb') as f:
                    f.write(b'x')
                try:
                    frames_to_video(d, os.path.join(d, 'out.mp4'))
                except ImportError as exc:
                    msg = str(exc)
                    assert 'cv2' in msg or 'OpenCV' in msg, msg
                    print('RAISED:', msg.splitlines()[0])
                else:
                    raise AssertionError('frames_to_video did not raise')
            """
        )
        assert result.returncode == 0, result.stderr
        assert "RAISED:" in result.stdout


# ===================================================================
# Sanity: when cv2 IS available, the functions still work in-process.
# ===================================================================
# Detect availability WITHOUT ``pytest.importorskip`` at module level so the
# "without-cv2" tests above still run on environments where cv2 is absent.
_HAS_CV2 = True
try:
    import cv2  # noqa: F401
except ImportError:
    _HAS_CV2 = False


@pytest.mark.skipif(
    not _HAS_CV2,
    reason="cv2 not installed; skipping 'with-opencv' sanity checks.",
)
class TestWithCv2Available:
    def test_draw_box_2d_runs(self):
        import numpy as np
        from spatialai_data_utils.visualization.box_2d import draw_box_2d

        out = draw_box_2d(np.zeros((20, 20, 3), np.uint8), [1, 1, 10, 10])
        assert out.shape == (20, 20, 3)

    def test_draw_camera_tag_runs(self):
        import numpy as np
        from spatialai_data_utils.visualization.draw_utils import draw_camera_tag

        img = np.zeros((200, 400, 3), np.uint8)
        out = draw_camera_tag(img, "Camera_01")
        assert out.shape == (200, 400, 3)

    def test_draw_bbox3d_on_bev_runs(self):
        import numpy as np
        from spatialai_data_utils.visualization.box_3d import draw_bbox3d_on_bev

        bev = draw_bbox3d_on_bev(np.zeros((0, 9)), 200)
        assert bev.shape == (200, 200, 3)
