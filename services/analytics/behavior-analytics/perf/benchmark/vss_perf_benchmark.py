# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Write a Behavior Analytics playback benchmark result from a latency report."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from latency import build_result
from safe_fs import safe_write_text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-ref", default="")
    parser.add_argument("--scenario", default="quick_test")
    parser.add_argument("--config-id", default="warehouse_2d-kafka-playback")
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--warmup-seconds", type=int, default=10)
    args = parser.parse_args()

    result = build_result(
        args.report.read_text(),
        artifact_ref=args.artifact_ref,
        scenario=args.scenario,
        config_id=args.config_id,
        duration_seconds=args.duration_seconds,
        warmup_seconds=args.warmup_seconds,
    )
    safe_write_text(args.output, json.dumps(result, indent=2) + "\n")
    print(args.output)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
