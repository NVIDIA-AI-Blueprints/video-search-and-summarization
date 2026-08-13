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

"""Resolution of the pipeline process count from ``alert_agent.processes``."""

import os
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

PROCESSES_AUTO = "auto"
DEFAULT_PROCESS_COUNT = 1

_ERROR = (
    "alert_agent.processes must be a positive integer or {auto!r}, got {value!r}"
)


def available_cpus() -> int:
    """CPU count the process may actually run on.

    ``sched_getaffinity`` respects cpuset restrictions, so a container pinned
    to 4 of 128 host cores resolves ``auto`` to 4 rather than 128.
    """
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def source_topics(config: Optional[Dict[str, Any]]) -> List[str]:
    """Non-heartbeat Kafka source topics, empty when the source is not Kafka."""
    bridge = (config or {}).get("event_bridge", {}) or {}
    if str(bridge.get("sourceType", "")).lower() != "kafka":
        return []
    topics = (bridge.get("kafka_source", {}) or {}).get("topics") or {}
    return [topic for name, topic in topics.items() if name != "heartbeat" and topic]


def source_partition_count(config: Optional[Dict[str, Any]], timeout: float = 10.0) -> Optional[int]:
    """Total partitions across the source topics, or None if unknown.

    Summed, not minimised: one group member can hold partitions from several
    subscribed topics, so the number of members that can receive work is the
    total. Taking the minimum let a low-traffic companion topic decide the
    answer - a one-partition ``mdx-alerts`` alongside an eight-partition
    ``mdx-incidents`` reported 1, which warned wrongly and, worse, clamped
    ``processes: "auto"`` to a single process.

    Read through an admin client, which fetches metadata without joining the
    consumer group — a member that joined and then stopped polling would stall
    the partitions assigned to it. Best effort: an unreachable broker or a
    missing topic returns None and startup continues.
    """
    topics = source_topics(config)
    if not topics:
        return None

    bootstrap = ((config or {}).get("kafka", {}) or {}).get("bootstrap_servers")
    if not bootstrap:
        return None

    try:
        from confluent_kafka.admin import AdminClient

        metadata = AdminClient({"bootstrap.servers": bootstrap}).list_topics(timeout=timeout)
    except Exception:
        logger.debug("Could not read Kafka topic metadata for partition sizing", exc_info=True)
        return None

    total = 0
    for topic in topics:
        topic_metadata = getattr(metadata, "topics", {}).get(topic)
        if topic_metadata is None or getattr(topic_metadata, "error", None) is not None:
            continue
        total += len(topic_metadata.partitions or ())
    return total or None


def resolve_process_count(
    config: Optional[Dict[str, Any]],
    partition_count: Optional[int] = None,
) -> int:
    """Return the number of pipeline processes to run (>= 1).

    ``partition_count`` only bounds ``"auto"``. Effective parallelism is
    ``min(processes, partitions)``, so on a 256-core host with 8 partitions
    ``auto`` would otherwise start 248 processes that consume memory and never
    receive a partition. An explicit integer is an operator instruction and is
    left alone; the caller warns instead.
    """
    raw = (config or {}).get("alert_agent", {}).get("processes", DEFAULT_PROCESS_COUNT)

    if raw is None:
        return DEFAULT_PROCESS_COUNT

    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized == PROCESSES_AUTO:
            count = max(1, available_cpus())
            if partition_count and partition_count > 0:
                count = min(count, partition_count)
            return count
        try:
            raw = int(normalized)
        except ValueError:
            raise ValueError(_ERROR.format(auto=PROCESSES_AUTO, value=raw))

    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(_ERROR.format(auto=PROCESSES_AUTO, value=raw))

    return raw
