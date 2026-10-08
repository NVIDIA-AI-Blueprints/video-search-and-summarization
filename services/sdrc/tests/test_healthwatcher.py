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

import queue
from unittest.mock import patch

from lib.podprovisioner.healthwatcher import WorkloadHealthWatcher

POD_NAME = "vss-rtvi-cv-0"
POD = {"podName": POD_NAME, "podIp": "10.0.0.8", "podPort": 8000}


def _watcher(resolve_pods):
    return WorkloadHealthWatcher(
        {"WDM_WL_HEALTH_CHECK_URL": "/api/v1/live"},
        resolve_pods=resolve_pods,
    )


def _events(watcher):
    events = []
    while True:
        try:
            events.append(watcher._events.get_nowait())
        except queue.Empty:
            return events


def test_health_gated_add_establishes_baseline_without_startup_recovery():
    watcher = _watcher(lambda: [POD])

    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        side_effect=[False, False, True],
    ):
        # Background startup polls see the pod before the application is ready.
        watcher.poll_once()
        watcher.poll_once()
        # This is the exact path used immediately before SDRC issues /add.
        assert watcher.wait_until_healthy(POD, timeout_sec=1)

    assert _events(watcher) == []
    assert watcher.is_pod_known(POD_NAME)
    assert watcher.is_pod_healthy(POD_NAME)


def test_saved_workload_first_seen_down_recovers_and_reapplies_streams():
    watcher = _watcher(lambda: [POD])
    watcher.seed_startup_recovery_candidates([POD_NAME])

    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        side_effect=[False, True],
    ):
        watcher.poll_once()
        assert _events(watcher) == []
        watcher.poll_once()

    assert _events(watcher) == [(False, POD_NAME, POD_NAME)]
    assert watcher.consume_startup_recovery(POD_NAME)
    assert not watcher.consume_startup_recovery(POD_NAME)


def test_saved_workload_first_seen_healthy_only_establishes_baseline():
    watcher = _watcher(lambda: [POD])
    watcher.seed_startup_recovery_candidates([POD_NAME])

    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=True,
    ):
        watcher.poll_once()
        watcher.poll_once()

    assert _events(watcher) == []


def test_saved_workload_absent_at_startup_recovers_when_it_returns():
    state = {"pods": []}
    watcher = _watcher(lambda: state["pods"])
    watcher.seed_startup_recovery_candidates([POD_NAME])

    watcher.poll_once()
    assert _events(watcher) == []

    state["pods"] = [POD]
    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=True,
    ):
        watcher.poll_once()

    assert _events(watcher) == [(False, POD_NAME, POD_NAME)]
    assert watcher.consume_startup_recovery(POD_NAME)


def test_recovery_is_emitted_only_after_post_baseline_down_event():
    watcher = _watcher(lambda: [POD])

    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        side_effect=[True, False, False, True],
    ):
        watcher.poll_once()
        assert _events(watcher) == []

        watcher.poll_once()
        assert _events(watcher) == [(True, POD_NAME, POD_NAME)]

        watcher.poll_once()
        assert _events(watcher) == []

        watcher.poll_once()
        assert _events(watcher) == [(False, POD_NAME, POD_NAME)]
        assert not watcher.consume_startup_recovery(POD_NAME)


def test_lookup_error_keeps_snapshot_until_a_real_health_change():
    state = {"pods": [POD], "fail": False}

    def resolve_pods():
        if state["fail"]:
            raise RuntimeError("lookup failed")
        return state["pods"]

    watcher = _watcher(resolve_pods)

    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=True,
    ):
        watcher.poll_once()
    assert _events(watcher) == []
    assert watcher.is_pod_healthy(POD_NAME)

    state["fail"] = True
    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=False,
    ) as probe:
        snapshot = watcher.poll_once()
    probe.assert_not_called()
    assert snapshot == {POD_NAME: True}
    assert _events(watcher) == []
    assert watcher.is_pod_known(POD_NAME)
    assert watcher.is_pod_healthy(POD_NAME)

    state["fail"] = False
    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=True,
    ):
        watcher.poll_once()
    assert _events(watcher) == []
    assert watcher.is_pod_healthy(POD_NAME)

    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=False,
    ):
        watcher.poll_once()
    assert _events(watcher) == [(True, POD_NAME, POD_NAME)]
    assert watcher.is_pod_known(POD_NAME)
    assert not watcher.is_pod_healthy(POD_NAME)


def test_successful_empty_inventory_marks_pod_down_and_forgets_it():
    state = {"pods": [POD]}

    def resolve_pods():
        return state["pods"]

    watcher = _watcher(resolve_pods)
    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=True,
    ):
        watcher.poll_once()
    assert _events(watcher) == []

    state["pods"] = []
    watcher.poll_once()
    assert _events(watcher) == [(True, POD_NAME, POD_NAME)]
    assert not watcher.is_pod_known(POD_NAME)

    state["pods"] = [POD]
    with patch(
        "lib.podprovisioner.healthwatcher.probe_pod_health",
        return_value=True,
    ):
        watcher.poll_once()
    assert _events(watcher) == [(False, POD_NAME, POD_NAME)]
    assert watcher.is_pod_healthy(POD_NAME)
