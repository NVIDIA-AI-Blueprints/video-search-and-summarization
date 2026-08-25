#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Validate warehouse Foundation constraints that `resolved.yml` cannot express.

`validate_resolved_yml.py` checks the resolved Compose model. The constraints
here are env-level: MODE, BP_PROFILE, HARDWARE_PROFILE and SAMPLE_VIDEO_DATASET
appear in no service `environment:` block, so they are structurally invisible
there. Each rule below fails at bring-up or silently at runtime -- never at
`docker compose config` time.

Env files are parsed directly, never sourced through a shell: the warehouse
`.env` carries an unquoted JSON value that shell quote-removal mangles, and the
shell environment outranks --env-file in Compose interpolation.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (dataset, allowed (mode, bp_profile) pairs, expected NUM_STREAMS)
DATASETS = {
    "nv-warehouse-4cams": ({("2d", "bp_wh")}, 4),
    "warehouse-loading-dock-3cams-synthetic": (
        {("2d", "bp_wh_kafka"), ("2d", "bp_wh_redis")},
        3,
    ),
    "warehouse-4cams-20mx20m-synthetic": (
        {("3d", "bp_wh_kafka"), ("3d", "bp_wh_redis")},
        4,
    ),
}

MODES = {"2d", "3d"}
BP_PROFILES = {"bp_wh", "bp_wh_kafka", "bp_wh_redis"}

# The service lists this skill supports. overrides.env defines others; selecting
# one of those is a routing error, so the check is an allowlist rather than a
# denylist -- a list added upstream is rejected until it is reviewed here.
IN_SCOPE_VARIANTS = {
    "COMPOSE_PROFILES_WH_2D",
    "COMPOSE_PROFILES_WH_KAFKA_2D",
    "COMPOSE_PROFILES_WH_REDIS_2D",
    "COMPOSE_PROFILES_WH_KAFKA_3D",
    "COMPOSE_PROFILES_WH_REDIS_3D",
    "COMPOSE_PROFILES_WH_KAFKA_2D_MINIMAL",
    "COMPOSE_PROFILES_WH_REDIS_2D_MINIMAL",
    "COMPOSE_PROFILES_WH_KAFKA_3D_MINIMAL",
    "COMPOSE_PROFILES_WH_REDIS_3D_MINIMAL",
}

# Services every warehouse list carries that no capability names. The
# forward-closure prune in composition.md must not remove them.
INFRA_FLOOR = [
    "init-dirs",
    "render-config",
    "wdm-env-from-config",
    "wait-for-redis",
    "wait-for-docker-workloads",
    "sdr-controller",
    "centralizedb",
    "vst-ingress",
    "sensor-bp-wait-bp-configurator",
    "turnserver-init",
    "turnserver",
    "redis",
]

ASSIGN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")

# Warehouse service keys carry the deployment mode as a token, e.g. perception-3d,
# bp-configurator-2d-init, vss-behavior-analytics-3d. The token must agree with MODE:
# a 2D detector in a 3D build resolves cleanly, boots healthy, and publishes to the
# wrong topic -- 2D writes mdx-raw while vss-behavior-analytics-3d reads mdx-bev, so
# analytics silently sees nothing.
MODE_TOKEN = re.compile(r"(?:^|-)(2d|3d)(?:-|$)")


def strip_value(value: str) -> str:
    """Unquote and drop an inline comment, matching Compose's env_file parser.

    Compose ends an *unquoted* value at the first whitespace-preceded `#`, and
    for a quoted value takes the quoted span and ignores the remainder. Keeping
    the comment instead corrupts real values: warehouse-operations/.env ships
    `NVSTREAMER_IP=vss-vios-nvstreamer # Compose service DNS name; ...`, and a
    hostname with prose appended fails DNS inside the container.
    """
    if value[:1] in ("'", '"'):
        quote = value[0]
        end = value.find(quote, 1)
        if end != -1:
            return value[1:end]
        return value[1:]
    head, hash_sep, _ = value.partition("#")
    if hash_sep and (not head or head[-1].isspace()):
        value = head
    return value.strip()


