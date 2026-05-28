# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for :mod:`config_listener`."""

import json
import logging
import os
import shutil
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import StreamMessage
from mdx.analytics.core.transform.config.config_listener import (
    ConfigListener,
    EVENT_TYPE_UPSERT,
    EVENT_TYPE_UPSERT_ALL,
    deserialize_config_message,
)
from mdx.analytics.core.transform.config.config_publisher import (
    EVENT_TYPE_ACK,
    EVENT_TYPE_REQUEST_CONFIG,
)


def _msg(
    *,
    key: bytes | None = b"behavior-analytics-config",
    event_type: bytes | None = b"upsert",
    reference_id: bytes | None = b"video-analytics-api-abc",
    body: dict | None = None,
    raw_body: bytes | None = None,
    timestamp_ms: int = 1700000000000,  # arbitrary fixed timestamp
) -> StreamMessage:
    """Convenience builder mirroring the on-the-wire shape ConfigPublisher emits."""
    headers: dict[str, bytes] = {}
    if event_type is not None:
        headers["event.type"] = event_type
    if reference_id is not None:
        headers["reference-id"] = reference_id
    if raw_body is not None:
        value = raw_body
    elif body is not None:
        value = json.dumps(body).encode("utf-8")
    else:
        value = json.dumps({"status": None, "config": None, "error": None}).encode("utf-8")
    return StreamMessage(key=key, value=value, headers=headers, timestamp=timestamp_ms)


