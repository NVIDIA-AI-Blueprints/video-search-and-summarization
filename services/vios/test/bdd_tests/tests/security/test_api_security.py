"""
Security BDD tests for VST REST APIs.

Implemented:
    BDD-GAP-072 - SQL-injection payloads in sensor name do not alter the DB
                  (NVBug 5512494). Closed-fixed regression guard for
                  parameterised query enforcement.
    BDD-GAP-073 - Storage upload rejects absolute filenames (NVBug 5493703).
                  Defense in depth against absolute-path filename payloads.
    BDD-GAP-074 - /storage/file/protect with non-object body returns 4xx and
                  does not crash streamprocessing-ms.
                  Regression guard for the validator first-match short-circuit
                  + top-level exception guard + handler isObject() check.
"""
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import pytest
import requests
from pytest_bdd import scenarios, given, when, then

logger = logging.getLogger(__name__)

scenarios("../../features/security/api_security.feature")


SQL_INJECTION_PAYLOAD = "'); DROP TABLE sensors;--"
SQL_INJECTION_NAME_PREFIX = "bdd-gap-072-"

# RFC 5737 TEST-NET-1 — guaranteed-unreachable but legal-format RTSP URL.
# verifyRtsp defaults to false on /sensor/add so this body still gets accepted
# down to whatever validation layer matters for the SQL-injection check.
SQL_INJECTION_RTSP_URL = "rtsp://192.0.2.1:554/sql-injection-test"

ABSOLUTE_PATH_FILENAME = "/etc/hosts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_sensor_list(
    api_config: Dict[str, Any], timeout: int
) -> List[Dict[str, Any]]:
    """Fetch /sensor/list and return the parsed array. Asserts table-still-exists."""
    url = f"{api_config['base_url']}/vst/api/v1/sensor/list"
    resp = requests.get(
        url, timeout=timeout, verify=api_config.get("verify_ssl", False)
    )
    assert resp.status_code == 200, (
        f"sensor/list must remain reachable (the sensors table must still "
        f"exist) but got status={resp.status_code}, body={resp.text[:300]}"
    )
    data = resp.json()
    assert isinstance(data, list), (
        f"sensor/list must return a JSON array, got {type(data).__name__}"
    )
    return data


def _delete_sensor(
    api_config: Dict[str, Any], sensor_id: str, timeout: int
) -> int:
    url = f"{api_config['base_url']}/vst/api/v1/sensor/{sensor_id}"
    resp = requests.delete(
        url, timeout=timeout, verify=api_config.get("verify_ssl", False)
    )
    return resp.status_code


# ---------------------------------------------------------------------------
# Given
# ---------------------------------------------------------------------------

@given("the VST API is configured for security tests")
def vst_api_configured_for_security(api_config: Dict[str, Any]) -> None:
    assert api_config["base_url"], "Base URL must be configured"


# ---------------------------------------------------------------------------
# When
# ---------------------------------------------------------------------------

@when("I POST to sensor/add with a SQL-injection payload as the name")
def post_sensor_add_with_sql_injection(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any]
) -> None:
    timeout = security_test_params["timeout"]

    baseline = _get_sensor_list(api_config, timeout)
    sec_context.baseline_sensor_count = len(baseline)
    sec_context.baseline_sensor_ids = [
        s.get("sensorId") for s in baseline if isinstance(s, dict)
    ]
    logger.info("Baseline sensor count: %d", sec_context.baseline_sensor_count)

    # Append a UUID so successive runs do not collide on name uniqueness.
    name = f"{SQL_INJECTION_NAME_PREFIX}{uuid.uuid4().hex[:8]}-{SQL_INJECTION_PAYLOAD}"
    sec_context.injected_sensor_name = name
    body = {
        "name": name,
        "sensorUrl": SQL_INJECTION_RTSP_URL,
        "location": "bdd-test",
        "tags": "bdd-gap-072",
    }
    url = f"{api_config['base_url']}/vst/api/v1/sensor/add"
    resp = requests.post(
        url,
        json=body,
        timeout=timeout,
        verify=api_config.get("verify_ssl", False),
    )
    sec_context.response = resp
    sec_context.status_code = resp.status_code
    try:
        sec_context.response_json = resp.json()
    except ValueError:
        sec_context.response_json = None
    logger.info(
        "POST /sensor/add SQL-injection: status=%d, body=%s",
        resp.status_code, str(sec_context.response_json)[:300],
    )


