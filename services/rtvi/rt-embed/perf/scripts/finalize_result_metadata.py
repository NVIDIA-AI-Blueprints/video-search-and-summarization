# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os

import yaml
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", required=True)
    parser.add_argument("--triggered-by", required=True)
    parser.add_argument("--pipeline-url", default="")
    parser.add_argument("--config", help="Effective runtime config used by this evaluation")
    parser.add_argument("--profile", help="Original service-owned benchmark profile")
    args = parser.parse_args()

    config_metadata = {}
    if args.config:
        runtime = yaml.safe_load(Path(args.config).read_text())
        settings = runtime["global"]
        config_metadata["vlm_gpus"] = settings["vlm_gpus"]
        config_metadata["gpu_count"] = len(settings["vlm_gpus"])
        model = settings.get("model_preset") or settings.get("model")
        if model:
            config_metadata["vlm"] = model
        if settings.get("model_preset"):
            config_metadata["model_preset"] = settings["model_preset"]
        if os.environ.get("GPU_MODEL"):
            config_metadata["gpu_model"] = os.environ["GPU_MODEL"]
    if args.profile:
        profile = Path(args.profile).resolve()
        try:
            profile_name = str(profile.relative_to(Path(__file__).resolve().parents[1]))
        except ValueError:
            profile_name = profile.name
        config_metadata["benchmark_profile"] = profile_name

    result_files = list(Path(args.results).glob("*.json"))
    if not result_files:
        raise SystemExit(f"No canonical result JSON found under {args.results}")
    for result_file in result_files:
        result = json.loads(result_file.read_text())
        metadata = result.setdefault("metadata", {})
        if config_metadata:
            metadata.setdefault("config", {}).update(config_metadata)
        run_info = metadata.setdefault("run_info", {})
        run_info["triggered_by"] = args.triggered_by
        if args.pipeline_url:
            run_info["pipeline_url"] = args.pipeline_url
        result_file.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
