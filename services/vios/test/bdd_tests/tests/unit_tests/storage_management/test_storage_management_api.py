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
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

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
# Regression coverage for NVBug 6164097.
#
# POST /api/v1/storage/file returns several identifiers for the uploaded media
# (``id``, ``sensorId`` and ``streamId``). Both ``id`` and ``streamId`` are
# returned as unique handles for the same upload, yet
# DELETE /api/v1/storage/file/{handle} historically accepted only the ``id``
# value: deleting with the returned ``streamId`` failed with
# ``InvalidParameterError`` ("File not found for Unique ID ..."), leaving the
# uploaded file orphaned on disk. The scenarios below assert that BOTH
# identifiers handed back by the upload are accepted by the delete API and that
# the file is actually removed.
# ===========================================================================


STATIC_VIDEO = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "test_video.mp4"
)

# Worker-scoped naming prefix so concurrent xdist workers do not clobber each
# other's in-flight uploads during the pre/post sweep.
TEST_PREFIX = (
    f"vios-uldelid-{os.environ.get('PYTEST_XDIST_WORKER', 'main')}-"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _post_upload(
    base_url: str,
    filename: str,
    verify_ssl: bool,
    timeout: int,
) -> requests.Response:
    """Upload the static video via multipart POST /storage/file.

    Mirrors the bug's reproduction curl:
        curl -F "file=@sample.mp4" -F "fileName=sample.mp4" \
             -F "fileSize=<bytes>" .../storage/file
    """
    url = f"{base_url}/vst/api/v1/storage/file"
    payload = STATIC_VIDEO.read_bytes()
    files = {"file": (filename, payload, "video/mp4")}
    data = {"fileName": filename, "fileSize": str(len(payload))}
    return requests.post(
        url, files=files, data=data, timeout=timeout, verify=verify_ssl,
    )


def _file_in_storage_list(
    base_url: str, needle: str, verify_ssl: bool, timeout: int,
) -> bool:
    """Report whether *needle* (filename or id) is referenced by the file list."""
    try:
        resp = api_get(
            base_url, "/vst/api/v1/storage/file/list",
            verify_ssl=verify_ssl, timeout=timeout,
        )
        if resp.status_code == 200:
            return needle in resp.text
    except Exception as exc:
        logger.warning("storage/file/list probe failed: %s", exc)
    return False


def _wait_file_gone(
    base_url: str,
    needle: str,
    verify_ssl: bool,
    timeout: int,
    poll_attempts: int = 120,
    poll_delay: float = 0.5,
) -> bool:
    """Poll /storage/file/list until *needle* disappears or attempts exhausted."""
    for _ in range(poll_attempts):
        if not _file_in_storage_list(base_url, needle, verify_ssl, timeout):
            return True
        time.sleep(poll_delay)
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function", autouse=True)
def cleanup_uploaded_file(request, api_config: dict, unit_test_params: dict):
    """Best-effort teardown: delete the uploaded file by its (always-valid) id.

    The "delete by streamId" scenario leaves the file behind on the unfixed
    code, so we always attempt an id-based delete afterwards to avoid leaking
    uploads across runs.
    """
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    yield

    ctx = request.node.funcargs.get("context")
    file_id = getattr(ctx, "upload_id", None) if ctx is not None else None
    if not file_id:
        return
    try:
        api_delete(
            base_url, f"/vst/api/v1/storage/file/{file_id}",
            verify_ssl=verify_ssl, timeout=timeout,
        )
    except Exception as exc:
        logger.warning("teardown delete by id %s failed: %s", file_id, exc)


# ---------------------------------------------------------------------------
# Given (NVBug 6164097)
# ---------------------------------------------------------------------------


@given("the static test video is available")
def static_video_available() -> None:
    assert STATIC_VIDEO.exists(), f"Static test video missing: {STATIC_VIDEO}"


# ---------------------------------------------------------------------------
# When (NVBug 6164097)
# ---------------------------------------------------------------------------


@when("I upload a media file via POST to the storage service")
def upload_via_post(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 60)

    filename = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}.mp4"
    resp = _post_upload(base_url, filename, verify_ssl, timeout)
    assert resp.status_code in (200, 201), (
        f"POST upload should succeed, got {resp.status_code}: {resp.text[:300]}"
    )
    body = resp.json()
    assert isinstance(body, dict), f"Upload response should be a JSON object: {body!r}"

    context.upload_filename = filename  # type: ignore[attr-defined]
    context.upload_id = body.get("id")  # type: ignore[attr-defined]
    context.upload_stream_id = body.get("streamId")  # type: ignore[attr-defined]
    context.response_json = body


@when("I delete the uploaded file using the returned streamId")
def delete_by_stream_id(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    stream_id = getattr(context, "upload_stream_id", None)
    assert stream_id, (
        "Upload response did not include a streamId to delete with; "
        f"response was: {context.response_json!r}"
    )
    context.response = api_delete(
        base_url, f"/vst/api/v1/storage/file/{stream_id}",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    context.status_code = context.response.status_code


@when("I delete the uploaded file using the returned id")
def delete_by_id(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)

    file_id = getattr(context, "upload_id", None)
    assert file_id, (
        "Upload response did not include an id to delete with; "
        f"response was: {context.response_json!r}"
    )
    context.response = api_delete(
        base_url, f"/vst/api/v1/storage/file/{file_id}",
        verify_ssl=verify_ssl, timeout=timeout,
    )
    context.status_code = context.response.status_code


# ---------------------------------------------------------------------------
# Then (NVBug 6164097)
# ---------------------------------------------------------------------------


@then("the upload response contains both an id and a streamId")
def upload_has_both_identifiers(context: UnitTestContext) -> None:
    body = context.response_json
    assert isinstance(body, dict), f"Upload response should be a JSON object: {body!r}"
    file_id = body.get("id")
    stream_id = body.get("streamId")
    assert file_id, f"Upload response must include a non-empty 'id': {body!r}"
    assert stream_id, f"Upload response must include a non-empty 'streamId': {body!r}"


@then("the storage delete response status is 200")
def delete_status_200(context: UnitTestContext) -> None:
    resp = context.response
    assert resp is not None, "No delete response was captured"
    body = resp.text[:300]
    assert resp.status_code == 200, (
        f"Deleting the uploaded file with a handle returned by the upload "
        f"response must succeed (200); got {resp.status_code}: {body}. "
        f"Both 'id' and 'streamId' are advertised by the upload as identifiers "
        f"for the same file, so the delete API must accept either."
    )


@then("the uploaded file is no longer present on the storage service")
def uploaded_file_gone(
    context: UnitTestContext, api_config: dict, unit_test_params: dict,
) -> None:
    base_url = api_config["base_url"]
    verify_ssl = api_config.get("verify_ssl", False)
    timeout = unit_test_params.get("timeout", 30)
    filename: Optional[str] = getattr(context, "upload_filename", None)
    assert filename, "No uploaded filename recorded"
    assert _wait_file_gone(base_url, filename, verify_ssl, timeout), (
        f"File {filename} is still listed after a successful delete; the "
        f"returned identifier must actually remove the uploaded media from disk."
    )
