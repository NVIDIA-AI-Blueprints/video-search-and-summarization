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
Regression test for bug 6167064: file sensor delete via /api/v1/sensor/{id}
must decrement the count in scaled deployments so subsequent uploads succeed.

These tests intentionally drive the deployment up to max_devices_supported
using uploaded file sensors and then exercise the sensor-delete path. They
do not generate video files on the fly; instead they reuse the static test
video shipped in test/bdd_tests/data/.

SKIPPED BY DEFAULT
------------------
Filling a default-configured deployment to the limit requires uploading
max_devices_supported file sensors (default 16, scaled 500). That's slow and
disruptive on shared CI machines that do not pre-set a low limit. The test is
therefore skipped unless the env var RUN_SENSOR_LIMIT_TEST is set to 1 (the
intent is for CI to lower max_devices_supported first, then set this var).

  RUN_SENSOR_LIMIT_TEST=1 poetry run pytest \\
      tests/unit_tests/sensor_management/test_sensor_limit_count.py
"""
import logging
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import List, Set

import pytest
import requests
from pytest_bdd import scenarios, given, when, then, parsers

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SENSOR_LIMIT_TEST", "").lower() not in ("1", "true", "yes"),
    reason=(
        "Sensor-limit regression for bug 6167064 fills the deployment to "
        "max_devices_supported and is not safe to run in default CI until the "
        "pipeline can pre-configure a low limit. Opt in with RUN_SENSOR_LIMIT_TEST=1."
    ),
)

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_post,
    api_delete,
    validate_dict_response,
    validate_list_response,
    extract_sensor_ids,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/sensor_management/sensor_limit_count.feature")


SENSOR_TYPE_FILE = "sensor_file"
SENSOR_TYPE_RTSP = "sensor_rtsp"
STATIC_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "test_video.mp4"
)
# NVStreamer host for live RTSP test sources. Falls back to RFC 5737 docs IP
# if NVStreamer is down (sensor entries still occupy slots either way).
NVSTREAMER_BASE_URL = "http://10.24.216.253:31000"
NVSTREAMER_FALLBACK_URL_BASE = "rtsp://192.0.2.1:554/bug6167064"
RTSP_TEST_NAME_PREFIX = "bug6167064-rtsp-"
# Patterns identifying file sensors this suite creates; safe to clean up
# even when not tracked (e.g. leftovers from a crashed run).
FILE_TEST_ID_PREFIXES = ("bug6167064-",)
FILE_TEST_NAME_PREFIXES = ("limit_filler_", "limit_post_delete_", "limit_refill_")


def _is_test_owned_file_sensor(sensor: dict) -> bool:
    """True if the sensor matches the file-sensor patterns this test creates."""
    sid = sensor.get("sensorId") or ""
    name = sensor.get("name") or ""
    if any(sid.startswith(p) for p in FILE_TEST_ID_PREFIXES):
        return True
    if any(name.startswith(p) for p in FILE_TEST_NAME_PREFIXES):
        return True
    return False


def _discover_nvstreamer_rtsp_urls(timeout: int = 10) -> List[str]:
    """Fetch live RTSP URLs from the NVStreamer /sensor/streams endpoint.

    Returns an empty list if NVStreamer is not reachable; callers fall back
    to NVSTREAMER_FALLBACK_URL_BASE in that case.
    """
    try:
        resp = requests.get(
            f"{NVSTREAMER_BASE_URL}/api/v1/sensor/streams",
            timeout=timeout,
        )
    except Exception as exc:
        logger.warning("NVStreamer unreachable for RTSP URL discovery: %s", exc)
        return []
    if resp.status_code != 200:
        logger.warning(
            "NVStreamer /sensor/streams returned %s, skipping URL discovery",
            resp.status_code,
        )
        return []
    try:
        data = resp.json()
    except ValueError:
        return []
    urls: List[str] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        for streams in entry.values():
            if not isinstance(streams, list):
                continue
            for s in streams:
                if isinstance(s, dict) and s.get("type") == "Rtsp":
                    url = s.get("url")
                    if url:
                        urls.append(url)
    return urls


def _next_rtsp_url(context: UnitTestContext, timeout: int) -> str:
    """Return the next live RTSP URL, lazily caching the discovered list."""
    if not getattr(context, "_rtsp_url_pool", None):
        context._rtsp_url_pool = _discover_nvstreamer_rtsp_urls(timeout)
        context._rtsp_url_cursor = 0
    pool: List[str] = context._rtsp_url_pool
    if not pool:
        # NVStreamer wasn't reachable — fall back to a unique unreachable URL.
        return f"{NVSTREAMER_FALLBACK_URL_BASE}/{uuid.uuid4().hex[:8]}"
    idx = context._rtsp_url_cursor % len(pool)
    context._rtsp_url_cursor += 1
    return pool[idx]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _file_sensor_ids(sensors: List[dict]) -> List[str]:
    """Return sensor IDs for file-type sensors only."""
    return [
        s["sensorId"]
        for s in sensors
        if isinstance(s, dict)
        and s.get("type") == SENSOR_TYPE_FILE
        and "sensorId" in s
    ]


def _rtsp_sensor_ids(sensors: List[dict]) -> List[str]:
    """Return sensor IDs for RTSP-type sensors only."""
    return [
        s["sensorId"]
        for s in sensors
        if isinstance(s, dict)
        and s.get("type") == SENSOR_TYPE_RTSP
        and "sensorId" in s
    ]


def _sensor_by_id(sensors: List[dict], sensor_id: str) -> dict:
    for s in sensors:
        if isinstance(s, dict) and s.get("sensorId") == sensor_id:
            return s
    return {}


def _add_rtsp_sensor(
    base_url: str,
    name: str,
    rtsp_url: str,
    verify_ssl: bool,
    timeout: int,
) -> requests.Response:
    """POST /vst/api/v1/sensor/add to register an RTSP sensor.

    verifyRtsp is omitted so the server does NOT run a DESCRIBE pre-flight —
    we want the sensor entry persisted purely to occupy/free a slot, not to
    actually stream from it.
    """
    body = {
        "name": name,
        "sensorUrl": rtsp_url,
        "location": "bug6167064-test",
        "tags": "bug6167064",
    }
    return api_post(
        base_url,
        "/vst/api/v1/sensor/add",
        json_body=body,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


def _online_sensor_count(sensors: List[dict]) -> int:
    """Count sensors reporting state == online."""
    return sum(
        1
        for s in sensors
        if isinstance(s, dict) and s.get("state") == "online"
    )


def _live_sensor_count(sensors: List[dict]) -> int:
    """Count non-tombstoned sensors. Matches the server's limit check, which
    counts every row in sensor_details regardless of online/offline state."""
    return sum(
        1
        for s in sensors
        if isinstance(s, dict) and s.get("state") != "removed"
    )


def _upload_file_sensor(
    base_url: str,
    file_path: Path,
    filename: str,
    sensor_id: str,
    verify_ssl: bool,
    timeout: int,
) -> requests.Response:
    """PUT /vst/api/v1/storage/file/{filename}?sensorId=... using the static video."""
    url = f"{base_url}/vst/api/v1/storage/file/{filename}"
    params = {"sensorId": sensor_id, "timestamp": "2025-01-01T00:00:00.000Z"}
    with open(file_path, "rb") as f:
        body = f.read()
    return requests.put(
        url,
        params=params,
        data=body,
        headers={"Content-Type": "application/octet-stream"},
        timeout=timeout,
        verify=verify_ssl,
    )


def _get_max_sensors(base_url: str, verify_ssl: bool, timeout: int) -> int:
    """Read max_devices_supported via the sensor configuration API."""
    resp = api_get(
        base_url,
        "/vst/api/v1/sensor/configuration",
        verify_ssl=verify_ssl,
        timeout=timeout,
    )
    cfg = validate_dict_response(resp)
    for key in ("maxSensorsSupported", "max_devices_supported"):
        if key in cfg:
            return int(cfg[key])
    raise AssertionError(
        f"Sensor configuration response missing max sensors field: {list(cfg.keys())[:20]}"
    )


def _list_sensors(base_url: str, verify_ssl: bool, timeout: int) -> List[dict]:
    resp = api_get(
        base_url,
        "/vst/api/v1/sensor/list",
        verify_ssl=verify_ssl,
        timeout=timeout,
    )
    return validate_list_response(resp)


# ---------------------------------------------------------------------------
# Fixtures / context
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def upload_workdir():
    """Per-scenario temporary directory for staged upload files."""
    d = Path(tempfile.mkdtemp(prefix="vst_sensor_limit_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture(scope="function", autouse=True)
def cleanup_filler_sensors(request, context: UnitTestContext, api_config: dict, unit_test_params: dict):
    """Per-scenario teardown: delete sensors this scenario created, restore any
    pre-existing RTSP sensors it borrowed, and sweep leftover RTSP sensors
    matching the test prefix from prior failed runs.
    """
    yield
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    # Delete sensors this scenario created.
    tracked: Set[str] = getattr(context, "tracked_sensor_ids", set())
    if tracked:
        logger.info("Post-scenario cleanup: removing %d tracked sensors", len(tracked))
        for sid in list(tracked):
            try:
                api_delete(
                    base_url,
                    f"/vst/api/v1/sensor/{sid}",
                    verify_ssl=verify_ssl,
                    timeout=timeout,
                )
            except Exception as exc:
                logger.warning("Cleanup: failed to delete tracked sensor %s: %s", sid, exc)

    # Restore pre-existing RTSP sensors the test removed.
    restorations: List[dict] = getattr(context, "deleted_pre_existing_rtsp", [])
    for entry in restorations:
        try:
            resp = _add_rtsp_sensor(
                base_url,
                entry["name"],
                entry["sensorUrl"],
                verify_ssl,
                timeout,
            )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "Cleanup: failed to restore RTSP sensor %s (url=%s): %s %s",
                    entry["name"], entry["sensorUrl"], resp.status_code,
                    resp.text[:200],
                )
        except Exception as exc:
            logger.warning(
                "Cleanup: exception restoring RTSP sensor %s: %s",
                entry.get("name"), exc,
            )

    # Sweep any test-prefixed sensors left behind by prior failed runs.
    try:
        sensors_now = _list_sensors(base_url, verify_ssl, timeout)
    except Exception as exc:
        logger.warning("Cleanup: sensor/list failed during sweep: %s", exc)
        return
    for s in sensors_now:
        if not isinstance(s, dict):
            continue
        name = s.get("name") or ""
        sid = s.get("sensorId")
        if not sid:
            continue
        if sid not in tracked and (
            name.startswith(RTSP_TEST_NAME_PREFIX)
            or _is_test_owned_file_sensor(s)
        ):
            try:
                api_delete(
                    base_url,
                    f"/vst/api/v1/sensor/{sid}",
                    verify_ssl=verify_ssl,
                    timeout=timeout,
                )
            except Exception as exc:
                logger.warning("Cleanup sweep: failed to delete %s: %s", sid, exc)


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST sensor management API is accessible")
def sensor_api_accessible(api_config: dict) -> None:
    """Verify the sensor management API base URL is configured and the test video exists."""
    assert api_config.get("base_url"), "Base URL must be configured"
    assert STATIC_VIDEO.exists(), (
        f"Static test video required but not found at {STATIC_VIDEO}"
    )


@given("the configured sensor limit is known")
def discover_sensor_limit(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Read max_devices_supported from the sensor configuration API and stash it."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    context.max_sensors = _get_max_sensors(base_url, verify_ssl, timeout)
    logger.info("Discovered max_devices_supported = %d", context.max_sensors)
    assert context.max_sensors > 0, "Sensor limit must be positive"


@given("the deployment is filled to the sensor limit with uploaded file sensors")
def fill_to_limit(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    upload_workdir: Path,
) -> None:
    """Top off the deployment with file-sensor uploads until total live sensors
    equals the limit. Also ensure at least MIN_TEST_FILE_SENSORS test-owned file
    sensors exist (swapping out RTSPs if the deployment is already at limit
    without file sensors), since several scenarios require file sensors to
    delete."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    # Largest count of file sensors any scenario in this suite needs at once.
    MIN_TEST_FILE_SENSORS = 3

    context.tracked_sensor_ids = set()
    context.uploaded_file_records = []
    context.deleted_pre_existing_rtsp = []

    def _upload_one_filler(index: int) -> None:
        run_tag = uuid.uuid4().hex[:8]
        filename = f"limit_filler_{run_tag}_{index}.mp4"
        staged = upload_workdir / filename
        shutil.copy2(STATIC_VIDEO, staged)
        sensor_id = f"bug6167064-fill-{run_tag}-{index}"
        resp = _upload_file_sensor(
            base_url, staged, filename, sensor_id, verify_ssl, timeout,
        )
        assert resp.status_code in (200, 201), (
            f"Filler upload #{index} failed unexpectedly: "
            f"status={resp.status_code} body={resp.text[:300]}"
        )
        body = resp.json() if resp.text else {}
        # Track by the requested sensorId (the response body's "id" is an
        # internal value that does not match /sensor/list or DELETE).
        context.tracked_sensor_ids.add(sensor_id)
        context.uploaded_file_records.append({
            "sensorId": sensor_id,
            "streamId": body.get("streamId"),
            "filename": filename,
        })

    sensors = _list_sensors(base_url, verify_ssl, timeout)
    # Fill against the server's limit-check basis (every live sensor row).
    existing_count = _live_sensor_count(sensors)
    needed = context.max_sensors - existing_count
    if needed < 0:
        pytest.skip(
            f"Deployment already over the sensor limit: {existing_count} live, "
            f"max {context.max_sensors}. Refusing to disturb state."
        )

    if needed > 0:
        logger.info(
            "Uploading %d file sensors to fill from %d to limit %d",
            needed, existing_count, context.max_sensors,
        )
        for i in range(needed):
            _upload_one_filler(i)
        sensors = _list_sensors(base_url, verify_ssl, timeout)

    # Ensure enough test-owned file sensors exist for file-delete scenarios.
    # If the deployment is at limit with only RTSP sensors, free slots by
    # borrowing pre-existing RTSPs (cleanup will restore them) and replace
    # with test file sensors.
    test_owned_files = [
        s for s in sensors
        if isinstance(s, dict)
        and s.get("type") == SENSOR_TYPE_FILE
        and _is_test_owned_file_sensor(s)
    ]
    deficit = MIN_TEST_FILE_SENSORS - len(test_owned_files)
    if deficit > 0:
        rtsp_to_swap = [
            s for s in sensors
            if isinstance(s, dict) and s.get("type") == SENSOR_TYPE_RTSP
            and s.get("sensorId") not in context.tracked_sensor_ids
        ][:deficit]
        if len(rtsp_to_swap) < deficit:
            pytest.skip(
                f"Need {deficit} more test file sensors but only {len(rtsp_to_swap)} "
                f"RTSP sensor(s) available to swap. Deployment state too constrained."
            )
        logger.info(
            "Swapping %d RTSP sensor(s) for test file sensors so file-delete scenarios can run",
            len(rtsp_to_swap),
        )
        for record in rtsp_to_swap:
            sid = record.get("sensorId")
            sensor_url = record.get("sensorUrl") or record.get("url") or ""
            if not sensor_url:
                info_resp = api_get(
                    base_url, f"/vst/api/v1/sensor/{sid}/info",
                    verify_ssl=verify_ssl, timeout=timeout,
                )
                if info_resp.status_code == 200:
                    try:
                        sensor_url = (info_resp.json().get("sensorUrl")
                                      or info_resp.json().get("url") or "")
                    except ValueError:
                        sensor_url = ""
            resp = api_delete(
                base_url, f"/vst/api/v1/sensor/{sid}",
                verify_ssl=verify_ssl, timeout=timeout,
            )
            assert resp.status_code in (200, 204), (
                f"RTSP delete for {sid} returned {resp.status_code}: {resp.text[:300]}"
            )
            context.deleted_pre_existing_rtsp.append({
                "sensorId": sid,
                "name": record.get("name") or sid,
                "sensorUrl": sensor_url,
            })
        for i in range(deficit):
            _upload_one_filler(i + 1000)  # offset to avoid name collision

    sensors_after = _list_sensors(base_url, verify_ssl, timeout)
    live_after = _live_sensor_count(sensors_after)
    assert live_after == context.max_sensors, (
        f"After filler uploads expected exactly {context.max_sensors} live sensors, "
        f"got {live_after}. Sensors: {[s.get('sensorId') for s in sensors_after]}"
    )


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I delete one uploaded file sensor via the sensor delete API")
def delete_one_file_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Delete one file sensor (preferring tracked, then test-owned, then any) via the sensor delete API."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    sensors = _list_sensors(base_url, verify_ssl, timeout)
    file_sensors = [s for s in sensors if isinstance(s, dict) and s.get("type") == SENSOR_TYPE_FILE]
    # Priority: tracked -> matches test naming patterns -> any file sensor.
    tracked = [s for s in file_sensors if s.get("sensorId") in context.tracked_sensor_ids]
    test_owned = [s for s in file_sensors if _is_test_owned_file_sensor(s)]
    candidates = tracked or test_owned or file_sensors
    assert candidates, (
        "No file sensors available to delete; full sensor list: "
        f"{[s.get('sensorId') for s in sensors]}"
    )
    victim = candidates[0].get("sensorId")
    resp = api_delete(
        base_url,
        f"/vst/api/v1/sensor/{victim}",
        verify_ssl=verify_ssl,
        timeout=timeout,
    )
    assert resp.status_code in (200, 204), (
        f"Sensor delete returned {resp.status_code}: {resp.text[:300]}"
    )
    context.last_deleted_sensor_id = victim
    context.tracked_sensor_ids.discard(victim)
    record = next(
        (r for r in context.uploaded_file_records if r["sensorId"] == victim),
        None,
    )
    context.last_deleted_record = record
    logger.info("Deleted file sensor: %s", victim)


