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

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from google.protobuf import json_format
from google.protobuf.json_format import MessageToDict
from mdx.anomaly.kafka_message_broker import KafkaMessageBroker
from mdx.anomaly.protobuf import Incident as nvSchemaIncident
from webhook.openclaw_notifier import OpenClawNotifier

logger = logging.getLogger(__name__)


def _decode_kafka_value(value: Any) -> Dict[str, Any]:
    """Decode a Kafka message value that may be Protobuf binary or JSON text.

    Both paths round-trip through the Protobuf schema so the downstream
    webhook payload uses camelCase keys matching the nvSchema convention
    (e.g. ``sensorId``, not ``sensor_id``).

    Raises :class:`ValueError` when the decoded message contains no
    non-default fields, which guards against garbage binary input that
    ``ParseFromString`` silently accepts.
    """
    raw_bytes = value if isinstance(value, bytes) else value.encode("utf-8")

    proto_msg = nvSchemaIncident()

    try:
        text = raw_bytes.decode("utf-8")
        json.loads(text)
        json_format.Parse(text, proto_msg)
    except (UnicodeDecodeError, json.JSONDecodeError):
        proto_msg.ParseFromString(raw_bytes)

    result = MessageToDict(proto_msg)
    if not result:
        raise ValueError("decoded message is empty (no non-default fields)")
    return result


class WebhookKafkaForwarder:
    """Poll a Kafka topic and forward every message to the OpenClaw webhook.
    """

    def __init__(self, config: Dict[str, Any], notifier: OpenClawNotifier) -> None:
        self._notifier = notifier
        self._consumer = None

        if not notifier.enabled or not notifier.topic:
            return

        webhook_cfg = (config.get("webhook") or {}).get("openclaw") or {}
        group_id = webhook_cfg.get("group_id", f"openclaw-webhook-{notifier.topic}")

        self._broker = KafkaMessageBroker(config)
        try:
            self._consumer = self._broker.get_consumer(notifier.topic, group_id)
        except Exception as exc:
            logger.error("Webhook forwarder failed to create Kafka consumer: %s", exc)
            return

        logger.info(
            "Webhook forwarder ready [topic=%s group=%s]",
            notifier.topic,
            group_id,
        )

    def poll_and_forward(self) -> None:
        """Poll the webhook topic and POST each message to the webhook.

        Safe to call on every loop iteration — returns immediately when
        the forwarder is disabled or there are no new messages.
        """
        if self._consumer is None:
            return

        try:
            topic_messages = self._broker.get_consumed_messages(self._consumer)
        except Exception as exc:
            logger.warning("Webhook forwarder poll error: %s", exc)
            return

        for _partition, msgs in topic_messages.items():
            for _key, value, *_rest in msgs:
                try:
                    incident = _decode_kafka_value(value)
                except Exception as exc:
                    logger.warning(
                        "Webhook forwarder: cannot decode message, skipping: %s",
                        exc,
                    )
                    continue

                self._notifier.notify(incident)

    def close(self) -> None:
        if self._consumer is not None:
            try:
                self._consumer.close()
            except Exception:
                pass
            self._consumer = None
            logger.info("Webhook forwarder closed")
