# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit test for VST sensor remove and re-add flow.

Validates that after removing all sensor_rtsp sensors and triggering a network
scan, the sensors are re-discovered and recording resumes with current
timelines.
"""
import logging
import random
import time
from datetime import datetime, timezone

import pytest
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_post,
    api_delete,
)

logger = logging.getLogger(__name__)

scenarios(
    "../../../features/unit_tests/sensor_management/sensor_remove_readd.feature"
)

TARGET_SENSOR_TYPE = "sensor_rtsp"
TIMELINE_TOLERANCE_SECONDS = 120


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST sensor management API is accessible")
def sensor_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


@given("at least one non-file sensor exists with streams")
def at_least_one_non_file_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Fetch sensor list and collect all sensor_rtsp sensors that have streams."""
    timeout = unit_test_params.get("timeout", 30)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)

    sensor_resp = api_get(
        base_url, "/vst/api/v1/sensor/list",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert sensor_resp.status_code == 200, (
        f"Failed to fetch sensor list: {sensor_resp.status_code}"
    )
    sensor_list = sensor_resp.json()
    assert isinstance(sensor_list, list), "Sensor list must be a JSON array"

    rtsp_sensors = [
        s for s in sensor_list
        if isinstance(s, dict)
        and s.get("type") == TARGET_SENSOR_TYPE
        and s.get("state") == "online"
    ]
    if len(rtsp_sensors) == 0:
        pytest.skip(
            f"No online {TARGET_SENSOR_TYPE} sensors available, skipping test. "
            f"Sensor types found: {[s.get('type') for s in sensor_list]}"
        )

    sensors_with_streams = []
    for sensor in rtsp_sensors:
        sensor_id = sensor.get("sensorId")
        per_sensor_resp = api_get(
            base_url, f"/vst/api/v1/sensor/{sensor_id}/streams",
            verify_ssl=verify_ssl, timeout=timeout,
        )
        if per_sensor_resp.status_code == 200:
            per_sensor_streams = per_sensor_resp.json()
            if per_sensor_streams:
                sensors_with_streams.append({
                    "sensor_id": sensor_id,
                    "type": sensor.get("type", "unknown"),
                    "streams": per_sensor_streams,
                })

    if not sensors_with_streams:
        pytest.skip(
            f"No {TARGET_SENSOR_TYPE} sensor with streams found, skipping test"
        )

    selected = random.choice(sensors_with_streams)
    context.all_sensors = [selected]
    logger.info(
        "Randomly selected sensor: %s (from %d available %s sensors)",
        selected["sensor_id"], len(sensors_with_streams), TARGET_SENSOR_TYPE,
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I remove all non-file sensors")
def remove_all_sensors(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)

    for sensor in context.all_sensors:
        sensor_id = sensor["sensor_id"]
        resp = api_delete(
            base_url,
            f"/vst/api/v1/sensor/{sensor_id}",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        assert resp.status_code == 200, (
            f"Failed to remove sensor {sensor_id}: "
            f"{resp.status_code} - {resp.text[:500]}"
        )
        logger.info("Removed sensor: %s", sensor_id)

    logger.info(
        "All %d sensors removed successfully", len(context.all_sensors),
    )


@when("I verify all sensors are removed from the sensor list")
def verify_all_sensors_removed(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    resp = api_get(
        api_config["base_url"],
        "/vst/api/v1/sensor/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"Failed to fetch sensor list: {resp.status_code}"
    )
    sensor_list = resp.json()
    remaining_ids = [
        s.get("sensorId") for s in sensor_list
        if isinstance(s, dict) and s.get("state") != "removed"
    ]

    removed_ids = [s["sensor_id"] for s in context.all_sensors]
    still_present = [sid for sid in removed_ids if sid in remaining_ids]
    assert not still_present, (
        f"Sensors still present after removal: {still_present}. "
        f"Active sensor IDs: {remaining_ids}"
    )
    logger.info(
        "Verified all %d sensors are removed from sensor list",
        len(removed_ids),
    )


@when("I wait 2 seconds and trigger a sensor scan")
def wait_and_scan(api_config: dict, unit_test_params: dict) -> None:
    logger.info("Waiting 2 seconds before triggering sensor scan...")
    time.sleep(2)

    timeout = unit_test_params.get("timeout", 30)
    resp = api_post(
        api_config["base_url"],
        "/vst/api/v1/sensor/scan",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"Sensor scan failed: {resp.status_code} - {resp.text[:500]}"
    )
    logger.info("Sensor scan triggered successfully")


@when("I wait 5 seconds and check recording timelines for all sensors")
def wait_and_check_timelines_all(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    logger.info("Waiting 5 seconds for sensors to resume recording...")
    time.sleep(5)

    timeout = unit_test_params.get("timeout", 30)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)

    context.timeline_results = {}
    for sensor in context.all_sensors:
        sensor_id = sensor["sensor_id"]
        resp = api_get(
            base_url,
            f"/vst/api/v1/sensor/{sensor_id}/timelines",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        context.timeline_results[sensor_id] = {
            "status_code": resp.status_code,
            "data": resp.json() if resp.status_code == 200 else None,
        }
        logger.info(
            "Timeline response for %s: status=%d", sensor_id, resp.status_code,
        )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("all sensors should have current recording timelines")
def check_all_current_timelines(context: UnitTestContext) -> None:
    now = datetime.now(timezone.utc)
    failures = []

    for sensor in context.all_sensors:
        sensor_id = sensor["sensor_id"]
        result = context.timeline_results.get(sensor_id, {})

        if result.get("status_code") != 200:
            failures.append(
                f"{sensor_id}: timeline request failed "
                f"(status={result.get('status_code')})"
            )
            continue

        timelines = result.get("data")
        if not isinstance(timelines, list) or len(timelines) == 0:
            failures.append(f"{sensor_id}: no recording timelines found")
            continue

        latest_end = None
        for entry in timelines:
            end_str = entry.get("endTime", "")
            if end_str:
                end_time = datetime.fromisoformat(
                    end_str.replace("Z", "+00:00")
                )
                if latest_end is None or end_time > latest_end:
                    latest_end = end_time

        if latest_end is None:
            failures.append(f"{sensor_id}: no valid endTime in timelines")
            continue

        time_diff = abs((now - latest_end).total_seconds())
        if time_diff >= TIMELINE_TOLERANCE_SECONDS:
            failures.append(
                f"{sensor_id}: latest endTime is {time_diff:.1f}s from now "
                f"(tolerance: {TIMELINE_TOLERANCE_SECONDS}s)"
            )
        else:
            logger.info(
                "Sensor %s timeline OK (diff: %.1fs)", sensor_id, time_diff,
            )

    assert not failures, (
        f"Timeline check failed for {len(failures)} sensor(s):\n"
        + "\n".join(f"  - {f}" for f in failures)
    )


@then("all sensor recording statuses should be active")
def check_all_recording_statuses(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    timeout = unit_test_params.get("timeout", 30)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    failures = []

    for sensor in context.all_sensors:
        sensor_id = sensor["sensor_id"]
        resp = api_get(
            base_url,
            f"/vst/api/v1/sensor/{sensor_id}/status",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        if resp.status_code != 200:
            failures.append(
                f"{sensor_id}: status request failed "
                f"(status={resp.status_code})"
            )
            continue

        state = resp.json().get("state", "")
        if state != "online":
            failures.append(f"{sensor_id}: state is '{state}', expected 'online'")
        else:
            logger.info("Sensor %s is online after re-add", sensor_id)

    assert not failures, (
        f"Status check failed for {len(failures)} sensor(s):\n"
        + "\n".join(f"  - {f}" for f in failures)
    )