@when(parsers.parse("I delete {count:d} uploaded file sensors via the sensor delete API"))
def delete_multiple_file_sensors(
    context: UnitTestContext, api_config: dict, unit_test_params: dict, count: int
) -> None:
    """Delete N file sensors using the same priority order as the single-delete step."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    sensors_before = _list_sensors(base_url, verify_ssl, timeout)
    context.live_count_before_bulk_delete = _live_sensor_count(sensors_before)
    file_sensors = [s for s in sensors_before if isinstance(s, dict) and s.get("type") == SENSOR_TYPE_FILE]
    tracked = [s.get("sensorId") for s in file_sensors if s.get("sensorId") in context.tracked_sensor_ids]
    test_owned = [s.get("sensorId") for s in file_sensors if _is_test_owned_file_sensor(s)]
    all_file_ids = [s.get("sensorId") for s in file_sensors]
    # Same priority order as the single-delete step.
    seen: Set[str] = set()
    candidates: List[str] = []
    for source in (tracked, test_owned, all_file_ids):
        for sid in source:
            if sid and sid not in seen:
                candidates.append(sid)
                seen.add(sid)
    assert len(candidates) >= count, (
        f"Need {count} file sensors, only {len(candidates)} available "
        f"(tracked={len(tracked)}, test_owned={len(test_owned)}, total={len(all_file_ids)})."
    )
    for sid in candidates[:count]:
        resp = api_delete(
            base_url,
            f"/vst/api/v1/sensor/{sid}",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        assert resp.status_code in (200, 204), (
            f"Sensor delete for {sid} returned {resp.status_code}: {resp.text[:300]}"
        )
        context.tracked_sensor_ids.discard(sid)
    context.last_bulk_delete_count = count


@when("I upload another file sensor")
def upload_another(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    upload_workdir: Path,
) -> None:
    """Upload one more file sensor; capture the response so the rejection / success can be asserted."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    run_tag = uuid.uuid4().hex[:8]
    filename = f"limit_post_delete_{run_tag}.mp4"
    staged = upload_workdir / filename
    shutil.copy2(STATIC_VIDEO, staged)
    sensor_id = f"bug6167064-post-{run_tag}"
    resp = _upload_file_sensor(
        base_url, staged, filename, sensor_id, verify_ssl, timeout,
    )
    context.last_upload_response = resp
    if resp.status_code in (200, 201):
        body = resp.json() if resp.text else {}
        context.tracked_sensor_ids.add(sensor_id)
        context.uploaded_file_records.append({
            "sensorId": sensor_id,
            "streamId": body.get("streamId"),
            "filename": filename,
        })


