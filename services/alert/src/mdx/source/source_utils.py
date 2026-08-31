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

"""Helpers shared by every event bridge source.

The record-key alignment guardrail lives here rather than in one transport's
module because dedup determinism is a property of the payload contract, not of
Kafka or Redis: both transports carry the same MDX envelope, and both need the
same answer to "did the producer key this record by sensorId?".
"""

from __future__ import annotations

import json
from typing import Any

# Guarded so a minimal environment without the metrics package cannot break the
# consume path.
try:  # pragma: no cover - exercised indirectly
    from metrics import recorder as _metrics
except Exception:  # pragma: no cover
    _metrics = None


def classify_key_alignment(key: Any, value: Any) -> str:
    """Classify whether an envelope ``key`` aligns with the payload sensorId.

    Dedup determinism relies on the producer keying records by ``sensorId`` so
    a cohort always lands on one consumer (the partition-key contract).
    Returns ``"yes"`` when the key matches the payload's sensorId, ``"no"``
    when it clearly does not, and ``"unknown"`` when it cannot be determined
    (missing key, non-JSON/protobuf payload). Best-effort and fully defensive —
    never raises.
    """
    try:
        if key is None:
            return "unknown"
        key_str = key.decode("utf-8") if isinstance(key, (bytes, bytearray)) else str(key)
        key_str = key_str.strip()
        if not key_str:
            return "unknown"
        if isinstance(value, (bytes, bytearray)):
            try:
                value = value.decode("utf-8")
            except Exception:
                return "unknown"
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                return "unknown"
        if not isinstance(value, dict):
            return "unknown"
        # Incident payloads carry top-level ``sensorId``; alert/anomaly
        # payloads carry the id nested under ``sensor.id`` (mirrors the
        # anomaly cohort-key builder). Support both so the guardrail covers
        # both cohort classes (the partition-key contract).
        sensor_id = value.get("sensorId")
        if not sensor_id:
            sensor = value.get("sensor")
            if isinstance(sensor, dict):
                sensor_id = sensor.get("id")
        if not sensor_id:
            return "unknown"
        # Producer keys records by sensorId; every cohort key is a superset
        # beginning with sensorId, so alignment holds when the record key is
        # (or begins with) the payload sensorId.
        return "yes" if key_str == str(sensor_id) or key_str.startswith(str(sensor_id)) else "no"
    except Exception:
        return "unknown"


def record_key_alignment(key: Any, value: Any) -> None:
    """Report the alignment verdict to the metrics recorder, if one is present."""
    if _metrics is None:
        return
    try:
        _metrics.inc_record_key_alignment(classify_key_alignment(key, value))
    except Exception:  # pragma: no cover - metrics must never break consume
        pass


def record_source_drop(transport: str, reason: str) -> None:
    """Report one entry discarded on the read path.

    The sources are at-most-once, so an undecodable entry is acked and dropped
    rather than replayed forever. That keeps a poison pill from wedging the
    consumer, but it also means a producer emitting garbage degrades the
    pipeline with nothing but log lines to show it — hence the counter.
    """
    if _metrics is None:
        return
    try:
        _metrics.inc_source_dropped(transport, reason)
    except Exception:  # pragma: no cover - metrics must never break consume
        pass
