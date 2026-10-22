# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Shared test fixtures for service_adapters tests."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_cluster_rep_offset(monkeypatch):
    """Validate the core cluster_id -> object_id mapping independent of the
    deployment-only CLUSTER_REP_DEBUG_OFFSET (which shifts cluster-rep ids into
    a high range purely for log/search readability). Pin it to 0 so tests
    assert the un-shifted representative ids."""
    monkeypatch.setattr("cluster_manager.compression_labeling.CLUSTER_REP_DEBUG_OFFSET", 0)
