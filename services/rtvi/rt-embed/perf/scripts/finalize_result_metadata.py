# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--triggered-by", required=True)
    parser.add_argument("--pipeline-url", default="")
    args = parser.parse_args()

    result_files = list(Path(args.results).glob("*.json"))
    if not result_files:
        raise SystemExit(f"No canonical result JSON found under {args.results}")
    for result_file in result_files:
        result = json.loads(result_file.read_text())
        run_info = result.setdefault("metadata", {}).setdefault("run_info", {})
        run_info["triggered_by"] = args.triggered_by
        if args.pipeline_url:
            run_info["pipeline_url"] = args.pipeline_url
        result_file.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
