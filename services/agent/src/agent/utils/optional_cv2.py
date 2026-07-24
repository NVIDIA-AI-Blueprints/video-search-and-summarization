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
"""Helpers for treating OpenCV (``cv2``) as an optional dependency.

The pre-built VSS Agent container does **not** bundle ``opencv-python-headless``.
That wheel ships FFmpeg libraries containing patent-encumbered codecs (H.264,
H.265, ...), which NVIDIA must not redistribute. Any feature that decodes video
therefore depends on the user opting in to install the proprietary codecs at
container startup (``INSTALL_PROPRIETARY_CODECS=true``), which installs
``opencv-python-headless`` on the user's own machine.

Modules that use ``cv2`` import it guardedly so the package still imports when the
codecs are absent, and call :func:`ensure_codecs` before using it so callers get
a clear, actionable error instead of an ``AttributeError`` on ``None``.
"""

PROPRIETARY_CODECS_NOT_INSTALLED = (
    "This feature requires OpenCV video decoding, which depends on patent-encumbered "
    "multimedia codecs (H.264/H.265). These are not bundled in the VSS Agent image for "
    "licensing reasons. Restart the container with INSTALL_PROPRIETARY_CODECS=true to "
    "install them at startup on your own machine. See services/agent/README.md "
    'section "Proprietary multimedia codecs".'
)


def ensure_codecs(cv2_module: object | None) -> None:
    """Raise a clear, actionable error if ``cv2`` is unavailable.

    Args:
        cv2_module: The module-level ``cv2`` reference (``None`` when the optional
            ``opencv-python-headless`` dependency is not installed).

    Raises:
        RuntimeError: If ``cv2`` is not installed.
    """
    if cv2_module is None:
        raise RuntimeError(PROPRIETARY_CODECS_NOT_INSTALLED)
