# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline raw clip AP at an explicit cutoff; never connects to a deployment."""

import argparse
import hashlib
import json
from pathlib import Path

from flows.clip_metrics import gallery_sensors, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--ingestion-manifest", type=Path, required=True)
    parser.add_argument("--capture-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    if not 1 <= args.top_k <= 1000:
        parser.error("--top-k must be between 1 and 1000")
    cohort = json.loads(args.queries.read_text())
    captures = {}
    for path in sorted(args.capture_dir.glob("*.json")):
        result = json.loads(path.read_text())
        metadata = result.get("metadata", {})
        if not isinstance(metadata, dict):
            raise TypeError("Malformed capture metadata")
        current, legacy = result.get("query_id"), metadata.get("query_id")
        if current is not None and legacy is not None and current != legacy:
            raise ValueError("Conflicting query capture identities")
        qid = current if current is not None else legacy
        if not isinstance(qid, str) or not qid:
            raise ValueError("Missing query capture identity")
        if qid in captures:
            raise ValueError("Duplicate query capture")
        captures[qid] = result
    sensors = gallery_sensors(json.loads(args.ingestion_manifest.read_text())["clips"])
    result = summarize(cohort["queries"], captures, sensors, k=args.top_k)
    result["inputs"] = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [args.queries, args.ingestion_manifest]
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
