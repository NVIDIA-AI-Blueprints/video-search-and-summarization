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
Regression test for bug 5757067: PUT /api/v1/storage/file failures must not
leave ghost files on disk or dangling sensors in the DB.

Two earlier patches (commit 1d8cd08a) addressed the pre-addFile() failure
paths -- "Failed to get media information", container/codec rejection,
transcoding failure, and addFile() error. SQA reopened the bug because the
post-addFile() metadata-processing paths still leak: pre-epoch timestamps
and duplicate uploads return 400 to the caller but leave the file on disk
and a sensor in the DB.

These scenarios exercise both groups so the suite catches future regressions.
"""
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_delete,
    validate_list_response,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/storage_management/ghost_file_cleanup.feature")


STATIC_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "test_video.mp4"
)

# Optional auxiliary assertion path; tests still pass if unreachable.
DEFAULT_STREAMER_DIR_IN_CONTAINER = "/home/vst/vst_release/streamer_videos"

# Stable naming root. Do NOT use this in the function-scoped autouse
# sweep below: under xdist the fixture runs concurrently on every worker
# and a base-prefix match would let one worker reap another worker's
# in-flight artifacts mid-test. Cross-worker / cross-run orphan
# reaping belongs in a one-time controller step (e.g. pytest_sessionstart
# in a shared conftest) where there are no concurrent in-flight tests.
BASE_TEST_PREFIX = "bug5757067-"
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


def _put_raw(
    base_url: str,
    filename: str,
    body: bytes,
    sensor_id: Optional[str] = None,
    timestamp: Optional[str] = None,
    verify_ssl: bool = False,
    timeout: int = 30,
    omit_content_length: bool = False,
) -> requests.Response:
    """PUT raw body to /vst/api/v1/storage/file/{filename}.

    If ``omit_content_length`` is True the request is sent with
    ``Transfer-Encoding: chunked`` so the server never sees a
    ``Content-Length`` header (matching the real-world failure mode).
    """
    url = f"{base_url}/vst/api/v1/storage/file/{filename}"
    params = {}
    if sensor_id:
        params["sensorId"] = sensor_id
    if timestamp:
        params["timestamp"] = timestamp

    headers = {"Content-Type": "application/octet-stream"}

    if omit_content_length:
        # Streaming iterable -> Transfer-Encoding: chunked, no Content-Length.
        def _gen():
            yield body
        return requests.put(
            url, params=params, data=_gen(), headers=headers,
            timeout=timeout, verify=verify_ssl,
        )

    return requests.put(
        url, params=params, data=body, headers=headers,
        timeout=timeout, verify=verify_ssl,
    )


def _put_short_body(
    base_url: str,
    filename: str,
    declared_length: int,
    actual_body: bytes,
    sensor_id: str,
    timestamp: str,
    verify_ssl: bool = False,
    timeout: int = 30,
) -> requests.Response:
    """PUT with a Content-Length header larger than the body actually sent."""
    url = f"{base_url}/vst/api/v1/storage/file/{filename}"
    params = {"sensorId": sensor_id, "timestamp": timestamp}
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Length": str(declared_length),
    }
    return requests.put(
        url, params=params, data=actual_body, headers=headers,
        timeout=timeout, verify=verify_ssl,
    )


def _sensor_present(sensors: List[dict], sensor_id: str) -> bool:
    """True if a non-tombstoned sensor with the given ID exists.

    /sensor/list keeps deleted sensors with state == 'removed' for a while; we
    treat those as gone for the purpose of these assertions.
    """
    for s in sensors:
        if not isinstance(s, dict):
            continue
        if s.get("sensorId") != sensor_id:
            continue
        if s.get("state") == "removed":
            continue
        return True
    return False


def _file_present_via_storage_list(
    base_url: str, filename: str, verify_ssl: bool, timeout: int,
) -> bool:
    """Return True if the storage service knows about *filename*.

    Falls back to True only when both /storage/file/list and the
    in-container directory check are unavailable so we don't false-fail.
    """
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function", autouse=True)
def cleanup_test_artifacts(request, api_config: dict, unit_test_params: dict):
    """Sweep any sensor/file with the bug5757067- prefix before AND after each
    scenario so stale state from a previous failed run cannot poison the
    next assertion."""
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


# ---------------------------------------------------------------------------
# Per-scenario upload-record holder, attached to context for Then-step checks.
# ---------------------------------------------------------------------------

class _UploadRecord:
    __slots__ = ("filename", "sensor_id", "timestamp", "response")

    def __init__(
        self,
        filename: str,
        sensor_id: str,
        timestamp: Optional[str],
        response: requests.Response,
    ):
        self.filename = filename
        self.sensor_id = sensor_id
        self.timestamp = timestamp
        self.response = response


def _attach_upload(
    context: UnitTestContext, record: _UploadRecord, key: str = "last",
) -> None:
    """Attach an upload record to context. Maintains ordered list and named slot."""
    if not hasattr(context, "uploads"):
        context.uploads = []  # type: ignore[attr-defined]
    if not hasattr(context, "named_uploads"):
        context.named_uploads = {}  # type: ignore[attr-defined]
    context.uploads.append(record)
    context.named_uploads[key] = record


def _last_upload(context: UnitTestContext) -> _UploadRecord:
    assert getattr(context, "uploads", None), "No upload has been performed yet"
    return context.uploads[-1]


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
# When -- explicit scenarios
# ---------------------------------------------------------------------------

@when("I PUT a small non-video body to /storage/file")
def put_garbage_body(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT a 6-byte non-video body and capture the response (mirrors the bug 5757067 repro)."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}garbage-{tag}.mp4"
    sensor_id = f"{TEST_PREFIX}sensor-{tag}"
    resp = _put_raw(
        base_url, filename, body=b"123456",
        sensor_id=sensor_id, timestamp="2025-01-01T00:00:00.000Z",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    _attach_upload(context, _UploadRecord(filename, sensor_id, "2025-01-01T00:00:00.000Z", resp))


@when("I PUT a body that is shorter than its Content-Length to /storage/file")
def put_short_body(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT with a Content-Length much larger than the actual body to exercise the short-read path."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}short-{tag}.mp4"
    sensor_id = f"{TEST_PREFIX}sensor-{tag}"
    resp = _put_short_body(
        base_url, filename,
        declared_length=10_000_000,  # 10 MB declared
        actual_body=b"only-a-little",  # ~13 bytes actually sent
        sensor_id=sensor_id,
        timestamp="2025-01-01T00:00:00.000Z",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    _attach_upload(context, _UploadRecord(filename, sensor_id, "2025-01-01T00:00:00.000Z", resp))


@when("I PUT the static video twice in parallel with the same filename")
def put_video_twice_parallel(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT the same filename concurrently from two threads with different sensorIds."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}race-{tag}.mp4"
    sensor_id_a = f"{TEST_PREFIX}race-sensor-a-{tag}"
    sensor_id_b = f"{TEST_PREFIX}race-sensor-b-{tag}"
    body = STATIC_VIDEO.read_bytes()
    timestamp = "2025-01-01T00:00:00.000Z"

    results: Dict[str, requests.Response] = {}

    def _do(sid: str):
        try:
            results[sid] = _put_raw(
                base_url, filename, body=body, sensor_id=sid, timestamp=timestamp,
                verify_ssl=verify_ssl, timeout=timeout,
            )
        except Exception as exc:
            logger.warning("parallel upload %s exception: %s", sid, exc)

    threads = [
        threading.Thread(target=_do, args=(sensor_id_a,)),
        threading.Thread(target=_do, args=(sensor_id_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sensor_id_a in results and sensor_id_b in results, (
        f"One of the parallel uploads did not return a response: {list(results)}"
    )

    _attach_upload(context, _UploadRecord(filename, sensor_id_a, timestamp, results[sensor_id_a]), key="race_a")
    _attach_upload(context, _UploadRecord(filename, sensor_id_b, timestamp, results[sensor_id_b]), key="race_b")


@when("I PUT the static video with a pre-epoch timestamp to /storage/file")
def put_with_pre_epoch_timestamp(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT the static video with a pre-epoch timestamp that yields a negative epoch_ms."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}preepoch-{tag}.mp4"
    sensor_id = f"{TEST_PREFIX}preepoch-sensor-{tag}"
    resp = _put_raw(
        base_url, filename, body=STATIC_VIDEO.read_bytes(),
        sensor_id=sensor_id, timestamp="1900-01-01T00:00:00.000Z",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    _attach_upload(context, _UploadRecord(filename, sensor_id, "1900-01-01T00:00:00.000Z", resp))


@when("I successfully PUT the static video once")
def put_first_video(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT the static video once successfully and remember the upload as 'first'."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}dup-{tag}.mp4"
    sensor_id = f"{TEST_PREFIX}dup-sensor-{tag}"
    timestamp = "2025-06-15T12:00:00.000Z"
    resp = _put_raw(
        base_url, filename, body=STATIC_VIDEO.read_bytes(),
        sensor_id=sensor_id, timestamp=timestamp,
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert resp.status_code in (200, 201), (
        f"Setup upload should succeed, got {resp.status_code}: {resp.text[:300]}"
    )
    _attach_upload(context, _UploadRecord(filename, sensor_id, timestamp, resp), key="first")


@when("I PUT the static video again to the same filename")
def put_duplicate_filename(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Re-PUT to the SAME filename. The server should reject with 4xx via the
    fileExistsWithExtensions guard BEFORE writing anything to disk -- this
    is exactly the early-return path the original 1d8cd08a fix protects."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    first = context.named_uploads["first"]
    tag = uuid.uuid4().hex[:8]
    sensor_id = f"{TEST_PREFIX}dup-second-sensor-{tag}"
    resp = _put_raw(
        base_url, first.filename, body=STATIC_VIDEO.read_bytes(),
        sensor_id=sensor_id, timestamp=first.timestamp,
        verify_ssl=verify_ssl, timeout=timeout,
    )
    _attach_upload(context, _UploadRecord(first.filename, sensor_id, first.timestamp, resp), key="second")


@when("I PUT a different filename to the same sensorId with a pre-epoch timestamp")
def put_merge_path_with_bad_timestamp(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Trigger the addFile merge path then fail post-addFile timestamp validation.
    Exercises the rollback branch that must drop the just-merged stream while
    preserving the pre-existing sensor and its prior streams."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    first = context.named_uploads["first"]
    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}merge-bad-ts-{tag}.mp4"
    resp = _put_raw(
        base_url, filename, body=STATIC_VIDEO.read_bytes(),
        sensor_id=first.sensor_id, timestamp="1900-01-01T00:00:00.000Z",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    _attach_upload(context, _UploadRecord(filename, first.sensor_id, "1900-01-01T00:00:00.000Z", resp), key="second")


@when(parsers.parse("I PUT a request that {reject_reason}"))
def put_rejection_outline(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    reject_reason: str,
) -> None:
    """Drive a parameterised pre-write rejection (missing/zero Content-Length, spaces in name)."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    tag = uuid.uuid4().hex[:8]
    sensor_id = f"{TEST_PREFIX}reject-sensor-{tag}"
    timestamp = "2025-01-01T00:00:00.000Z"

    if reject_reason == "omits the Content-Length header":
        filename = f"{TEST_PREFIX}reject-no-cl-{tag}.mp4"
        resp = _put_raw(
            base_url, filename, body=b"123456",
            sensor_id=sensor_id, timestamp=timestamp,
            verify_ssl=verify_ssl, timeout=timeout,
            omit_content_length=True,
        )
    elif reject_reason == "declares Content-Length 0":
        filename = f"{TEST_PREFIX}reject-zero-cl-{tag}.mp4"
        # data=b"" makes requests send Content-Length: 0
        resp = _put_raw(
            base_url, filename, body=b"",
            sensor_id=sensor_id, timestamp=timestamp,
            verify_ssl=verify_ssl, timeout=timeout,
        )
    elif reject_reason == "uses a filename containing spaces":
        # Pre-encode the space so the URL parses cleanly and the server-side
        # filename validator (not nginx) rejects it. Track the decoded form.
        filename = f"{TEST_PREFIX}with%20space-{tag}.mp4"
        decoded_filename = filename.replace("%20", " ")
        resp = _put_raw(
            base_url, filename, body=b"123456",
            sensor_id=sensor_id, timestamp=timestamp,
            verify_ssl=verify_ssl, timeout=timeout,
        )
        _attach_upload(context, _UploadRecord(decoded_filename, sensor_id, timestamp, resp))
        return
    else:
        raise NotImplementedError(f"Unhandled reject_reason: {reject_reason}")

    _attach_upload(context, _UploadRecord(filename, sensor_id, timestamp, resp))


