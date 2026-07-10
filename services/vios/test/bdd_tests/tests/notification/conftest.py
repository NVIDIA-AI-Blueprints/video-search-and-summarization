# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for webhook notification BDD tests."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict

import pytest
import requests

from .webhook_test_utils import WebhookReceiver

logger = logging.getLogger(__name__)

TEST_PREFIX = "bdd-webhook-"


@dataclass
class WebhookTestContext:
    """State shared by the steps in one webhook scenario."""

    sensor_id: str
    filename: str
    receiver_cursor: int = 0
    sensor_created: bool = False
    sensor_deleted: bool = False


@pytest.fixture(scope="function")
def context() -> WebhookTestContext:
    """Create unique file-sensor identity for one scenario."""
    tag = uuid.uuid4().hex[:12]
    return WebhookTestContext(
        sensor_id=f"{TEST_PREFIX}sensor-{tag}",
        filename=f"{TEST_PREFIX}{tag}.mp4",
    )


@pytest.fixture(scope="session")
def notification_test_params(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return webhook notification parameters from the shared BDD config."""
    return config["tests"]["notification_tests"]["test_parameters"]


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
