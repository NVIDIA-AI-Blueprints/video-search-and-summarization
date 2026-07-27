# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end webhook tests driven by the file-sensor lifecycle."""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import requests
from pytest_bdd import given, scenarios, then, when

from ..test_utils import assert_with_detailed_failure
from .conftest import WebhookTestContext
from .webhook_test_utils import CapturedWebhookRequest, WebhookReceiver

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.notification, pytest.mark.webhook]

scenarios("../../features/notification/webhook_notifications.feature")

STATIC_VIDEO = Path(__file__).resolve().parent.parent.parent / "data" / "test_video.mp4"

# Schema of a camera_status_change notification, per .claude/commands/vios-architecture.md.
REQUIRED_PAYLOAD_KEYS = ("alert_type", "created_at", "event", "source")
REQUIRED_EVENT_KEYS = (
    "camera_id",
    "camera_name",
    "camera_type",
    "camera_url",
    "change",
    "metadata",
)
REQUIRED_EVENT_STRING_KEYS = tuple(key for key in REQUIRED_EVENT_KEYS if key != "metadata")
# camera_url scheme is dictated by camera_type.
CAMERA_URL_SCHEMES = {
    "file": ("http://", "https://"),
    "rtsp": ("rtsp://", "rtsps://"),
}


def _acceptable_camera_ids(context: WebhookTestContext) -> List[str]:
    """Return the camera_id values this scenario's sensor may publish under."""
    return context.expected_camera_ids or [context.sensor_id]


def _event_matches(
    request: CapturedWebhookRequest,
    path: str,
    change: str,
    camera_ids: List[str],
) -> bool:
    if request.path != path or not isinstance(request.json_body, dict):
        return False
    event = request.json_body.get("event")
    return (
        isinstance(event, dict)
        and event.get("change") == change
        and event.get("camera_id") in camera_ids
    )


def _wait_for_event(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    change: str,
    path_key: str | None = None,
    timeout: float | None = None,
) -> CapturedWebhookRequest:
    path_name = path_key or change
    path = notification_test_params["webhook_paths"][path_name]
    timeout = timeout or notification_test_params["delivery_timeout_sec"]
    camera_ids = _acceptable_camera_ids(context)
    try:
        return webhook_receiver.wait_for(
            predicate=lambda request: _event_matches(
                request, path, change, camera_ids
            ),
            start_sequence=context.receiver_cursor,
            timeout=timeout,
        )
    except TimeoutError as exc:
        captured = [
            request.summary()
            for request in webhook_receiver.requests_since(context.receiver_cursor)
        ]
        assert_with_detailed_failure(
            False,
            test_name=f"{path_name} webhook delivery",
            expected=(
                f"Webhook path={path!r}, camera_id in {camera_ids!r}, "
                f"change={change!r} within {timeout}s"
            ),
            actual=str(exc),
            additional_info=f"Captured requests: {captured}",
        )
        raise AssertionError("unreachable")


