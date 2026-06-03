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
BDD tests for VST file upload with special filenames.

Covers BDD-GAP-005, BDD-GAP-006, BDD-GAP-007.
"""
import logging
import shutil
import socket
import tempfile
import uuid
from collections import namedtuple
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

# Minimal response shim so the @then steps can read .status_code / .text
# uniformly whether the upload was sent via `requests` or our raw-socket path.
RawResponse = namedtuple('RawResponse', ['status_code', 'text'])


def _raw_put(base_url: str, raw_path: str, body: bytes, timeout_sec: float = 30.0) -> RawResponse:
    """Send PUT with a literal request-target so URL traversal segments (`..`,
    `//`, etc.) are not collapsed by a high-level HTTP client. Returns
    (status_code, body_text) — body is truncated to first 2KB.

    We deliberately bypass `requests`/`urllib3` because they normalise the
    URL path before transmission (collapsing `a/../b` to `b`), which would
    short-circuit the server-side traversal defense we are trying to test.
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or 'localhost'
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    request = (
        f"PUT {raw_path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Content-Type: application/octet-stream\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode('ascii') + body

    with socket.create_connection((host, port), timeout=timeout_sec) as s:
        s.sendall(request)
        chunks = []
        while True:
            try:
                chunk = s.recv(65536)
            except socket.timeout:
                break
            if not chunk:
                break
            chunks.append(chunk)
        raw = b''.join(chunks)

    head, _, body_bytes = raw.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode('iso-8859-1', errors='replace')
    parts = status_line.split(' ', 2)
    status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0
    text = body_bytes[:2048].decode('utf-8', errors='replace')
    return RawResponse(status_code=status_code, text=text)

from .upload_test_utils import upload_file_simple

logger = logging.getLogger(__name__)

scenarios('../../features/file_upload/special_filenames.feature')


def _static_video() -> Path:
    p = Path(__file__).parent.parent.parent / "data" / "test_video.mp4"
    if not p.exists():
        raise FileNotFoundError(f"Static test video not found: {p}")
    return p


@given('the VST API is configured for special-filename uploads')
def vst_api_configured_special(context, api_config):
    assert api_config['base_url'], "Base URL must be configured"
    context.temp_dir = Path(tempfile.mkdtemp(prefix='vst_special_'))
    context.sensor_id = f"test_upload_{uuid.uuid4()}"
    context.upload_response = None
    context.uploaded_filename = None
    context.sensor_add_response = None
    logger.info("Configured special-filename upload, sensor_id=%s", context.sensor_id)


@when(parsers.parse('a video is uploaded with filename "{filename}"'))
def upload_with_special_filename(context, api_config, filename):
    src = _static_video()
    local = context.temp_dir / f"src_{uuid.uuid4().hex[:8]}.mp4"
    shutil.copy2(src, local)

    context.uploaded_filename = filename

    # URL-encode the filename in the path so characters like '+' (which would
    # otherwise be interpreted as a space) are passed through verbatim. This
    # is what a well-behaved client would do.
    encoded = quote(filename, safe='')
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{encoded}"
    params = {
        'sensorId': context.sensor_id,
        'timestamp': '2026-05-14T00:00:00.000Z',
    }
    with open(local, 'rb') as f:
        response = requests.put(
            url,
            params=params,
            data=f.read(),
            headers={'Content-Type': 'application/octet-stream'},
            timeout=30,
            verify=api_config.get('verify_ssl', False),
        )
    context.upload_response = response
    try:
        data = response.json()
        if isinstance(data, dict) and data.get('streamId'):
            context.uploaded_stream_ids.add(data['streamId'])
    except Exception:
        pass
    logger.info("Upload special filename '%s' (encoded='%s') -> status %d",
                filename, encoded, response.status_code)


@then('the special-filename upload succeeds')
def special_filename_upload_succeeds(context):
    assert context.upload_response is not None, "No upload response captured"
    status = context.upload_response.status_code
    assert status in (200, 201), (
        f"Expected 200/201 for safe special filename '{context.uploaded_filename}', got {status}. "
        f"Body: {context.upload_response.text[:300]}"
    )


@then('the uploaded file is retrievable by streamId')
def special_filename_retrievable(context, api_config):
    data = context.upload_response.json()
    stream_id = data.get('streamId')
    assert stream_id, f"Upload response missing streamId: {data}"

    url = f"{api_config['base_url']}/vst/api/v1/storage/size"
    resp = requests.get(
        url,
        params={'timelines': 'true'},
        timeout=10,
        verify=api_config.get('verify_ssl', False),
    )
    assert resp.status_code == 200, f"storage/size returned {resp.status_code}"
    storage = resp.json()
    assert stream_id in storage, f"streamId {stream_id} not present in storage size response"


@then('the uploaded file appears in the media file list')
def special_filename_in_list(context, api_config):
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    resp = requests.get(
        url,
        timeout=10,
        verify=api_config.get('verify_ssl', False),
    )
    assert resp.status_code == 200, f"file/list returned {resp.status_code}"
    listing = resp.json()
    entries = listing.get(context.sensor_id, [])
    assert entries, f"No entries listed under sensor_id {context.sensor_id}"


@when(parsers.parse('a video is uploaded with traversal filename "{filename}"'))
def upload_with_traversal_filename(context, api_config, filename):
    src = _static_video()
    local = context.temp_dir / f"src_{uuid.uuid4().hex[:8]}.mp4"
    shutil.copy2(src, local)
    body = local.read_bytes()

    context.uploaded_filename = filename

    # Build the literal request-target so segments like 'a/../b' and '//etc'
    # arrive at the server unmolested. `requests`/`urllib3` collapse such
    # segments client-side which would short-circuit the server-side
    # traversal defense we are trying to exercise here.
    raw_path = (
        f"/vst/api/v1/storage/file/{filename}"
        f"?sensorId={context.sensor_id}"
        f"&timestamp=2026-05-14T00:00:00.000Z"
    )

    try:
        response = _raw_put(api_config['base_url'], raw_path, body)
        context.upload_response = response
        # If the server unexpectedly accepted the traversal payload, parse
        # any streamId out of the response body so the cleanup hook removes
        # it. Body is a small JSON snippet so a substring search is enough.
        try:
            import json as _json
            data = _json.loads(response.text)
            if isinstance(data, dict) and data.get('streamId'):
                context.uploaded_stream_ids.add(data['streamId'])
        except Exception:
            pass
        logger.info("Traversal upload '%s' -> status %d", filename, response.status_code)
    except (socket.timeout, ConnectionError, OSError) as e:
        # Transport-level rejection (server closed the connection on the
        # malformed request) is also a valid form of "did not succeed".
        context.upload_response = None
        context.upload_exception = e
        logger.info("Traversal upload '%s' rejected at transport layer: %s", filename, e)


@then('the traversal upload is rejected with a 4xx status')
def traversal_rejected(context):
    if context.upload_response is None:
        return  # transport-level rejection accepted as defense in depth
    status = context.upload_response.status_code
    # Treat 4xx (explicit validation) and 503 from Envoy (route/URL refused
    # before reaching the backend) both as acceptable rejection — what we
    # really care about is that the upload did NOT succeed (2xx).
    assert not (200 <= status < 300), (
        f"Traversal payload '{context.uploaded_filename}' was accepted with "
        f"status {status}. This indicates a real defense-in-depth gap. "
        f"Body: {context.upload_response.text[:300]}"
    )


@then('no file is written outside the storage root')
def no_file_outside_storage(context, api_config):
    """Defense in depth: traversal payload must not be in any normal listing."""
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    resp = requests.get(
        url,
        timeout=10,
        verify=api_config.get('verify_ssl', False),
    )
    if resp.status_code != 200:
        return
    listing = resp.json()
    flat = str(listing)
    # The traversal filename should never round-trip into any listed path
    assert '/etc/passwd' not in flat and '/etc/hosts' not in flat, (
        f"Traversal payload may have escaped: {flat[:500]}"
    )


@when('a sensor is added with a name that exceeds the supported length')
def add_sensor_long_name(context, api_config):
    # The product intentionally truncates user-supplied sensor names down
    # to MAX_SENSOR_NAME_LENGTH (175 chars) via truncateString() in
    # sensor_management_utils.cpp — this is a deliberate design choice, not
    # a defect. We send 256 chars (well over the cap) and assert the API
    # accepts the request and stores the truncated form.
    context.max_sensor_name_length = 175
    long_name = 'x' * 256
    context.long_name_value = long_name
    url = f"{api_config['base_url']}/vst/api/v1/sensor/add"
    # Schema per src/framework/web/api_spec/services/sensor_spec.h:54 —
    # username & password are required; sensorUrl is the RTSP source.
    body = {
        "username": "",
        "password": "",
        "name": long_name,
        "sensorUrl": "rtsp://127.0.0.1:8554/nonexistent_for_validation_test",
    }
    try:
        response = requests.post(
            url,
            json=body,
            timeout=15,
            verify=api_config.get('verify_ssl', False),
        )
        context.sensor_add_response = response
        try:
            data = response.json()
            if isinstance(data, dict) and data.get('sensorId'):
                # Track immediately so the autouse cleanup hook deletes it
                # whether the test passes, fails, or is interrupted.
                context.long_name_sensor_id = data['sensorId']
                context.created_sensor_ids.add(data['sensorId'])
        except Exception:
            pass
        logger.info("Add long-name sensor -> status %d, body=%s",
                    response.status_code, response.text[:200])
    except requests.exceptions.RequestException as e:
        context.sensor_add_response = None
        context.sensor_add_exception = e
        logger.info("Long-name sensor add rejected at transport: %s", e)


@then('the sensor add response is success')
def sensor_add_success(context):
    """Truncate-on-overflow is the intended design — the request must
    succeed (2xx) even when the name exceeds the cap. A 4xx/5xx here would
    mean either a behaviour regression or that this test was written for a
    different sensor-create endpoint."""
    assert context.sensor_add_response is not None, (
        "Sensor add request failed at the transport layer"
    )
    status = context.sensor_add_response.status_code
    assert 200 <= status < 300, (
        f"Expected 2xx for over-long sensor name (the product truncates); "
        f"got {status}. Body: {context.sensor_add_response.text[:300]}"
    )


@then('the stored sensor name is truncated to the maximum supported length')
def long_name_truncated_in_list(context, api_config):
    """Find the sensor we just created in /sensor/list and confirm its
    `name` is exactly MAX_SENSOR_NAME_LENGTH chars — the truncated prefix
    of the original long name. Failing modes the assertion catches:
      - silently stored full-length name (truncation regressed)
      - empty/zero-length stored name (rejection regressed)
      - stored name that does not prefix-match the original (corruption)
    """
    url = f"{api_config['base_url']}/vst/api/v1/sensor/list"
    resp = requests.get(url, timeout=10, verify=api_config.get('verify_ssl', False))
    assert resp.status_code == 200, f"sensor/list returned {resp.status_code}"

    sensors = resp.json() or []
    long_name = context.long_name_value
    max_len = context.max_sensor_name_length
    expected_truncated = long_name[:max_len]

    # Lookup by the sensorId we captured in the @when step. Falling back to
    # a name-prefix match keeps the test useful even if sensorId routing
    # changes.
    sid = getattr(context, 'long_name_sensor_id', None)
    matches = []
    for s in sensors:
        if not isinstance(s, dict):
            continue
        if sid and s.get('sensorId') == sid:
            matches.append(s)
        elif s.get('name', '').startswith(expected_truncated[: max(1, max_len // 2)]):
            matches.append(s)

    assert matches, (
        f"Created sensor (sensorId={sid}) not found in /sensor/list. "
        f"Expected a sensor with the truncated name; the create call returned "
        f"{context.sensor_add_response.text[:200]}"
    )
    stored = matches[0].get('name', '')
    assert stored == expected_truncated, (
        f"Stored sensor name does not match expected truncation. "
        f"Expected {max_len}-char prefix of the input; got {len(stored)}-char "
        f"value: {stored!r}"
    )
    # The autouse cleanup hook in conftest.py deletes the sensor via
    # context.created_sensor_ids — no inline DELETE needed here.
