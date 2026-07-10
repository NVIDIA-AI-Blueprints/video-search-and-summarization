# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end webhook tests driven by the file-sensor lifecycle."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import pytest
import requests
from pytest_bdd import given, scenarios, then, when

from ..test_utils import assert_with_detailed_failure
from .conftest import WebhookTestContext
from .webhook_test_utils import CapturedWebhookRequest, WebhookReceiver

pytestmark = [pytest.mark.notification, pytest.mark.webhook]

scenarios("../../features/notification/webhook_notifications.feature")

STATIC_VIDEO = Path(__file__).resolve().parent.parent.parent / "data" / "test_video.mp4"


def _event_matches(
    request: CapturedWebhookRequest,
    path: str,
    change: str,
    sensor_id: str,
) -> bool:
    if request.path != path or not isinstance(request.json_body, dict):
        return False
    event = request.json_body.get("event")
    return (
        isinstance(event, dict)
        and event.get("change") == change
        and event.get("camera_id") == sensor_id
    )


def _wait_for_event(
    context: WebhookTestContext,
    webhook_receiver: WebhookReceiver,
    notification_test_params: Dict[str, Any],
    change: str,
) -> CapturedWebhookRequest:
    path = notification_test_params["webhook_paths"][change]
    timeout = notification_test_params["delivery_timeout_sec"]
    try:
        return webhook_receiver.wait_for(
            predicate=lambda request: _event_matches(
                request, path, change, context.sensor_id
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
            test_name=f"{change} webhook delivery",
            expected=(
                f"Webhook path={path!r}, camera_id={context.sensor_id!r}, "
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
) -> None:
    expected_path = notification_test_params["webhook_paths"][change]
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
