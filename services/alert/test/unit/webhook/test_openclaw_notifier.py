# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for ``webhook.openclaw_notifier``.

The notifier is fire-and-forget: the enhancer loop calls :meth:`notify` and
must never block on a slow OpenClaw endpoint. These tests pin down the
config validation performed at construction, the bounded-queue backpressure
that protects process memory, and the guarantee that the semaphore is always
released no matter how delivery ends.

Delivery is made deterministic by patching ``requests.post`` and draining the
thread pool via :meth:`close`, which shuts down with ``wait=True``.
"""

from unittest.mock import MagicMock, patch

import pytest

from webhook.openclaw_notifier import OpenClawNotifier


def make_config(**openclaw):
    """Build an Alert Bridge config dict with a ``webhook.openclaw`` section."""
    return {"webhook": {"openclaw": openclaw}}


ENABLED_CONFIG = make_config(enabled=True, url="https://openclaw.test/hook")


@pytest.fixture
def mock_post():
    """Patch ``requests.post`` inside the notifier module."""
    with patch("webhook.openclaw_notifier.requests.post") as post:
        post.return_value = MagicMock(ok=True, status_code=200)
        yield post


class TestConstruction:
    """Config parsing and validation in ``__init__``."""

    def test_disabled_by_default(self):
        notifier = OpenClawNotifier({})
        assert notifier.enabled is False
        assert notifier.topic == ""
        assert notifier._pool is None

    def test_webhook_key_present_but_null(self):
        """``webhook: ~`` in YAML yields None and must not blow up."""
        notifier = OpenClawNotifier({"webhook": None})
        assert notifier.enabled is False

    def test_openclaw_key_present_but_null(self):
        notifier = OpenClawNotifier({"webhook": {"openclaw": None}})
        assert notifier.enabled is False

    def test_disabled_skips_url_validation(self):
        """A bad URL is tolerated while disabled — nothing will be sent."""
        notifier = OpenClawNotifier(make_config(enabled=False, url="not-a-url"))
        assert notifier.enabled is False
        assert notifier._pool is None

    def test_enabled_builds_pool_and_semaphore(self):
        notifier = OpenClawNotifier(ENABLED_CONFIG)
        try:
            assert notifier.enabled is True
            assert notifier._pool is not None
            assert notifier._backpressure is not None
        finally:
            notifier.close()

    def test_enabled_exposes_topic(self):
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="https://openclaw.test/hook", topic="incidents")
        )
        try:
            assert notifier.topic == "incidents"
        finally:
            notifier.close()

    def test_default_timeout_is_five_seconds(self):
        notifier = OpenClawNotifier(ENABLED_CONFIG)
        try:
            assert notifier._timeout == 5
        finally:
            notifier.close()

    def test_custom_timeout_is_coerced_to_int(self):
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="https://openclaw.test/hook", timeout_seconds="12")
        )
        try:
            assert notifier._timeout == 12
        finally:
            notifier.close()

    def test_none_url_becomes_empty_string(self):
        """``url: ~`` must not become the literal string "None"."""
        notifier = OpenClawNotifier(make_config(enabled=False, url=None))
        assert notifier._url == ""

    @pytest.mark.parametrize(
        "url",
        ["", "ftp://openclaw.test/hook", "openclaw.test/hook", "ws://openclaw.test"],
    )
    def test_enabled_rejects_unsupported_scheme(self, url):
        with pytest.raises(ValueError, match="unsupported scheme"):
            OpenClawNotifier(make_config(enabled=True, url=url))

    def test_enabled_rejects_url_without_host(self):
        with pytest.raises(ValueError, match="no host"):
            OpenClawNotifier(make_config(enabled=True, url="http:///hook"))

    @pytest.mark.parametrize("url", ["http://openclaw.test/hook", "https://openclaw.test/hook"])
    def test_enabled_accepts_http_and_https(self, url):
        notifier = OpenClawNotifier(make_config(enabled=True, url=url))
        try:
            assert notifier.enabled is True
        finally:
            notifier.close()

    def test_url_with_port_is_accepted(self):
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="http://openclaw.test:9000/hook")
        )
        try:
            assert notifier.enabled is True
        finally:
            notifier.close()


class TestNotify:
    """Submission path, including backpressure."""

    def test_notify_is_noop_when_disabled(self, mock_post):
        notifier = OpenClawNotifier({})
        notifier.notify({"sensorId": "cam-1"})
        mock_post.assert_not_called()

    def test_notify_delivers_incident(self, mock_post):
        notifier = OpenClawNotifier(ENABLED_CONFIG)
        incident = {"sensorId": "cam-1", "category": "intrusion"}
        notifier.notify(incident)
        notifier.close()  # waits for the in-flight delivery

        mock_post.assert_called_once_with(
            "https://openclaw.test/hook", json=incident, timeout=5
        )

    def test_notify_delivers_each_incident(self, mock_post):
        notifier = OpenClawNotifier(ENABLED_CONFIG)
        for i in range(5):
            notifier.notify({"sensorId": f"cam-{i}"})
        notifier.close()

        assert mock_post.call_count == 5

    def test_backpressure_drops_incident_when_queue_full(self, mock_post):
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="https://openclaw.test/hook", max_pending=1)
        )
        try:
            # Occupy the single permit so the next notify() cannot acquire one.
            assert notifier._backpressure.acquire(blocking=False) is True
            notifier.notify({"sensorId": "cam-1"})
            notifier.close()

            mock_post.assert_not_called()
        finally:
            notifier.close()

    def test_semaphore_released_when_pool_rejects_submission(self, mock_post):
        """A shut-down pool raises RuntimeError; the permit must come back."""
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="https://openclaw.test/hook", max_pending=2)
        )
        try:
            notifier._pool.submit = MagicMock(side_effect=RuntimeError("pool is shut down"))
            notifier.notify({"sensorId": "cam-1"})

            # Both permits are still available, so two more notifies fit.
            assert notifier._backpressure.acquire(blocking=False) is True
            assert notifier._backpressure.acquire(blocking=False) is True
        finally:
            notifier.close()

    def test_permit_is_returned_after_successful_delivery(self, mock_post):
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="https://openclaw.test/hook", max_pending=1)
        )
        notifier.notify({"sensorId": "cam-1"})
        notifier.close()

        assert notifier._backpressure.acquire(blocking=False) is True


class TestSafeDeliver:
    """``_safe_deliver`` must release the permit even when delivery explodes."""

    def test_releases_permit_on_exception(self):
        notifier = OpenClawNotifier(
            make_config(enabled=True, url="https://openclaw.test/hook", max_pending=1)
        )
        try:
            notifier._backpressure.acquire()
            notifier._deliver = MagicMock(side_effect=RuntimeError("boom"))

            with pytest.raises(RuntimeError, match="boom"):
                notifier._safe_deliver({"sensorId": "cam-1"})

            assert notifier._backpressure.acquire(blocking=False) is True
        finally:
            notifier.close()


class TestDeliver:
    """``_deliver`` swallows every transport failure — it runs in a worker thread."""

    def _notifier(self):
        return OpenClawNotifier(make_config(enabled=False, url="https://openclaw.test/hook"))

    def test_posts_to_configured_url_with_timeout(self, mock_post):
        notifier = OpenClawNotifier(
            make_config(
                enabled=False, url="https://openclaw.test/hook", timeout_seconds=30
            )
        )
        incident = {"sensorId": "cam-1", "category": "fire"}
        notifier._deliver(incident)

        mock_post.assert_called_once_with(
            "https://openclaw.test/hook", json=incident, timeout=30
        )

    def test_non_ok_response_does_not_raise(self, mock_post):
        mock_post.return_value = MagicMock(ok=False, status_code=503)
        self._notifier()._deliver({"sensorId": "cam-1"})

    def test_transport_error_does_not_raise(self, mock_post):
        mock_post.side_effect = ConnectionError("connection refused")
        self._notifier()._deliver({"sensorId": "cam-1"})

    def test_incident_without_sensor_or_category_does_not_raise(self, mock_post):
        """Log formatting falls back to "N/A" rather than KeyError."""
        self._notifier()._deliver({})
        mock_post.assert_called_once()


class TestClose:
    def test_close_is_idempotent(self):
        notifier = OpenClawNotifier(ENABLED_CONFIG)
        notifier.close()
        notifier.close()
        assert notifier._pool is None

    def test_close_on_disabled_notifier_is_noop(self):
        notifier = OpenClawNotifier({})
        notifier.close()
        assert notifier._pool is None
