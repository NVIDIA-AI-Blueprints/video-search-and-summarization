# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""CLI entry point for the FL Model Runner.

Usage:
    python -m core.model_runner.cli run-amc-compatible -v /path/to/video.mp4 -o /out
    python -m core.model_runner.cli run-amc-compatible --image /path/to/img.jpg --output /out
    python -m core.model_runner.cli benchmark --image /path/to/img.jpg --output /out --n-runs 10
"""
import argparse
import json
import logging
import sys
from pathlib import Path

import yaml

from .schemas import RunnerRequest, RunnerResult
from .coordinator import ModelCoordinator
from .exporters.amc_exporter import AmcExporter

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CONFIG_DIR = _PROJECT_ROOT / "configs" / "config_AutoMagicCalib"
_DEFAULT_MANIFEST = _CONFIG_DIR / "models_manifest.yaml"
_DEFAULT_RUNNER_CONFIG = _CONFIG_DIR / "runner_config.yaml"


def _load_runner_config(path: Path) -> dict:
    if path.exists():
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _parse_args(argv=None):
    p = argparse.ArgumentParser(prog="model_runner", description="FL Model Runner")
    p.add_argument("--manifest", type=Path, default=_DEFAULT_MANIFEST)
    p.add_argument("--runner-config", type=Path, default=_DEFAULT_RUNNER_CONFIG)
    p.add_argument("--project-root", type=Path, default=None,
                   help="AMC project root (auto-detected from manifest location if omitted)")
    p.add_argument("--verbose", "--debug", action="store_true")

    sub = p.add_subparsers(dest="command", required=True)

    # --- run-amc-compatible ---
    run_p = sub.add_parser("run-amc-compatible", help="Single run producing AMC artifacts")
    _add_common_args(run_p)
    run_p.add_argument("-c", "--amc-config", type=Path, default=None,
                       help="Global AMC config.yaml to merge into runner config")
    run_p.add_argument("--rectification_config", type=Path, default=None,
                       help="Rectification config YAML; camera model is read from distortion.model field")

    # --- benchmark ---
    bench_p = sub.add_parser("benchmark", help="Repeatability benchmark")
    _add_common_args(bench_p)
    bench_p.add_argument("--n-runs", type=int, default=10)
    bench_p.add_argument("--seed", type=int, default=42)

    return p.parse_args(argv)


def _add_common_args(p):
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("-i", "--image", type=Path)
    g.add_argument("-v", "--video", type=Path)
    p.add_argument(
        "--video-frame-index",
        type=int,
        default=None,
        help="Use this exact video frame instead of extracting a background image",
    )
    p.add_argument("-o", "--output", type=Path, required=True)
    p.add_argument("--camera-model", default="pinhole",
                   choices=["pinhole", "simple_radial", "radial", "simple_divisional"])
    p.add_argument("--models", nargs="*", default=None,
                   help="Model IDs to run (default: all enabled in manifest)")
    p.add_argument("--save-per-model", action="store_true",
                   help="Save per-model intermediate outputs to <output>/<model_id>/")


def _resolve_camera_model(args) -> str:
    """Determine camera model: explicit --camera-model wins, else read from --rectification_config."""
    if args.camera_model != "pinhole":
        return args.camera_model
    rect_cfg_path = getattr(args, "rectification_config", None)
    if rect_cfg_path and rect_cfg_path.exists():
        try:
            with open(rect_cfg_path) as f:
                rect_cfg = yaml.safe_load(f) or {}
            model = rect_cfg.get("distortion", {}).get("model")
            if model in ("pinhole", "simple_radial", "radial", "simple_divisional"):
                logger.info(f"Camera model from rectification config: {model}")
                return model
        except Exception as e:
            logger.warning(f"Could not read camera model from rectification config: {e}")
    return args.camera_model


def cmd_run_amc_compatible(args):
    runner_cfg = _load_runner_config(args.runner_config)

    global_cfg = {}
    if args.amc_config and args.amc_config.exists():
        with open(args.amc_config) as f:
            global_cfg = yaml.safe_load(f) or {}

    camera_model = _resolve_camera_model(args)

    coord = ModelCoordinator(
        manifest_path=args.manifest,
        runner_config=runner_cfg,
        project_root=args.project_root,
    )

    request = RunnerRequest(
        input_image=args.image,
        input_video=args.video,
        video_frame_index=args.video_frame_index,
        output_dir=args.output,
        enabled_models=args.models or [],
        camera_model=camera_model,
        config=global_cfg,
        save_per_model_output=args.save_per_model,
    )

    result = coord.run(request)

    export_cfg = dict(global_cfg)
    if "focal_length_search_range" in runner_cfg:
        export_cfg.setdefault("focal_length_search_range", runner_cfg["focal_length_search_range"])

    exporter = AmcExporter()
    exporter.export(result, args.output, export_cfg)

    _print_summary(result)
    return result


def cmd_benchmark(args):
    runner_cfg = _load_runner_config(args.runner_config)

    coord = ModelCoordinator(
        manifest_path=args.manifest,
        runner_config=runner_cfg,
        project_root=args.project_root,
    )

    request = RunnerRequest(
        input_image=args.image,
        input_video=args.video,
        video_frame_index=args.video_frame_index,
        output_dir=args.output,
        enabled_models=args.models or [],
        n_runs=args.n_runs,
        camera_model=args.camera_model,
        save_per_model_output=args.save_per_model,
        seed=args.seed,
    )

    result = coord.run(request)

    _write_benchmark_report(result, args.output)
    _generate_benchmark_plots(args.output)
    _print_summary(result)
    return result


def _print_summary(result: RunnerResult):
    print("\n===== Runner Summary =====")
    for mid, mr in result.per_model.items():
        n = len(mr.outputs)
        focals = [o.focal_length_px for o in mr.outputs if o.focal_length_px is not None]
        if focals:
            import numpy as np
            print(f"  {mid}: {n} run(s), focal median={np.median(focals):.1f} px")
        else:
            print(f"  {mid}: {n} run(s), no focal data")
    if result.aggregate:
        sel = result.aggregate.get("selected_focal_length_px")
        pol = result.aggregate.get("policy")
        print(f"  Consolidated focal: {sel:.1f} px ({pol})" if sel else "  No consolidated focal")
    print("==========================\n")


def _write_benchmark_report(result: RunnerResult, output_dir: Path):
    import csv
    import numpy as np

    output_dir.mkdir(parents=True, exist_ok=True)

    csv_path = output_dir / "benchmark_summary.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["model", "run", "focal_length_px", "runtime_ms", "status"])
        for mid, mr in result.per_model.items():
            for i, out in enumerate(mr.outputs):
                writer.writerow([mid, i + 1, out.focal_length_px, f"{out.runtime_ms:.1f}", out.status])

    md_path = output_dir / "benchmark_report.md"
    lines = ["# Benchmark Report\n"]
    for mid, mr in result.per_model.items():
        lines.append(f"## {mid}\n")
        focals = [o.focal_length_px for o in mr.outputs if o.focal_length_px is not None]
        if focals:
            arr = np.array(focals)
            lines.append(f"- Runs: {len(mr.outputs)}")
            lines.append(f"- Focal mean: {np.mean(arr):.2f} px")
            lines.append(f"- Focal median: {np.median(arr):.2f} px")
            lines.append(f"- Focal std: {np.std(arr):.2f} px")
            mean_v = np.mean(arr)
            cv = (np.std(arr) / mean_v * 100) if mean_v != 0 else float("inf")
            lines.append(f"- CV: {cv:.2f}%")
        runtimes = [o.runtime_ms for o in mr.outputs]
        if runtimes:
            lines.append(f"- Mean runtime: {np.mean(runtimes):.0f} ms")
        lines.append("")

    with open(md_path, "w") as f:
        f.write("\n".join(lines))

    logger.info(f"Wrote {csv_path} and {md_path}")


def _generate_benchmark_plots(output_dir: Path):
    csv_path = output_dir / "benchmark_summary.csv"
    if not csv_path.exists():
        return
    try:
        from .benchmark_viz import generate_plots
        generate_plots(csv_path, output_dir)
    except ImportError as e:
        logger.warning(f"Skipping plots (missing dependency): {e}")
    except Exception as e:
        logger.warning(f"Plot generation failed: {e}")


def main(argv=None):
    args = _parse_args(argv)

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(levelname)s - %(name)s - %(message)s")

    if args.command == "run-amc-compatible":
        cmd_run_amc_compatible(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