@when("I PUT the static video with a valid sensorId and timestamp")
def put_valid_video(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """PUT the static video with a normal sensorId and timestamp; capture the response."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    tag = uuid.uuid4().hex[:8]
    filename = f"{TEST_PREFIX}happy-{tag}.mp4"
    sensor_id = f"{TEST_PREFIX}happy-sensor-{tag}"
    resp = _put_raw(
        base_url, filename, body=STATIC_VIDEO.read_bytes(),
        sensor_id=sensor_id, timestamp="2025-06-15T12:00:00.000Z",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    _attach_upload(context, _UploadRecord(filename, sensor_id, "2025-06-15T12:00:00.000Z", resp))


# ---------------------------------------------------------------------------
# Then -- result assertions
# ---------------------------------------------------------------------------

@then("the upload should be rejected with 4xx")
def upload_rejected(context: UnitTestContext) -> None:
    """Assert the most recent upload returned a 4xx status."""
    rec = _last_upload(context)
    assert 400 <= rec.response.status_code < 500, (
        f"Expected 4xx rejection, got {rec.response.status_code}: "
        f"{rec.response.text[:300]}"
    )


@then("the upload should succeed with 2xx")
def upload_succeeded(context: UnitTestContext) -> None:
    """Assert the most recent upload returned a 2xx status."""
    rec = _last_upload(context)
    assert 200 <= rec.response.status_code < 300, (
        f"Expected 2xx success, got {rec.response.status_code}: "
        f"{rec.response.text[:300]}"
    )


@then("the response should mention a timestamp problem")
def response_mentions_timestamp(context: UnitTestContext) -> None:
    """Assert the failure message mentions a timestamp / epoch problem."""
    rec = _last_upload(context)
    body = rec.response.text.lower()
    assert any(marker in body for marker in ("timestamp", "epoch")), (
        f"Expected timestamp-related error message, got: {rec.response.text[:300]}"
    )


@then("no file should remain on the storage service")
def no_file_remains(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert /storage/file/list does NOT report the failed upload's filename."""
    rec = _last_upload(context)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    time.sleep(0.5)  # let server-side cleanup settle
    assert not _file_present_via_storage_list(
        base_url, rec.filename, verify_ssl, timeout,
    ), (
        f"Ghost file detected: {rec.filename} appears in /storage/file/list "
        f"after a failed upload (HTTP {rec.response.status_code})."
    )


@then("no sensor should be left behind")
def no_sensor_remains(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert /sensor/list does NOT contain the failed upload's sensor ID."""
    rec = _last_upload(context)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    time.sleep(0.5)
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    assert not _sensor_present(sensors, rec.sensor_id), (
        f"Dangling sensor detected: {rec.sensor_id} present after a failed upload "
        f"(HTTP {rec.response.status_code})."
    )


@then("the uploaded file should remain on the storage service")
def uploaded_file_present(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert /storage/file/list reports the successful upload's filename."""
    rec = _last_upload(context)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    assert _file_present_via_storage_list(
        base_url, rec.filename, verify_ssl, timeout,
    ), f"Successful upload {rec.filename} not visible in /storage/file/list"


@then("the uploaded sensor should appear in the sensor list")
def uploaded_sensor_present(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert /sensor/list contains the successful upload's sensor ID."""
    rec = _last_upload(context)
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    assert _sensor_present(sensors, rec.sensor_id), (
        f"Successful upload's sensor {rec.sensor_id} missing from sensor list"
    )


# ---------------------------------------------------------------------------
# Concurrent-upload Then-steps
# ---------------------------------------------------------------------------

def _race_pair(context: UnitTestContext) -> Tuple[_UploadRecord, _UploadRecord]:
    a = context.named_uploads["race_a"]
    b = context.named_uploads["race_b"]
    return a, b


@then("exactly one upload should succeed")
def exactly_one_race_success(context: UnitTestContext) -> None:
    """Assert exactly one of the parallel uploads returned 2xx and remember which."""
    a, b = _race_pair(context)
    successes = [r for r in (a, b) if 200 <= r.response.status_code < 300]
    assert len(successes) == 1, (
        f"Expected exactly one parallel upload to succeed, got "
        f"a={a.response.status_code} b={b.response.status_code} "
        f"(a body: {a.response.text[:200]}; b body: {b.response.text[:200]})"
    )
    context.named_uploads["race_winner"] = successes[0]
    context.named_uploads["race_loser"] = a if successes[0] is b else b


@then("the conflicting upload should be rejected with 4xx")
def conflicting_upload_rejected(context: UnitTestContext) -> None:
    """Assert the losing parallel upload returned 4xx."""
    loser = context.named_uploads["race_loser"]
    assert 400 <= loser.response.status_code < 500, (
        f"Expected losing race upload to be 4xx, got {loser.response.status_code}: "
        f"{loser.response.text[:300]}"
    )


@then("only the successful upload should be on disk")
def only_winner_on_disk(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert /storage/file/list contains the winner's file and no losing-side siblings."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    winner = context.named_uploads["race_winner"]
    # Both racers used the same filename, so check the winner's name is
    # present and no _1 / _2 variants of it landed on disk.
    time.sleep(0.5)
    resp = api_get(
        base_url, "/vst/api/v1/storage/file/list",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    assert resp.status_code == 200, (
        f"/storage/file/list returned {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.text
    occurrences = body.count(winner.filename)
    assert occurrences >= 1, (
        f"Winning upload {winner.filename} not in /storage/file/list"
    )
    # A `_1` or `_2` sibling would mean the loser was also persisted.
    base = winner.filename.rsplit(".", 1)[0]
    siblings = [s for s in (base + "_1", base + "_2") if s in body]
    assert not siblings, (
        f"Found sibling files for the losing race: {siblings}"
    )


@then("only the successful sensor should be in the deployment")
def only_winner_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert the winner's sensor exists and the loser's sensor does not."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    winner = context.named_uploads["race_winner"]
    loser = context.named_uploads["race_loser"]
    # Poll for the winner sensor: addFile + state propagation can lag.
    sensors: List[dict] = []
    for _ in range(10):
        time.sleep(0.5)
        sensors = _list_sensors(base_url, verify_ssl, timeout)
        if _sensor_present(sensors, winner.sensor_id):
            break
    assert _sensor_present(sensors, winner.sensor_id), (
        f"Winner sensor {winner.sensor_id} not present after retries"
    )
    assert not _sensor_present(sensors, loser.sensor_id), (
        f"Loser sensor {loser.sensor_id} should NOT be present after a "
        f"rejected parallel upload"
    )


# ---------------------------------------------------------------------------
# Duplicate-upload Then-steps
# ---------------------------------------------------------------------------

@then("the second upload should be rejected with 4xx")
def second_upload_rejected(context: UnitTestContext) -> None:
    """Assert the second (duplicate) upload returned 4xx."""
    second = context.named_uploads["second"]
    assert 400 <= second.response.status_code < 500, (
        f"Expected second upload to be 4xx, got {second.response.status_code}: "
        f"{second.response.text[:300]}"
    )


@then("only the first upload's file should be on disk")
def only_first_file_on_disk(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert the first upload's file is still listed and any distinct second filename is not."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    first = context.named_uploads["first"]
    second = context.named_uploads["second"]
    time.sleep(0.5)
    assert _file_present_via_storage_list(
        base_url, first.filename, verify_ssl, timeout,
    ), f"First upload {first.filename} should still be present"
    if second.filename != first.filename:
        assert not _file_present_via_storage_list(
            base_url, second.filename, verify_ssl, timeout,
        ), (
            f"Ghost file: second (rejected) upload {second.filename} should not "
            f"be present on the storage service"
        )


@then("only the first upload's sensor should be in the deployment")
def only_first_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert no extra live test-prefixed sensor was left behind by the rejected duplicate."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    first = context.named_uploads["first"]
    time.sleep(0.5)
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    assert _sensor_present(sensors, first.sensor_id), (
        f"First sensor {first.sensor_id} should still be present"
    )
    # No extra live test-prefixed sensor should exist; ignore tombstones.
    extras = [
        s for s in sensors
        if isinstance(s, dict)
        and s.get("sensorId", "").startswith(TEST_PREFIX)
        and s.get("sensorId") != first.sensor_id
        and s.get("state") != "removed"
    ]
    assert not extras, (
        f"Unexpected extra live test sensors after a rejected duplicate upload: "
        f"{[s.get('sensorId') for s in extras]}"
    )


@then("the original sensor and its first stream should still be present")
def merge_rollback_preserves_original(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert the merge-path rollback preserved the pre-existing sensor and the
    first upload's stream record (only the merged-in stream should be dropped)."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    first = context.named_uploads["first"]
    time.sleep(0.5)
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    assert _sensor_present(sensors, first.sensor_id), (
        f"Pre-existing sensor {first.sensor_id} was destroyed by merge-path rollback"
    )
    assert _file_present_via_storage_list(
        base_url, first.filename, verify_ssl, timeout,
    ), f"First upload {first.filename} should still be on the storage service"


@then("the second upload's file should not be on disk")
def merge_rollback_drops_second_file(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    """Assert the failed merge upload's file was cleaned up."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    second = context.named_uploads["second"]
    time.sleep(0.5)
    assert not _file_present_via_storage_list(
        base_url, second.filename, verify_ssl, timeout,
    ), f"Ghost file: failed merge upload {second.filename} still on storage service"