def _validate_event(
    request: CapturedWebhookRequest,
    context: WebhookTestContext,
    notification_test_params: Dict[str, Any],
    change: str,
    expected_method: str,
    path_key: str | None = None,
) -> None:
    path_name = path_key or change
    expected_path = notification_test_params["webhook_paths"][path_name]
    body = request.json_body if isinstance(request.json_body, dict) else {}
    event = body.get("event") if isinstance(body.get("event"), dict) else {}

    failures = []
    checks = [
        (request.method == expected_method, f"method={request.method!r}"),
        (request.path == expected_path, f"path={request.path!r}"),
        (request.query.get("change") == [change], f"query={request.query!r}"),
        (
            request.header("Content-Type") == "application/json",
            f"Content-Type={request.header('Content-Type')!r}",
        ),
        (
            request.header("streamId") == context.sensor_id,
            f"streamId={request.header('streamId')!r}",
        ),
        (body.get("alert_type") == "camera_status_change", f"body={body!r}"),
        (
            body.get("webhook_id") == notification_test_params["webhook_ids"][change],
            f"webhook_id={body.get('webhook_id')!r}",
        ),
        (body.get("source") == "vst", f"source={body.get('source')!r}"),
        (bool(body.get("created_at")), f"created_at={body.get('created_at')!r}"),
        (event.get("change") == change, f"event.change={event.get('change')!r}"),
        (
            event.get("camera_id") == context.sensor_id,
            f"event.camera_id={event.get('camera_id')!r}",
        ),
        (
            event.get("camera_name") == Path(context.filename).stem,
            f"event.camera_name={event.get('camera_name')!r}",
        ),
        (
            event.get("camera_type") == "file",
            f"event.camera_type={event.get('camera_type')!r}",
        ),
    ]
    if change == "camera_add":
        checks.append(
            (event.get("camera_url") == "", f"event.camera_url={event.get('camera_url')!r}")
        )
    if change == "camera_streaming":
        checks.append(
            (bool(event.get("camera_url")), f"event.camera_url={event.get('camera_url')!r}")
        )

    for passed, detail in checks:
        if not passed:
            failures.append({"description": detail})

    assert_with_detailed_failure(
        not failures,
        test_name=f"{change} webhook validation",
        expected=(
            f"{expected_method} {expected_path} with the complete file-sensor "
            f"{change} payload"
        ),
        actual=request.summary(),
        failed_items=failures,
    )


def _assert_event_not_received(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    change: str,
    path_key: str,
) -> None:
    path = notification_test_params["webhook_paths"][path_key]
    timeout = notification_test_params["filter_absence_timeout_sec"]
    try:
        request = webhook_receiver.wait_for(
            predicate=lambda captured: _event_matches(
                captured, path, change, _acceptable_camera_ids(context)
            ),
            start_sequence=context.receiver_cursor,
            timeout=timeout,
        )
    except TimeoutError:
        return

    captured = [
        item.summary()
        for item in webhook_receiver.requests_since(context.receiver_cursor)
    ]
    assert_with_detailed_failure(
        False,
        test_name=f"{path_key} webhook camera_type filter",
        expected=(
            f"No webhook at path={path!r} for file camera_id="
            f"{context.sensor_id!r} during {timeout}s"
        ),
        actual=request.summary(),
        additional_info=f"Captured requests: {captured}",
    )


@given("the webhook receiver is running")
def webhook_receiver_is_running(
    context: WebhookTestContext, webhook_receiver: WebhookReceiver
) -> None:
    context.receiver_cursor = webhook_receiver.next_sequence()


@given("the static webhook test video is available")
def static_video_is_available() -> None:
    assert STATIC_VIDEO.is_file(), f"Static test video not found: {STATIC_VIDEO}"
    assert STATIC_VIDEO.stat().st_size > 0, f"Static test video is empty: {STATIC_VIDEO}"


@when("I upload a uniquely named file sensor for webhook testing")
def upload_file_sensor(
    context: WebhookTestContext,
    api_config: Dict[str, Any],
    notification_test_params: Dict[str, Any],
) -> None:
    response = requests.put(
        f"{api_config['base_url']}/vst/api/v1/storage/file/{context.filename}",
        params={
            "sensorId": context.sensor_id,
            "timestamp": notification_test_params["upload_timestamp"],
        },
        data=STATIC_VIDEO.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
        timeout=notification_test_params["upload_timeout_sec"],
        verify=api_config.get("verify_ssl", False),
    )
    context.sensor_created = response.status_code in (200, 201)

    assert response.status_code in (200, 201), (
        f"File-sensor upload failed: HTTP {response.status_code}: {response.text[:500]}"
    )
    body = response.json()
    assert body.get("sensorId") == context.sensor_id, (
        f"Upload returned unexpected sensorId: {body!r}"
    )
    assert body.get("streamId") == context.sensor_id, (
        f"First file upload should use sensorId as streamId: {body!r}"
    )


