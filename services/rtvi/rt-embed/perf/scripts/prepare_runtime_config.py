# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
from pathlib import Path
import sys

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
from benchmark_config import validate_benchmark_config  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--native-output", required=True)
    parser.add_argument("--release", default="")
    parser.add_argument("--gpus", default="0")
    parser.add_argument("--scenario", default=None)
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.source).read_text())
    if not isinstance(config, dict) or not isinstance(config.get("global"), dict):
        parser.error("Configuration requires a global mapping")
    global_config = config.setdefault("global", {})
    global_config["output_dir"] = str(Path(args.native_output).resolve())
    global_config["release"] = args.release
    global_config["vlm_gpus"] = [int(gpu) for gpu in args.gpus.split(",") if gpu.strip()]
    try:
        validate_benchmark_config(config, args.scenario)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))
    destination = Path(args.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(config, sort_keys=False))


if __name__ == "__main__":
    main()
