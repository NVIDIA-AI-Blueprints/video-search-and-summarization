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

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lib.podprovisioner.kubernetes.k8sclient import k8sclient


def _client(health_watcher):
    client = k8sclient.__new__(k8sclient)
    client.health_watcher = health_watcher
    client.downpodsArray = []
    client.app_config = {"WDM_WL_HEALTH_CHECK_URL": "/api/v1/live"}
    client.namespace = "wh-test"
    client.wlobjname = "vss-rtvi-cv"
    client.initiatorWLObjname = "vss-rtvi-cv"
    client.k8sclientCore = MagicMock()
    return client


def test_watch_pod_state_uses_http_health_transitions_when_attached():
    health_watcher = MagicMock()
    health_watcher.iter_transitions.return_value = iter(
        [
            (True, "vss-rtvi-cv-0", "vss-rtvi-cv"),
            (False, "vss-rtvi-cv-0", "vss-rtvi-cv"),
        ]
    )
    client = _client(health_watcher)

    with patch(
        "lib.podprovisioner.kubernetes.k8sclient.watch.Watch",
        side_effect=AssertionError("Kubernetes watch must not be used"),
    ):
        transitions = client.watchPodState()

        assert next(transitions) == (True, "vss-rtvi-cv-0", "vss-rtvi-cv")
        assert client.downpodsArray == ["vss-rtvi-cv-0"]
        assert next(transitions) == (False, "vss-rtvi-cv-0", "vss-rtvi-cv")
        assert client.downpodsArray == []
        with pytest.raises(StopIteration):
            next(transitions)


def test_watch_pod_state_keeps_kubernetes_readiness_fallback_without_watcher():
    client = _client(None)
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="vss-rtvi-cv-0",
            generate_name="vss-rtvi-cv-",
        ),
        status=SimpleNamespace(
            phase="Pending",
            container_statuses=None,
        ),
    )
    kube_watch = MagicMock()
    kube_watch.stream.return_value = iter([{"type": "MODIFIED", "object": pod}])

    with patch(
        "lib.podprovisioner.kubernetes.k8sclient.watch.Watch",
        return_value=kube_watch,
    ):
        assert next(client.watchPodState()) == (
            True,
            "vss-rtvi-cv-0",
            "vss-rtvi-cv",
        )

    kube_watch.stream.assert_called_once_with(
        client.k8sclientCore.list_namespaced_pod,
        namespace="wh-test",
    )
