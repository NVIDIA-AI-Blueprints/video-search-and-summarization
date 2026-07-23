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
import uuid

from vss_agents.utils.uuid_string import is_standard_uuid_string


def test_is_standard_uuid_string_accepts_valid_uuid() -> None:
    u = str(uuid.uuid4())
    assert is_standard_uuid_string(u) is True
    assert is_standard_uuid_string(u.upper()) is True


def test_is_standard_uuid_string_rejects_camera_like_name() -> None:
    # 36 chars, 4 hyphens, but not hex — old heuristic wrongly treated this as UUID
    assert is_standard_uuid_string("camera-rtsp-stream-name-with-4-hyphens-ok") is False


def test_is_standard_uuid_string_rejects_wrong_grouping() -> None:
    assert is_standard_uuid_string("aaa-aaaaaaa-aaaa-aaaa-aaaaaaaaaaaa") is False


def test_is_standard_uuid_string_rejects_empty() -> None:
    assert is_standard_uuid_string("") is False


def test_is_standard_uuid_string_rejects_non_string() -> None:
    assert is_standard_uuid_string(123) is False
    assert is_standard_uuid_string(None) is False
