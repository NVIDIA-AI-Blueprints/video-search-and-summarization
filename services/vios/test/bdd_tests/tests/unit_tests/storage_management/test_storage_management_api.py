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
Unit tests for the VST Storage Management Service API.

Tests: storage size, info, version, help, configuration, file list, protected files.
"""
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

from ..unit_test_utils import (
    UnitTestContext,
    api_get,
    api_delete,
    validate_json_response,
    validate_list_response,
    validate_string_response,
    validate_help_response,
    validate_dict_response,
)

logger = logging.getLogger(__name__)

scenarios("../../../features/unit_tests/storage_management/storage_management_api.feature")


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST storage management API is accessible")
def storage_api_accessible(api_config: dict) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I request the total storage size")
def request_storage_size(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/size",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage info")
def request_storage_info(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/info",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage management service version")
def request_storage_version(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/version",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage management service help")
def request_storage_help(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/help",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the storage management service configuration")
def request_storage_configuration(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/configuration",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the list of all media files")
def request_file_list(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/file/list",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


@when("I request the protected file list")
def request_protected_files(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    timeout = unit_test_params.get("timeout", 30)
    context.response = api_get(
        api_config["base_url"],
        "/vst/api/v1/storage/file/protected",
        verify_ssl=api_config.get("verify_ssl", False),
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# Then
# ---------------------------------------------------------------------------

@then("the storage response status is 200")
def check_storage_status_200(context: UnitTestContext) -> None:
    assert context.response.status_code == 200, (
        f"Expected 200, got {context.response.status_code}: {context.response.text[:500]}"
    )


@then("the storage info contains total used and available fields")
def check_storage_info_fields(context: UnitTestContext) -> None:
    data = validate_dict_response(context.response)
    expected_fields = ["total", "used", "available"]
    for field in expected_fields:
        assert field in data, f"Missing field '{field}' in storage info: {list(data.keys())}"
    logger.info("Storage: total=%s, used=%s, available=%s",
                data.get("total"), data.get("used"), data.get("available"))


@then("the storage response is a valid version string")
def check_storage_version_string(context: UnitTestContext) -> None:
    version = validate_string_response(context.response)
    assert len(version) > 0, "Version string is empty"
    logger.info("Service version: %s", version)


@then("the storage response is a list of supported API paths")
def check_storage_help_list(context: UnitTestContext) -> None:
    data = validate_help_response(context.response)
    logger.info("Supported APIs: %d", len(data))


@then("the storage response contains configuration fields")
def check_storage_configuration_fields(context: UnitTestContext) -> None:
    data = validate_json_response(context.response)
    assert isinstance(data, dict), "Configuration must be a JSON object"
    assert len(data) > 0, "Configuration is empty"
    logger.info("Configuration has %d fields", len(data))


# ===========================================================================
# Regression for NVBug 6221886: file/list ?tag= / ?eventInfo= filtering
#
# The file-upload metadata "tag" and "eventInfo" are persisted verbatim in the
# metadata_json column and round-trip in GET /v1/storage/file/list, but the
# list endpoint accepts only sensorId/startTime/endTime/offset/limit -- there
# is no ?tag= or ?eventInfo= predicate, so any such query parameter is silently
# ignored and the full unfiltered set is returned. These scenarios upload three
# files with distinct tag / eventInfo values and assert the list actually
# filters (exact match on tag, substring match on eventInfo). All values are
# salted with a per-run token so assertions are scoped to this run's uploads.
# ===========================================================================

STATIC_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "test_video.mp4"
)

# Per-run salt keeps tag/eventInfo/sensorId unique so assertions are scoped to
# this run and the cleanup sweep cannot reap another worker's artifacts.
RUN_TOKEN = f"{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-{uuid.uuid4().hex[:8]}"
SENSOR_PREFIX = f"bug6221886-{RUN_TOKEN}-"

UPLOAD_API = "/vst/api/v1/storage/file"
LIST_API = "/vst/api/v1/storage/file/list"


# ---------------------------------------------------------------------------
# Helpers (NVBug 6221886)
# ---------------------------------------------------------------------------

def _list_sensors(base_url: str, verify_ssl: bool, timeout: int) -> List[dict]:
    resp = api_get(
        base_url, "/vst/api/v1/sensor/list",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    try:
        return validate_list_response(resp)
    except Exception:
        return []


def _upload_with_metadata(
    base_url: str,
    filename: str,
    metadata: Dict[str, object],
    verify_ssl: bool,
    timeout: int,
) -> requests.Response:
    """Upload STATIC_VIDEO via multipart POST, carrying the full metadata blob.

    The multipart ``metadata`` form field is the only upload path that lets a
    client supply tag / eventInfo (the raw PUT API exposes just sensorId and
    timestamp), and it is stored verbatim in metadata_json.
    """
    chunk_identifier = str(uuid.uuid4())
    headers = {
        "nvstreamer-chunk-number": "1",
        "nvstreamer-total-chunks": "1",
        "nvstreamer-is-last-chunk": "true",
        "nvstreamer-identifier": chunk_identifier,
        "nvstreamer-file-name": filename,
    }
    with open(STATIC_VIDEO, "rb") as f:
        files = {"mediaFile": (filename, f, "application/octet-stream")}
        data = {"filename": filename, "metadata": json.dumps(metadata)}
        resp = requests.post(
            f"{base_url}{UPLOAD_API}",
            files=files,
            data=data,
            headers=headers,
            timeout=timeout,
            verify=verify_ssl,
        )
    logger.info("Upload %s (tag=%s): status %d", filename,
                metadata.get("tag"), resp.status_code)
    return resp


def _list_metadata_by_sensor(
    base_url: str, verify_ssl: bool, timeout: int,
    params: Optional[Dict[str, str]] = None,
) -> Dict[str, List[dict]]:
    """GET file/list and return {sensorId: [metadata, ...]} for the response.

    The list response is an object keyed by sensorId whose values are arrays of
    fileInfo objects; we project to the per-file ``metadata`` sub-object.
    """
    resp = api_get(
        base_url, LIST_API, verify_ssl=verify_ssl, timeout=timeout, params=params,
    )
    assert resp.status_code == 200, (
        f"file/list returned {resp.status_code}: {resp.text[:500]}"
    )
    body = resp.json()
    assert isinstance(body, dict), f"file/list must be a JSON object, got {type(body)}"
    out: Dict[str, List[dict]] = {}
    for sensor_id, files in body.items():
        metas = []
        if isinstance(files, list):
            for fileinfo in files:
                if isinstance(fileinfo, dict) and isinstance(fileinfo.get("metadata"), dict):
                    metas.append(fileinfo["metadata"])
        out[sensor_id] = metas
    return out


# ---------------------------------------------------------------------------
# Fixtures (NVBug 6221886)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function", autouse=True)
def cleanup_test_artifacts(request, api_config: dict, unit_test_params: dict):
    """Delete any sensor created by this run before and after each scenario so
    a failed run cannot poison the next assertion."""
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    def _sweep():
        for s in _list_sensors(base_url, verify_ssl, timeout):
            if not isinstance(s, dict):
                continue
            sid = s.get("sensorId")
            name = s.get("name")
            if not sid:
                continue
            if sid.startswith(SENSOR_PREFIX) or (name and str(name).startswith(SENSOR_PREFIX)):
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


# ---------------------------------------------------------------------------
# Given (NVBug 6221886)
# ---------------------------------------------------------------------------

@given("three media files are uploaded with distinct tag and eventInfo metadata")
def upload_three_files(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    shared_tag = f"Warehouse1_{RUN_TOKEN}"
    other_tag = f"Warehouse2_{RUN_TOKEN}"
    # Substring shared by files 1 and 2 but absent from file 3's eventInfo.
    shared_event_substr = f"Motion-{RUN_TOKEN}"

    # (label, sensorId, tag, eventInfo)
    plan = [
        ("file1", f"{SENSOR_PREFIX}cam-A", shared_tag, f"{shared_event_substr} Zone 3"),
        ("file2", f"{SENSOR_PREFIX}cam-B", other_tag, f"{shared_event_substr} Zone 4"),
        ("file3", f"{SENSOR_PREFIX}cam-C", shared_tag, f"Door open {RUN_TOKEN}"),
    ]

    uploaded: Dict[str, dict] = {}
    for idx, (label, sensor_id, tag, event_info) in enumerate(plan):
        filename = f"{SENSOR_PREFIX}{label}-{uuid.uuid4().hex[:6]}.mp4"
        metadata = {
            "sensorId": sensor_id,
            "timestamp": f"2025-01-0{idx + 1}T00:00:00.000Z",
            "tag": tag,
            "eventInfo": event_info,
            "streamName": filename,
        }
        resp = _upload_with_metadata(base_url, filename, metadata, verify_ssl, timeout)
        assert resp.status_code in (200, 201), (
            f"Upload of {label} failed: {resp.status_code} {resp.text[:500]}"
        )
        uploaded[label] = {
            "sensor_id": sensor_id,
            "tag": tag,
            "event_info": event_info,
            "filename": filename,
        }

    context.error = None
    context.streams = uploaded  # reuse the free-form slot to carry upload plan
    context.first_sensor_id = shared_tag
    context.first_stream_id = shared_event_substr
    context.sensor_list = [v["sensor_id"] for v in uploaded.values()]


@given("the uploaded files appear in the file list with their tag metadata")
def verify_metadata_persisted(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    by_sensor = _list_metadata_by_sensor(base_url, verify_ssl, timeout)
    for label, info in context.streams.items():
        sid = info["sensor_id"]
        assert sid in by_sensor, (
            f"{label}: sensor {sid} missing from unfiltered file/list "
            f"(present sensors: {[s for s in by_sensor if s.startswith(SENSOR_PREFIX)]})"
        )
        tags = [m.get("tag") for m in by_sensor[sid]]
        assert info["tag"] in tags, (
            f"{label}: expected tag {info['tag']!r} persisted for {sid}, got {tags}"
        )


# ---------------------------------------------------------------------------
# When (NVBug 6221886)
# ---------------------------------------------------------------------------

@when("I request the file list filtered by the tag shared by two of the files")
def filter_by_tag(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    shared_tag = context.first_sensor_id
    context.response_json = _list_metadata_by_sensor(
        base_url, verify_ssl, timeout, params={"tag": shared_tag},
    )


@when("I request the file list filtered by an eventInfo substring shared by two of the files")
def filter_by_event_info(context: UnitTestContext, api_config: dict, unit_test_params: dict) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    substr = context.first_stream_id
    context.response_json = _list_metadata_by_sensor(
        base_url, verify_ssl, timeout, params={"eventInfo": substr},
    )


# ---------------------------------------------------------------------------
# Then (NVBug 6221886)
# ---------------------------------------------------------------------------

@then("only the files carrying that tag are returned")
def assert_tag_filtered(context: UnitTestContext) -> None:
    by_sensor = context.response_json
    plan = context.streams
    cam_a = plan["file1"]["sensor_id"]
    cam_b = plan["file2"]["sensor_id"]
    cam_c = plan["file3"]["sensor_id"]

    # Files 1 (cam-A) and 3 (cam-C) carry the shared tag and must be present.
    assert cam_a in by_sensor, f"Tagged file1/{cam_a} missing from filtered list"
    assert cam_c in by_sensor, f"Tagged file3/{cam_c} missing from filtered list"

    # File 2 (cam-B) has a different tag and MUST be excluded. On the unfixed
    # code the ?tag= parameter is ignored, so cam-B is still present here.
    assert cam_b not in by_sensor, (
        f"?tag= filter ignored: file2/{cam_b} (tag {plan['file2']['tag']!r}) "
        f"was returned when filtering for tag {context.first_sensor_id!r}. "
        f"Filtered sensors from this run: "
        f"{[s for s in by_sensor if s.startswith(SENSOR_PREFIX)]}"
    )


@then("only the files whose eventInfo contains that substring are returned")
def assert_event_info_filtered(context: UnitTestContext) -> None:
    by_sensor = context.response_json
    plan = context.streams
    cam_a = plan["file1"]["sensor_id"]
    cam_b = plan["file2"]["sensor_id"]
    cam_c = plan["file3"]["sensor_id"]

    # Files 1 (cam-A) and 2 (cam-B) eventInfo contains the substring.
    assert cam_a in by_sensor, f"Matching file1/{cam_a} missing from filtered list"
    assert cam_b in by_sensor, f"Matching file2/{cam_b} missing from filtered list"

    # File 3 (cam-C) eventInfo does not contain it and MUST be excluded. On the
    # unfixed code the ?eventInfo= parameter is ignored, so cam-C is present.
    assert cam_c not in by_sensor, (
        f"?eventInfo= filter ignored: file3/{cam_c} "
        f"(eventInfo {plan['file3']['event_info']!r}) was returned when "
        f"filtering for substring {context.first_stream_id!r}. "
        f"Filtered sensors from this run: "
        f"{[s for s in by_sensor if s.startswith(SENSOR_PREFIX)]}"
    )