@when("I PUT a file using an absolute-path filename")
def put_file_with_absolute_filename(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any]
) -> None:
    timeout = security_test_params["timeout"]
    sec_context.attempted_filename = ABSOLUTE_PATH_FILENAME

    # URL-encode every slash and special char so the literal '/etc/hosts'
    # arrives at the server as the {filename} path parameter rather than as
    # nested URL segments. safe='' forces all slashes to be percent-encoded.
    encoded = quote(ABSOLUTE_PATH_FILENAME, safe="")
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/{encoded}"
    params = {
        "sensorId": f"test_upload_gap073_{uuid.uuid4().hex[:8]}",
        "timestamp": "2025-01-01T00:00:00.000Z",
    }
    body = b"x" * 64  # tiny non-zero payload, not a valid MP4

    resp = requests.put(
        url,
        params=params,
        data=body,
        headers={"Content-Type": "application/octet-stream"},
        timeout=timeout,
        verify=api_config.get("verify_ssl", False),
    )
    sec_context.response = resp
    sec_context.status_code = resp.status_code
    logger.info(
        "PUT absolute-path filename: status=%d, body=%s",
        resp.status_code, resp.text[:300],
    )


# ---------------------------------------------------------------------------
# Then - GAP-072 (SQL injection)
# ---------------------------------------------------------------------------

@then("the sensor add response status is not 500")
def assert_sensor_add_not_500(sec_context) -> None:
    assert sec_context.status_code != 500, (
        f"POST /sensor/add returned 500 for a SQL-injection payload. "
        f"This indicates the malicious string reached an unparameterised "
        f"query path and crashed the handler. Body: "
        f"{str(sec_context.response_json)[:500]}"
    )


@then("the sensors table is intact and queryable")
def assert_sensors_table_intact(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any]
) -> None:
    """If the DROP TABLE had executed, /sensor/list would 5xx. It must 200."""
    timeout = security_test_params["timeout"]
    sec_context.post_attack_list = _get_sensor_list(api_config, timeout)


@then("the sensor count reflects only the legitimate insert")
def assert_count_reflects_legit_insert(sec_context) -> None:
    """
    The injection POST is allowed to either:
      - succeed (server safely stored the literal string)  -> count == baseline + 1
      - be rejected with 4xx (validation rejected)         -> count == baseline
    Either is acceptable. What is forbidden is a count delta that looks like
    additional rows were processed (e.g. negative delta from DROP-then-recreate)
    or the table being missing entirely (covered by the previous step).
    """
    assert sec_context.baseline_sensor_count is not None, (
        "Baseline sensor count was not captured"
    )
    post_count = len(sec_context.post_attack_list)
    delta = post_count - sec_context.baseline_sensor_count

    if 200 <= sec_context.status_code < 300:
        assert delta == 1, (
            f"Server accepted the injection POST (status={sec_context.status_code}) "
            f"but sensor count delta is {delta} (expected exactly +1 for a single "
            f"legitimate insert). baseline={sec_context.baseline_sensor_count}, "
            f"post_attack={post_count}"
        )
        # If accepted, locate the new sensor and remember its id for cleanup.
        injected = [
            s for s in sec_context.post_attack_list
            if isinstance(s, dict) and s.get("name") == sec_context.injected_sensor_name
        ]
        if injected:
            sec_context.injected_sensor_id = injected[0].get("sensorId")
            logger.info(
                "Injection sensor stored verbatim under id=%s with name=%r",
                sec_context.injected_sensor_id, sec_context.injected_sensor_name,
            )
        else:
            pytest.fail(
                "Server returned 2xx but the malicious-name sensor is not "
                f"present in /sensor/list. Expected name verbatim: "
                f"{sec_context.injected_sensor_name!r}"
            )
    else:
        assert delta == 0, (
            f"Server rejected the injection POST (status={sec_context.status_code}) "
            f"but sensor count delta is {delta} (expected 0 - no row should "
            f"persist on rejection). baseline={sec_context.baseline_sensor_count}, "
            f"post_attack={post_count}"
        )


@then("I clean up the SQL-injection sensor if it was persisted")
def cleanup_injection_sensor(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any]
) -> None:
    sensor_id = sec_context.injected_sensor_id
    if not sensor_id:
        return
    status = _delete_sensor(api_config, sensor_id, security_test_params["timeout"])
    if status not in (200, 204):
        logger.warning(
            "Cleanup DELETE for SQL-injection sensor %s returned %d",
            sensor_id, status,
        )


# ---------------------------------------------------------------------------
# Then - GAP-073 (absolute-path filename)
# ---------------------------------------------------------------------------

@then("the upload response status is 4xx")
def assert_upload_4xx(sec_context) -> None:
    assert 400 <= sec_context.status_code < 500, (
        f"Expected 4xx rejection for absolute-path filename "
        f"{sec_context.attempted_filename!r}, got {sec_context.status_code}. "
        f"A 2xx here means the server accepted an absolute path as a "
        f"filename, which is the bug GAP-073 guards against. "
        f"Body: {sec_context.response.text[:500] if sec_context.response is not None else ''}"
    )


@then("the upload response status is not 500")
def assert_upload_not_500(sec_context) -> None:
    assert sec_context.status_code != 500, (
        f"Storage upload returned 500 for an absolute-path filename. "
        f"Body: {sec_context.response.text[:500] if sec_context.response is not None else ''}"
    )


