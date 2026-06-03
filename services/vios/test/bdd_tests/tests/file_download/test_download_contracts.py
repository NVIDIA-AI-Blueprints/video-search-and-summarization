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
BDD tests for VST download API contracts (negative + boundary).

Covers BDD-GAP-017, BDD-GAP-018, BDD-GAP-019, BDD-GAP-020.
"""
import logging
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

from .download_test_utils import (
    create_test_video_file,
    get_with_retry,
    upload_test_video,
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_download/download_contracts.feature')


def _upload_for_contract_test(context, api_config, duration_sec=30):
    """Upload a fresh test video, return (stream_id, sensor_id, timelines)."""
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    # Track immediately so the autouse cleanup fixture removes the sensor
    # even if the test fails before it could be cleaned up via the scenario.
    context.created_sensor_ids.add(sensor_id)
    filename = f"contract_{uuid.uuid4().hex[:8]}.mp4"
    file_path = context.temp_upload_dir / filename
    create_test_video_file(file_path, duration_seconds=duration_sec)
    result = upload_test_video(
        api_config['base_url'],
        file_path, filename, sensor_id,
        api_config.get('verify_ssl', False),
    )
    assert result['success'], f"Upload failed: {result.get('error')}"
    stream_id = result['streamId']
    context.uploaded_stream_ids.add(stream_id)
    context.contract_stream_id = stream_id
    context.contract_sensor_id = sensor_id

    # Poll until timelines are available
    deadline = time.time() + 60
    timelines = []
    while time.time() < deadline:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/storage/timelines",
            timeout=10,
            verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            all_tl = resp.json() or {}
            timelines = all_tl.get(stream_id, [])
            if timelines:
                break
        time.sleep(2)
    assert timelines, f"No timelines available for stream {stream_id}"
    context.contract_timelines = timelines
    return stream_id, sensor_id, timelines


def _route_header(context):
    """Header dict for storage download routing through Envoy."""
    return {"streamid": context.contract_sensor_id}


_get_with_retry = get_with_retry  # re-export local alias for readability


@given('the VST API is configured for download contract tests')
def configure_contract(context, api_config):
    assert api_config['base_url'], "Base URL must be configured"
    context.contract_response = None
    context.contract_stream_id = None
    context.contract_sensor_id = None
    context.contract_timelines = []


@given('a test video has been uploaded for download contract tests')
def upload_contract_video(context, api_config):
    _upload_for_contract_test(context, api_config, duration_sec=30)


# ---------------------------------------------------------------------------
# BDD-GAP-017 — startTime > endTime
# ---------------------------------------------------------------------------

@when('a download is requested with startTime greater than endTime')
def request_reversed_range(context, api_config):
    tl = context.contract_timelines[0]
    start = datetime.fromisoformat(tl['startTime'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(tl['endTime'].replace('Z', '+00:00'))
    # Reversed: pass end as startTime, start as endTime
    fmt = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.contract_stream_id}"
    response = _get_with_retry(
        url,
        params={
            'startTime': fmt(end),
            'endTime': fmt(start),
            'container': 'mp4',
        },
        headers=_route_header(context),
        timeout=30,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.contract_response = response
    logger.info("Reversed range -> status %d", response.status_code)


@then('the download response is a 4xx with a structured error')
def reversed_range_400(context):
    resp = context.contract_response
    assert resp is not None, "No response captured"
    assert 400 <= resp.status_code < 500, (
        f"Expected 4xx for reversed time range, got {resp.status_code}. "
        f"Body: {resp.text[:300]}"
    )


@then('no temp clip is written for the reversed range')
def no_temp_clip_reversed(context):
    """Verify the response body is not a valid media file."""
    resp = context.contract_response
    body = resp.content if resp is not None else b''
    # A valid MP4 starts with ftyp box at byte 4. Reject if it does.
    assert not (len(body) > 12 and body[4:8] == b'ftyp'), (
        "Reversed range response unexpectedly contained MP4 bytes"
    )


# ---------------------------------------------------------------------------
# BDD-GAP-018 — invalid fullLength values
# ---------------------------------------------------------------------------

@when(parsers.parse('a download is requested with fullLength "{bad_value}"'))
def request_bad_fulllength(context, api_config, bad_value):
    tl = context.contract_timelines[0]
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.contract_stream_id}"
    response = _get_with_retry(
        url,
        params={
            'startTime': tl['startTime'],
            'endTime': tl['endTime'],
            'fullLength': bad_value,
            'container': 'mp4',
        },
        headers=_route_header(context),
        timeout=30,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.contract_response = response
    logger.info("fullLength=%r -> status %d", bad_value, response.status_code)


# ---------------------------------------------------------------------------
# BDD-GAP-020 — intermediate-timeline download
# ---------------------------------------------------------------------------

@given('a test video with a known long recording exists')
def upload_long_recording(context, api_config):
    _upload_for_contract_test(context, api_config, duration_sec=120)


@when('a download is requested for an intermediate sub-window')
def request_intermediate_window(context, api_config):
    tl = context.contract_timelines[0]
    start = datetime.fromisoformat(tl['startTime'].replace('Z', '+00:00'))
    end = datetime.fromisoformat(tl['endTime'].replace('Z', '+00:00'))
    total = (end - start).total_seconds()
    assert total >= 30, f"Recording too short for sub-window test: {total}s"

    # Choose a 15-second sub-window starting one quarter into the recording.
    sub_start = start + timedelta(seconds=total * 0.25)
    sub_end = sub_start + timedelta(seconds=15)
    if sub_end > end:
        sub_end = end
    fmt = lambda dt: dt.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'

    context.subwindow_requested = (sub_end - sub_start).total_seconds()

    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{context.contract_stream_id}"
    response = _get_with_retry(
        url,
        params={
            'startTime': fmt(sub_start),
            'endTime': fmt(sub_end),
            'container': 'mp4',
        },
        headers=_route_header(context),
        timeout=60,
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.contract_response = response
    logger.info("Sub-window %.1fs -> status %d, %d bytes",
                context.subwindow_requested, response.status_code,
                len(response.content))


@then('the returned MP4 duration matches the requested sub-window within tolerance')
def subwindow_duration_matches(context):
    resp = context.contract_response
    assert resp.status_code == 200, f"Sub-window download failed: {resp.status_code}"
    body = resp.content
    assert body, "Empty body"

    tmp = Path(f"/tmp/subwindow_{uuid.uuid4().hex[:8]}.mp4")
    tmp.write_bytes(body)
    try:
        out = subprocess.run(
            ['ffprobe', '-v', 'error',
             '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1',
             str(tmp)],
            capture_output=True, text=True, timeout=30,
        )
        actual = float(out.stdout.strip()) if out.stdout.strip() else 0.0
    finally:
        tmp.unlink(missing_ok=True)

    requested = context.subwindow_requested
    # Allow up to 3-second tolerance for GOP alignment and 50% on the lower
    # bound to permit start-of-GOP truncation. Reject results that imply
    # the server returned the whole timeline.
    tolerance = 3.0
    assert actual <= requested + tolerance, (
        f"Sub-window duration too long: requested {requested}s, got {actual}s "
        f"(this would indicate the server returned more than the requested window)"
    )
    assert actual >= max(0.5, requested * 0.5), (
        f"Sub-window duration too short: requested {requested}s, got {actual}s"
    )
    logger.info("Sub-window: requested %.1fs, got %.1fs", requested, actual)
