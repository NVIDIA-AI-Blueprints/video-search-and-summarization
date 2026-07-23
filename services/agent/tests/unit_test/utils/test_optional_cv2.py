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
"""Tests for the optional-cv2 helper."""

import pytest

from vss_agents.utils.optional_cv2 import PROPRIETARY_CODECS_NOT_INSTALLED
from vss_agents.utils.optional_cv2 import ensure_codecs


class TestEnsureCodecs:
    def test_raises_when_cv2_missing(self):
        with pytest.raises(RuntimeError, match="INSTALL_PROPRIETARY_CODECS=true"):
            ensure_codecs(None)

    def test_passes_when_cv2_present(self):
        # Any non-None object represents an installed cv2 module.
        assert ensure_codecs(object()) is None

    def test_message_is_actionable(self):
        assert "INSTALL_PROPRIETARY_CODECS=true" in PROPRIETARY_CODECS_NOT_INSTALLED
        assert "README" in PROPRIETARY_CODECS_NOT_INSTALLED