@then("no file is created outside the configured storage root")
def assert_no_file_created_outside_root(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any]
) -> None:
    """
    API-level check: small settle, then list all files. The malicious
    filename string must not appear anywhere in the storage file list.
    Host-level filesystem inspection is out of scope for this test.
    """
    time.sleep(1)
    timeout = security_test_params["timeout"]
    url = f"{api_config['base_url']}/vst/api/v1/storage/file/list"
    resp = requests.get(
        url, timeout=timeout, verify=api_config.get("verify_ssl", False)
    )
    assert resp.status_code == 200, (
        f"Could not fetch /storage/file/list to verify GAP-073 contract: "
        f"status={resp.status_code}"
    )
    body_text = resp.text
    needle = sec_context.attempted_filename or ABSOLUTE_PATH_FILENAME
    assert needle not in body_text, (
        f"The absolute-path filename {needle!r} appears in the storage "
        f"file list. The PUT call should have been rejected without "
        f"creating any record."
    )


# ---------------------------------------------------------------------------
# BDD-GAP-074 - /storage/file/protect must not SIGABRT on
# malformed bodies (was: validator fall-through let an array body reach the
# handler, which called Json::Value::get() on it and threw an uncaught
# Json::LogicError, killing process).
# ---------------------------------------------------------------------------

# Each entry is the literal payload sent as the request body. We deliberately
# use json.dumps-able primitives that are NOT objects so they exercise every
# non-object branch of the validator + handler.
_PROTECT_MALFORMED_BODIES: Dict[str, Any] = {
    "array":   [{"sensorId": "x", "startTime": 0, "endTime": 0, "action": "add"}],
    "string":  "this is not an object",
    "number":  42,
    "boolean": True,
    "null":    None,
}


@when("I POST <body_kind> body to /storage/file/protect")
def post_malformed_body_to_protect(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any],
    body_kind: str,
) -> None:
    timeout = security_test_params["timeout"]
    payload = _PROTECT_MALFORMED_BODIES[body_kind]

    url = f"{api_config['base_url']}/vst/api/v1/storage/file/protect"
    # `requests` will serialise None as the JSON literal `null`, so we use
    # `json=` for all kinds — the server side is what we are testing, not the
    # client encoding.
    try:
        resp = requests.post(
            url, json=payload, timeout=timeout,
            verify=api_config.get("verify_ssl", False),
        )
    except requests.exceptions.ConnectionError as exc:
        # A connection error on this call is *itself* the failure mode the
        # bug describes: the server crashed mid-request and Envoy reset the
        # connection. Surface as a clean assertion failure rather than a test
        # error so the regression is unambiguous in the report.
        pytest.fail(
            f"POST /storage/file/protect with body_kind={body_kind!r} "
            f"caused a connection error ({exc}). This indicates "
            f"process crashed."
        )

    sec_context.protect_response = resp
    sec_context.protect_status_code = resp.status_code
    logger.info(
        "POST /storage/file/protect (body_kind=%s): status=%d, body=%s",
        body_kind, resp.status_code, resp.text[:300],
    )


@then("the protect response status is 4xx")
def assert_protect_status_4xx(sec_context) -> None:
    assert 400 <= sec_context.protect_status_code < 500, (
        f"Expected a 4xx response for a malformed /storage/file/protect body, "
        f"got status={sec_context.protect_status_code}"
    )


@then("the protect response status is not 5xx")
def assert_protect_status_not_5xx(sec_context) -> None:
    assert sec_context.protect_status_code < 500, (
        f"/storage/file/protect returned a 5xx ({sec_context.protect_status_code}) "
        f"for a malformed body. The validator + top-level exception guard must "
        f"convert this into a 4xx without crashing the upstream container "
        f"process."
    )


@then("/storage/info still returns 200 immediately afterwards")
def assert_storage_info_still_alive(
    sec_context, api_config: Dict[str, Any], security_test_params: Dict[str, Any]
) -> None:
    """
    Liveness probe right after the malformed protect call. If the container
    crashed, Envoy will return 503 (upstream connect error / connection
    termination) for at least the docker-restart window (~28s). If it stayed
    up, /storage/info answers 200 immediately.

    We do not retry: the whole point of this test is to assert that the
    process *did not* go down, not that it eventually came back.
    """
    timeout = security_test_params["timeout"]
    url = f"{api_config['base_url']}/vst/api/v1/storage/info"
    try:
        resp = requests.get(
            url, timeout=timeout, verify=api_config.get("verify_ssl", False),
        )
    except requests.exceptions.ConnectionError as exc:
        pytest.fail(
            f"GET /storage/info raised a connection error ({exc}) right "
            f"after a malformed /storage/file/protect call. The upstream "
            f"process is down."
        )

    sec_context.storage_info_status_code = resp.status_code
    assert resp.status_code == 200, (
        f"GET /storage/info returned status={resp.status_code} immediately "
        f"after a malformed /storage/file/protect call. Expected 200 (process "
        f"alive). A 503 here means streamprocessing-ms crashed and Envoy is "
        f"unable to reach it."
    )