class TestDeserializeConfigMessage(unittest.TestCase):
    """``deserialize_config_message`` is the gatekeeper for the dispatch loop."""

    def test_valid_upsert(self) -> None:
        sm = _msg(event_type=b"upsert", body={"status": None, "config": {"app": []}, "error": None})
        cm = deserialize_config_message(sm)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["event_type"], "upsert")
        self.assertEqual(cm["reference_id"], "video-analytics-api-abc")
        self.assertEqual(cm["config"], {"app": []})

    def test_wrong_key_filtered_silently(self) -> None:
        cm = deserialize_config_message(_msg(key=b"calibration"))
        self.assertIsNone(cm)

    def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(deserialize_config_message(_msg(key=None)))

    def test_missing_event_type_warned_and_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(_msg(event_type=None)))

    def test_missing_reference_id_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(_msg(reference_id=None)))

    def test_unknown_event_type_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(_msg(event_type=b"frobnicate")))

    def test_invalid_json_body_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(_msg(raw_body=b"not-json{")))

    def test_non_object_body_dropped(self) -> None:
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(_msg(raw_body=b"[1, 2, 3]")))

    def test_empty_value_dropped(self) -> None:
        sm = StreamMessage(
            key=b"behavior-analytics-config",
            value=None,
            headers={"event.type": b"upsert", "reference-id": b"x"},
        )
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(sm))

    def test_invalid_utf8_in_key_dropped(self) -> None:
        sm = StreamMessage(
            key=b"\xff\xfe-not-utf8",
            value=b"{}",
            headers={"event.type": b"upsert", "reference-id": b"x"},
        )
        self.assertIsNone(deserialize_config_message(sm))

    def test_invalid_utf8_in_header_dropped(self) -> None:
        sm = StreamMessage(
            key=b"behavior-analytics-config",
            value=b"{}",
            headers={"event.type": b"\xff\xfe", "reference-id": b"x"},
        )
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(sm))

    def test_all_known_event_types_accepted(self) -> None:
        for et in [EVENT_TYPE_UPSERT, EVENT_TYPE_UPSERT_ALL, EVENT_TYPE_ACK, EVENT_TYPE_REQUEST_CONFIG]:
            with self.subTest(event_type=et):
                cm = deserialize_config_message(_msg(event_type=et.encode("utf-8")))
                self.assertIsNotNone(cm)
                self.assertEqual(cm["event_type"], et)

    def test_kafka_record_timestamp_negative_falls_back(self) -> None:
        """Kafka timestamp == -1 (not set) -> fall back to wall clock."""
        cm = deserialize_config_message(_msg(timestamp_ms=-1))
        self.assertIsNotNone(cm)
        # Wall-clock fallback => recent timestamp (within a few seconds of now).
        self.assertLess(abs((datetime.now(timezone.utc) - cm["timestamp"]).total_seconds()), 5)

    def test_malformed_kafka_timestamp_dropped(self) -> None:
        """An out-of-range broker timestamp (treated like 1e30 sec from epoch)
        is logged and the message is dropped -- mirrors the calibration
        deserializer's ISO-8601 parse guard."""
        sm = _msg(timestamp_ms=10**18)  # ~31_700_000_000 years -- well past max year
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_config_message(sm))
        self.assertTrue(any("malformed timestamp" in line for line in ctx.output))

    def test_non_string_status_passes_through_deserializer(self) -> None:
        """Deserializer no longer constructs ``ConfigMessage``; a producer
        bug like ``"status": 42`` flows through as a raw dict so the
        validator can catch the shape violation and emit a structured
        ``failure`` ack back to the producer. See
        :func:`validate_envelope` tests for the catch + ack path.
        """
        sm = _msg(event_type=b"upsert", body={"status": 42, "config": None, "error": None})
        envelope = deserialize_config_message(sm)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope["status"], 42)

    def test_non_dict_config_passes_through_deserializer(self) -> None:
        """``config: Any`` -- non-dict values flow through; the validator
        returns ``failure`` downstream so web-api still gets an ack."""
        sm = _msg(event_type=b"upsert", body={"status": None, "config": [], "error": None})
        envelope = deserialize_config_message(sm)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope["config"], [])

    def test_body_extras_preserved_in_envelope(self) -> None:
        """Top-level keys the producer adds to the JSON body (typos,
        contract drift, new fields web-api may add later) must survive the
        deserializer so ``validate_envelope`` can surface them as a
        structured failure rather than silently swallowing them here."""
        sm = _msg(
            event_type=b"upsert",
            body={"status": None, "config": {"app": []}, "error": None, "version": "1.0"},
        )
        envelope = deserialize_config_message(sm)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope.get("version"), "1.0")
        # Envelope fields layered on top of the body still win.
        self.assertEqual(envelope["event_type"], "upsert")
        self.assertEqual(envelope["reference_id"], "video-analytics-api-abc")

    def test_missing_status_in_body_preserved_as_absent(self) -> None:
        """Producer omitted ``status`` from the body -- the deserializer
        does not synthesize one. ``validate_envelope`` then relies on
        :class:`ConfigMessage`'s ``status: str | None = None`` default."""
        sm = _msg(event_type=b"upsert", body={"config": {"app": []}, "error": None})
        envelope = deserialize_config_message(sm)
        self.assertIsNotNone(envelope)
        self.assertNotIn("status", envelope)
        self.assertEqual(envelope["config"], {"app": []})

    # ----- Reference-id acceptance filter -----------------------------------
    # Applied only to ``upsert-all`` and ``upsert`` event types. ``ack`` and
    # ``request-config`` skip the filter (see test_all_known_event_types_accepted).

    def test_upsert_all_accepts_behavior_analytics_prefix(self) -> None:
        """The bootstrap reply (web-api echoes our request-config reference-id)
        arrives as an ``upsert-all`` whose reference-id starts with
        ``behavior-analytics-``. Must be accepted."""
        sm = _msg(
            event_type=EVENT_TYPE_UPSERT_ALL.encode("utf-8"),
            reference_id=b"behavior-analytics-deadbeef",
        )
        cm = deserialize_config_message(sm)
        self.assertIsNotNone(cm)
        self.assertEqual(cm["event_type"], EVENT_TYPE_UPSERT_ALL)
        self.assertEqual(cm["reference_id"], "behavior-analytics-deadbeef")

    def test_upsert_all_rejects_other_prefix(self) -> None:
        sm = _msg(event_type=EVENT_TYPE_UPSERT_ALL.encode("utf-8"), reference_id=b"legacy-ref")
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_config_message(sm))
        self.assertTrue(any("unrecognized reference-id" in line for line in ctx.output))

    def test_upsert_rejects_other_prefix_when_source_type_none(self) -> None:
        """Without a source_type arg, only the video-analytics-api- prefix is accepted."""
        sm = _msg(reference_id=b"legacy-ref")
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_config_message(sm))
        self.assertTrue(any("unrecognized reference-id" in line for line in ctx.output))

    def test_upsert_kafka_source_type_accepts_kafka_literal_and_appends_epoch(self) -> None:
        ts_ms = 1700000000000
        sm = _msg(reference_id=b"kafka", timestamp_ms=ts_ms)
        cm = deserialize_config_message(sm, source_type="kafka")
        self.assertIsNotNone(cm)
        self.assertEqual(cm["reference_id"], f"kafka-{ts_ms}")

    def test_upsert_redis_stream_source_type_accepts_redis_literal(self) -> None:
        """``redisStream`` source maps to the ``redis`` reference-id literal."""
        ts_ms = 1710000000123
        sm = _msg(reference_id=b"redis", timestamp_ms=ts_ms)
        cm = deserialize_config_message(sm, source_type="redisStream")
        self.assertIsNotNone(cm)
        self.assertEqual(cm["reference_id"], f"redis-{ts_ms}")

    def test_upsert_mqtt_source_type_accepts_mqtt_literal(self) -> None:
        ts_ms = 1700000000999
        sm = _msg(reference_id=b"mqtt", timestamp_ms=ts_ms)
        cm = deserialize_config_message(sm, source_type="mqtt")
        self.assertIsNotNone(cm)
        self.assertEqual(cm["reference_id"], f"mqtt-{ts_ms}")

    def test_upsert_source_type_mismatch_dropped(self) -> None:
        """Active source is ``kafka`` but reference-id is ``redis`` -- drop."""
        sm = _msg(reference_id=b"redis")
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING) as ctx:
            self.assertIsNone(deserialize_config_message(sm, source_type="kafka"))
        self.assertTrue(any("unrecognized reference-id" in line for line in ctx.output))

    def test_upsert_video_analytics_api_prefix_ignores_source_type(self) -> None:
        """The web-api prefix always passes; source_type does not affect it
        and the reference-id is not rewritten."""
        sm = _msg(reference_id=b"video-analytics-api-abc")
        cm = deserialize_config_message(sm, source_type="kafka")
        self.assertIsNotNone(cm)
        self.assertEqual(cm["reference_id"], "video-analytics-api-abc")

    def test_upsert_all_source_type_does_not_open_source_literal_path(self) -> None:
        """``upsert-all`` has no direct-publisher path -- even with a matching
        source_type, the bare ``kafka`` reference-id is rejected."""
        sm = _msg(event_type=EVENT_TYPE_UPSERT_ALL.encode("utf-8"), reference_id=b"kafka")
        with self.assertLogs("mdx.analytics.core.transform.config.config_listener", level=logging.WARNING):
            self.assertIsNone(deserialize_config_message(sm, source_type="kafka"))


