#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Explain whether a failed SonarQube scan looks like NVIDIA infrastructure."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

NETWORK_FAILURES = {
    6: "DNS resolution failed",
    7: "connection failed or no route to host",
    28: "connection timed out",
    35: "TLS negotiation failed",
    52: "the server returned no response",
    56: "the network connection was interrupted",
}


def probe(
    host_url: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    endpoint = f"{host_url.rstrip('/')}/api/v2/analysis/version"
    return runner(
        [
            "curl",
            "--silent",
            "--show-error",
            "--connect-timeout",
            "5",
            "--max-time",
            "15",
            "--output",
            "/dev/null",
            endpoint,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def classify(result: subprocess.CompletedProcess[str]) -> tuple[str, str]:
    reason = NETWORK_FAILURES.get(result.returncode)
    if reason is not None:
        return (
            "error",
            "Likely transient NVIDIA SonarQube infrastructure error: "
            f"{reason} (curl exit {result.returncode}). This is not a repository "
            "code finding. Keep the SonarQube gate red and retry after NVIDIA "
            "network/service connectivity is restored.",
        )
    if result.returncode == 0:
        return (
            "notice",
            "The SonarQube endpoint is currently reachable. Inspect the scanner "
            "output or quality-gate result for a repository-specific failure.",
        )
    return (
        "warning",
        "The SonarQube connectivity probe failed with an unclassified curl "
        f"exit code ({result.returncode}). Inspect the scanner output before "
        "attributing the failure to repository code.",
    )


def emit(level: str, message: str, summary_path: str = "") -> None:
    print(f"::{level} title=SonarQube failure classification::{message}")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write("### SonarQube failure classification\n\n")
            summary.write(f"{message}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host-url",
        default=os.environ.get("SONAR_HOST_URL", ""),
        help="SonarQube base URL (defaults to SONAR_HOST_URL)",
    )
    parser.add_argument(
        "--summary",
        default=os.environ.get("GITHUB_STEP_SUMMARY", ""),
        help="Optional GitHub step-summary path",
    )
    args = parser.parse_args(argv)
    if not args.host_url:
        emit(
            "warning",
            "SONAR_HOST_URL is unavailable, so connectivity could not be classified.",
            args.summary,
        )
        return 0
    level, message = classify(probe(args.host_url))
    emit(level, message, args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
