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

"""Resolution and validation of the pipeline process count."""

import os
import time
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_PROCESS_COUNT = 1

# The whole of startup -- reading topic metadata, seeding prompts, warming the
# VLM and waiting for every pipeline to join its group -- is bounded by one
# budget. Two independent timeouts were tried first and could not be reasoned
# about together: an instance could spend 300s on metadata and then 600s more
# waiting for children.
#
# Raise it where the default cannot be met: on Kubernetes the topics come from
# a Job with no ordering against this Deployment, and several children building
# Elasticsearch-backed stores at once take longer than one does. Compose gates
# on the topic-init container and never spends any of this.
DEFAULT_STARTUP_TIMEOUT_SECONDS = 60.0
# Mirrors MIN_FLEET_WAIT_SECONDS in the entry point: the share held back for
# the group join, and therefore the floor a whole budget has to clear.
MINIMUM_STARTUP_TIMEOUT_SECONDS = 15.0
PARTITION_POLL_SECONDS = 5.0


def startup_timeout(config: Optional[Dict[str, Any]]) -> float:
    """Seconds the whole of startup may take, from ``alert_agent``."""
    raw = (config or {}).get("alert_agent", {}).get("startup_timeout_seconds")
    if raw is None:
        return DEFAULT_STARTUP_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(
            f"alert_agent.startup_timeout_seconds must be a number, got {raw!r}"
        )
    if value < MINIMUM_STARTUP_TIMEOUT_SECONDS * 2:
        # Steps before the group join are each given what is left after the
        # fleet's reserved share. At or below that share they are given zero,
        # so the metadata read fails instantly and blames the broker for a
        # timeout it was never allowed to spend.
        raise ValueError(
            f"alert_agent.startup_timeout_seconds must be at least "
            f"{MINIMUM_STARTUP_TIMEOUT_SECONDS * 2:.0f}, got {raw!r}. The last "
            f"{MINIMUM_STARTUP_TIMEOUT_SECONDS:.0f}s are reserved for the "
            f"pipeline processes to join their consumer groups, and the steps "
            f"before them share what is left -- so anything under twice the "
            f"reservation gives the topic-metadata read close to nothing and "
            f"fails blaming the broker."
        )
    return value

# No upper bound. One was added from the spec's "1-64" wording and then
# removed: the partition count is what actually bounds an instance, and a
# ceiling with no product or resource basis behind it only refuses
# configurations the partitions would already have refused.
_ERROR = (
    "alert_agent.processes must be a positive integer, got {value!r}. "
    "Pick a count deliberately: it must not exceed the source partition "
    "count, and every process beyond it would idle."
)


def available_cpus() -> int:
    """CPU count the process may actually run on.

    ``sched_getaffinity`` respects cpuset restrictions, so a container pinned
    to 4 of 128 host cores reports 4 rather than 128. Advisory only: it sizes
    the guidance an operator gets, never the process count itself.
    """
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def source_topics(config: Optional[Dict[str, Any]]) -> List[str]:
    """Non-heartbeat Kafka source topics, empty when the source is not Kafka.

    Both lookups mirror the event-bridge factory and SourceKafka rather than
    only reading the modern spelling. A config that omits ``sourceType`` still
    gets a Kafka source, and one without ``kafka_source.topics`` still reads
    the legacy ``kafka.anomalyTopic``; reporting no topics for either would
    make the partition count unreadable and fail a valid multi-process
    configuration that this cannot check.
    """
    config = config or {}
    bridge = config.get("event_bridge", {}) or {}
    if str(bridge.get("sourceType", "kafka")).lower() != "kafka":
        return []

    topics = (bridge.get("kafka_source", {}) or {}).get("topics") or {}
    if topics:
        return [topic for name, topic in topics.items() if name != "heartbeat" and topic]

    legacy_topic = (config.get("kafka", {}) or {}).get("anomalyTopic")
    return [legacy_topic] if legacy_topic else []