class _ListenerTestBase(unittest.TestCase):
    """Per-test temp dir for the listener's CONFIG_DIR + a real AppConfig + Mock applier/publisher."""

    def setUp(self) -> None:
        self.config = AppConfig()
        self.applier = Mock()
        self.publisher = Mock()
        self.bootstrap_ref = "behavior-analytics-deadbeef"
        self.config_dir = Path(f"/tmp/test_config_listener_{os.getpid()}_{id(self)}")
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)

        self._patcher = patch(
            "mdx.analytics.core.transform.config.config_listener.CONFIG_DIR",
            str(self.config_dir),
        )
        self._patcher.start()

        self.listener = ConfigListener(
            config=self.config,
            applier=self.applier,
            publisher=self.publisher,
            notification_consumer_fn=lambda: [],
            bootstrap_ref_id=self.bootstrap_ref,
        )

    def tearDown(self) -> None:
        self.listener.running = False
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)
        self._patcher.stop()

    def _cm(
        self,
        event_type: str,
        reference_id: str = "ref",
        config=None,
        status=None,
        err=None,
        timestamp: datetime | None = None,
    ) -> dict:
        """Build a wire-envelope dict (what :func:`deserialize_config_message`
        produces today) ready for :meth:`ConfigListener._dispatch`."""
        return {
            "event_type": event_type,
            "reference_id": reference_id,
            "timestamp": timestamp or datetime.now(timezone.utc),
            "config": config,
            "status": status,
            "error": err,
        }


