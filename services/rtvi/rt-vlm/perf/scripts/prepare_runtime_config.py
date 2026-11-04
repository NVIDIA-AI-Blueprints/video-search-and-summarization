# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--native-output", required=True)
    parser.add_argument("--release", default="")
    parser.add_argument("--gpus", default="0")
    args = parser.parse_args()

    config = yaml.safe_load(Path(args.source).read_text())
    global_config = config.setdefault("global", {})
    global_config["output_dir"] = str(Path(args.native_output).resolve())
    global_config["release"] = args.release
    global_config["vlm_gpus"] = [int(gpu) for gpu in args.gpus.split(",") if gpu.strip()]
    destination = Path(args.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(config, sort_keys=False))


if __name__ == "__main__":
    main()