def source_partitions_by_topic(
    config: Optional[Dict[str, Any]], timeout: float = 10.0
) -> Optional[Dict[str, int]]:
    """Partitions per source topic, or None while that is not yet knowable.

    Read through an admin client, which fetches metadata without joining the
    consumer group -- a member that joined and then stopped polling would
    stall the partitions assigned to it.
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

    sizes: Dict[str, int] = {}
    for topic in topics:
        topic_metadata = getattr(metadata, "topics", {}).get(topic)
        if topic_metadata is None or getattr(topic_metadata, "error", None) is not None:
            # Unknown, not zero. A topic missing from the metadata is usually
            # one that has not been created yet, and reporting the rest would
            # give the caller a number it would treat as authoritative.
            return None
        sizes[topic] = len(topic_metadata.partitions or ())
    return sizes or None


def source_partition_count(config: Optional[Dict[str, Any]], timeout: float = 10.0) -> Optional[int]:
    """Total partitions across the source topics, or None if unknown.

    Summed, not minimised: one group member can hold partitions from several
    subscribed topics, so the number of members that can receive work is the
    total. Taking the minimum let a low-traffic companion topic decide the
    answer - a one-partition ``mdx-alerts`` alongside an eight-partition
    ``mdx-incidents`` reported 1, which would reject a nine-process
    configuration that the partitions in fact support.
    """
    sizes = source_partitions_by_topic(config, timeout)
    if not sizes:
        return None
    return sum(sizes.values()) or None


def topics_short_of_processes(sizes: Dict[str, int], process_count: int) -> Dict[str, int]:
    """Topics that cannot give every process a partition.

    Each process runs one consumer per topic, all in the same group, so Kafka
    assigns each topic independently: a topic with fewer partitions than there
    are processes leaves the difference holding none of it, however large the
    total across topics. The total bounds the group; the per-topic count is
    what decides whether a process has work.
    """
    return {
        topic: partitions
        for topic, partitions in sorted(sizes.items())
        if partitions < process_count
    }


def resolve_process_count(config: Optional[Dict[str, Any]]) -> int:
    """Return the number of pipeline processes to run (>= 1).

    A positive integer only. Deriving it from the CPU count read well but hid
    the constraint that actually binds: parallelism is capped by the source
    partition count, and a derived value silently produced processes that
    could never receive one. An explicit count is checked against the
    partitions instead, so a wrong number fails startup rather than idling.
    """
    raw = (config or {}).get("alert_agent", {}).get("processes", DEFAULT_PROCESS_COUNT)

    if raw is None:
        return DEFAULT_PROCESS_COUNT

    if isinstance(raw, str):
        # Rendered configs substitute environment variables textually, so a
        # templated count arrives as a string. The value still has to be a
        # positive integer; only the spelling is relaxed.
        try:
            raw = int(raw.strip())
        except ValueError:
            raise ValueError(_ERROR.format(value=raw))

    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(_ERROR.format(value=raw))

    return raw


def await_source_partitions(
    config: Optional[Dict[str, Any]],
    required: int,
    timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
    interval: float = PARTITION_POLL_SECONDS,
) -> Dict[str, int]:
    """Block until the source topics exist, then return their partitions per topic.

    Per topic rather than summed, because that is the shape of the constraint:
    each topic is assigned independently, so a caller given only the total
    would report headroom that raising the process count cannot use.

    Raises ``RuntimeError`` if they never appear or carry fewer partitions
    than ``required``. Waiting rather than reading once is what lets the same
    check hold on both deployment paths: Compose starts this process only
    after the topic-init container has completed, so the first read is already
    authoritative, while on Kubernetes the topics arrive from a Job that races
    this one and an immediate read would fail a perfectly good install.
    """
    deadline = time.monotonic() + timeout
    warned = False
    while True:
        # Each metadata read is capped by what is left, or a single call could
        # overshoot the budget the whole of startup shares.
        remaining = max(0.0, deadline - time.monotonic())
        sizes = source_partitions_by_topic(config, timeout=min(10.0, remaining))
        total = sum(sizes.values()) if sizes else None
        if total:
            short = topics_short_of_processes(sizes, required)
            if short:
                detail = ", ".join(f"{t} has {n}" for t, n in short.items())
                raise RuntimeError(
                    f"alert_agent.processes={required} exceeds the partitions on "
                    f"{detail}. Each process runs one consumer per topic in the same "
                    f"group, so a topic is assigned independently of the others: "
                    f"{len(short)} topic(s) would leave processes holding none of "
                    f"them, whatever the {total} partitions total across topics. "
                    f"Lower processes to at most {min(short.values())}, or raise the "
                    f"partition count on the topics named; with N replicas the "
                    f"constraint is replicas x processes <= partitions, per topic."
                )
            return sizes

        if time.monotonic() >= deadline:
            if required <= 1:
                # One process needs no partition-count check -- a topic with
                # one partition already satisfies it. All this wait buys at
                # N=1 is failing fast when the topics are missing, and a
                # broker with auto-create, or one that is briefly unreachable,
                # used to recover on its own. Refusing there would break
                # installs that work today, so report and let it proceed.
                logger.warning(
                    "Could not read the partition count for %s within %.0fs; "
                    "continuing, since one pipeline process needs no "
                    "partition-count check",
                    ", ".join(source_topics(config)) or "the source topics",
                    timeout,
                )
                return {}
            raise RuntimeError(
                f"Could not read the partition count for "
                f"{', '.join(source_topics(config)) or 'the source topics'} within "
                f"{timeout:.0f}s. alert_agent.processes={required} cannot be validated "
                f"against a broker that is unreachable or topics that do not exist."
            )
        if not warned:
            logger.info(
                "Waiting for source topic metadata before starting %d pipeline processes",
                required,
            )
            warned = True
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