class TestUpsertHandling(_ListenerTestBase):
    """Flow A: validate -> write -> apply -> ack."""

    def test_upsert_success_writes_file_applies_and_acks(self) -> None:
        msg = self._cm(
            "upsert",
            reference_id="ref-A",
            config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
        )
        self.listener._dispatch(msg)

        # apply called with the validated subset (kv format) and empty sensors.
        self.applier.apply.assert_called_once_with([{"name": "behaviorWatermarkSec", "value": "30"}], [])
        # Ack carries the inbound reference-id.
        self.publisher.publish_ack.assert_called_once()
        self.assertEqual(self.publisher.publish_ack.call_args[0][0], "ref-A")
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "success")
        self.assertEqual(ack.config, self.config.to_mutable_snapshot())
        # File on disk contains the filtered subset only (no envelope).
        files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("upsert-config-"))
        self.assertEqual(
            json.loads(files[0].read_text()),
            {"app": [{"name": "behaviorWatermarkSec", "value": "30"}], "sensors": []},
        )
        # Timestamp advanced.
        self.assertEqual(self.listener.last_insert_timestamp, msg["timestamp"])

    def test_upsert_with_null_config_acks_failure(self) -> None:
        """An upsert with ``config=null`` is a producer error, not a no-op:
        the envelope gate rejects it with "no config to update" and the
        listener acks ``failure`` so web-api gets a clear signal."""
        self.listener._dispatch(self._cm("upsert", config=None))
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_called_once()
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "failure")
        self.assertIn("no config to update", ack.error)
        self.assertEqual(list(self.config_dir.iterdir()), [])

    def test_upsert_with_extra_envelope_key_acks_failure(self) -> None:
        """The producer's body carried an unrecognized top-level key
        (e.g. typo, contract drift). Envelope gate rejects it and the
        listener acks ``failure`` with the offending key list."""
        env = self._cm("upsert", reference_id="ref-extra", config={"app": []})
        env["bogus"] = "x"
        self.listener._dispatch(env)
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_called_once()
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "failure")
        self.assertIn("unrecognized envelope keys", ack.error)
        self.assertIn("bogus", ack.error)
        self.assertEqual(list(self.config_dir.iterdir()), [])

    def test_upsert_validation_failure_acks_no_write_no_apply(self) -> None:
        """Non-dict config short-circuits at the validator."""
        self.listener._dispatch(self._cm("upsert", reference_id="ref-bad", config=[1, 2]))
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_called_once()
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "failure")
        self.assertIn("not a JSON object", ack.error)
        self.assertEqual(list(self.config_dir.iterdir()), [])

    def test_upsert_pure_forbidden_section_acks_failure(self) -> None:
        """A payload with ONLY forbidden sections (nothing salvageable) -> failure, no write."""
        self.listener._dispatch(self._cm("upsert", config={"kafka": {}, "app": []}))
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_called_once()
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "failure")
        self.assertIn("kafka", ack.error)
        self.assertEqual(list(self.config_dir.iterdir()), [])

    def test_upsert_non_allowlisted_app_key_acks_failure(self) -> None:
        """A name that's not in ALLOWED_APP_KEYS (e.g. restart-required ``numProcesses``)
        is rejected per-item; if it's the only item, ack is failure with the
        not-allowlisted reason."""
        self.listener._dispatch(
            self._cm("upsert", reference_id="ref-tier3", config={"app": [{"name": "numProcesses", "value": "8"}]})
        )
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_called_once()
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "failure")
        self.assertIn("numProcesses", ack.error)
        self.assertIn("not allowlisted", ack.error)
        self.assertEqual(list(self.config_dir.iterdir()), [])

    def test_upsert_allowlisted_alongside_non_allowlisted_is_partial_success(self) -> None:
        """Allowlisted + non-allowlisted -> partial-success; only the allowlisted item is persisted/applied."""
        msg = self._cm(
            "upsert",
            reference_id="ref-mixed-allow",
            config={"app": [
                {"name": "behaviorWatermarkSec", "value": "30"},
                {"name": "numProcesses", "value": "8"},
            ]},
        )
        self.listener._dispatch(msg)
        self.applier.apply.assert_called_once_with([{"name": "behaviorWatermarkSec", "value": "30"}], [])
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "partial-success")
        self.assertIn("not allowlisted", ack.error)
        files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(len(files), 1)
        self.assertEqual(
            json.loads(files[0].read_text()),
            {"app": [{"name": "behaviorWatermarkSec", "value": "30"}], "sensors": []},
        )

    def test_upsert_forbidden_with_valid_app_is_partial_success(self) -> None:
        """Forbidden section + valid app item -> partial-success, file holds only the valid app."""
        msg = self._cm(
            "upsert",
            reference_id="ref-mixed",
            config={"kafka": {"brokers": "x"}, "app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
        )
        self.listener._dispatch(msg)
        # Valid app item is applied; kafka is silently dropped from the file.
        self.applier.apply.assert_called_once_with([{"name": "behaviorWatermarkSec", "value": "30"}], [])
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "partial-success")
        self.assertIn("kafka", ack.error)
        self.assertIn("read-only", ack.error)
        # File contains only the applied subset, not the kafka section.
        files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(len(files), 1)
        body = json.loads(files[0].read_text())
        self.assertEqual(body, {"app": [{"name": "behaviorWatermarkSec", "value": "30"}], "sensors": []})
        self.assertNotIn("kafka", body)

    def test_upsert_write_failure_acks_failure_no_apply(self) -> None:
        """Validator passes but disk write fails -> ack failure, applier never called."""
        with patch(
            "mdx.analytics.core.transform.config.config_listener.os.rename",
            side_effect=OSError("disk full"),
        ):
            self.listener._dispatch(
                self._cm("upsert", config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}]})
            )
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_called_once()
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "failure")
        self.assertIn("failed to persist", ack.error)
        # No final file on disk.
        json_files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(json_files, [])
        # Timestamp NOT advanced.
        self.assertNotEqual(self.listener.last_insert_timestamp.year, datetime.now(timezone.utc).year)

    def test_upsert_partial_success_writes_filtered_and_acks(self) -> None:
        """Mixed valid/invalid items -> partial-success, file holds only the valid ones."""
        msg = self._cm(
            "upsert",
            reference_id="ref-P",
            config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}, {"name": "", "value": "x"}]},
        )
        self.listener._dispatch(msg)
        # apply called with only the valid item.
        self.applier.apply.assert_called_once_with([{"name": "behaviorWatermarkSec", "value": "30"}], [])
        ack = self.publisher.publish_ack.call_args[0][1]
        self.assertEqual(ack.status, "partial-success")
        self.assertIn("app[1]", ack.error)
        files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(len(files), 1)
        self.assertEqual(
            json.loads(files[0].read_text()),
            {"app": [{"name": "behaviorWatermarkSec", "value": "30"}], "sensors": []},
        )

    def test_old_timestamp_skipped(self) -> None:
        future_ts = datetime(2099, 1, 1, tzinfo=timezone.utc)
        self.listener.last_insert_timestamp = future_ts
        msg = self._cm(
            "upsert",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            config={"app": []},
        )
        self.listener._dispatch(msg)
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_not_called()
        self.assertEqual(list(self.config_dir.iterdir()), [])


