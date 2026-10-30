#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or its
# affiliates is strictly prohibited.

"""Create the VSS Kafka topics declared by the KAFKA_TOPICS JSON env var."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    print("+ " + " ".join(command), flush=True)
    return subprocess.run(
        command,
        check=False,
        capture_output=capture,
        text=True,
    )


def _load_topics() -> list[dict[str, str]]:
    topics_json = os.environ.get("KAFKA_TOPICS")
    if not topics_json:
        print("KAFKA_TOPICS is required and must be a JSON list of topic specs.", file=sys.stderr)
        sys.exit(1)

    try:
        topics = json.loads(topics_json)
    except json.JSONDecodeError as exc:
        print(f"KAFKA_TOPICS is not valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    if not isinstance(topics, list):
        print("KAFKA_TOPICS must be a JSON list.", file=sys.stderr)
        sys.exit(1)

    for topic in topics:
        if not isinstance(topic, dict) or not topic.get("name"):
            print(f"Invalid Kafka topic spec: {topic!r}", file=sys.stderr)
            sys.exit(1)

    return topics


def _wait_for_kafka(bootstrap: str) -> None:
    max_attempts = int(_env("KAFKA_TOPIC_BOOTSTRAP_MAX_ATTEMPTS", "0"))
    interval_seconds = int(_env("KAFKA_TOPIC_BOOTSTRAP_RETRY_INTERVAL_SECONDS", "5"))

    print(f"Waiting for Kafka at {bootstrap}...", flush=True)
    attempt = 1
    while max_attempts <= 0 or attempt <= max_attempts:
        result = _run(
            ["kafka-broker-api-versions", "--bootstrap-server", bootstrap],
            capture=True,
        )
        if result.returncode == 0:
            print("Kafka is reachable.", flush=True)
            return

        limit = "unlimited" if max_attempts <= 0 else str(max_attempts)
        print(
            f"Kafka is not ready yet ({attempt}/{limit}); retrying in {interval_seconds}s.",
            flush=True,
        )
        time.sleep(interval_seconds)
        attempt += 1

    print(f"Kafka did not become reachable at {bootstrap}.", file=sys.stderr)
    sys.exit(1)


def _topic_policy(topic: dict[str, str]) -> dict[str, str]:
    return {
        "name": str(topic["name"]),
        "partitions": str(topic.get("partitions") or _env("DEFAULT_PARTITIONS", "8")),
        "replication_factor": str(
            topic.get("replication_factor") or _env("DEFAULT_REPLICATION_FACTOR", "1")
        ),
        "retention_ms": str(topic.get("retention_ms") or _env("DEFAULT_RETENTION_MS", "14400000")),
        "segment_ms": str(topic.get("segment_ms") or _env("DEFAULT_SEGMENT_MS", "3600000")),
    }


def _create_topic(bootstrap: str, topic: dict[str, str]) -> None:
    policy = _topic_policy(topic)

    result = _run(
        [
            "kafka-topics",
            "--bootstrap-server",
            bootstrap,
            "--create",
            "--if-not-exists",
            "--topic",
            policy["name"],
            "--partitions",
            policy["partitions"],
            "--replication-factor",
            policy["replication_factor"],
            "--config",
            f"retention.ms={policy['retention_ms']}",
            "--config",
            f"segment.ms={policy['segment_ms']}",
        ],
        capture=True,
    )

    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)
    if result.returncode != 0:
        print(f"Failed to create Kafka topic {policy['name']!r}.", file=sys.stderr)
        sys.exit(result.returncode)


def _parse_topic_description(output: str, topic_name: str) -> dict[str, str]:
    topic_line = next((line for line in output.splitlines() if f"Topic: {topic_name}" in line), "")
    if not topic_line:
        return {}

    parsed: dict[str, str] = {}
    for key, output_key in (
        ("partitions", "PartitionCount"),
        ("replication_factor", "ReplicationFactor"),
    ):
        match = re.search(rf"{output_key}:\s*([^\t ]+)", topic_line)
        if match:
            parsed[key] = match.group(1)

    configs_match = re.search(r"Configs:\s*([^\t]+)", topic_line)
    if configs_match:
        for item in configs_match.group(1).split(","):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key == "retention.ms":
                parsed["retention_ms"] = value
            elif key == "segment.ms":
                parsed["segment_ms"] = value

    return parsed


def _verify_topic_policy(bootstrap: str, topic: dict[str, str]) -> None:
    expected = _topic_policy(topic)
    name = expected["name"]
    result = _run(
        ["kafka-topics", "--bootstrap-server", bootstrap, "--describe", "--topic", name],
        capture=True,
    )
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        print(f"Failed to describe Kafka topic {name!r}.", file=sys.stderr)
        sys.exit(result.returncode)

    actual = _parse_topic_description(result.stdout, name)
    mismatches = {
        key: {"expected": value, "actual": actual.get(key)}
        for key, value in expected.items()
        if key != "name" and actual.get(key) != value
    }
    if mismatches:
        print(
            f"Kafka topic {name!r} exists but does not match declared policy: {mismatches}. "
            "Delete or update the existing Kafka data/topic before rerunning bootstrap.",
            file=sys.stderr,
        )
        sys.exit(1)


def _verify_topics(bootstrap: str, expected_topic_specs: list[dict[str, str]]) -> None:
    result = _run(["kafka-topics", "--bootstrap-server", bootstrap, "--list"], capture=True)
    if result.returncode != 0:
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        print("Failed to list Kafka topics after bootstrap.", file=sys.stderr)
        sys.exit(result.returncode)

    expected_topics = [str(topic["name"]) for topic in expected_topic_specs]
    existing_topics = {line.strip() for line in result.stdout.splitlines() if line.strip()}
    missing_topics = sorted(set(expected_topics) - existing_topics)
    if missing_topics:
        print(f"Kafka topic bootstrap did not create required topics: {missing_topics}", file=sys.stderr)
        sys.exit(1)

    for topic in expected_topic_specs:
        _verify_topic_policy(bootstrap, topic)

    print("Kafka topic bootstrap complete. Topics:")
    for topic in sorted(existing_topics):
        print(f"  {topic}")


def main() -> None:
    bootstrap = os.environ.get("KAFKA_BOOTSTRAP_SERVERS")
    if not bootstrap:
        bootstrap = f"{_env('BOOTSTRAP_HOST', 'kafka')}:{_env('KAFKA_PORT', '9092')}"

    topics = _load_topics()
    _wait_for_kafka(bootstrap)

    print(f"Creating {len(topics)} Kafka topics...")
    for topic in topics:
        _create_topic(bootstrap, topic)

    _verify_topics(bootstrap, topics)


if __name__ == "__main__":
    main()