@when(parsers.parse("I can upload {count:d} more file sensors"))
@then(parsers.parse("I can upload {count:d} more file sensors"))
def upload_multiple_more(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    upload_workdir: Path,
    count: int,
) -> None:
    """Upload N additional file sensors and assert each one succeeds."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    for i in range(count):
        run_tag = uuid.uuid4().hex[:8]
        filename = f"limit_refill_{run_tag}_{i}.mp4"
        staged = upload_workdir / filename
        shutil.copy2(STATIC_VIDEO, staged)
        sensor_id = f"bug6167064-refill-{run_tag}-{i}"
        resp = _upload_file_sensor(
            base_url, staged, filename, sensor_id, verify_ssl, timeout,
        )
        assert resp.status_code in (200, 201), (
            f"Refill upload #{i} failed: status={resp.status_code} body={resp.text[:300]}"
        )
        body = resp.json() if resp.text else {}
        context.tracked_sensor_ids.add(sensor_id)
        context.uploaded_file_records.append({
            "sensorId": sensor_id,
            "streamId": body.get("streamId"),
            "filename": filename,
        })


@when("I list all sensors to refresh the cache")
def list_all_sensors_step(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Hit /sensor/list once to force the streamprocessing-ms sensor cache to refresh from the DB."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    context.sensor_list = sensors


@when("I delete one RTSP sensor via the sensor delete API")
def delete_one_rtsp_sensor(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """
    Delete an RTSP sensor. We prefer one we added ourselves (tracked); if none
    are available we borrow a pre-existing RTSP sensor from the deployment and
    capture its details so cleanup can restore it later. This keeps RTSP-delete
    scenarios runnable on deployments that don't already have an extra slot for
    a tracked RTSP sensor.
    """
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    sensors = _list_sensors(base_url, verify_ssl, timeout)
    rtsp_ids = _rtsp_sensor_ids(sensors)
    # No RTSP sensor in the deployment: free a slot by removing a
    # test-owned file sensor, then add a real RTSP sensor to delete.
    if not rtsp_ids:
        provisional_file = [
            s.get("sensorId") for s in sensors
            if isinstance(s, dict) and s.get("type") == SENSOR_TYPE_FILE
            and (s.get("sensorId") in context.tracked_sensor_ids
                 or _is_test_owned_file_sensor(s))
        ]
        assert provisional_file, (
            "No RTSP sensors and no test-owned file sensor available to free a slot."
        )
        api_delete(
            base_url, f"/vst/api/v1/sensor/{provisional_file[0]}",
            verify_ssl=verify_ssl, timeout=timeout,
        )
        context.tracked_sensor_ids.discard(provisional_file[0])
        run_tag = uuid.uuid4().hex[:8]
        name = f"{RTSP_TEST_NAME_PREFIX}{run_tag}"
        url = _next_rtsp_url(context, timeout)
        resp = _add_rtsp_sensor(base_url, name, url, verify_ssl, timeout)
        assert resp.status_code in (200, 201), (
            f"RTSP provisioning add failed: status={resp.status_code} body={resp.text[:300]}"
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        added_id = body.get("sensorId") or body.get("id")
        if added_id:
            context.tracked_sensor_ids.add(added_id)
        sensors = _list_sensors(base_url, verify_ssl, timeout)
        rtsp_ids = _rtsp_sensor_ids(sensors)
        assert rtsp_ids, "RTSP provisioning did not register a new RTSP sensor."

    # Prefer tracked (test-owned) RTSP sensors.
    victim = next((sid for sid in rtsp_ids if sid in context.tracked_sensor_ids), None)
    borrowed_from_deployment = False
    if victim is None:
        victim = rtsp_ids[0]
        borrowed_from_deployment = True
        record = _sensor_by_id(sensors, victim)
        sensor_url = record.get("sensorUrl") or record.get("url") or ""
        # /sensor/list doesn't always include the live RTSP URL — fetch /info
        if not sensor_url:
            info_resp = api_get(
                base_url,
                f"/vst/api/v1/sensor/{victim}/info",
                verify_ssl=verify_ssl,
                timeout=timeout,
            )
            if info_resp.status_code == 200:
                try:
                    sensor_url = (
                        info_resp.json().get("sensorUrl")
                        or info_resp.json().get("url")
                        or ""
                    )
                except ValueError:
                    sensor_url = ""

    resp = api_delete(
        base_url,
        f"/vst/api/v1/sensor/{victim}",
        verify_ssl=verify_ssl,
        timeout=timeout,
    )
    assert resp.status_code in (200, 204), (
        f"RTSP sensor delete returned {resp.status_code}: {resp.text[:300]}"
    )
    if borrowed_from_deployment:
        context.deleted_pre_existing_rtsp.append({
            "sensorId": victim,
            "name": record.get("name") or victim,
            "sensorUrl": sensor_url,
        })
    context.last_deleted_sensor_id = victim
    context.tracked_sensor_ids.discard(victim)
    logger.info(
        "Deleted RTSP sensor: %s (borrowed_from_deployment=%s)",
        victim, borrowed_from_deployment,
    )


@when("I add an RTSP sensor")
def add_rtsp_sensor_step(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """POST /sensor/add with a real NVStreamer RTSP URL and capture the response."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    run_tag = uuid.uuid4().hex[:8]
    name = f"{RTSP_TEST_NAME_PREFIX}{run_tag}"
    url = _next_rtsp_url(context, timeout)
    resp = _add_rtsp_sensor(base_url, name, url, verify_ssl, timeout)
    context.last_rtsp_add_response = resp
    if resp.status_code in (200, 201):
        try:
            body = resp.json()
        except ValueError:
            body = {}
        added_id = body.get("sensorId") or body.get("id")
        if added_id:
            context.tracked_sensor_ids.add(added_id)


