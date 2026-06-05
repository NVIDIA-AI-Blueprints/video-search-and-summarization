# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: MIT
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE

"""
Coverage for the sensor-delete file-cleanup behaviour: when a file-type
sensor (created by PUT /api/v1/storage/file) is deleted via the sensor
delete API, the recording(s) backing its streams must be removed from
the storage service along with the sensor record.
"""
import logging
import os
import time
import uuid
from pathlib import Path
from typing import List, Optional

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_delete,
    api_get,
    validate_list_response,
)

logger = logging.getLogger(__name__)

scenarios(
    "../../../features/unit_tests/storage_management/file_sensor_delete_cleanup.feature"
)


STATIC_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "test_video.mp4"
)

# Stable naming root. Do NOT use this in the function-scoped autouse
# sweep below: under xdist the fixture runs concurrently on every worker
# and a base-prefix match would let one worker reap another worker's
# in-flight artifacts mid-test. Cross-worker / cross-run orphan
# reaping belongs in a one-time controller step (e.g. pytest_sessionstart
# in a shared conftest) where there are no concurrent in-flight tests.
BASE_TEST_PREFIX = "vios-delcleanup-"
# Worker-scoped prefix used for creating sensors / files, for
# same-worker assertions, AND for the per-scenario sweep. The worker
# id keeps concurrent xdist workers from clobbering each other's
# in-flight artifacts.
TEST_PREFIX = f"{BASE_TEST_PREFIX}{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _list_sensors(base_url: str, verify_ssl: bool, timeout: int) -> List[dict]:
    resp = api_get(
        base_url, "/vst/api/v1/sensor/list",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    return validate_list_response(resp)


def _put_file(
    base_url: str,
    filename: str,
    sensor_id: str,
    timestamp: str,
    verify_ssl: bool,
    timeout: int,
) -> requests.Response:
    """PUT the static video to /storage/file/{filename} with sensorId/timestamp."""
    url = f"{base_url}/vst/api/v1/storage/file/{filename}"
    params = {"sensorId": sensor_id, "timestamp": timestamp}
    headers = {"Content-Type": "application/octet-stream"}
    return requests.put(
        url, params=params, data=STATIC_VIDEO.read_bytes(), headers=headers,
        timeout=timeout, verify=verify_ssl,
    )


def _sensor_present(sensors: List[dict], sensor_id: str) -> bool:
    """True if a non-tombstoned sensor with the given ID exists."""
    for s in sensors:
        if not isinstance(s, dict):
            continue
        if s.get("sensorId") != sensor_id:
            continue
        if s.get("state") == "removed":
            continue
        return True
    return False


def _file_in_storage_list(
    base_url: str, filename: str, verify_ssl: bool, timeout: int,
) -> bool:
    """Probe /storage/file/list and report whether *filename* is referenced."""
    try:
        resp = api_get(
            base_url, "/vst/api/v1/storage/file/list",
            verify_ssl=verify_ssl, timeout=timeout,
        )
        if resp.status_code == 200:
            return filename in resp.text
    except Exception as exc:
        logger.warning("storage/file/list probe failed: %s", exc)
    return False


def _wait_file_gone(
    base_url: str,
    filename: str,
    verify_ssl: bool,
    timeout: int,
    poll_attempts: int = 120,
    poll_delay: float = 0.5,
) -> bool:
    """Poll /storage/file/list until *filename* disappears or attempts exhausted.

    Default budget is 60s -- the sensor-ms -> SDR -> proxy/delete -> file-unlink
    chain is bounded by SDR's serial event consumer, so under sustained delete
    pressure (full unit-test suite, scenarios filling to the sensor limit) the
    visible delete can lag many seconds. 60s is well above worst-case observed
    in the count=5 stress run.
    """
    for _ in range(poll_attempts):
        if not _file_in_storage_list(base_url, filename, verify_ssl, timeout):
            return True
        time.sleep(poll_delay)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function", autouse=True)
def cleanup_test_artifacts(request, api_config: dict, unit_test_params: dict):
    """Sweep any sensor with the test prefix before and after each scenario."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    def _sweep():
        try:
            sensors = _list_sensors(base_url, verify_ssl, timeout)
        except Exception as exc:
            logger.warning("pre/post-scenario sweep: sensor/list failed: %s", exc)
            return
        for s in sensors:
            sid = s.get("sensorId") if isinstance(s, dict) else None
            name = s.get("name") if isinstance(s, dict) else None
            if not sid:
                continue
            if (sid.startswith(TEST_PREFIX) or
                (name and name.startswith(TEST_PREFIX))):
                try:
                    api_delete(
                        base_url, f"/vst/api/v1/sensor/{sid}",
                        verify_ssl=verify_ssl, timeout=timeout,
                    )
                except Exception as exc:
                    logger.warning("sweep: failed to delete %s: %s", sid, exc)

    _sweep()
    yield
    _sweep()


@pytest.fixture(scope="function")
def context() -> UnitTestContext:
    return UnitTestContext()


class _UploadRecord:
    __slots__ = ("filename", "sensor_id", "timestamp", "response")

    def __init__(
        self,
        filename: str,
        sensor_id: str,
        timestamp: str,
        response: requests.Response,
    ):
        self.filename = filename
        self.sensor_id = sensor_id
        self.timestamp = timestamp
        self.response = response


def _ensure_uploads(context: UnitTestContext) -> List[_UploadRecord]:
    if not hasattr(context, "uploads"):
        context.uploads = []  # type: ignore[attr-defined]
    return context.uploads  # type: ignore[attr-defined]


def _upload_one(
    context: UnitTestContext,
    base_url: str,
    verify_ssl: bool,
    timeout: int,
    sensor_id: Optional[str] = None,
    timestamp: str = "2025-06-15T12:00:00.000Z",
) -> _UploadRecord:
    """Upload a uniquely-named file sensor and return the upload record."""
    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}{tag}.mp4"
    sid = sensor_id or f"{TEST_PREFIX}sensor-{tag}"
    resp = _put_file(
        base_url, filename, sensor_id=sid, timestamp=timestamp,
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert resp.status_code in (200, 201), (
        f"Setup upload should succeed, got {resp.status_code}: {resp.text[:300]}"
    )
    rec = _UploadRecord(filename, sid, timestamp, resp)
    _ensure_uploads(context).append(rec)
    return rec


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------


@given("the VST storage management API is accessible")
def storage_api_accessible(api_config: dict) -> None:
    """Verify the storage management API base URL is configured."""
    assert api_config.get("base_url"), "Base URL must be configured"


@given("the static test video is available")
def static_video_available() -> None:
    """Verify the static test video file is on disk."""
    assert STATIC_VIDEO.exists(), f"Static test video missing: {STATIC_VIDEO}"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------


@when("I upload a file sensor successfully")
def upload_one_file_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Upload a single file sensor and remember the upload record."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)
    _upload_one(context, base_url, verify_ssl, timeout)


@when("I upload two file sensors successfully")
def upload_two_file_sensors(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Upload two independent file sensors with different sensorIds."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)
    _upload_one(context, base_url, verify_ssl, timeout)
    _upload_one(context, base_url, verify_ssl, timeout)


@when("I add a second upload to the same sensor via the merge path")
def upload_merge_path_second_file(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT a second file with a different filename but the same sensorId; addFile
    takes the SENSOR_TYPE_FILE merge path and the sensor ends up with two streams,
    each backed by its own physical file."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)
    uploads = _ensure_uploads(context)
    assert uploads, "merge-path step requires an earlier successful upload"
    first = uploads[0]
    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}merge-{tag}.mp4"
    # Different timestamp so the merge produces a distinct stream segment.
    timestamp = "2025-06-15T13:00:00.000Z"
    resp = _put_file(
        base_url, filename, sensor_id=first.sensor_id, timestamp=timestamp,
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert resp.status_code in (200, 201), (
        f"Merge-path upload should succeed, got {resp.status_code}: {resp.text[:300]}"
    )
    uploads.append(_UploadRecord(filename, first.sensor_id, timestamp, resp))


@when("I delete that file sensor via the sensor delete API")
def delete_the_file_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """DELETE /sensor/{id} for the (most recent) uploaded file sensor."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    uploads = _ensure_uploads(context)
    assert uploads, "Delete step requires at least one prior upload"
    # In the multi-stream case all uploads share the same sensorId.
    target = uploads[-1]
    resp = api_delete(
        base_url, f"/vst/api/v1/sensor/{target.sensor_id}",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert resp.status_code in (200, 204), (
        f"sensor delete should succeed, got {resp.status_code}: {resp.text[:300]}"
    )


@when("I delete the first uploaded file sensor via the sensor delete API")
def delete_first_file_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """DELETE the first of the two uploaded file sensors so the second one remains."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    uploads = _ensure_uploads(context)
    assert len(uploads) >= 2, "Two-sensor scenarios require two prior uploads"
    first = uploads[0]
    resp = api_delete(
        base_url, f"/vst/api/v1/sensor/{first.sensor_id}",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert resp.status_code in (200, 204), (
        f"sensor delete should succeed, got {resp.status_code}: {resp.text[:300]}"
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------


@then("the deleted sensor's file should no longer be on the storage service")
def deleted_sensor_file_gone(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert the deleted sensor's file disappears from /storage/file/list."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    target = _ensure_uploads(context)[-1]
    assert _wait_file_gone(base_url, target.filename, verify_ssl, timeout), (
        f"File {target.filename} is still listed after the sensor was deleted; "
        f"sensor delete must remove the backing recording from disk."
    )


@then("the deleted sensor should no longer be in the sensor list")
def deleted_sensor_absent(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert /sensor/list does not contain the deleted sensor."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    target = _ensure_uploads(context)[-1]
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    assert not _sensor_present(sensors, target.sensor_id), (
        f"Sensor {target.sensor_id} should not be in the sensor list after delete"
    )


@then("the first uploaded sensor's file should no longer be on the storage service")
def first_sensor_file_gone(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    first = _ensure_uploads(context)[0]
    assert _wait_file_gone(base_url, first.filename, verify_ssl, timeout), (
        f"First sensor's file {first.filename} is still listed after delete"
    )


@then("the second uploaded sensor's file should still be on the storage service")
def second_sensor_file_present(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    second = _ensure_uploads(context)[1]
    assert _file_in_storage_list(base_url, second.filename, verify_ssl, timeout), (
        f"Second sensor's file {second.filename} should still be present; "
        f"deleting one file sensor must not affect another sensor's recordings."
    )


@then("the second uploaded sensor should still be in the sensor list")
def second_sensor_present(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    second = _ensure_uploads(context)[1]
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    assert _sensor_present(sensors, second.sensor_id), (
        f"Second sensor {second.sensor_id} should still be in the sensor list"
    )


@then("both uploaded files should no longer be on the storage service")
def both_files_gone(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Multi-stream coverage: every file uploaded to the deleted sensor is removed."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    uploads = _ensure_uploads(context)
    assert len(uploads) >= 2, "multi-stream scenario requires at least two uploads"
    stragglers = []
    for rec in uploads:
        if not _wait_file_gone(base_url, rec.filename, verify_ssl, timeout):
            stragglers.append(rec.filename)
    assert not stragglers, (
        f"Deleting the merge-path sensor must remove every backing file; "
        f"still listed: {stragglers}"
    )


@then("a download of the deleted file should return 4xx")
def download_of_deleted_file_404s(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """The file's download endpoint must fail (4xx) once the sensor is deleted."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    target = _ensure_uploads(context)[-1]
    # Stream download by sensorId (== streamId for a single-upload file sensor).
    resp = api_get(
        base_url, f"/vst/api/v1/storage/file/{target.sensor_id}",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert 400 <= resp.status_code < 500, (
        f"Downloading the deleted sensor's file should return 4xx, "
        f"got {resp.status_code}: {resp.text[:300]}"
    )
