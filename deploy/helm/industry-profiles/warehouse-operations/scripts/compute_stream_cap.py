#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Cap NUM_STREAMS by GPU (same table as Compose's blueprint_config.yml) and
write a bp-configurator.env values-override for helm upgrade/install -f.

    compute_stream_cap.py --mode 2d --num-streams 6
    compute_stream_cap.py --mode 3d --num-streams 30 --hardware-profile H100
    compute_stream_cap.py --mode mv3dt --num-streams 4 --gpu-index 1 -o values-streams.yaml
"""
import argparse
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[5]
BLUEPRINT_CONFIG = (
    REPO_ROOT
    / "deploy/docker/industry-profiles/warehouse-operations/blueprint-configurator/blueprint_config.yml"
)
HELM_WAREHOUSE_DIR = REPO_ROOT / "deploy/helm/industry-profiles/warehouse-operations"

# nvidia-smi GPU name substring -> HARDWARE_PROFILE, matching
# skills/vss-deploy-profile/references/warehouse.md "Supported Hardware".
GPU_NAME_TO_PROFILE = [
    ("RTX PRO 6000 Blackwell Server Edition", "RTXPRO6000BW-SE"),
    ("RTX PRO 6000 Blackwell", "RTXPRO6000BW"),
    ("RTX PRO 4500 Blackwell", "RTXPRO4500BW"),
    ("H100", "H100"),
    ("A6000 Ada", "RTXA6000ADA"),
    ("RTX A6000", "RTXA6000"),
    ("L40S", "L40S"),
    ("L4", "L4"),
]


def detect_hardware_profile(gpu_index: int) -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name", "--format=csv,noheader"],
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        sys.exit(f"error: nvidia-smi failed ({exc}); pass --hardware-profile explicitly")

    rows = [line.strip() for line in out.splitlines() if line.strip()]
    row = next((r for r in rows if r.startswith(f"{gpu_index},")), None)
    if row is None:
        sys.exit(f"error: no GPU at index {gpu_index} in nvidia-smi output: {rows}")

    name = row.split(",", 1)[1].strip()
    for substring, profile in GPU_NAME_TO_PROFILE:
        if substring.lower() in name.lower():
            return profile

    sys.exit(
        f"error: unrecognized GPU '{name}' — no tuned HARDWARE_PROFILE. "
        "Pass --hardware-profile explicitly, or treat this GPU as unsupported "
        "for the warehouse blueprint (see references/warehouse.md)."
    )


def max_streams_supported(hardware_profile: str, mode: str) -> int | None:
    with open(BLUEPRINT_CONFIG) as f:
        config = yaml.safe_load(f)

    profile = config.get(hardware_profile)
    if profile is None:
        print(
            f"warning: '{hardware_profile}' has no tuned section in "
            f"{BLUEPRINT_CONFIG.name} — no stream cap will be applied",
            file=sys.stderr,
        )
        return None

    mode_cfg = profile.get(mode)
    if mode_cfg is None or "max_streams_supported" not in mode_cfg:
        print(
            f"warning: '{hardware_profile}' has no '{mode}' tuning in "
            f"{BLUEPRINT_CONFIG.name} — no stream cap will be applied",
            file=sys.stderr,
        )
        return None

    return int(mode_cfg["max_streams_supported"])


def patch_env_list(env: list, num_streams: int, hardware_profile: str) -> list:
    patched = [dict(e) for e in env]

    def upsert(name: str, value: str) -> None:
        for entry in patched:
            if entry.get("name") == name:
                entry["value"] = value
                return
        patched.append({"name": name, "value": value})

    upsert("NUM_STREAMS", str(num_streams))
    upsert("HARDWARE_PROFILE", hardware_profile)
    return patched


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=["2d", "3d", "mv3dt"])
    parser.add_argument("--num-streams", required=True, type=int, help="Requested stream count")
    parser.add_argument(
        "--hardware-profile",
        help="Skip nvidia-smi auto-detection and use this HARDWARE_PROFILE directly",
    )
    parser.add_argument("--gpu-index", type=int, default=0, help="GPU index to detect (default: 0)")
    parser.add_argument(
        "-o",
        "--output",
        default="values-stream-cap.generated.yaml",
        help="Path to write the values-override file",
    )
    args = parser.parse_args()

    if args.num_streams <= 0:
        sys.exit("error: --num-streams must be positive")

    hardware_profile = args.hardware_profile or detect_hardware_profile(args.gpu_index)
    cap = max_streams_supported(hardware_profile, args.mode)

    effective = args.num_streams if cap is None else min(args.num_streams, cap)
    if cap is not None and effective < args.num_streams:
        print(
            f"[compute_stream_cap] requested {args.num_streams} streams, "
            f"{hardware_profile}/{args.mode} supports {cap} — capping to {effective}",
            file=sys.stderr,
        )
    else:
        print(
            f"[compute_stream_cap] {hardware_profile}/{args.mode}: using {effective} stream(s)"
            + (f" (cap: {cap})" if cap is not None else " (no tuned cap)"),
            file=sys.stderr,
        )

    chart_dir = HELM_WAREHOUSE_DIR / f"warehouse-{args.mode}-app"
    chart_values_path = chart_dir / "values.yaml"
    if not chart_values_path.exists():
        sys.exit(f"error: chart values not found: {chart_values_path}")

    with open(chart_values_path) as f:
        chart_values = yaml.safe_load(f)

    bp_configurator = chart_values.get("bp-configurator", {})
    env = bp_configurator.get("env", [])
    patched_env = patch_env_list(env, effective, hardware_profile)

    output = {"bp-configurator": {"env": patched_env}}
    with open(args.output, "w") as f:
        yaml.safe_dump(output, f, sort_keys=False, default_flow_style=False)

    print(f"[compute_stream_cap] wrote {args.output}", file=sys.stderr)
    print(
        "[compute_stream_cap] keep vios.vss-vios-nvstreamer.syncFileCount in step "
        f"with NUM_STREAMS={effective} (README.md: 'Keep in step with bp-configurator NUM_STREAMS')",
        file=sys.stderr,
    )
    print(
        f"\nhelm upgrade --install <release> {chart_dir} -n <namespace> "
        f"-f {args.output} --set vios.vss-vios-nvstreamer.syncFileCount={effective} ..."
    )


if __name__ == "__main__":
    main()