@when(parsers.parse(
    "I delete {rtsp_count:d} RTSP sensors and {file_count:d} uploaded file sensor via the sensor delete API"
))
@when(parsers.parse(
    "I delete {rtsp_count:d} RTSP sensors and {file_count:d} uploaded file sensors via the sensor delete API"
))
def delete_mixed_sensors(
    context: UnitTestContext,
    api_config: dict,
    unit_test_params: dict,
    rtsp_count: int,
    file_count: int,
) -> None:
    """Delete N RTSP and M file sensors in one step; self-provision RTSP sensors if short."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    sensors_before = _list_sensors(base_url, verify_ssl, timeout)

    # Self-provision RTSP sensors when the deployment is short: drop
    # test-owned file sensors to free slots, then add real RTSP sensors.
    # Total online count stays stable because fill_to_limit ran first.
    rtsp_existing = _rtsp_sensor_ids(sensors_before)
    rtsp_short = max(0, rtsp_count - len(rtsp_existing))
    if rtsp_short > 0:
        provisional_file = [
            s.get("sensorId") for s in sensors_before
            if isinstance(s, dict) and s.get("type") == SENSOR_TYPE_FILE
            and (s.get("sensorId") in context.tracked_sensor_ids
                 or _is_test_owned_file_sensor(s))
        ]
        assert len(provisional_file) >= rtsp_short, (
            f"Need to free {rtsp_short} slot(s) for RTSP sensors but only "
            f"{len(provisional_file)} test-owned file sensor(s) are available."
        )
        for sid in provisional_file[:rtsp_short]:
            api_delete(
                base_url, f"/vst/api/v1/sensor/{sid}",
                verify_ssl=verify_ssl, timeout=timeout,
            )
            context.tracked_sensor_ids.discard(sid)
        for _ in range(rtsp_short):
            run_tag = uuid.uuid4().hex[:8]
            name = f"{RTSP_TEST_NAME_PREFIX}{run_tag}"
            url = _next_rtsp_url(context, timeout)
            resp = _add_rtsp_sensor(base_url, name, url, verify_ssl, timeout)
            assert resp.status_code in (200, 201), (
                f"RTSP provisioning add failed: status={resp.status_code} body={resp.text[:300]}"
            )
            try:
                body = resp.json()
            except ValueError:
                body = {}
            added_id = body.get("sensorId") or body.get("id")
            if added_id:
                context.tracked_sensor_ids.add(added_id)
        sensors_before = _list_sensors(base_url, verify_ssl, timeout)

    context.live_count_before_bulk_delete = _live_sensor_count(sensors_before)

    rtsp_ids = _rtsp_sensor_ids(sensors_before)
    # Prefer test-owned RTSP sensors (cleanup-safe) over pre-existing ones.
    rtsp_ids.sort(key=lambda sid: 0 if sid in context.tracked_sensor_ids else 1)
    file_sensors = [s for s in sensors_before if isinstance(s, dict) and s.get("type") == SENSOR_TYPE_FILE]
    tracked_file = [s.get("sensorId") for s in file_sensors if s.get("sensorId") in context.tracked_sensor_ids]
    test_owned_file = [s.get("sensorId") for s in file_sensors if _is_test_owned_file_sensor(s)]
    all_file_ids = [s.get("sensorId") for s in file_sensors]
    seen: Set[str] = set()
    file_ids: List[str] = []
    for source in (tracked_file, test_owned_file, all_file_ids):
        for sid in source:
            if sid and sid not in seen:
                file_ids.append(sid)
                seen.add(sid)
    assert len(rtsp_ids) >= rtsp_count, (
        f"Need {rtsp_count} RTSP sensors after provisioning, only {len(rtsp_ids)} available."
    )
    assert len(file_ids) >= file_count, (
        f"Need {file_count} file sensors, only {len(file_ids)} available."
    )

    # For each RTSP we delete, remember it so cleanup can restore it.
    for sid in rtsp_ids[:rtsp_count]:
        borrowed = sid not in context.tracked_sensor_ids
        if borrowed:
            record = _sensor_by_id(sensors_before, sid)
            sensor_url = record.get("sensorUrl") or record.get("url") or ""
            if not sensor_url:
                info_resp = api_get(
                    base_url,
                    f"/vst/api/v1/sensor/{sid}/info",
                    verify_ssl=verify_ssl,
                    timeout=timeout,
                )
                if info_resp.status_code == 200:
                    try:
                        sensor_url = (
                            info_resp.json().get("sensorUrl")
                            or info_resp.json().get("url")
                            or ""
                        )
                    except ValueError:
                        sensor_url = ""
        resp = api_delete(
            base_url,
            f"/vst/api/v1/sensor/{sid}",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        assert resp.status_code in (200, 204), (
            f"RTSP delete for {sid} returned {resp.status_code}: {resp.text[:300]}"
        )
        if borrowed:
            context.deleted_pre_existing_rtsp.append({
                "sensorId": sid,
                "name": record.get("name") or sid,
                "sensorUrl": sensor_url,
            })
        context.tracked_sensor_ids.discard(sid)

    for sid in file_ids[:file_count]:
        resp = api_delete(
            base_url,
            f"/vst/api/v1/sensor/{sid}",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        assert resp.status_code in (200, 204), (
            f"File delete for {sid} returned {resp.status_code}: {resp.text[:300]}"
        )
        context.tracked_sensor_ids.discard(sid)

    context.last_bulk_delete_count = rtsp_count + file_count


@when(parsers.parse("I add {count:d} RTSP sensor"))
@when(parsers.parse("I add {count:d} RTSP sensors"))
@then(parsers.parse("I add {count:d} RTSP sensor"))
@then(parsers.parse("I add {count:d} RTSP sensors"))
def add_multiple_rtsp(
    context: UnitTestContext, api_config: dict, unit_test_params: dict, count: int
) -> None:
    """POST /sensor/add N times using real NVStreamer RTSP URLs; assert each succeeds."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    for _ in range(count):
        run_tag = uuid.uuid4().hex[:8]
        name = f"{RTSP_TEST_NAME_PREFIX}{run_tag}"
        url = _next_rtsp_url(context, timeout)
        resp = _add_rtsp_sensor(base_url, name, url, verify_ssl, timeout)
        assert resp.status_code in (200, 201), (
            f"RTSP add failed: status={resp.status_code} body={resp.text[:300]}"
        )
        try:
            body = resp.json()
        except ValueError:
            body = {}
        added_id = body.get("sensorId") or body.get("id")
        if added_id:
            context.tracked_sensor_ids.add(added_id)


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the new upload should succeed")
def upload_succeeded(context: UnitTestContext) -> None:
    """Assert the most recent upload returned a 2xx status."""
    resp = context.last_upload_response
    assert resp is not None, "No upload response captured"
    assert resp.status_code in (200, 201), (
        f"Expected upload success, got {resp.status_code}: {resp.text[:300]}"
    )


