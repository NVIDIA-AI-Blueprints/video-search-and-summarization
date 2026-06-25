# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import os

from protos.nv_pb2 import LLM, Query, VisionLLM
from via_logger import logger

DEFAULT_STRUCTURED_SUMMARY_TOPIC = "mdx-structured-events-summary"
DOC_TYPE_STRUCTURED_EVENTS = "structured_events"
DOC_TYPE_AGGREGATED_SUMMARY = "aggregated_summary"


class ViaKafkaProducer:
    """Kafka producer for publishing structured/aggregated VLM summaries.

    In the streaming-only architecture, raw VLM events are published directly
    by RTVI-VLM (which owns the ``mdx-vlm-captions`` topic). This
    producer is responsible only for the ``mdx-structured-events-summary`` topic:
    event-batch documents and the aggregated narrative summary that via-engine
    builds after CA-RAG aggregation completes.
    """

    def __init__(self):
        """Initialize ViaKafkaProducer.

        Reads ``KAFKA_ENABLED``, ``KAFKA_BOOTSTRAP_SERVERS``, and
        ``KAFKA_STRUCTURED_SUMMARY_TOPIC`` from the environment. When enabled,
        creates a KafkaProducer with idempotent delivery (falling back to
        non-idempotent if unsupported). Disables itself gracefully on any
        initialization failure.
        """
        kafka_enabled_env = os.environ.get("KAFKA_ENABLED", "false").lower()
        self._enabled = kafka_enabled_env in ("true", "1")

        self._structured_summary_topic = os.environ.get(
            "KAFKA_STRUCTURED_SUMMARY_TOPIC", DEFAULT_STRUCTURED_SUMMARY_TOPIC
        )
        self._producer = None

        if not self._enabled:
            logger.info("Kafka producer is disabled (KAFKA_ENABLED=%s)", kafka_enabled_env)
            return

        bootstrap_servers = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "")
        if not bootstrap_servers:
            logger.warning(
                "KAFKA_ENABLED is true but KAFKA_BOOTSTRAP_SERVERS not set. Kafka disabled."
            )
            self._enabled = False
            return

        try:
            from kafka import KafkaProducer

            servers_list = [s.strip() for s in bootstrap_servers.split(",")]

            # value_serializer is identity (pass-through bytes) because messages
            # are serialized to Protobuf bytes before being handed to send().
            producer_config = {
                "bootstrap_servers": servers_list,
                "value_serializer": lambda v: v,
                "acks": "all",
                "retries": 3,
                "max_in_flight_requests_per_connection": 1,
                "request_timeout_ms": 60000,
                "metadata_max_age_ms": 300000,
                "connections_max_idle_ms": 540000,
            }

            try:
                producer_config["enable_idempotence"] = True
                self._producer = KafkaProducer(**producer_config)
            except Exception:
                logger.warning("Idempotent producer not supported, falling back to non-idempotent")
                producer_config.pop("enable_idempotence", None)
                self._producer = KafkaProducer(**producer_config)

            logger.info(
                "Kafka producer initialized: bootstrap_servers=%s, structured_summary_topic=%s",
                bootstrap_servers,
                self._structured_summary_topic,
            )
        except Exception as e:
            logger.error("Failed to initialize Kafka producer: %s", e)
            self._enabled = False

    @property
    def enabled(self):
        """Return True if the Kafka producer is active and ready to publish."""
        return self._enabled

    def _on_send_success(self, record_metadata):
        logger.debug(
            "Kafka message delivered: topic=%s partition=%d offset=%d",
            record_metadata.topic,
            record_metadata.partition,
            record_metadata.offset,
        )

    def _on_send_error(self, excp):
        logger.error("Kafka message delivery failed: %s", excp)

    def publish_structured_doc(
        self,
        text,
        metadata,
        collection_name,
        doc_type,
        batch_i=None,
    ):
        """Publish a structured document as a VisionLLM protobuf.

        Used for both event-batch documents (``doc_type=structured_events``,
        one per batch) and the final narrative aggregate
        (``doc_type=aggregated_summary``, one per video).

        Wire shape (matches RTVI-native VisionLLM exactly):
        - ``llm.queries[0].response`` carries ``text``
        - ``llm.queries[0].id`` carries ``str(batch_i)`` for event batches and
          the literal ``"summary"`` for the aggregated summary
        - ``info["collection_name"]``, ``info["doc_type"]`` and every flattened
          metadata key live in the protobuf ``info`` map. Logstash reverses
          the type coercion on ingest.
        """
        if not self._enabled or self._producer is None:
            return

        try:
            meta_copy = dict(metadata) if metadata else {}
            meta_copy["doc_type"] = doc_type
            meta_copy["collection_name"] = collection_name

            query_id = (
                "summary"
                if doc_type == DOC_TYPE_AGGREGATED_SUMMARY
                else str(batch_i if batch_i is not None else 0)
            )
            query_msg = Query(id=query_id, response=text)
            llm_msg = LLM(queries=[query_msg])
            message = VisionLLM(llm=llm_msg)
            for k, v in meta_copy.items():
                if isinstance(v, bool):
                    message.info[k] = "true" if v else "false"
                elif isinstance(v, list):
                    message.info[k] = str(v)
                elif v is None:
                    message.info[k] = ""
                else:
                    message.info[k] = str(v)

            uuid = meta_copy.get("uuid", "")
            key = f"{uuid}:{query_id}".encode("utf-8")
            future = self._producer.send(
                self._structured_summary_topic,
                key=key,
                value=message.SerializeToString(),
            )
            future.add_callback(self._on_send_success)
            future.add_errback(self._on_send_error)
        except Exception as e:
            logger.error("Error publishing structured doc to Kafka: %s", e)

    def flush(self, timeout=10):
        """Block until all buffered messages have been delivered."""
        if self._producer is not None:
            self._producer.flush(timeout=timeout)

    def close(self):
        """Flush pending messages and close the Kafka producer."""
        if self._producer is not None:
            try:
                self._producer.flush(timeout=10)
                self._producer.close(timeout=10)
                logger.info("Kafka producer closed")
            except Exception as e:
                logger.warning("Error closing Kafka producer: %s", e)