@then("the camera_add webhook is received and valid")
def camera_add_webhook_is_valid(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    request = _wait_for_event(
        context, webhook_receiver, notification_test_params, "camera_add"
    )
    _validate_event(
        request, context, notification_test_params, "camera_add", "POST"
    )


@then("the unfiltered camera_add webhook is received and valid")
def unfiltered_camera_add_webhook_is_valid(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    path_key = "camera_add_unfiltered"
    request = _wait_for_event(
        context,
        webhook_receiver,
        notification_test_params,
        "camera_add",
        path_key=path_key,
    )
    _validate_event(
        request,
        context,
        notification_test_params,
        "camera_add",
        "POST",
        path_key=path_key,
    )


@then("the rtsp-only camera_add webhook is not received")
def rtsp_only_camera_add_webhook_is_not_received(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    _assert_event_not_received(
        context,
        webhook_receiver,
        notification_test_params,
        "camera_add",
        "camera_add_rtsp_only",
    )


@then("the camera_streaming webhook is received and valid")
def camera_streaming_webhook_is_valid(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    request = _wait_for_event(
        context, webhook_receiver, notification_test_params, "camera_streaming"
    )
    _validate_event(
        request, context, notification_test_params, "camera_streaming", "PUT"
    )


def _resolve_camera_ids(
    context: WebhookTestContext,
    api_config: Dict[str, Any],
    notification_test_params: Dict[str, Any],
) -> List[str]:
    """Return the sensor id plus every stream id VIOS created for the sensor.

    An RTSP sensor publishes camera_streaming under its stream id, which the
    add response does not return, so poll /sensor/<id>/streams until it is
    populated. Falls back to the sensor id alone so the webhook wait still runs
    and reports the real failure.
    """
    url = f"{api_config['base_url']}/vst/api/v1/sensor/{context.sensor_id}/streams"
    deadline = time.monotonic() + notification_test_params["rtsp_stream_resolve_timeout_sec"]
    camera_ids = [context.sensor_id]

    while time.monotonic() < deadline:
        try:
            response = requests.get(
                url,
                timeout=notification_test_params["api_timeout_sec"],
                verify=api_config.get("verify_ssl", False),
            )
        except requests.RequestException as exc:
            logger.warning("sensor/streams poll failed for %s: %s", context.sensor_id, exc)
            time.sleep(1)
            continue

        streams = response.json() if response.status_code == 200 else None
        if isinstance(streams, list) and streams:
            for stream in streams:
                stream_id = stream.get("streamId") if isinstance(stream, dict) else None
                if isinstance(stream_id, str) and stream_id and stream_id not in camera_ids:
                    camera_ids.append(stream_id)
            return camera_ids
        time.sleep(1)

    logger.warning(
        "No streams reported for sensor %s within %ss; matching on the sensor id alone",
        context.sensor_id,
        notification_test_params["rtsp_stream_resolve_timeout_sec"],
    )
    return camera_ids


def _captured_streaming_payload(
    context: WebhookTestContext,
) -> Tuple[CapturedWebhookRequest, Dict[str, Any], Dict[str, Any]]:
    """Return the camera_streaming request captured by the structure step."""
    request = context.streaming_event
    assert request is not None, (
        "The camera_streaming structure step must run before this step"
    )
    body = request.json_body if isinstance(request.json_body, dict) else {}
    event = body.get("event")
    return request, body, event if isinstance(event, dict) else {}


def _is_iso8601(value: str) -> bool:
    """Return True when the string parses as an ISO 8601 timestamp."""
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


@when("I add the configured RTSP sensor for webhook testing")
def add_rtsp_sensor(
    context: WebhookTestContext,
    api_config: Dict[str, Any],
    notification_test_params: Dict[str, Any],
    rtsp_sensor_url: str,
) -> None:
    if not rtsp_sensor_url:
        pytest.skip(
            "tests.notification_tests.test_parameters.rtsp_sensor is empty in config.json; "
            "set it to an RTSP URL to run the RTSP webhook test"
        )

    context.streaming_path_key = "camera_streaming_rtsp"
    context.streaming_timeout_sec = notification_test_params["rtsp_streaming_timeout_sec"]

    response = requests.post(
        f"{api_config['base_url']}/vst/api/v1/sensor/add",
        json={"sensorUrl": rtsp_sensor_url, "name": context.rtsp_sensor_name},
        timeout=notification_test_params["api_timeout_sec"],
        verify=api_config.get("verify_ssl", False),
    )
    assert response.status_code in (200, 201), (
        f"RTSP sensor/add failed: HTTP {response.status_code}: {response.text[:500]}"
    )

    body = response.json()
    sensor_id = body.get("sensorId") if isinstance(body, dict) else None
    assert isinstance(sensor_id, str) and sensor_id, (
        f"sensor/add did not return a sensorId: {body!r}"
    )
    context.sensor_id = sensor_id
    context.sensor_created = True
    context.expected_camera_ids = _resolve_camera_ids(
        context, api_config, notification_test_params
    )
    logger.info(
        "Added RTSP sensor %s (name=%s); camera ids %s",
        sensor_id,
        context.rtsp_sensor_name,
        context.expected_camera_ids,
    )


@then("the camera_streaming notification has the expected structure")
def camera_streaming_structure_is_valid(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    request = _wait_for_event(
        context,
        webhook_receiver,
        notification_test_params,
        "camera_streaming",
        path_key=context.streaming_path_key,
        timeout=context.streaming_timeout_sec,
    )
    context.streaming_event = request

    body = request.json_body
    failures = []
    if not isinstance(body, dict):
        failures.append({"description": f"payload is not a JSON object: {request.body[:200]!r}"})
        body = {}

    failures.extend(
        {"description": f"missing top-level key {key!r}"}
        for key in REQUIRED_PAYLOAD_KEYS
        if key not in body
    )

    event = body.get("event")
    if not isinstance(event, dict):
        failures.append({"description": f"event is not a JSON object: {event!r}"})
    elif not event:
        failures.append({"description": "event object is empty"})
    else:
        failures.extend(
            {"description": f"missing event key {key!r}"}
            for key in REQUIRED_EVENT_KEYS
            if key not in event
        )

    assert_with_detailed_failure(
        not failures,
        test_name="camera_streaming notification structure",
        expected=(
            f"top-level keys {list(REQUIRED_PAYLOAD_KEYS)} and a non-empty event "
            f"carrying {list(REQUIRED_EVENT_KEYS)}"
        ),
        actual=(
            f"{request.summary()} top-level={sorted(body)} "
            f"event={sorted(event) if isinstance(event, dict) else event!r}"
        ),
        failed_items=failures,
    )


@then("the camera_streaming notification values are valid")
def camera_streaming_values_are_valid(context: WebhookTestContext) -> None:
    request, body, event = _captured_streaming_payload(context)

    failures = []
    for key in ("alert_type", "created_at", "source"):
        value = body.get(key)
        if not isinstance(value, str):
            failures.append({"description": f"{key} is not a string: {value!r}"})
        elif not value.strip():
            failures.append({"description": f"{key} is empty"})

    for key in REQUIRED_EVENT_STRING_KEYS:
        value = event.get(key)
        if not isinstance(value, str):
            failures.append({"description": f"event.{key} is not a string: {value!r}"})
        elif not value.strip():
            failures.append({"description": f"event.{key} is empty"})

    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        failures.append({"description": f"event.metadata is not a JSON object: {metadata!r}"})

    created_at = body.get("created_at")
    if isinstance(created_at, str) and created_at.strip() and not _is_iso8601(created_at):
        failures.append({"description": f"created_at is not ISO 8601: {created_at!r}"})

    if body.get("alert_type") not in (None, "camera_status_change"):
        failures.append({"description": f"alert_type={body.get('alert_type')!r}"})
    if event.get("change") not in (None, "camera_streaming"):
        failures.append({"description": f"event.change={event.get('change')!r}"})
    if event.get("camera_id") not in [None, *_acceptable_camera_ids(context)]:
        failures.append(
            {
                "description": (
                    f"event.camera_id={event.get('camera_id')!r}, expected one of "
                    f"{_acceptable_camera_ids(context)!r}"
                )
            }
        )

    camera_type = event.get("camera_type")
    camera_url = event.get("camera_url")
    schemes = CAMERA_URL_SCHEMES.get(camera_type) if isinstance(camera_type, str) else None
    if schemes is None:
        failures.append(
            {
                "description": (
                    f"event.camera_type={camera_type!r} is not one of "
                    f"{sorted(CAMERA_URL_SCHEMES)}"
                )
            }
        )
    elif isinstance(camera_url, str) and not camera_url.startswith(schemes):
        failures.append(
            {
                "description": (
                    f"event.camera_url={camera_url!r} does not use {list(schemes)} "
                    f"required for camera_type={camera_type!r}"
                )
            }
        )

    assert_with_detailed_failure(
        not failures,
        test_name="camera_streaming notification values",
        expected=(
            "non-empty string fields, an ISO 8601 created_at, a metadata object, and a "
            "camera_url whose scheme matches camera_type (rtsp:// for rtsp, http:// for file)"
        ),
        actual=f"{request.summary()} body={body!r}",
        failed_items=failures,
    )


@then("the camera_streaming notification metadata is valid for the camera type")
def camera_streaming_metadata_is_valid(
    context: WebhookTestContext,
    notification_test_params: Dict[str, Any],
) -> None:
    request, _, event = _captured_streaming_payload(context)
    metadata = event.get("metadata")
    camera_type = event.get("camera_type")
    expected_start_time = notification_test_params["upload_timestamp"]

    failures = []
    if not isinstance(metadata, dict) or not metadata:
        failures.append({"description": f"event.metadata is empty or not an object: {metadata!r}"})
    elif camera_type == "file":
        file_start_time = metadata.get("file_start_time")
        if file_start_time is None:
            failures.append(
                {"description": f"metadata.file_start_time missing; metadata={metadata!r}"}
            )
        elif not isinstance(file_start_time, str):
            failures.append(
                {"description": f"metadata.file_start_time is not a string: {file_start_time!r}"}
            )
        elif file_start_time != expected_start_time:
            failures.append(
                {
                    "description": (
                        f"metadata.file_start_time={file_start_time!r}, "
                        f"expected {expected_start_time!r}"
                    )
                }
            )

    assert_with_detailed_failure(
        not failures,
        test_name="camera_streaming notification metadata",
        expected=(
            f"non-empty metadata carrying file_start_time={expected_start_time!r} "
            f"for a file sensor"
        ),
        actual=f"{request.summary()} camera_type={camera_type!r} metadata={metadata!r}",
        failed_items=failures,
    )


@when("I delete the uploaded webhook test sensor")
def delete_file_sensor(
    context: WebhookTestContext,
    api_config: Dict[str, Any],
    notification_test_params: Dict[str, Any],
) -> None:
    response = requests.delete(
        f"{api_config['base_url']}/vst/api/v1/sensor/{context.sensor_id}",
        timeout=notification_test_params["api_timeout_sec"],
        verify=api_config.get("verify_ssl", False),
    )
    context.sensor_deleted = response.status_code in (200, 204)
    assert response.status_code in (200, 204), (
        f"File-sensor delete failed: HTTP {response.status_code}: {response.text[:500]}"
    )


@then("the camera_remove webhook is received and valid")
def camera_remove_webhook_is_valid(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
) -> None:
    request = _wait_for_event(
        context, webhook_receiver, notification_test_params, "camera_remove"
    )
    _validate_event(
        request, context, notification_test_params, "camera_remove", "DELETE"
    )
