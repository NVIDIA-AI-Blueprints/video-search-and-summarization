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
BDD tests for VST file upload lifecycle scenarios.

Covers BDD-GAP-008, BDD-GAP-010, BDD-GAP-011.

GAP-009 (ghost-file cleanup on validation failure) is covered by
features/unit_tests/storage_management/ghost_file_cleanup.feature.
"""
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from .upload_test_utils import upload_file_simple

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/upload_lifecycle.feature')


def _static_video() -> Path:
    p = Path(__file__).parent.parent.parent / "data" / "test_video.mp4"
    if not p.exists():
        raise FileNotFoundError(f"Static test video not found: {p}")
    return p


def _delete_stream_timelines(api_config, stream_id):
    base = api_config['base_url']
    verify = api_config.get('verify_ssl', False)
    storage = requests.get(
        f"{base}/vst/api/v1/storage/size",
        params={'timelines': 'true'},
        timeout=10,
        verify=verify,
    )
    if storage.status_code != 200:
        return 0
    data = storage.json()
    stream_info = data.get(stream_id) or {}
    timelines = stream_info.get('timelines', [])
    deleted = 0
    for tl in timelines:
        start = tl.get('startTime')
        end = tl.get('endTime')
        if not start or not end:
            continue
        resp = requests.delete(
            f"{base}/vst/api/v1/storage/file/{stream_id}",
            params={'startTime': start, 'endTime': end},
            timeout=10,
            verify=verify,
        )
        if resp.status_code in (200, 204):
            deleted += 1
    return deleted


@given('the VST API is configured for upload lifecycle tests')
def configure_lifecycle(context, api_config):
    assert api_config['base_url'], "Base URL must be configured"
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_lifecycle_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    context.first_upload_response = None
    context.second_upload_response = None
    context.deleted_stream_id = None
    context.cancelled_filename = None
    context.passthru_response = None
    context.passthru_delete_response = None


# ---------------------------------------------------------------------------
# BDD-GAP-008 — re-upload after delete
# ---------------------------------------------------------------------------

@given('a video file has been uploaded and then deleted')
def upload_then_delete(context, api_config):
    src = _static_video()
    local = context.temp_dir / f"src_{uuid.uuid4().hex[:8]}.mp4"
    shutil.copy2(src, local)

    filename = f"re_upload_{uuid.uuid4().hex[:8]}.mp4"
    context.lifecycle_filename = filename
    context.lifecycle_local = local

    response = upload_file_simple(
        api_config['base_url'], local, filename,
        sensor_id=context.sensor_id,
        timestamp='2026-05-14T00:00:00.000Z',
        verify_ssl=api_config.get('verify_ssl', False),
    )
    assert response.status_code in (200, 201), (
        f"Initial upload failed: {response.status_code} {response.text[:300]}"
    )
    data = response.json()
    stream_id = data.get('streamId')
    assert stream_id, f"Initial upload missing streamId: {data}"
    context.deleted_stream_id = stream_id
    context.uploaded_stream_ids.add(stream_id)

    # Wait briefly for storage indexing
    time.sleep(2)
    deleted = _delete_stream_timelines(api_config, stream_id)
    assert deleted > 0, f"Could not delete any timeline for streamId {stream_id}"

    # Wait for the delete to settle in storage state
    time.sleep(3)


@when('the same file is uploaded again')
def upload_same_filename_again(context, api_config):
    response = upload_file_simple(
        api_config['base_url'], context.lifecycle_local, context.lifecycle_filename,
        sensor_id=context.sensor_id,
        timestamp='2026-05-14T00:00:00.000Z',
        verify_ssl=api_config.get('verify_ssl', False),
    )
    context.second_upload_response = response
    try:
        data = response.json()
        if data and data.get('streamId'):
            context.uploaded_stream_ids.add(data['streamId'])
    except Exception:
        pass


@then('the re-upload succeeds')
def reupload_succeeds(context):
    resp = context.second_upload_response
    assert resp.status_code in (200, 201), (
        f"Re-upload after delete should succeed; got {resp.status_code}. "
        f"Body: {resp.text[:300]}"
    )


@then('the re-uploaded file appears in the media file list')
def reuploaded_in_list(context, api_config):
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    resp = requests.get(url, timeout=10, verify=api_config.get('verify_ssl', False))
    assert resp.status_code == 200, f"file/list returned {resp.status_code}"
    listing = resp.json()
    entries = listing.get(context.sensor_id, [])
    assert entries, f"No entries for sensor {context.sensor_id} after re-upload"


# ---------------------------------------------------------------------------
# BDD-GAP-010 — cancelled PUT mid-upload removes partial bytes
# ---------------------------------------------------------------------------

@when('a PUT upload is started for a large file and aborted mid-transfer')
def cancelled_put_midway(context, api_config):
    filename = f"cancelled_{uuid.uuid4().hex[:8]}.mp4"
    context.cancelled_filename = filename
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{filename}"
    params = {
        'sensorId': context.sensor_id,
        'timestamp': '2026-05-14T00:00:00.000Z',
    }

    # ~32MB synthetic body — large enough to abort mid-stream
    total_bytes = 32 * 1024 * 1024
    abort_after_bytes = 1 * 1024 * 1024  # abort after ~1MB sent

    def chunk_generator():
        sent = 0
        chunk = b'\x00' * 65536
        while sent < total_bytes:
            if sent >= abort_after_bytes:
                # Simulate a client-side disconnect mid-upload
                raise requests.exceptions.ConnectionError("simulated mid-upload abort")
            yield chunk
            sent += len(chunk)

    try:
        resp = requests.put(
            url,
            params=params,
            data=chunk_generator(),
            headers={
                'Content-Type': 'application/octet-stream',
                'Content-Length': str(total_bytes),
            },
            timeout=30,
            verify=api_config.get('verify_ssl', False),
        )
        # If the server unexpectedly completed the upload, track the streamId
        # so the autouse cleanup hook deletes it. Then fail the assertion.
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get('streamId'):
                context.uploaded_stream_ids.add(data['streamId'])
        except Exception:
            pass
        pytest.fail(
            f"Expected the PUT to be aborted mid-stream, but the server "
            f"returned {resp.status_code}: {resp.text[:200]}"
        )
    except requests.exceptions.ConnectionError:
        logger.info("PUT '%s' aborted mid-transfer as expected", filename)
    except requests.exceptions.RequestException as e:
        logger.info("PUT '%s' interrupted: %s", filename, e)


@then('within 30 seconds no entry for the aborted file is in the media file list')
def aborted_not_in_list(context, api_config):
    deadline = time.time() + 30
    while time.time() < deadline:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/storage/file/list",
            timeout=10,
            verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            listing = resp.json() or {}
            entries = listing.get(context.sensor_id, []) or []
            present = any(
                isinstance(e, dict)
                and context.cancelled_filename in str(e.get('metadata', {}))
                for e in entries
            )
            if not present:
                return
        time.sleep(2)
    pytest.fail(
        f"Aborted file '{context.cancelled_filename}' still listed after 30s"
    )


@then('no usedBytes are reported for the aborted streamId')
def aborted_usedbytes_zero(context, api_config):
    """Verify no streamId tied to the cancelled filename is reporting bytes."""
    resp = requests.get(
        f"{api_config['base_url']}/vst/api/v1/storage/size",
        params={'timelines': 'true'},
        timeout=10,
        verify=api_config.get('verify_ssl', False),
    )
    if resp.status_code != 200:
        return
    data = resp.json() or {}
    for stream_id, info in data.items():
        if not isinstance(info, dict):
            continue
        # Storage-size keys are streamIds, not filenames; the cancelled file
        # should not have produced a sensor/stream entry at all.
        if context.sensor_id in stream_id and info.get('usedBytes'):
            # An entry exists — fail if it's the aborted one. Allow the deleted
            # re-upload case for separate scenarios.
            pytest.fail(
                f"Aborted upload may have left bytes: stream={stream_id} info={info}"
            )


# ---------------------------------------------------------------------------
# BDD-GAP-011 — DELETE pass-through uploaded file
# ---------------------------------------------------------------------------

@given('a video file has been uploaded as a pass-through upload')
def upload_passthrough(context, api_config):
    src = _static_video()
    local = context.temp_dir / f"src_{uuid.uuid4().hex[:8]}.mp4"
    shutil.copy2(src, local)
    filename = f"passthru_{uuid.uuid4().hex[:8]}.mp4"
    context.passthru_filename = filename

    response = upload_file_simple(
        api_config['base_url'], local, filename,
        sensor_id=context.sensor_id,
        timestamp='2026-05-14T00:00:00.000Z',
        verify_ssl=api_config.get('verify_ssl', False),
    )
    assert response.status_code in (200, 201), (
        f"Pass-through upload failed: {response.status_code} {response.text[:300]}"
    )
    data = response.json()
    context.passthru_response = data
    stream_id = data.get('streamId')
    assert stream_id, f"Pass-through upload missing streamId: {data}"
    context.passthru_stream_id = stream_id
    context.uploaded_stream_ids.add(stream_id)
    time.sleep(2)


@when('the file is deleted via the storage DELETE API')
def delete_passthru_file(context, api_config):
    deleted = _delete_stream_timelines(api_config, context.passthru_stream_id)
    context.passthru_delete_response = deleted
    assert deleted > 0, (
        f"DELETE for pass-through streamId {context.passthru_stream_id} affected no timelines"
    )


@then('the DELETE response is success')
def delete_response_success(context):
    assert context.passthru_delete_response and context.passthru_delete_response > 0


@then('the deleted file no longer appears in the media file list')
def passthru_not_in_list(context, api_config):
    deadline = time.time() + 15
    target = context.passthru_filename
    while time.time() < deadline:
        resp = requests.get(
            f"{api_config['base_url']}/vst/api/v1/storage/file/list",
            timeout=10,
            verify=api_config.get('verify_ssl', False),
        )
        if resp.status_code == 200:
            listing = resp.json() or {}
            entries = listing.get(context.sensor_id, []) or []
            present = any(target in str(e) for e in entries)
            if not present:
                return
        time.sleep(2)
    pytest.fail(
        f"Pass-through file '{target}' still listed after DELETE"
    )