class TestUpsertAllHandling(_ListenerTestBase):
    """Flow B: bootstrap reply matching ref-id -> validate subset -> write -> apply, no ack."""

    def test_upsert_all_matching_ref_applies_writes_no_ack(self) -> None:
        self.listener._dispatch(
            self._cm(
                "upsert-all",
                reference_id=self.bootstrap_ref,
                # Full config from the video analytics api includes read-only sections; listener extracts app+sensors.
                config={
                    "app": [{"name": "behaviorWatermarkSec", "value": "30"}],
                    "sensors": [{"id": "cam1", "configs": [{"name": "tripwireMinPoints", "value": "5"}]}],
                    "kafka": {"brokers": "stale-from-webapi"},
                    "redisStream": {},
                },
                status="success",
            )
        )
        self.applier.apply.assert_called_once_with(
            [{"name": "behaviorWatermarkSec", "value": "30"}],
            [{"id": "cam1", "configs": [{"name": "tripwireMinPoints", "value": "5"}]}],
        )
        # Bootstrap is silent on the wire.
        self.publisher.publish_ack.assert_not_called()
        files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].name.startswith("upsert-all-config-"))
        # File body excludes the read-only sections that the video analytics api sent.
        body = json.loads(files[0].read_text())
        self.assertNotIn("kafka", body)
        self.assertNotIn("redisStream", body)
        self.assertEqual(body["app"], [{"name": "behaviorWatermarkSec", "value": "30"}])
        self.assertTrue(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_shape_failure_silent_no_ack(self) -> None:
        """Upsert-all is ack-less by contract -- even on a shape failure
        (unrecognized envelope key here), the listener logs a warning and
        drops the message rather than emitting an ack."""
        env = self._cm("upsert-all", reference_id=self.bootstrap_ref, config={"app": []})
        env["bogus"] = "x"
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_listener", level=logging.WARNING,
        ) as ctx:
            self.listener._dispatch(env)
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_not_called()
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assertTrue(any("dropping upsert-all" in line for line in ctx.output))

    def test_upsert_all_non_matching_ref_ignored(self) -> None:
        self.listener._dispatch(
            self._cm("upsert-all", reference_id="someone-else", config={"app": []})
        )
        self.applier.apply.assert_not_called()
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assertFalse(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_failure_status_unblocks_no_apply(self) -> None:
        """status='failure' (DB empty): unblock start() but don't apply or write."""
        self.listener._dispatch(
            self._cm(
                "upsert-all",
                reference_id=self.bootstrap_ref,
                status="failure",
                err="no config in DB",
                config=None,
            )
        )
        self.applier.apply.assert_not_called()
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assertTrue(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_invalid_app_items_logged_no_apply(self) -> None:
        """Bootstrap reply with malformed app items -> log error, no apply, no write."""
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_listener", level=logging.ERROR
        ) as ctx:
            self.listener._dispatch(
                self._cm(
                    "upsert-all",
                    reference_id=self.bootstrap_ref,
                    config={"app": [{"name": "", "value": "x"}]},
                )
            )
        self.applier.apply.assert_not_called()
        self.assertTrue(any("rejected" in line for line in ctx.output))
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assertTrue(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_partial_success_applies_logs_warning(self) -> None:
        """Bootstrap reply with mix of valid + invalid: keep the valid, log the rejections."""
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_listener", level=logging.WARNING
        ) as ctx:
            self.listener._dispatch(
                self._cm(
                    "upsert-all",
                    reference_id=self.bootstrap_ref,
                    config={
                        "app": [{"name": "behaviorWatermarkSec", "value": "30"}, {"name": "", "value": "x"}],
                    },
                )
            )
        self.applier.apply.assert_called_once_with([{"name": "behaviorWatermarkSec", "value": "30"}], [])
        self.assertTrue(any("per-item rejections" in line for line in ctx.output))
        self.assertTrue(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_empty_subset_logs_info_unblocks(self) -> None:
        """Bootstrap reply with no applicable items is a legitimate success
        no-op: log info, no apply, no write, unblock."""
        with self.assertLogs(
            "mdx.analytics.core.transform.config.config_listener", level=logging.INFO
        ) as ctx:
            self.listener._dispatch(
                self._cm("upsert-all", reference_id=self.bootstrap_ref, config={"app": [], "sensors": []})
            )
        self.applier.apply.assert_not_called()
        self.assertEqual(list(self.config_dir.iterdir()), [])
        self.assertTrue(any("no applicable items" in line for line in ctx.output))
        self.assertTrue(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_old_timestamp_skipped(self) -> None:
        future_ts = datetime(2099, 1, 1, tzinfo=timezone.utc)
        self.listener.last_insert_timestamp = future_ts
        msg = self._cm(
            "upsert-all",
            reference_id=self.bootstrap_ref,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
        )
        self.listener._dispatch(msg)
        self.applier.apply.assert_not_called()
        self.assertEqual(list(self.config_dir.iterdir()), [])
        # bootstrap unblocks regardless of skip cause.
        self.assertTrue(self.listener.bootstrap_done_event.is_set())

    def test_upsert_all_write_failure_captures_for_start_to_raise(self) -> None:
        """Write failure on bootstrap path: error log, no apply, still
        unblocks ``bootstrap_done_event`` (so ``start()`` wakes up), and
        captures the exception in ``bootstrap_disk_failure`` so
        ``start()`` can raise. Disk-side failures are pod-specific --
        unlike network failure which all replicas hit consistently --
        so this replica must abort to avoid running on stale config
        while peers run on the latest."""
        boom = OSError("disk full")
        with patch(
            "mdx.analytics.core.transform.config.config_listener.os.rename",
            side_effect=boom,
        ):
            with self.assertLogs(
                "mdx.analytics.core.transform.config.config_listener", level=logging.ERROR
            ) as ctx:
                self.listener._dispatch(
                    self._cm(
                        "upsert-all",
                        reference_id=self.bootstrap_ref,
                        config={"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
                    )
                )
        self.applier.apply.assert_not_called()
        self.assertTrue(any("failed to persist bootstrap" in line for line in ctx.output))
        json_files = [f for f in self.config_dir.iterdir() if f.name.endswith(".json")]
        self.assertEqual(json_files, [])
        self.assertTrue(self.listener.bootstrap_done_event.is_set())
        # Exception captured for start() to surface as a fatal startup error.
        self.assertIs(self.listener.bootstrap_disk_failure, boom)


class TestIgnoredEventTypes(_ListenerTestBase):
    """Inbound ack and request-config are own/peer traffic; never trigger work."""

    def test_ack_message_ignored(self) -> None:
        self.listener._dispatch(self._cm("ack"))
        self.applier.apply.assert_not_called()
        self.publisher.publish_ack.assert_not_called()

    def test_request_config_message_ignored(self) -> None:
        self.listener._dispatch(self._cm("request-config"))
        self.applier.apply.assert_not_called()

    def test_none_message_silently_skipped(self) -> None:
        self.listener._dispatch(None)
        self.applier.apply.assert_not_called()


class TestPruner(_ListenerTestBase):
    """Pruner thread deletes files older than CONFIG_RETAIN_SECONDS."""

    def _touch(self, name: str, age_seconds: float) -> Path:
        p = self.config_dir / name
        p.write_text("{}")
        past = time.time() - age_seconds
        os.utime(p, (past, past))
        return p

    def test_prunes_old_files_only(self) -> None:
        from mdx.analytics.core.transform.config.config_listener import CONFIG_RETAIN_SECONDS
        old = self._touch("old.json", age_seconds=CONFIG_RETAIN_SECONDS + 60)
        recent = self._touch("recent.json", age_seconds=10)
        self.listener._prune_old_files()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_pruner_loop_runs_prune_pass(self) -> None:
        with patch(
            "mdx.analytics.core.transform.config.config_listener.CONFIG_PRUNE_INTERVAL_SECONDS", 1,
        ):
            with patch.object(self.listener, "_prune_old_files") as mock_prune:
                self.listener.start()
                time.sleep(1.5)
                self.listener.close()
        mock_prune.assert_called()

    def test_empty_dir_is_noop(self) -> None:
        self.listener._prune_old_files()  # no-op, no raise

    def test_directories_are_skipped(self) -> None:
        from mdx.analytics.core.transform.config.config_listener import CONFIG_RETAIN_SECONDS
        subdir = self.config_dir / "subdir"
        subdir.mkdir()
        old_file = self._touch("old.json", age_seconds=CONFIG_RETAIN_SECONDS + 60)

        self.listener._prune_old_files()

        self.assertTrue(subdir.exists())
        self.assertFalse(old_file.exists())

    def test_unlink_oserror_logged_and_continues(self) -> None:
        from mdx.analytics.core.transform.config.config_listener import CONFIG_RETAIN_SECONDS
        bad = self._touch("bad.json", age_seconds=CONFIG_RETAIN_SECONDS + 60)
        good = self._touch("good.json", age_seconds=CONFIG_RETAIN_SECONDS + 60)

        original_unlink = Path.unlink

        def selective_unlink(self, *args, **kwargs):
            if self.name == "bad.json":
                raise OSError("simulated permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", selective_unlink):
            with self.assertLogs(
                "mdx.analytics.core.transform.config.config_listener", level=logging.WARNING
            ) as ctx:
                self.listener._prune_old_files()
        self.assertTrue(any("failed to prune" in line for line in ctx.output))
        self.assertFalse(good.exists())


class TestAtomicWrite(_ListenerTestBase):
    """Atomic-write helper: tmp file is cleaned up on rename failure."""

    def test_atomic_write_cleans_tmp_on_rename_failure(self) -> None:
        target = self.config_dir / "dest.json"
        with patch(
            "mdx.analytics.core.transform.config.config_listener.os.rename",
            side_effect=OSError("simulated"),
        ):
            with self.assertRaises(OSError):
                self.listener._atomic_write(target, "{}")
        # No .tmp left behind.
        leftovers = [p for p in self.config_dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])
        # No final dest either.
        self.assertFalse(target.exists())


class TestConfigListenerLifecycle(unittest.TestCase):
    """Threaded lifecycle: ``start()`` publishes request-config and waits."""

    def setUp(self) -> None:
        self.config = AppConfig()
        self.applier = Mock()
        self.publisher = Mock()
        self.bootstrap_ref = "behavior-analytics-cafef00d"
        self.config_dir = Path(f"/tmp/test_config_listener_lc_{os.getpid()}_{id(self)}")
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)
        self._patcher = patch(
            "mdx.analytics.core.transform.config.config_listener.CONFIG_DIR",
            str(self.config_dir),
        )
        self._patcher.start()

    def tearDown(self) -> None:
        if self.config_dir.exists():
            shutil.rmtree(self.config_dir)
        self._patcher.stop()

    def _make_listener(self, consume, **kwargs) -> ConfigListener:
        return ConfigListener(
            config=self.config,
            applier=self.applier,
            publisher=self.publisher,
            notification_consumer_fn=consume,
            bootstrap_ref_id=self.bootstrap_ref,
            **kwargs,
        )

    def test_start_publishes_request_config_and_returns_after_reply(self) -> None:
        delivered = threading.Event()

        def consume() -> list:
            if not delivered.is_set():
                delivered.set()
                return [
                    {
                        "event_type": "upsert-all",
                        "reference_id": self.bootstrap_ref,
                        "timestamp": datetime.now(timezone.utc),
                        "config": {"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
                        "status": "success",
                        "error": None,
                    }
                ]
            time.sleep(0.01)
            return []

        listener = self._make_listener(consume, bootstrap_timeout=2.0)
        try:
            t0 = time.time()
            listener.start()
            elapsed = time.time() - t0
            self.assertLess(elapsed, 1.0)
            self.publisher.publish_request_config.assert_called_once_with(self.bootstrap_ref)
            self.applier.apply.assert_called_once()
        finally:
            listener.close()

    def test_start_raises_when_bootstrap_disk_write_fails(self) -> None:
        """If the bootstrap reply arrives but the file write fails on
        this pod (disk full, perm lost, mount gone), ``start()`` must
        raise so the orchestrator restarts the pod. Continuing on the
        disk baseline while peer replicas applied the latest config
        would put this replica uniquely behind the deployment."""
        def consume() -> list:
            return [
                {
                    "event_type": "upsert-all",
                    "reference_id": self.bootstrap_ref,
                    "timestamp": datetime.now(timezone.utc),
                    "config": {"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
                    "status": "success",
                    "error": None,
                },
            ]

        listener = self._make_listener(consume, bootstrap_timeout=2.0)
        try:
            with patch(
                "mdx.analytics.core.transform.config.config_listener.os.rename",
                side_effect=OSError("disk full"),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    listener.start()
            self.assertIn("bootstrap config write failed", str(ctx.exception))
            # The original OSError is preserved as __cause__.
            self.assertIsInstance(ctx.exception.__cause__, OSError)
        finally:
            listener.close()

    def test_start_returns_after_timeout_when_no_reply(self) -> None:
        listener = self._make_listener(lambda: [], bootstrap_timeout=0.2)
        try:
            with self.assertLogs(
                "mdx.analytics.core.transform.config.config_listener", level=logging.WARNING
            ) as ctx:
                t0 = time.time()
                listener.start()
                elapsed = time.time() - t0
            self.assertGreaterEqual(elapsed, 0.2)
            self.assertLess(elapsed, 1.0)
            self.assertTrue(any("timed out" in line for line in ctx.output))
        finally:
            listener.close()

    def test_start_is_idempotent(self) -> None:
        listener = self._make_listener(lambda: [], bootstrap_timeout=0.1)
        try:
            listener.start()
            listener.start()  # no-op
        finally:
            listener.close()
        self.assertEqual(self.publisher.publish_request_config.call_count, 1)

    def test_close_stops_loop_promptly(self) -> None:
        listener = self._make_listener(
            lambda: (time.sleep(0.05) or []), bootstrap_timeout=0.05,
        )
        listener.start()
        listener.close()
        self.assertFalse(listener.consumer_thread.is_alive())

    def test_close_without_start_is_safe(self) -> None:
        listener = self._make_listener(lambda: [])
        listener.close()  # should not raise
        self.assertFalse(listener.running)

    def test_dispatch_exception_is_swallowed_and_loop_continues(self) -> None:
        delivered = {"once": False}
        self.applier.apply.side_effect = RuntimeError("apply boom")

        def consume() -> list:
            if not delivered["once"]:
                delivered["once"] = True
                return [
                    {
                        "event_type": "upsert",
                        "reference_id": "r",
                        "timestamp": datetime.now(timezone.utc),
                        "config": {"app": [{"name": "behaviorWatermarkSec", "value": "30"}]},
                        "status": None,
                        "error": None,
                    }
                ]
            time.sleep(0.01)
            return []

        listener = self._make_listener(consume, bootstrap_timeout=0.1)
        try:
            with self.assertLogs(
                "mdx.analytics.core.transform.config.config_listener", level=logging.ERROR
            ) as ctx:
                listener.start()
                # The dispatch happens in the consumer thread; on a slow CI
                # runner a fixed ``time.sleep`` may end before the thread gets
                # to dispatch + log, which previously caused ``assertLogs``
                # to raise "no logs of level ERROR or higher" even though the
                # log fired a few ms later. Poll for the expected log
                # instead, with a generous wall-clock deadline.
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if any("dispatch failed" in line for line in ctx.output):
                        break
                    time.sleep(0.01)
            self.assertTrue(any("dispatch failed" in line for line in ctx.output))
            self.assertTrue(listener.consumer_thread.is_alive())
        finally:
            listener.close()

    def test_publish_request_config_happens_after_consumer_polls(self) -> None:
        """Locks in the consumer_ready_event ordering: consumer's first poll
        returns BEFORE bootstrap publish.

        Without this gate, a fast video-analytics-api round-trip could land
        the upsert-all reply before the Kafka consumer finishes partition
        assignment + seek-to-latest, silently timing out the bootstrap.
        :meth:`ConfigListener.start` waits on ``consumer_ready_event``
        (set by ``_consume_loop`` after the first ``source.poll`` returns)
        before calling ``publish_request_config``. We assert
        ``notification_consumer_fn`` was invoked at least once before
        the publisher's ``publish_request_config``.
        """
        consumer_polled = threading.Event()

        def consume() -> list:
            consumer_polled.set()
            time.sleep(0.01)
            return []

        observed_polled_at_publish: list[bool] = []

        def record_publish(_ref_id: str) -> None:
            observed_polled_at_publish.append(consumer_polled.is_set())

        self.publisher.publish_request_config.side_effect = record_publish
        listener = self._make_listener(consume, bootstrap_timeout=0.2)
        try:
            listener.start()
        finally:
            listener.close()

        self.assertEqual(len(observed_polled_at_publish), 1)
        self.assertTrue(
            observed_polled_at_publish[0],
            "publish_request_config fired before the consumer thread had polled",
        )

    def test_start_warns_and_proceeds_if_consumer_never_ready(self) -> None:
        """If the consumer thread can't reach its first poll (e.g. broker
        permanently unreachable), :meth:`start` should log a warning and
        publish ``request-config`` anyway -- bootstrap_timeout will then
        surface the failure."""
        # consume() blocks indefinitely so consumer_ready_event is never set.
        block = threading.Event()

        def consume() -> list:
            block.wait()  # blocks until tearDown / close releases it
            return []

        listener = self._make_listener(consume, bootstrap_timeout=0.05)
        try:
            with patch(
                "mdx.analytics.core.transform.config.config_listener.READY_TIMEOUT", 0.05,
            ):
                with self.assertLogs(
                    "mdx.analytics.core.transform.config.config_listener", level=logging.WARNING
                ) as ctx:
                    listener.start()
            # Warning fired and publish still happened.
            self.assertTrue(any("consumer not ready" in line for line in ctx.output))
            self.publisher.publish_request_config.assert_called_once_with(self.bootstrap_ref)
        finally:
            block.set()  # unblock consume() so the thread can exit
            listener.close()

    def test_consume_loop_swallows_consumer_exceptions(self) -> None:
        attempts = {"n": 0}

        def flaky() -> list:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("kafka hiccup")
            return []

        listener = self._make_listener(flaky, bootstrap_timeout=0.1)
        try:
            with self.assertLogs(
                "mdx.analytics.core.transform.config.config_listener", level=logging.ERROR
            ):
                listener.start()
                time.sleep(0.1)
            self.assertGreaterEqual(attempts["n"], 2)
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
