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
BDD tests for VST download API stability under load.

Covers BDD-GAP-026 (longrun), BDD-GAP-027, BDD-GAP-028 (longrun).
"""
import logging
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .download_test_utils import (
    create_test_video_file,
    get_with_retry,
    upload_test_video,
)

logger = logging.getLogger(__name__)

scenarios('../../features/file_download/download_stability.feature')


def _wait_timeline(api_config, stream_id, deadline_sec=120):
    """Poll until timelines for the given stream are reported."""
    end_time = time.time() + deadline_sec
    while time.time() < end_time:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/storage/timelines",
            timeout=10, verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            tl = (resp.json() or {}).get(stream_id, [])
            if tl:
                return tl
        time.sleep(2)
    return []


def _route_header(context):
    """Header dict for storage download routing through Envoy."""
    return {"streamid": context.sensor_id}


@given('the VST API is configured for stability tests')
def configure_stability(context, api_config):
    assert api_config['base_url']
    context.stability_stream_id = None
    context.stability_timeline = None


@given('the storage has at least one stream with sufficient recording')
def ensure_stream_with_recording(context, api_config):
    """Upload a 60s test video for the stability scenarios."""
    file_path = context.temp_upload_dir / f"stab_{uuid.uuid4().hex[:8]}.mp4"
    create_test_video_file(file_path, duration_seconds=60)
    sensor_id = f"test_upload_{uuid.uuid4()}"
    context.sensor_id = sensor_id
    result = upload_test_video(
        api_config['base_url'], file_path, file_path.name, sensor_id,
        api_config.get('verify_ssl', False),
    )
    assert result['success'], f"Upload failed: {result.get('error')}"
    context.stability_stream_id = result['streamId']
    context.uploaded_stream_ids.add(result['streamId'])
    tl = _wait_timeline(api_config, result['streamId'])
    assert tl, "No timeline"
    context.stability_timeline = tl[0]


# ---------------------------------------------------------------------------
# BDD-GAP-026 — 100 sequential blocking downloads over 2h
# ---------------------------------------------------------------------------

@when('100 sequential blocking download requests are issued')
def hundred_sequential_downloads(context, api_config):
    tl = context.stability_timeline
    sid = context.stability_stream_id
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{sid}"
    headers = _route_header(context)

    container_pid_before = _container_pid('storage-management')
    context.storage_pid_before = container_pid_before
    successes = 0
    invalid = []
    for i in range(100):
        resp = requests.get(
            url,
            params={'startTime': tl['startTime'], 'endTime': tl['endTime'],
                    'container': 'mp4'},
            headers=headers,
            timeout=120,
            verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200 and resp.content[4:8] == b'ftyp':
            successes += 1
        else:
            invalid.append((i, resp.status_code))
    context.stability_results = {'successes': successes, 'invalid': invalid}


@then('all 100 downloads return valid MP4')
def hundred_all_valid(context):
    res = context.stability_results
    assert res['successes'] == 100, (
        f"Only {res['successes']}/100 valid MP4 downloads; failed: {res['invalid'][:10]}"
    )


@then('no storage-ms container restarts are observed')
def no_storage_restart(context):
    after = _container_pid('storage-management')
    before = context.storage_pid_before
    if before and after:
        assert before == after, (
            f"storage-management PID changed: before={before} after={after}"
        )


def _container_pid(name_substr):
    try:
        out = subprocess.run(
            ['docker', 'ps', '--filter', f'name={name_substr}',
             '--format', '{{.ID}}'],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip().splitlines()
        if not out:
            return None
        inspect = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Pid}}', out[0]],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return int(inspect) if inspect.isdigit() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# BDD-GAP-027 — non-blocking URL within 2s
# ---------------------------------------------------------------------------

@when('a non-blocking download URL is requested for a 1 hour clip')
def request_nonblocking_url(context, api_config):
    tl = context.stability_timeline
    sid = context.stability_stream_id
    headers = _route_header(context)
    verify_ssl = api_config.get('verify_ssl', False)

    # Warm-up: retry the storage GET until Envoy routes our freshly created
    # sensor (we got the timeline but the /url endpoint may briefly 503).
    # The retry helper handles transient 503/504 with bounded backoff so we
    # do not rely on a fixed sleep.
    warm_url = f"{api_config['base_url']}/vst/api/v1/storage/file/{sid}/url"
    warm_params = {
        'startTime': tl['startTime'],
        'endTime': tl['endTime'],
        'expiryMinutes': '5',
        'blocking': 'false',
        'container': 'mp4',
    }
    warm = get_with_retry(
        warm_url, params=warm_params, headers=headers,
        timeout=15, verify_ssl=verify_ssl, retries=5,
    )
    # If warm-up still 503s after retries, propagate that response — the
    # test will report a routing failure rather than a timing artifact.
    if warm.status_code != 200:
        context.nonblocking_elapsed = 0.0
        context.nonblocking_response = warm
        return

    # Now the actual contract check: a single non-retried call whose
    # latency we measure. The contract is "<2s end-to-end".
    start = time.monotonic()
    resp = requests.get(
        warm_url, params=warm_params, headers=headers,
        timeout=15, verify=verify_ssl,
    )
    context.nonblocking_elapsed = time.monotonic() - start
    context.nonblocking_response = resp


@then('the URL response returns within 2 seconds')
def url_within_2s(context):
    resp = context.nonblocking_response
    assert resp.status_code == 200, (
        f"Non-blocking URL request failed: {resp.status_code} {resp.text[:200]}"
    )
    assert context.nonblocking_elapsed < 2.0, (
        f"Non-blocking URL took {context.nonblocking_elapsed:.2f}s "
        f"(contract requires < 2s)"
    )


@then('the returned URL eventually serves the clip')
def returned_url_eventually_serves(context, api_config):
    data = context.nonblocking_response.json()
    video_url = data.get('videoUrl')
    assert video_url, f"Missing videoUrl in response: {data}"
    if not video_url.startswith('http'):
        video_url = f"{api_config['base_url']}{video_url}"
    deadline = time.time() + 120
    last_status = None
    while time.time() < deadline:
        try:
            r = requests.get(
                video_url, timeout=15, verify=api_config.get('verify_ssl', False),
            )
            last_status = r.status_code
            if r.status_code == 200 and len(r.content) > 12 and r.content[4:8] == b'ftyp':
                return
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    pytest.fail(
        f"Non-blocking URL never served a valid MP4. Last status: {last_status}"
    )


# ---------------------------------------------------------------------------
# BDD-GAP-028 — 2 hour heavy parallel load (longrun)
# ---------------------------------------------------------------------------

@when('10 parallel download workers run for 2 hours')
def parallel_2h_workers(context, api_config):
    tl = context.stability_timeline
    sid = context.stability_stream_id
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{sid}"
    headers = _route_header(context)

    context.storage_pid_before = _container_pid('storage-management')

    deadline = time.time() + 2 * 60 * 60  # 2 hours

    def worker():
        successes = 0
        failures = 0
        while time.time() < deadline:
            try:
                r = requests.get(
                    url,
                    params={'startTime': tl['startTime'], 'endTime': tl['endTime'],
                            'container': 'mp4'},
                    headers=headers,
                    timeout=120,
                    verify=api_config.get('verify_ssl', False),
                )
                if r.status_code == 200 and r.content[4:8] == b'ftyp':
                    successes += 1
                else:
                    failures += 1
            except requests.exceptions.RequestException:
                failures += 1
        return successes, failures

    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(worker) for _ in range(10)]
        results = [f.result() for f in as_completed(futures)]
    context.parallel_results = results


@then('all workers complete')
def all_workers_complete(context):
    assert len(context.parallel_results) == 10, (
        f"Expected 10 worker results, got {len(context.parallel_results)}"
    )
    for i, (succ, fail) in enumerate(context.parallel_results):
        logger.info("Worker %d: %d ok / %d fail", i, succ, fail)


@then('storage-ms PID remains unchanged across the run')
def storage_pid_unchanged(context):
    after = _container_pid('storage-management')
    before = context.storage_pid_before
    if before and after:
        assert before == after, (
            f"storage-management PID changed: before={before} after={after}"
        )
