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

from unittest.mock import MagicMock

import pytest

from lib.podprovisioner.kubernetes.dockerclient import dockerclient


def _client(health_watcher):
    client = dockerclient.__new__(dockerclient)
    client.health_watcher = health_watcher
    client.downpodsArray = []
    client.app_config = {"WDM_WL_HEALTH_CHECK_URL": "/api/v1/live"}
    return client


def test_watch_pod_state_uses_shared_http_health_transitions():
    health_watcher = MagicMock()
    health_watcher.iter_transitions.return_value = iter(
        [
            (True, "vss-rtvi-cv", "vss-rtvi-cv"),
            (False, "vss-rtvi-cv", "vss-rtvi-cv"),
        ]
    )
    client = _client(health_watcher)
    transitions = client.watchPodState()

    assert next(transitions) == (True, "vss-rtvi-cv", "vss-rtvi-cv")
    assert client.downpodsArray == ["vss-rtvi-cv"]
    assert next(transitions) == (False, "vss-rtvi-cv", "vss-rtvi-cv")
    assert client.downpodsArray == []
    with pytest.raises(StopIteration):
        next(transitions)


def test_if_pod_down_uses_shared_http_health_state():
    health_watcher = MagicMock()
    client = _client(health_watcher)

    health_watcher.is_pod_known.return_value = False
    assert client.ifPodDown("vss-rtvi-cv")

    health_watcher.is_pod_known.return_value = True
    health_watcher.is_pod_healthy.return_value = False
    assert client.ifPodDown("vss-rtvi-cv")

    health_watcher.is_pod_healthy.return_value = True
    assert not client.ifPodDown("vss-rtvi-cv")
