# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Command-line interface for shared VSS performance reporting."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from .storage import publish_result_directory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vss-perf-reporting")
    commands = parser.add_subparsers(dest="command", required=True)
    publish = commands.add_parser(
        "publish", description="Validate and publish canonical performance reports"
    )
    publish.add_argument("--report-dir", required=True)
    publish.add_argument("--endpoint")
    publish.add_argument("--bucket")
    publish.add_argument("--secure", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "publish":
        receipts = publish_result_directory(
            args.report_dir,
            endpoint=args.endpoint,
            bucket=args.bucket,
            secure=args.secure,
        )
        for receipt in receipts:
            print(json.dumps(receipt, sort_keys=True))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
