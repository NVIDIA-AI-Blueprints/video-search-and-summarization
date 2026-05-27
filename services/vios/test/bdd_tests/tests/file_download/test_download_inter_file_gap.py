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

import logging
import random
from typing import Any, Dict, List, Optional

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import (
    ENDPOINTS,
    epoch_ms_to_iso_z,
    fetch_rtsp_sensors,
    fetch_sensor_file_starts_ms,
    parse_download_response_times_ms,
)
from ..test_utils import assert_with_detailed_failure

logger = logging.getLogger(__name__)

scenarios('../../features/file_download/download_inter_file_gap.feature')


GAP_END_OFFSET_MS = 10
DOWNLOAD_WINDOW_MS = 10_000


@given('the VST API is configured')
def vst_api_configured(api_config):
    assert api_config['base_url'], "Base URL must be configured"


@given('an RTSP sensor with at least two recorded files is selected')
def select_rtsp_sensor(context, api_config, test_params):
    timeout = test_params['timeout']
    verify_ssl = api_config.get('verify_ssl', False)

    rtsp_sensors = fetch_rtsp_sensors(api_config['base_url'], timeout, verify_ssl)
    if not rtsp_sensors:
        pytest.skip("No RTSP sensors available on the running VST instance")

    eligible: List[Dict[str, Any]] = []
    for sensor in rtsp_sensors:
        sensor_id = sensor.get('sensorId')
        if not sensor_id:
            continue
        starts = fetch_sensor_file_starts_ms(
            api_config['base_url'], sensor_id, timeout, verify_ssl
        )
        if len(starts) >= 2:
            eligible.append({'sensorId': sensor_id, 'file_starts_ms': starts})

    if not eligible:
        pytest.skip("No RTSP sensor has at least two recorded files")

    chosen = random.choice(eligible)
    context.test_data.append({
        'sensor_id': chosen['sensorId'],
        'file_starts_ms': chosen['file_starts_ms'],
    })
    logger.info(
        "Selected RTSP sensor %s with %d recorded file(s)",
        chosen['sensorId'], len(chosen['file_starts_ms']),
    )


@when('a non-first recorded file is picked at random as the boundary file')
def pick_boundary_file(context):
    selection = context.test_data[-1]
    file_starts = selection['file_starts_ms']
    boundary_ms = random.choice(file_starts[1:])
    selection['boundary_file_start_ms'] = boundary_ms
    logger.info("Picked boundary file start: %d ms (%s)",
                boundary_ms, epoch_ms_to_iso_z(boundary_ms))


def _download_and_parse(api_base_url: str, sensor_id: str,
                        start_ms: int, end_ms: int,
                        timeout: int, verify_ssl: bool) -> Dict[str, Any]:
    url = f"{api_base_url}{ENDPOINTS['storage_file'].format(stream_id=sensor_id)}"
    params = {
        'startTime': epoch_ms_to_iso_z(start_ms),
        'endTime': epoch_ms_to_iso_z(end_ms),
        'container': 'mp4',
    }
    logger.info("GET %s params=%s", url, params)
    response = requests.get(url, params=params, timeout=timeout,
                            verify=verify_ssl, stream=True)
    try:
        response.raise_for_status()
        # Drain to release the connection; we only care about the header.
        for _ in response.iter_content(chunk_size=64 * 1024):
            pass
    finally:
        response.close()

    parsed = parse_download_response_times_ms(
        response.headers.get('Content-Disposition', '')
    )
    parsed['requested_start_ms'] = start_ms
    parsed['requested_end_ms'] = end_ms
    return parsed


@when('the storage download API is called with end time 10 ms before the boundary file start and start time 10 seconds before that')
def call_download_gap(context, api_config, test_params):
    selection = context.test_data[-1]
    boundary_ms: int = selection['boundary_file_start_ms']
    end_ms = boundary_ms - GAP_END_OFFSET_MS
    start_ms = end_ms - DOWNLOAD_WINDOW_MS

    selection['result'] = _download_and_parse(
        api_config['base_url'], selection['sensor_id'],
        start_ms, end_ms,
        test_params.get('download_timeout', 120),
        api_config.get('verify_ssl', False),
    )
    logger.info("Download result: %s", selection['result'])


@when('the storage download API is called with end time equal to the boundary file start and start time 10 seconds before that')
def call_download_boundary(context, api_config, test_params):
    selection = context.test_data[-1]
    boundary_ms: int = selection['boundary_file_start_ms']
    end_ms = boundary_ms
    start_ms = end_ms - DOWNLOAD_WINDOW_MS

    selection['result'] = _download_and_parse(
        api_config['base_url'], selection['sensor_id'],
        start_ms, end_ms,
        test_params.get('download_timeout', 120),
        api_config.get('verify_ssl', False),
    )
    logger.info("Download result: %s", selection['result'])


def _assert_end_time(context, *, op: str):
    selection = context.test_data[-1]
    boundary_ms: int = selection['boundary_file_start_ms']
    result = selection['result']
    actual_end_ms: int = result['end_ms']

    requested_end_ms: int = result['requested_end_ms']
    if op == 'lt':
        ok = actual_end_ms < boundary_ms
        expected = f"result end time < boundary file start ({boundary_ms} ms)"
    elif op == 'ge':
        ok = actual_end_ms >= requested_end_ms
        expected = f"result end time >= requested end time ({requested_end_ms} ms)"
    else:
        raise ValueError(op)

    actual = (
        f"result end time = {actual_end_ms} ms ({epoch_ms_to_iso_z(actual_end_ms)}); "
        f"boundary = {boundary_ms} ms ({epoch_ms_to_iso_z(boundary_ms)}); "
        f"diff = {actual_end_ms - boundary_ms} ms; "
        f"requested end = {result['requested_end_ms']} ms; "
        f"filename = {result['filename']}"
    )

    assert_with_detailed_failure(
        ok,
        "Inter-File Gap End-Time Validation",
        expected,
        actual,
        failed_items=None if ok else [{
            'description': (
                f"sensor={selection['sensor_id']} boundary_ms={boundary_ms} "
                f"actual_end_ms={actual_end_ms} filename={result['filename']}"
            )
        }],
    )


@then('the result file end time is strictly less than the boundary file start time')
def assert_end_lt_boundary(context):
    _assert_end_time(context, op='lt')


@then('the result file end time is greater than or equal to the requested end time')
def assert_end_ge_requested(context):
    _assert_end_time(context, op='ge')
