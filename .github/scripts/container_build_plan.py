#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Plan GHCR coordinates and verify published multiarch manifests."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
Runner = Callable[[list[str]], str]


@dataclass(frozen=True)
class CandidateCoordinates:
    image: str
    tag: str
    tree_sha: str
    # Content-addressed alias: identical source content shares this tag across
    # commits, so a later build of unchanged content can re-tag from it instead
    # of rebuilding (see ghcr_image_guard.py `reuse`).
    content_tag: str


@dataclass(frozen=True)
class ManifestEvidence:
    digest: str
    platforms: tuple[str, ...]


def candidate_coordinates(
    *,
    ref_name: str,
    commit_sha: str,
    owner: str,
    image_name: str,
    tree_sha: str,
    tag_suffix: str = "",
) -> CandidateCoordinates:
    short = commit_sha[:12]
    if not re.fullmatch(r"[0-9a-f]{12}", short):
        raise ValueError("commit SHA must start with at least 12 lowercase hex characters")
    if not re.fullmatch(r"[0-9a-f]{40}", tree_sha):
        raise ValueError("source tree SHA must be 40 lowercase hex characters")
    if match := re.fullmatch(r"pull-request/(\d+)", ref_name):
        tag = f"pr-{match.group(1)}-{short}"
    elif ref_name == "develop":
        tag = f"develop-{short}"
    else:
        safe_ref = re.sub(r"[^A-Za-z0-9_.-]+", "-", ref_name).strip("-")
        if not safe_ref:
            raise ValueError(f"ref name {ref_name!r} cannot form a container tag")
        tag = f"{safe_ref}-{short}"
    normalized_owner = owner.lower()
    if not normalized_owner or "/" in normalized_owner:
        raise ValueError(f"invalid GHCR owner {owner!r}")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", image_name):
        raise ValueError(f"invalid image name {image_name!r}")
    if tag_suffix and not re.fullmatch(r"-[A-Za-z0-9_.-]+", tag_suffix):
        raise ValueError(
            f"tag suffix must be empty or start with '-' and contain only "
            f"tag-safe characters, got {tag_suffix!r}"
        )
    return CandidateCoordinates(
        image=f"ghcr.io/{normalized_owner}/vss/{image_name}",
        tag=f"{tag}{tag_suffix}",
        tree_sha=tree_sha,
        content_tag=f"tree-{tree_sha}{tag_suffix}",
    )


def validate_manifest(
    manifest: dict, expected_platforms: list[str]
) -> ManifestEvidence:
    digest = str(manifest.get("digest") or "")
    if not DIGEST_RE.fullmatch(digest):
        raise ValueError(f"manifest has invalid digest {digest!r}")
    actual = sorted(
        {
            f"{platform.get('os')}/{platform.get('architecture')}"
            for item in manifest.get("manifests", [])
            if isinstance(item, dict)
            and isinstance((platform := item.get("platform")), dict)
            and platform.get("os") not in (None, "unknown")
            and platform.get("architecture") not in (None, "unknown")
        }
    )
    expected = sorted(set(expected_platforms))
    if actual != expected:
        raise ValueError(
            f"manifest platform set {actual!r} does not match inventory {expected!r}"
        )
    return ManifestEvidence(digest=digest, platforms=tuple(actual))


def command_runner(command: list[str]) -> str:
    result = subprocess.run(command, text=True, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(
            f"{command[0]} command failed with exit {result.returncode}: "
            f"{detail[:500]}"
        )
    return result.stdout.strip()


def inspect_manifest(ref: str, runner: Runner = command_runner) -> dict:
    output = runner(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            ref,
            "--format",
            "{{json .Manifest}}",
        ]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise ValueError(f"registry returned invalid manifest JSON for {ref}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"registry returned unexpected manifest shape for {ref}")
    return payload


def write_github_outputs(path: Path, values: dict[str, str]) -> None:
    with path.open("a") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    metadata = subparsers.add_parser("metadata")
    metadata.add_argument("--ref-name", required=True)
    metadata.add_argument("--commit-sha", required=True)
    metadata.add_argument("--owner", required=True)
    metadata.add_argument("--image-name", required=True)
    metadata.add_argument("--tree-sha", required=True)
    metadata.add_argument("--tag-suffix", default="")
    metadata.add_argument("--github-output", type=Path, required=True)

    verify = subparsers.add_parser("verify-manifest")
    verify.add_argument("--ref", required=True)
    verify.add_argument("--platforms", required=True)
    verify.add_argument("--github-output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "metadata":
        coordinates = candidate_coordinates(
            ref_name=args.ref_name,
            commit_sha=args.commit_sha,
            owner=args.owner,
            image_name=args.image_name,
            tree_sha=args.tree_sha,
            tag_suffix=args.tag_suffix,
        )
        write_github_outputs(
            args.github_output,
            {
                "tag": coordinates.tag,
                "tree_sha": coordinates.tree_sha,
                "image": coordinates.image,
                "content_tag": coordinates.content_tag,
            },
        )
        print(
            f"[container-build-plan] candidate={coordinates.image}:"
            f"{coordinates.tag} tree={coordinates.tree_sha}"
        )
        return 0

    manifest = inspect_manifest(args.ref)
    evidence = validate_manifest(
        manifest, [item for item in args.platforms.split(",") if item]
    )
    write_github_outputs(args.github_output, {"digest": evidence.digest})
    print(
        f"[container-build-plan] ref={args.ref} digest={evidence.digest} "
        f"platforms={','.join(evidence.platforms)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            f"[container-build-plan] ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        raise