def parse_env(path: Path) -> dict[str, str]:
    """Minimal dotenv parse. No shell, no interpolation, last assignment wins."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = ASSIGN.match(line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        value = strip_value(value)
        values[key] = value
    return values


def layered_env(repo: Path, foundation_dir: Path, build_dir: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    for path in (
        repo / "deploy/docker/containers.env",
        foundation_dir / ".env",
        foundation_dir / "overrides.env",
        build_dir / "override.env",
    ):
        merged.update(parse_env(path))
    return merged


def check(env: dict[str, str], repo: Path, foundation_dir: Path) -> list[str]:
    errors: list[str] = []
    warnings: list[str] = []

    mode = env.get("MODE", "")
    bp = env.get("BP_PROFILE", "")
    hw = env.get("HARDWARE_PROFILE", "")
    profiles = [p for p in env.get("COMPOSE_PROFILES", "").split(",") if p]
    variant = env.get("FOUNDATION_VARIANT", "")


    if mode not in MODES:
        errors.append(
            f"MODE={mode!r} is not supported by this skill; use one of {sorted(MODES)}"
        )

    if bp not in BP_PROFILES:
        errors.append(
            f"BP_PROFILE={bp!r} is not supported by this skill; "
            f"use one of {sorted(BP_PROFILES)}"
        )

    if variant and variant not in IN_SCOPE_VARIANTS:
        errors.append(
            f"FOUNDATION_VARIANT={variant!r} is not a service list this skill "
            f"supports; use one of {sorted(IN_SCOPE_VARIANTS)}"
        )

    # 1/3. bp_wh is 2d-only.
    if bp == "bp_wh" and mode == "3d":
        errors.append(
            "BP_PROFILE=bp_wh is unsupported with MODE=3d "
            "(agents run in 2d only); use bp_wh_kafka or bp_wh_redis"
        )

    # 2. bp_wh 2d is rejected on edge platforms.
    if bp == "bp_wh" and mode == "2d" and hw in {"IGX-THOR", "DGX-SPARK"}:
        errors.append(
            f"BP_PROFILE=bp_wh in 2d mode is not supported on {hw} "
            "(blueprint_config.yml rejects it)"
        )

    # 4. DGX-SPARK needs an sbsa perception image.
    if hw == "DGX-SPARK" and "sbsa" not in env.get("VSS_RT_CV_TAG", ""):
        errors.append(
            "HARDWARE_PROFILE=DGX-SPARK requires VSS_RT_CV_TAG to contain 'sbsa'; "
            f"got {env.get('VSS_RT_CV_TAG', '')!r}"
        )

    # 5. Dataset <-> variant <-> stream count.
    dataset = env.get("SAMPLE_VIDEO_DATASET", "")
    if dataset:
        if dataset not in DATASETS:
            warnings.append(f"SAMPLE_VIDEO_DATASET={dataset!r} is not a known sample dataset")
        else:
            allowed, streams = DATASETS[dataset]
            if mode and bp and (mode, bp) not in allowed:
                errors.append(
                    f"SAMPLE_VIDEO_DATASET={dataset!r} is not valid for "
                    f"MODE={mode} + BP_PROFILE={bp}"
                )
            actual = env.get("NUM_STREAMS", "")
            if actual and actual != str(streams):
                errors.append(
                    f"NUM_STREAMS={actual} does not match dataset {dataset!r} "
                    f"(expects {streams}); a short count looks like healthy containers "
                    "processing nothing"
                )

    # 6. Broker selection must agree with the variant.
    stream_type = env.get("STREAM_TYPE", "")
    if bp == "bp_wh_redis" and stream_type != "redis":
        errors.append(f"BP_PROFILE=bp_wh_redis requires STREAM_TYPE=redis, got {stream_type!r}")
    if bp in {"bp_wh", "bp_wh_kafka"} and stream_type not in {"", "kafka"}:
        errors.append(f"BP_PROFILE={bp} requires STREAM_TYPE=kafka, got {stream_type!r}")

    # 7. Local LLM needs a sizing file for this hardware profile.
    if env.get("LLM_MODE") == "local":
        slug = env.get("LLM_NAME_SLUG", "")
        sizing = repo / f"deploy/docker/services/nim/{slug}/hw-{hw}.env"
        if not sizing.is_file():
            available = sorted(
                p.name for p in (repo / f"deploy/docker/services/nim/{slug}").glob("hw-*.env")
            ) if slug else []
            errors.append(
                f"LLM_MODE=local needs {sizing.relative_to(repo)}, which does not exist "
                f"(compose fails with a bare 'no such file'). Available: {available or 'none'}"
            )
        if mode != "2d" or bp != "bp_wh":
            errors.append(
                "LLM_MODE=local is only valid with MODE=2d + BP_PROFILE=bp_wh; "
                f"got MODE={mode!r} + BP_PROFILE={bp!r}"
            )

    # 8. Warehouse uses the integrated RTVI VLM, never the standalone VLM NIM.
    for key in ("VLM_MODE", "VLM_NAME_SLUG"):
        if env.get(key, "none") != "none":
            errors.append(
                f"{key}={env.get(key)!r} must be 'none' on warehouse; "
                "the blueprint uses the integrated RTVI VLM, not the standalone VLM NIM"
            )

    # 9. Variant provenance, and the infra floor the prune must not remove.
    if not variant:
        errors.append("FOUNDATION_VARIANT is required when FOUNDATION=warehouse")
    else:
        baseline = parse_env(foundation_dir / "overrides.env").get(variant)
        if baseline is None:
            errors.append(
                f"FOUNDATION_VARIANT={variant!r} is not defined in "
                f"{(foundation_dir / 'overrides.env').name}"
            )
        elif profiles:
            # Report the delta rather than forbid it -- a delta is expected to
            # differ from its baseline; that is what makes it a delta.
            base_keys = {k for k in baseline.split(",") if k}
            current = set(profiles)
            added, removed = sorted(current - base_keys), sorted(base_keys - current)
            if added or removed:
                warnings.append(
                    f"COMPOSE_PROFILES differs from {variant}: "
                    f"+{added or '[]'} -{removed or '[]'}. Expected for a delta; "
                    "confirm it is intentional for a stock deploy"
                )

    # 9b. Mode coherence: a key whose mode token contradicts MODE is a defect,
    # not a delta. This is the one silent-wrong-data-plane failure the floor
    # check cannot see.
    if profiles and mode in MODES:
        mismatched = {}
        for key in profiles:
            tokens = set(MODE_TOKEN.findall(key))
            if tokens and mode not in tokens:
                mismatched[key] = "/".join(sorted(tokens))
        if mismatched:
            detail = ", ".join(f"{k} (mode {v})" for k, v in sorted(mismatched.items()))
            errors.append(
                f"COMPOSE_PROFILES contains service keys whose mode contradicts "
                f"MODE={mode}: {detail}. These resolve and boot healthy while "
                "publishing to the wrong topic, so no later gate catches them"
            )
    if not profiles:
        # Guarding the floor check on a non-empty list would let the worst case
        # through silently: with COMPOSE_PROFILES empty or unset, Compose selects
        # no services, `up -d` starts nothing and still exits 0, and a gate that
        # only inspects the list's *contents* reports clean.
        errors.append(
            "COMPOSE_PROFILES is empty or unset. Compose would select no services, "
            "so `up -d` starts nothing and still exits 0. Expand the "
            "FOUNDATION_VARIANT list into COMPOSE_PROFILES as a literal."
        )
    else:
        missing = [s for s in INFRA_FLOOR if s not in profiles]
        if missing:
            errors.append(
                "COMPOSE_PROFILES is missing warehouse infrastructure services that no "
                f"capability names and nothing boots without: {missing}. A build missing "
                "these resolves and validates cleanly, then fails at bring-up."
            )

    # 10. Calibration is checked in per sample dataset, NOT under VSS_DATA_DIR.
    # Compose bind-mounts it by path from the repo:
    #   warehouse-<mode>-app/calibration/sample-data/<SAMPLE_VIDEO_DATASET>/calibration.json
    # The shipped sample datasets already carry it, so nothing needs generating.
    # Only a custom dataset lacks it -- and a missing bind source makes Docker
    # create a directory where a file is expected.
    if mode in MODES and dataset:
        calib = (
            repo
            / "deploy/docker/industry-profiles/warehouse-operations"
            / f"warehouse-{mode}-app/calibration/sample-data"
            / dataset
            / "calibration.json"
        )
        if not calib.is_file():
            errors.append(
                f"no calibration for SAMPLE_VIDEO_DATASET={dataset!r} in {mode} mode: "
                f"{calib.relative_to(repo)} does not exist. Compose bind-mounts this "
                "path, so Docker will create a directory where a file is expected and "
                "perception will emit nothing. Generate it with "
                "vss-generate-video-calibration, or use a shipped sample dataset"
            )

    if mode == "3d" and variant.endswith("_MINIMAL"):
        warnings.append(
            "MODE=3d on a _MINIMAL list deploys no Elasticsearch, so the "
            "mdx-bev index is never persisted and BEV output cannot be verified"
        )

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build_dir", type=Path, help="path to _builds/<name>")
    parser.add_argument("--repo-root", required=True, type=Path)
    args = parser.parse_args()

    build_dir = args.build_dir
    repo = args.repo_root
    override = build_dir / "override.env"
    if not override.is_file():
        print(f"ERROR: {override} not found", file=sys.stderr)
        raise SystemExit(1)

    foundation = parse_env(override).get("FOUNDATION", "")
    if foundation != "warehouse":
        print(f"FOUNDATION={foundation!r} is not warehouse; nothing to check.")
        return

    foundation_dir = repo / "deploy/docker/industry-profiles/warehouse-operations"
    env = layered_env(repo, foundation_dir, build_dir)
    errors = check(env, repo, foundation_dir)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"warehouse constraints OK: MODE={env.get('MODE')} "
          f"BP_PROFILE={env.get('BP_PROFILE')} variant={env.get('FOUNDATION_VARIANT')}")


if __name__ == "__main__":
    main()
