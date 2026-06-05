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

"""Hot-reload guarantees for ``output_category`` at the sink boundary.

The sink used to load ``output_category`` from ``alert_type_config.json``
once at startup — PUT /verification/config edits never reached the
published payload. These tests pin the new contract: when an
``AlertConfigStore`` is wired, every publish reads the latest value
from Redis; the file-loaded mapping is now only a fallback.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from mdx.anomaly.sink.vlm_enhanced_sink.sink_kafka import VLMEnhancedKafkaSink


class FakeAlertConfigStore:
    """Minimal stand-in for ``handlers.alert_config.AlertConfigStore``."""

    def __init__(self, initial: Optional[Dict[str, Dict[str, Any]]] = None):
        self._records: Dict[str, Dict[str, Any]] = dict(initial or {})

    def set(self, alert_type: str, data: Dict[str, Any]) -> None:
        self._records[alert_type] = data

    def get(self, alert_type: str) -> Optional[Dict[str, Any]]:
        return self._records.get(alert_type)


def _make_kafka_sink(
    alert_config_store: Any = None,
    category_mapping: Optional[Dict[str, str]] = None,
) -> VLMEnhancedKafkaSink:
    return VLMEnhancedKafkaSink(
        producer=MagicMock(),
        incident_route={"topic": "test", "key_field": None, "message_type": "incident"},
        alert_route={"topic": "test", "key_field": None, "message_type": "alert"},
        category_mapping=category_mapping,
        alert_config_store=alert_config_store,
    )


def test_store_value_wins_over_static_mapping():
    store = FakeAlertConfigStore(
        {"fov count violation": {"output_category": "Live Value"}}
    )
    sink = _make_kafka_sink(
        alert_config_store=store,
        category_mapping={"fov count violation": "File Value"},
    )
    assert sink._resolve_output_category("fov count violation") == "Live Value"


def test_explicit_none_in_store_clears_static_mapping():
    # PUT /verification/config with ``output_category: null`` lands as
    # ``{"output_category": None}`` in the store. The cleared override
    # MUST NOT silently resurrect from the file-loaded mapping.
    store = FakeAlertConfigStore(
        {"fov count violation": {"output_category": None}}
    )
    sink = _make_kafka_sink(
        alert_config_store=store,
        category_mapping={"fov count violation": "File Value"},
    )
    assert sink._resolve_output_category("fov count violation") is None


def test_static_used_when_record_lacks_output_category_key():
    # Distinguishes "key absent" (legacy / never set) from "key present
    # with None" (explicit clear above).
    store = FakeAlertConfigStore({"fov count violation": {"prompt": "x"}})
    sink = _make_kafka_sink(
        alert_config_store=store,
        category_mapping={"fov count violation": "File Value"},
    )
    assert sink._resolve_output_category("fov count violation") == "File Value"


def test_store_failure_falls_back_to_static_mapping():
    class FailingStore:
        def get(self, alert_type):
            raise RuntimeError("simulated redis outage")

    sink = _make_kafka_sink(
        alert_config_store=FailingStore(),
        category_mapping={"fov count violation": "File Value"},
    )
    # Must not raise — keep the publish path resilient.
    assert sink._resolve_output_category("fov count violation") == "File Value"


def test_publish_picks_up_store_edits_without_restart(monkeypatch):
    """End-to-end: edit the store between publishes; the second
    payload's ``category`` reflects the new value."""
    store = FakeAlertConfigStore(
        {"fov count violation": {"output_category": "First"}}
    )
    sink = _make_kafka_sink(alert_config_store=store)

    captured: List[Dict[str, Any]] = []

    def fake_convert(doc):
        captured.append(dict(doc))
        return MagicMock(SerializeToString=lambda: b"")

    monkeypatch.setattr(
        "mdx.anomaly.sink.vlm_enhanced_sink.sink_kafka."
        "convert_incident_to_protobuf_incident",
        fake_convert,
    )

    document = {
        "id": "evt-1",
        "Id": "fingerprint-1",
        "category": "fov count violation",
        "timestamp": "2026-04-28T00:00:00Z",
    }
    sink._produce("incident", dict(document))
    assert captured[-1]["category"] == "First"

    # Simulate PUT API edit landing in Redis
    store.set("fov count violation", {"output_category": "Updated"})

    sink._produce("incident", dict(document))
    assert captured[-1]["category"] == "Updated"


def test_factory_does_not_auto_derive_store_from_redis_handler(tmp_path):
    """``RedisHandler`` reads the top-level ``redis`` section while the
    verification API + ``PromptManager`` build their store from
    ``event_bridge.redis_source``. Auto-deriving from RedisHandler
    would silently target a different backend than the API in
    deployments that split those sections, so PUT edits would never
    reach the sink. The factory enforces explicit threading."""
    from mdx.anomaly.sink.vlm_enhanced_sink.factory import build_vlm_enhanced_sink
    import mdx.anomaly.sink.vlm_enhanced_sink.sink_kafka as sk

    config = {
        "vlm_enhanced_sink": {
            "type": "kafka",
            "incident": {"kafka": {"topic": "t-i"}},
            "alert": {"kafka": {"topic": "t-a"}},
        },
        "alert_type_config_file": str(tmp_path / "missing.json"),
        "kafka": {"bootstrap_servers": "localhost:9092"},
    }
    fake_handler = MagicMock(_redis_client=MagicMock())

    original_broker = sk.KafkaMessageBroker
    sk.KafkaMessageBroker = MagicMock(return_value=MagicMock(
        get_producer=MagicMock(return_value=MagicMock())
    ))
    try:
        sink = build_vlm_enhanced_sink(config, redis_handler=fake_handler)
    finally:
        sk.KafkaMessageBroker = original_broker

    assert sink._alert_config_store is None