@then("the upload should be rejected with sensor limit reached")
def upload_rejected_with_limit(context: UnitTestContext) -> None:
    """Assert the upload was rejected and the body indicates the sensor-limit cause."""
    resp = context.last_upload_response
    assert resp is not None, "No upload response captured"
    # Server returns 500 VMSInternalError + "Failed to add sensor to sensor
    # management" when the limit is hit (addFile / addSensorManually).
    assert resp.status_code >= 400, (
        f"Expected an error status when over the limit, got {resp.status_code}: "
        f"{resp.text[:300]}"
    )
    body_text = resp.text.lower()
    assert any(
        marker in body_text
        for marker in (
            "sensor limit",
            "sensors limit",
            "count limit",
            "max count",
            "maximum number",
            "failed to add sensor",
        )
    ), f"Response does not indicate the sensor-limit cause: {resp.text[:300]}"


@then("the sensor count should match the sensor limit")
def count_matches_limit(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Assert live sensor count equals max_devices_supported (limit-check basis).

    Poll a few times because the delete cascade (sensor-ms -> SDR ->
    streamprocessing-ms) is asynchronous, so the cache count visible to
    /sensor/list can lag the upload/add that re-fills the slot.
    """
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    sensors: List[dict] = []
    live = -1
    for _ in range(10):
        sensors = _list_sensors(base_url, verify_ssl, timeout)
        live = _live_sensor_count(sensors)
        if live == context.max_sensors:
            return
        time.sleep(0.5)
    assert False, (
        f"Expected exactly {context.max_sensors} live sensors, got {live}. "
        f"sensorIds: {extract_sensor_ids(sensors)}"
    )


@then("the sensor count should drop by 3")
def count_dropped_by_3(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Assert live sensor count dropped by exactly 3 since the bulk-delete step ran."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    sensors = _list_sensors(base_url, verify_ssl, timeout)
    live_after = _live_sensor_count(sensors)
    expected = context.live_count_before_bulk_delete - 3
    assert live_after == expected, (
        f"Expected {expected} live sensors after deleting 3, got {live_after}. "
        f"sensorIds: {extract_sensor_ids(sensors)}"
    )


@then("the new RTSP add should succeed")
def rtsp_add_succeeded(context: UnitTestContext) -> None:
    """Assert the most recent RTSP-add returned a 2xx status."""
    resp = context.last_rtsp_add_response
    assert resp is not None, "No RTSP add response captured"
    assert resp.status_code in (200, 201), (
        f"Expected RTSP add success, got {resp.status_code}: {resp.text[:300]}"
    )


@then("the sensor add should be rejected with sensor limit reached")
def rtsp_add_rejected_with_limit(context: UnitTestContext) -> None:
    """Assert the RTSP-add was rejected and the body indicates the sensor-limit cause."""
    resp = context.last_rtsp_add_response
    assert resp is not None, "No RTSP add response captured"
    assert resp.status_code >= 400, (
        f"Expected an error status when adding RTSP at the limit, got {resp.status_code}: "
        f"{resp.text[:300]}"
    )
    body_text = resp.text.lower()
    assert any(
        marker in body_text
        for marker in (
            "sensor limit",
            "sensors limit",
            "count limit",
            "max count",
            "maximum number",
            "not supported",
        )
    ), f"Response does not indicate the sensor-limit cause: {resp.text[:300]}"


@then("the uploaded media file no longer exists on the storage service")
def media_file_removed(
    context: UnitTestContext, api_config: dict, unit_test_params: dict
) -> None:
    """Assert the deleted file sensor's recording is no longer listed by /storage/file/list.

    Poll the listing because the cleanup chain (sensor-ms publishes camera_remove ->
    SDR routes proxy/delete -> streamprocessing-ms unlinks the file -> next list
    response reflects it) is asynchronous. Under heavy load the chain can lag a
    single probe; a short retry window keeps the assertion robust.
    """
    record = getattr(context, "last_deleted_record", None)
    assert record is not None, "No record of the deleted file sensor to verify"
    filename = record["filename"]
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    last_body = ""
    for _ in range(120):
        resp = api_get(
            base_url,
            "/vst/api/v1/storage/file/list",
            verify_ssl=verify_ssl,
            timeout=timeout,
        )
        if resp.status_code != 200:
            time.sleep(0.5)
            continue
        last_body = resp.text
        if filename not in last_body:
            return
        time.sleep(0.5)
    assert False, (
        f"After sensor delete the uploaded file '{filename}' should have been removed "
        f"from the storage service. file list body: {last_body[:500]}"
    )
