# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
from pathlib import Path

import yaml


PERF_ROOT = Path(__file__).resolve().parents[1]


def select_source(source: Path, config_id: str) -> Path:
    # Explicit custom config files remain authoritative. Hardware rows can
    # select a native profile when the caller supplies the standard config.
    if source.resolve() != (PERF_ROOT / "configs/config.yaml").resolve():
        return source
    matrix = yaml.safe_load((PERF_ROOT / "configs/perf-configs.yaml").read_text())
    for row in matrix["perf_configs"]:
        if row["id"] == config_id and row.get("benchmarkConfig"):
            selected = (PERF_ROOT / row["benchmarkConfig"]).resolve()
            if not selected.is_relative_to(PERF_ROOT) or not selected.is_file():
                raise ValueError(f"Invalid benchmarkConfig for {config_id}")
            return selected
    return source


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--native-output", required=True)
    parser.add_argument("--release", default="")
    parser.add_argument("--gpus", default="0")
    parser.add_argument("--config-id", default="")
    args = parser.parse_args()

    source = select_source(Path(args.source), args.config_id)
    config = yaml.safe_load(source.read_text())
    global_config = config.setdefault("global", {})
    global_config["output_dir"] = str(Path(args.native_output).resolve())
    global_config["release"] = args.release
    global_config["vlm_gpus"] = [int(gpu) for gpu in args.gpus.split(",") if gpu.strip()]
    destination = Path(args.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(yaml.safe_dump(config, sort_keys=False))


if __name__ == "__main__":
    main()
