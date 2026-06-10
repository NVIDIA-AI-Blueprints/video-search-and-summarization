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
BDD tests for VST stream recorder continuous (alwaysOn) recording behaviour.

Covers BDD-GAP-060 (longrun), BDD-GAP-061, BDD-GAP-062.

These scenarios require an always-on RTSP sensor in the deployment. They
read the sensor identifier from
  tests.continuous_recording_tests.test_parameters.alwaysOn_sensor_id
in config.json. If not configured they skip.
"""
import logging
import subprocess
import time
from datetime import datetime

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import api_get

logger = logging.getLogger(__name__)

scenarios('../../../features/unit_tests/stream_recorder/continuous_recording.feature')


class ContinuousContext:
    def __init__(self):
        self.sensor_id = None
        self.timelines = []
        self.size_reported = 0
        self.size_on_disk = 0
        self.recording_start = None


@pytest.fixture
def context():
    return ContinuousContext()


def _params(config):
    return (
        config.get('tests', {})
        .get('continuous_recording_tests', {})
        .get('test_parameters', {})
    )


def _resolve_sensor(context, config, kind: str):
    """Resolve sensor id from config, or skip with guidance."""
    params = _params(config)
    sid = params.get('alwaysOn_sensor_id')
    if not sid:
        pytest.skip(
            f"continuous_recording.{kind}: no alwaysOn_sensor_id configured. "
            f"Add tests.continuous_recording_tests.test_parameters."
            f"alwaysOn_sensor_id to config.json."
        )
    context.sensor_id = sid


# ---------------------------------------------------------------------------
# BDD-GAP-060 — gap-free for 1 hour
# ---------------------------------------------------------------------------

@given('an always-on RTSP sensor exists for continuous recording')
def need_alwayson_sensor(context, config):
    _resolve_sensor(context, config, "GAP-060")


@when('the sensor records continuously for 60 minutes')
def record_for_60_minutes(context, api_config):
    sid = context.sensor_id
    # Record start time and sleep for 60 minutes
    context.recording_start = datetime.utcnow()
    time.sleep(60 * 60)


@then('the merged timeline has no gap longer than 1 second')
def merged_timeline_no_gaps(context, api_config):
    sid = context.sensor_id
    resp = requests.get(
        f"{api_config['base_url']}/vst/api/v1/sensor/{sid}/timelines",
        timeout=30,
        verify=api_config.get('verify_ssl', False),
    )
    assert resp.status_code == 200, f"Timelines fetch failed: {resp.status_code}"
    data = resp.json() or {}
    timelines = data.get(sid) or data
    if isinstance(timelines, dict):
        timelines = timelines.get('timelines', [])
    assert timelines, "No timelines reported"

    # Iterate sequential pairs and assert no gap > 1s
    sorted_tl = sorted(
        timelines,
        key=lambda x: x.get('startTime', ''),
    )
    last_end = None
    for tl in sorted_tl:
        start = datetime.fromisoformat(tl['startTime'].replace('Z', '+00:00'))
        end = datetime.fromisoformat(tl['endTime'].replace('Z', '+00:00'))
        if last_end is not None:
            gap = (start - last_end).total_seconds()
            assert gap <= 1.0, (
                f"Gap of {gap:.2f}s found between {last_end.isoformat()} "
                f"and {start.isoformat()}"
            )
        last_end = end


# ---------------------------------------------------------------------------
# BDD-GAP-061 — start within 1s of sensor going online
# ---------------------------------------------------------------------------

@given('an always-on sensor exists for cold-start measurement')
def need_alwayson_for_coldstart(context, config):
    _resolve_sensor(context, config, "GAP-061")


@given('the sensor is currently offline')
def sensor_offline(context, api_config):
    """Verify the sensor's reachable URL is not yet returning frames.

    We can't reliably take a sensor offline from outside the system, so the
    test only runs if the sensor is already offline at test entry; otherwise
    it skips.
    """
    sid = context.sensor_id
    resp = requests.get(
        f"{api_config['base_url']}/vst/api/v1/sensor/{sid}/status",
        timeout=15, verify=api_config.get('verify_ssl', False),
    )
    if resp.status_code == 200:
        body = resp.json()
        status = (
            body.get('status') if isinstance(body, dict)
            else (body[0].get('status') if isinstance(body, list) and body else '')
        ) or ''
        if str(status).lower() == 'online':
            pytest.skip(
                f"Sensor {sid} is already online; cold-start test requires "
                f"the sensor to start offline. Either preconfigure an offline "
                f"sensor or run after a redeployment."
            )


@when('the sensor comes online')
def wait_sensor_online(context, api_config):
    sid = context.sensor_id
    # Use monotonic time for both the wait budget and the recorded
    # "online_at" — mixing wall-clock and monotonic would be fragile under
    # NTP adjustments, and the downstream 1-second contract assertion in
    # segment_within_1s compares against context.online_at on the same
    # monotonic clock.
    deadline = time.monotonic() + 300
    online_at = None
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/sensor/{sid}/status",
            timeout=15, verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            body = resp.json()
            status = (
                body.get('status') if isinstance(body, dict)
                else (body[0].get('status') if isinstance(body, list) and body else '')
            ) or ''
            if str(status).lower() == 'online':
                online_at = time.monotonic()
                break
        time.sleep(0.5)
    assert online_at is not None, "Sensor never came online within 5 minutes"
    context.online_at = online_at


@then('a recording segment for the sensor appears within 1 second')
def segment_within_1s(context, api_config):
    sid = context.sensor_id
    deadline = context.online_at + 1.0 + 0.5  # +500ms slack for poll cadence
    while time.monotonic() < deadline:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/sensor/{sid}/recording_status",
            timeout=10, verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get('recording', False):
                return
        time.sleep(0.1)
    pytest.fail(
        f"Sensor {sid} did not start recording within 1 second of going online"
    )


# ---------------------------------------------------------------------------
# BDD-GAP-062 — reported size matches sum of on-disk MP4 sizes
# ---------------------------------------------------------------------------

@given('an always-on RTSP sensor exists with ongoing recording')
def need_alwayson_with_recording(context, config):
    _resolve_sensor(context, config, "GAP-062")


def _list_segments_sum(api_config, sensor_id):
    """Sum file sizes for the sensor's recorded segments via the API."""
    resp = requests.get(
        f"{api_config['base_url']}/vst/api/v1/storage/file/{sensor_id}/list",
        timeout=30, verify=api_config.get('verify_ssl', False),
    )
    if resp.status_code != 200:
        return None
    data = resp.json() or {}
    entries = data.get(sensor_id) or []
    total = 0
    for e in entries:
        if isinstance(e, dict):
            md = e.get('metadata', {})
            size = md.get('size') or e.get('size') or 0
            try:
                total += int(size)
            except (TypeError, ValueError):
                pass
    return total


