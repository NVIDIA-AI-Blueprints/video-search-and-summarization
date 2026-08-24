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

"""Unit tests for on-demand verification request schema."""

import os
import sys

import pytest
from pydantic import ValidationError

_web_root = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "alert-agent-web")
)
_repo_root = os.path.abspath(os.path.join(_web_root, ".."))

_saved = {k: sys.modules.pop(k) for k in list(sys.modules) if k == "app" or k.startswith("app.")}
_saved_path = sys.path[:]
sys.path = [p for p in sys.path if os.path.abspath(p) != _repo_root]
sys.path.insert(0, _web_root)
try:
    from app.schema.verification_schemas import OnDemandVerificationRequest
finally:
    sys.path = _saved_path
    sys.modules.update(_saved)


# ---------------------------------------------------------------------------
# Valid payloads
# ---------------------------------------------------------------------------

class TestRequestValid:

    def test_minimal_video(self):
        req = OnDemandVerificationRequest(
            category="collision",
            info={"media_urls": ["http://host/v.mp4"], "media_type": "video"},
        )
        assert req.category == "collision"
        assert req.info["media_type"] == "video"

    def test_minimal_image(self):
        req = OnDemandVerificationRequest(
            category="fire",
            info={"media_urls": ["http://host/img.jpg"], "media_type": "image"},
        )
        assert req.info["media_type"] == "image"

    def test_multiple_media_urls(self):
        req = OnDemandVerificationRequest(
            category="fire",
            info={"media_urls": ["http://a.jpg", "http://b.jpg", "http://c.jpg"], "media_type": "image"},
        )
        assert len(req.info["media_urls"]) == 3

    def test_category_normalized_lowercase(self):
        req = OnDemandVerificationRequest(
            category="  Collision  ",
            info={"media_urls": ["http://host/v.mp4"], "media_type": "video"},
        )
        assert req.category == "collision"

    def test_media_type_normalized_lowercase(self):
        req = OnDemandVerificationRequest(
            category="fire",
            info={"media_urls": ["http://host/img.jpg"], "media_type": "  VIDEO  "},
        )
        assert req.info["media_type"] == "video"

    def test_media_urls_whitespace_stripped(self):
        req = OnDemandVerificationRequest(
            category="fire",
            info={"media_urls": ["  http://host/img.jpg  ", "http://host/b.jpg"], "media_type": "image"},
        )
        assert req.info["media_urls"] == ["http://host/img.jpg", "http://host/b.jpg"]

    def test_blank_urls_filtered(self):
        req = OnDemandVerificationRequest(
            category="fire",
            info={"media_urls": ["http://host/img.jpg", "", "  "], "media_type": "image"},
        )
        assert req.info["media_urls"] == ["http://host/img.jpg"]

    def test_extra_incident_fields_accepted(self):
        req = OnDemandVerificationRequest(
            category="collision",
            info={"media_urls": ["http://host/v.mp4"], "media_type": "video"},
            id="incident-123",
            sensorId="sensor-01",
            timestamp="2026-01-01T00:00:00Z",
            end="2026-01-01T00:00:30Z",
        )
        assert req.id == "incident-123"
        assert req.sensorId == "sensor-01"

    def test_extra_info_fields_preserved(self):
        req = OnDemandVerificationRequest(
            category="collision",
            info={"media_urls": ["http://host/v.mp4"], "media_type": "video", "location": "37.0,-122.0"},
        )
        assert req.info["location"] == "37.0,-122.0"


# ---------------------------------------------------------------------------
# Invalid payloads
# ---------------------------------------------------------------------------

class TestRequestInvalid:

    def test_missing_category(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                info={"media_urls": ["http://host/v.mp4"], "media_type": "video"},
            )

    def test_missing_info(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(category="collision")

    def test_empty_category(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="   ",
                info={"media_urls": ["http://host/v.mp4"], "media_type": "video"},
            )

    def test_category_too_long(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="x" * 101,
                info={"media_urls": ["http://host/v.mp4"], "media_type": "video"},
            )

    def test_info_missing_media_urls(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="collision",
                info={"media_type": "video"},
            )

    def test_info_empty_media_urls(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="collision",
                info={"media_urls": [], "media_type": "video"},
            )

    def test_info_all_blank_media_urls(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="collision",
                info={"media_urls": ["", "  "], "media_type": "video"},
            )

    def test_info_missing_media_type(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="collision",
                info={"media_urls": ["http://host/v.mp4"]},
            )

    def test_info_invalid_media_type(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest(
                category="collision",
                info={"media_urls": ["http://host/v.mp4"], "media_type": "audio"},
            )

    def test_empty_body(self):
        with pytest.raises(ValidationError):
            OnDemandVerificationRequest()
