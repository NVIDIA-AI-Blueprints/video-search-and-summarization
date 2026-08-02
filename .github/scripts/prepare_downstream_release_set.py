#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Wait for this commit's GHCR release set and pass it to downstream CI."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

from detect_changed_images import changed_paths, resolve_diff_base
from release_set import load_inventory, validate_release_set
from update_pr_ghcr_candidates import GitHubApi, download_release_set


DEPLOY_PREFIX = "deploy/"
# Opt-in inventory flag. An image not published to GHCR can still want
# downstream coverage when its source changes -- mirrored and externally pinned
# components are deployed by the same profiles and break the same evals.
TRIGGER_FLAG = "trigger_downstream_from_source"


def downstream_relevant(changed: list[str] | None, inventory: dict) -> tuple[bool, str]:
    """Whether a change warrants a downstream acceptance run.

        (source changed AND (ghcr_build OR trigger_downstream_from_source))
        OR deploy/ changed

    Scoped on what *changed*, not on what got built. The previous gate keyed off
    has_ghcr_build_entries -- "did any GHCR image get rebuilt" -- which is a poor
    proxy twice over: build avoidance means a real source change can rebuild
    nothing, and deploy-only changes never rebuild anything yet are exactly what
    acceptance exists to catch. Config and deploy edits were getting no
    downstream coverage at all.

    ``changed is None`` means the diff could not be resolved; run downstream
    rather than silently skip it.
    """
    if changed is None:
        return True, "changed paths unavailable; running downstream"

    watched = {
        str(entry["source_path"]): str(entry["name"])
        for entry in inventory.get("images", [])
        if entry.get("source_path")
        and (entry.get("ghcr_build") or entry.get(TRIGGER_FLAG))
    }
    hit_images = sorted(
        {
            name
            for path in changed
            for source_path, name in watched.items()
            if path == source_path or path.startswith(source_path.rstrip("/") + "/")
        }
    )
    hit_deploy = any(path.startswith(DEPLOY_PREFIX) for path in changed)

    reasons = []
    if hit_images:
        reasons.append(f"source changed ({', '.join(hit_images)})")
    if hit_deploy:
        reasons.append("deploy/ changed")
    if reasons:
        return True, "; ".join(reasons)
    return False, "no watched source or deploy/ change"


def has_ghcr_build_entries(release_set: dict) -> bool:
    """Whether downstream has newly built GHCR images to accept/promote."""
    return any(
        image.get("strategy") == "build"
        and str(image.get("image", "")).startswith("ghcr.io/")
        for image in release_set.get("images", [])
    )


def downstream_variables(release_set: dict) -> dict[str, str]:
    encoded = base64.b64encode(
        (json.dumps(release_set, separators=(",", ":")) + "\n").encode()
    ).decode()
    return {
        "BUILD_TYPE": "ghcr-acceptance",
        "VSS_RELEASE_SET_ID": release_set["release_set_id"],
        "VSS_RELEASE_SET_B64": encoded,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--sha", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ref-name", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--before", default=os.environ.get("GITHUB_EVENT_BEFORE", ""))
    parser.add_argument("--attempts", type=int, default=240)
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--release-set", type=Path)
    parser.add_argument("--release-set-output", type=Path)
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    github_output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not args.sha or not github_env:
        raise SystemExit(
            "SHA and GITHUB_ENV are required"
        )

    if args.release_set:
        release_set = json.loads(args.release_set.read_text())
    else:
        if not token or not args.repository:
            raise SystemExit(
                "GITHUB_TOKEN and repository are required without --release-set"
            )
        release_set = download_release_set(
            GitHubApi(token),
            args.repository,
            args.sha,
            args.ref_name,
            args.attempts,
            args.interval_seconds,
        )
    if release_set.get("source", {}).get("commit") != args.sha:
        raise RuntimeError("release-set source commit does not match downstream SHA")
    problems = validate_release_set(
        release_set, load_inventory(Path.cwd())
    )
    if problems:
        raise RuntimeError("invalid release set: " + "; ".join(problems))

    if args.release_set_output:
        args.release_set_output.parent.mkdir(parents=True, exist_ok=True)
        args.release_set_output.write_text(
            json.dumps(release_set, indent=2, sort_keys=True) + "\n"
        )

    variables = downstream_variables(release_set)
    with Path(github_env).open("a") as output:
        output.write("DOWNSTREAM_EXTRA_VARIABLES_JSON<<EOF\n")
        output.write(json.dumps(variables, separators=(",", ":")) + "\n")
        output.write("EOF\n")
    has_builds = has_ghcr_build_entries(release_set)

    base, base_reason = resolve_diff_base(
        args.repo_root, "push", args.ref_name, args.before, "develop"
    )
    changed = changed_paths(args.repo_root, base) if base else None
    relevant, gate_reason = downstream_relevant(
        changed, load_inventory(args.repo_root)
    )

    if github_output:
        with Path(github_output).open("a") as output:
            output.write(
                f"has_ghcr_build_entries={'true' if has_builds else 'false'}\n"
            )
            output.write(f"run_downstream={'true' if relevant else 'false'}\n")
    print(
        f"Prepared release set {release_set['release_set_id']} "
        f"for downstream acceptance ({len(release_set['images'])} images, "
        f"GHCR builds: {'yes' if has_builds else 'no'}).\n"
        f"Downstream gate: {'run' if relevant else 'skip'} "
        f"-- {gate_reason} (base: {base_reason})."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[downstream-release-set] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