@when('the recording size is queried and the on-disk segment sizes are summed')
def query_size_and_sum(context, api_config):
    sid = context.sensor_id
    resp = requests.get(
        f"{api_config['base_url']}/vst/api/v1/sensor/{sid}/recording.size",
        timeout=15, verify=api_config.get('verify_ssl', False),
    )
    if resp.status_code != 200:
        # Fall back to storage/size which is widely supported
        size_resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/storage/size",
            timeout=15, verify=api_config.get('verify_ssl', False),
        )
        assert size_resp.status_code == 200, (
            f"Could not query recording size: {resp.status_code} / {size_resp.status_code}"
        )
        data = size_resp.json() or {}
        info = data.get(sid) or {}
        context.size_reported = int(info.get('usedBytes', 0) or 0)
    else:
        data = resp.json()
        if isinstance(data, dict):
            context.size_reported = int(
                data.get('size') or data.get('usedBytes') or 0
            )
        else:
            context.size_reported = int(data or 0)

    on_disk = _list_segments_sum(api_config, sid)
    if on_disk is None:
        pytest.skip(
            "Could not enumerate on-disk segment sizes via the storage API. "
            "This test requires storage/file/{sensor}/list to return segment "
            "metadata with 'size' fields."
        )
    context.size_on_disk = on_disk


@then('the two values match within 1 percent')
def sizes_match_within_one_percent(context):
    reported = context.size_reported
    on_disk = context.size_on_disk
    assert reported > 0, "Reported size is zero — sensor has no recording yet?"
    assert on_disk > 0, "On-disk size is zero — file list returned no sizes"
    diff = abs(reported - on_disk)
    tolerance = max(reported, on_disk) * 0.01
    assert diff <= tolerance, (
        f"Reported size {reported} bytes vs on-disk {on_disk} bytes; "
        f"diff {diff} exceeds 1% tolerance ({tolerance:.0f})"
    )
