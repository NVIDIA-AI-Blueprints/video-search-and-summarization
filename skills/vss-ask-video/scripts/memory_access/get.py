#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Read one authoritative unified-memory record from Elasticsearch."""

from __future__ import annotations

import argparse
from pathlib import Path

from vss_core.memory import build_memory_service


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--es-endpoint", required=True)
    parser.add_argument("--memory-index", required=True)
    args = parser.parse_args()
    service = build_memory_service(es_endpoint=args.es_endpoint, memory_index=args.memory_index)
    record = service.get(args.record_id)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(record.model_dump_json(by_alias=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

