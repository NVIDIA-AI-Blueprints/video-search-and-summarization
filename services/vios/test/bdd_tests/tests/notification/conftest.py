# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixtures and shared steps for webhook notification BDD tests."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import requests
from pytest_bdd import given, when

from .webhook_test_utils import CapturedWebhookRequest, WebhookReceiver

logger = logging.getLogger(__name__)

TEST_PREFIX = "bdd-webhook-"

STATIC_VIDEO = Path(__file__).resolve().parent.parent.parent / "data" / "test_video.mp4"


@dataclass
class WebhookTestContext:
    """State shared by the steps in one webhook scenario."""

    sensor_id: str
    filename: str
    rtsp_sensor_name: str
    receiver_cursor: int = 0
    sensor_created: bool = False
    sensor_deleted: bool = False
    streaming_event: Optional[CapturedWebhookRequest] = None
    # Which receiver the camera_streaming assertions read, and which camera_id
    # values are acceptable. RTSP sensors emit the event under a stream id that
    # the add response does not return, so the ids are resolved after the add.
    streaming_path_key: str = "camera_streaming"
    streaming_timeout_sec: Optional[float] = None
    expected_camera_ids: List[str] = field(default_factory=list)
    expected_camera_names: List[str] = field(default_factory=list)
    expected_camera_type: str = "file"


@pytest.fixture(scope="function")
def context() -> WebhookTestContext:
    """Create unique file-sensor identity for one scenario."""
    tag = uuid.uuid4().hex[:12]
    return WebhookTestContext(
        sensor_id=f"{TEST_PREFIX}sensor-{tag}",
        filename=f"{TEST_PREFIX}{tag}.mp4",
        rtsp_sensor_name=f"{TEST_PREFIX}rtsp-{tag}",
    )


@pytest.fixture(scope="session")
def notification_test_params(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return webhook notification parameters from the shared BDD config."""
    return config["tests"]["notification_tests"]["test_parameters"]


@pytest.fixture(scope="session")
def rtsp_sensor_url(notification_test_params: Dict[str, Any]) -> str:
    """Return the configured RTSP URL; empty means the RTSP scenario is skipped."""
    return str(notification_test_params.get("rtsp_sensor", "")).strip()


@pytest.fixture(scope="session", autouse=True)
def ensure_streams():
    """Override the global NVStreamer prerequisite; this test uploads its own file."""
    yield


@pytest.fixture(scope="session")
def webhook_receiver(
    notification_test_params: Dict[str, Any],
) -> WebhookReceiver:
    """Run the webhook receiver inside the pytest process."""
    receiver_config = notification_test_params["receiver"]
    receiver = WebhookReceiver(
        host=receiver_config["host"],
        port=int(receiver_config["port"]),
    )
    try:
        receiver.start()
    except OSError as exc:
        pytest.fail(
            f"Could not start webhook receiver at "
            f"{receiver.host}:{receiver.port}: {exc}"
        )
    logger.info("Webhook receiver listening at http://%s:%d", receiver.host, receiver.port)
    yield receiver
    receiver.stop()


# Steps shared by every webhook feature in this package.


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
    context.expected_camera_type = "file"
    context.expected_camera_names = [Path(context.filename).stem]

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


@when("I delete the uploaded webhook test sensor")
@when("I delete the added RTSP webhook test sensor")
def delete_webhook_test_sensor(
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


@pytest.fixture(scope="function", autouse=True)
def cleanup_webhook_test_sensor(
    context: WebhookTestContext,
    api_config: Dict[str, Any],
    notification_test_params: Dict[str, Any],
):
    """Delete only this scenario's sensor if the scenario did not delete it."""
    yield
    if context.sensor_created and not context.sensor_deleted:
        try:
            response = requests.delete(
                f"{api_config['base_url']}/vst/api/v1/sensor/{context.sensor_id}",
                timeout=notification_test_params["api_timeout_sec"],
                verify=api_config.get("verify_ssl", False),
            )
            if response.status_code not in (200, 204, 404):
                logger.warning(
                    "Webhook cleanup DELETE sensor %s returned %d: %s",
                    context.sensor_id,
                    response.status_code,
                    response.text[:300],
                )
        except Exception as exc:
            logger.warning(
                "Webhook scenario cleanup failed for sensor %s: %s",
                context.sensor_id,
                exc,
            )
