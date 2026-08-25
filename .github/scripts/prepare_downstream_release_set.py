#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Gate and parameterise the downstream acceptance run for this commit.

Waits for Build Dev Images to succeed, decides whether downstream is
warranted from what changed, and emits the variables the GitLab pipeline
reads. The release set itself is consumed inside the build workflow (it
drives the candidate alias publication); nothing is fetched here.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from detect_changed_images import (
    changed_paths,
    commit_exists,
    resolve_diff_base,
)
from github_build_run import GitHubApi, await_build_run
from release_set import load_inventory


DEPLOY_PREFIX = "deploy/"
# Opt-in inventory flag. An image not published to GHCR can still want
# downstream coverage when its source changes -- mirrored and externally pinned
# components are deployed by the same profiles and break the same evals.
TRIGGER_FLAG = "trigger_downstream_from_source"
PR_REF_PATTERN = re.compile(r"pull-request/(\d+)")


def pr_merge_base_sha(api: GitHubApi, repository: str, ref_name: str) -> str | None:
    """Return the merge base for a mirrored PR branch.

    A pull request API response exposes the target branch tip as ``base.sha``.
    The compare API supplies the actual common ancestor, which is the only
    correct starting point for a complete PR diff.
    """
    match = PR_REF_PATTERN.fullmatch(ref_name)
    if not match:
        return None
    pull = api.request("GET", f"/repos/{repository}/pulls/{match.group(1)}")
    target = str(pull.get("base", {}).get("sha", ""))
    head = str(pull.get("head", {}).get("sha", ""))
    if not all(re.fullmatch(r"[0-9a-f]{40}", sha) for sha in (target, head)):
        raise RuntimeError("PR metadata did not contain valid base and head SHAs")
    compare = api.request("GET", f"/repos/{repository}/compare/{target}...{head}")
    merge_base = str(compare.get("merge_base_commit", {}).get("sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", merge_base):
        raise RuntimeError("PR comparison did not contain a valid merge-base SHA")
    return merge_base


def downstream_relevant(changed: list[str] | None, inventory: dict) -> tuple[bool, str]:
    """Whether a change warrants a downstream acceptance run.

        (source changed AND (ghcr_build OR trigger_downstream_from_source))
        OR deploy/ changed

    Scoped on what *changed*, not on what got built. The previous gate keyed off
    "did any GHCR image get rebuilt", which is a poor proxy twice over: build avoidance means a real source change can rebuild
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


def candidate_container_tag(ref_name: str, sha: str) -> str:
    """The shared immutable GHCR tag this ref publishes for every image.

    Same scheme as container_build_plan.py: the build tags every GHCR image
    with it, including ones it did not rebuild, so a consumer can derive the
    coordinate from the ref and commit alone.
    """
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("commit must be a 40-hex SHA")
    if ref_name == "develop":
        prefix = "develop"
    elif match := PR_REF_PATTERN.fullmatch(ref_name):
        prefix = f"pr-{match.group(1)}"
    else:
        raise ValueError(
            f"ref {ref_name!r} does not publish a shared candidate tag"
        )
    return f"{prefix}-{sha[:12]}"


def downstream_variables(ref_name: str, sha: str) -> dict[str, str]:
    """Variables the downstream GitLab pipeline actually reads.

    The release set itself is no longer sent. ci-vss-oss retired every
    consumer of VSS_RELEASE_SET_B64/_ID (its validate/apply/promote scripts
    and the validate-ghcr-release-set job), so the payload was being
    base64-encoded and shipped on every trigger for nothing. BUILD_TYPE is
    now the only signal selecting acceptance mode there.
    """
    return {
        "BUILD_TYPE": "ghcr-acceptance",
        "VSS_CONTAINER_TAG": candidate_container_tag(ref_name, sha),
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
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    github_env = os.environ.get("GITHUB_ENV", "").strip()
    github_output = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not args.sha or not github_env:
        raise SystemExit(
            "SHA and GITHUB_ENV are required"
        )

    if not token or not args.repository:
        raise SystemExit("GITHUB_TOKEN and repository are required")
    await_build_run(
        GitHubApi(token),
        args.repository,
        args.sha,
        args.ref_name,
        args.attempts,
        args.interval_seconds,
    )

    if PR_REF_PATTERN.fullmatch(args.ref_name):
        try:
            base = pr_merge_base_sha(GitHubApi(token), args.repository, args.ref_name)
            if not base or not commit_exists(args.repo_root, base):
                raise RuntimeError("PR base commit is unavailable in this checkout")
            base_reason = f"PR merge base from GitHub metadata: {base[:12]}"
        except Exception as exc:
            base = None
            base_reason = f"PR base unavailable ({exc}); running downstream"
    else:
        base, base_reason = resolve_diff_base(
            args.repo_root, "push", args.ref_name, args.before, "develop"
        )
    changed = changed_paths(args.repo_root, base) if base else None
    relevant, gate_reason = downstream_relevant(
        changed, load_inventory(args.repo_root)
    )
    run_downstream = relevant

    variables = downstream_variables(args.ref_name, args.sha)
    with Path(github_env).open("a") as output:
        output.write("DOWNSTREAM_EXTRA_VARIABLES_JSON<<EOF\n")
        output.write(json.dumps(variables, separators=(",", ":")) + "\n")
        output.write("EOF\n")

    if github_output:
        with Path(github_output).open("a") as output:
            output.write(f"run_downstream={'true' if run_downstream else 'false'}\n")
    print(
        f"Downstream acceptance for {args.sha[:12]} on {args.ref_name}.\n"
        f"Downstream gate: {'run' if run_downstream else 'skip'} "
        f"-- {gate_reason} (base: {base_reason})."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[downstream-gate] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
