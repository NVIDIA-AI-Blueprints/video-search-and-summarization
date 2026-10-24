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

"""Wait until Kafka is reachable and the required topics exist."""

from __future__ import annotations

import os
import subprocess
import sys
import time


def _env(name: str, default: str) -> str:
    return os.environ.get(name) or default


def _load_required_topics() -> list[str]:
    topics = [
        topic.strip()
        for topic in _env("KAFKA_REQUIRED_TOPICS", "").split(",")
        if topic.strip()
    ]
    if not topics:
        print("KAFKA_REQUIRED_TOPICS must list at least one Kafka topic.", file=sys.stderr)
        sys.exit(1)
    return topics


def _list_topics(bootstrap: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["kafka-topics", "--bootstrap-server", bootstrap, "--list"],
        capture_output=True,
        text=True,
        check=False,
    )


def _deadline(timeout_seconds: int) -> float | None:
    return None if timeout_seconds <= 0 else time.monotonic() + timeout_seconds


def _timed_out(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _timeout_label(timeout_seconds: int) -> str:
    return "unlimited" if timeout_seconds <= 0 else f"{timeout_seconds}s"


def _wait_for_kafka(
    bootstrap: str,
    timeout_seconds: int,
    interval_seconds: int,
) -> set[str]:
    deadline = _deadline(timeout_seconds)

    print(f"Waiting for Kafka at {bootstrap}...", flush=True)
    attempt = 1
    while True:
        result = _list_topics(bootstrap)
        if result.returncode == 0:
            print("Kafka is reachable.", flush=True)
            return {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
            }

        if result.stderr:
            print(result.stderr, end="", file=sys.stderr, flush=True)
        print("Kafka is not reachable yet.", flush=True)

        if _timed_out(deadline):
            print(f"Timed out waiting for Kafka at {bootstrap}.", file=sys.stderr)
            sys.exit(1)

        print(
            f"Retrying in {interval_seconds}s "
            f"(attempt {attempt}, timeout {_timeout_label(timeout_seconds)})...",
            flush=True,
        )
        time.sleep(interval_seconds)
        attempt += 1


def _wait_for_topic(
    bootstrap: str,
    topic: str,
    timeout_seconds: int,
    interval_seconds: int,
    existing_topics: set[str],
) -> set[str]:
    deadline = _deadline(timeout_seconds)

    print(f"Waiting for Kafka topic {topic!r} at {bootstrap}...", flush=True)
    attempt = 1
    while True:
        if topic in existing_topics:
            print(f"Kafka topic {topic!r} is ready.", flush=True)
            return existing_topics

        result = _list_topics(bootstrap)
        if result.returncode == 0:
            existing_topics = {
                line.strip()
                for line in result.stdout.splitlines()
                if line.strip()
            }
            if topic in existing_topics:
                print(f"Kafka topic {topic!r} is ready.", flush=True)
                return existing_topics
            print(f"Kafka is reachable but topic {topic!r} is missing.", flush=True)
        else:
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr, flush=True)
            print("Kafka is not reachable yet.", flush=True)

        if _timed_out(deadline):
            print(
                f"Timed out waiting for Kafka topic {topic!r} at {bootstrap}.",
                file=sys.stderr,
            )
            sys.exit(1)

        print(
            f"Retrying in {interval_seconds}s "
            f"(attempt {attempt}, timeout {_timeout_label(timeout_seconds)})...",
            flush=True,
        )
        time.sleep(interval_seconds)
        attempt += 1


def main() -> None:
    bootstrap = _env("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")
    topics = _load_required_topics()
    timeout_seconds = int(_env("KAFKA_TOPIC_WAIT_TIMEOUT_SECONDS", "1200"))
    interval_seconds = int(_env("KAFKA_TOPIC_WAIT_INTERVAL_SECONDS", "2"))

    existing_topics = _wait_for_kafka(bootstrap, timeout_seconds, interval_seconds)
    for topic in topics:
        existing_topics = _wait_for_topic(
            bootstrap,
            topic,
            timeout_seconds,
            interval_seconds,
            existing_topics,
        )
    print("Required Kafka topics are ready.", flush=True)


if __name__ == "__main__":
    main()
