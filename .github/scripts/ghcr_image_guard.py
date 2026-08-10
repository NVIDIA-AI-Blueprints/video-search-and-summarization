#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Immutability and provenance guard around GHCR candidate pushes.

preflight
    Run BEFORE building: the target tag must not exist, or must already carry
    the exact source-tree SHA we are about to build (an idempotent re-run).
    Emits ``skip=true`` in the latter case so the workflow can no-op instead
    of overwriting. Any other collision fails: candidate tags are immutable.

verify
    Run AFTER pushing: read the image's config labels back from the registry
    (the same read path check_container_tag_source.py uses) and require that
    ``com.nvidia.vss.source_tree_sha`` equals the source content hash, not the
    commit SHA. For single-source images this remains
    ``git rev-parse HEAD:<source_path>``. This proves at build time that the
    container-source gate will accept the candidate, instead of discovering a
    contract mismatch at promotion time.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import (  # noqa: E402
    ImageManifestLabels,
    read_image_manifest_labels,
)


def preflight_decision(
    labels: ImageManifestLabels | None,
    reason: str | None,
    expected_tree_sha: str,
    *,
    can_fallback: bool = False,
) -> tuple[str, str]:
    """Return ``(action, message)`` where action is ``build`` | ``skip`` |
    ``fail``. Pure function so the collision policy is unit-testable.

    Fail-closed: only a clean 404 on the manifest fetch proves the tag is
    free. A network/auth error means we cannot prove immutability, and an
    existing-but-unlabelled tag is foreign content — both refuse the build.
    """
    if labels is None and reason:
        if "index fetch failed" in reason and "returned 404" in reason:
            return "build", "tag does not exist yet; building"
        if not can_fallback:
            return (
                "fail",
                f"cannot prove the tag is free ({reason}); refusing to build blind",
            )
    if labels and labels.source_tree_sha == expected_tree_sha:
        return (
            "skip",
            "tag already exists with the same source_tree_sha; idempotent re-run",
        )
    found = labels.source_tree_sha if labels else f"<no label: {reason}>"
    return (
        "fail",
        "tag already exists with DIFFERENT content "
        f"(source_tree_sha {found} != expected {expected_tree_sha}). "
        "Candidate tags are immutable: never overwrite; pick a new tag.",
    )


def reuse_decision(
    labels: ImageManifestLabels | None,
    reason: str | None,
    expected_tree_sha: str,
) -> tuple[bool, str]:
    """Return ``(reuse, message)`` for the content-addressed re-tag path.

    ``reuse`` is True only when a content-addressed image already exists AND
    carries the exact source-tree SHA we would otherwise build — in which case
    the caller can re-tag from it instead of rebuilding identical content.

    Fail-*open* to a rebuild: a missing content tag, a mislabelled one, or any
    fetch error just means "no safe shortcut — build normally". Unlike
    preflight, this can never fail the job; the worst case is the status quo.
    """
    if labels and labels.source_tree_sha == expected_tree_sha:
        return True, "content-addressed image already published; re-tagging instead of rebuilding"
    detail = labels.source_tree_sha if labels else f"<no label: {reason}>"
    return False, f"no reusable image for this content ({detail}); building"


def verify_decision(
    labels: ImageManifestLabels | None,
    reason: str | None,
    *,
    expected_tree_sha: str,
    expected_source_path: str,
    expected_image_name: str,
) -> tuple[bool, str]:
    """Return ``(ok, message)`` for the post-push provenance check."""
    if labels is None:
        return False, f"could not read image labels back from the registry: {reason}"
    problems = []
    if labels.source_tree_sha != expected_tree_sha:
        problems.append(
            f"source_tree_sha={labels.source_tree_sha!r} != expected source content "
            f"hash {expected_tree_sha!r} (labels must carry the source content "
            "hash, not the commit SHA)"
        )
    if labels.source_path != expected_source_path:
        problems.append(
            f"source_path={labels.source_path!r} != {expected_source_path!r}"
        )
    if labels.image_name != expected_image_name:
        problems.append(f"image_name={labels.image_name!r} != {expected_image_name!r}")
    if problems:
        return False, "; ".join(problems)
    return (
        True,
        "labels match the source content; the container-source gate will accept this candidate",
    )


def _emit_output(key: str, value: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"{key}={value}\n")


def cmd_preflight(args: argparse.Namespace) -> int:
    labels, reason, can_fallback = read_image_manifest_labels(args.ref)
    action, message = preflight_decision(
        labels,
        reason,
        args.expect_tree_sha,
        can_fallback=can_fallback,
    )
    print(f"{args.ref}: {message}")
    _emit_output("skip", "true" if action == "skip" else "false")
    return 1 if action == "fail" else 0


def cmd_reuse(args: argparse.Namespace) -> int:
    labels, reason, _ = read_image_manifest_labels(args.ref)
    reuse, message = reuse_decision(labels, reason, args.expect_tree_sha)
    print(f"{args.ref}: {message}")
    _emit_output("reuse", "true" if reuse else "false")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    labels, reason, _ = read_image_manifest_labels(args.ref)
    ok, message = verify_decision(
        labels,
        reason,
        expected_tree_sha=args.expect_tree_sha,
        expected_source_path=args.expect_source_path,
        expected_image_name=args.expect_image_name,
    )
    print(f"{args.ref}: {'OK' if ok else 'FAIL'} — {message}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    preflight = sub.add_parser("preflight", help="refuse overwriting a candidate tag")
    preflight.add_argument("--ref", required=True, help="registry/name:tag")
    preflight.add_argument("--expect-tree-sha", required=True)

    reuse = sub.add_parser(
        "reuse", help="check whether a content-addressed image can be re-tagged"
    )
    reuse.add_argument("--ref", required=True, help="registry/name:content-tag")
    reuse.add_argument("--expect-tree-sha", required=True)

    verify = sub.add_parser("verify", help="verify pushed labels match the source")
    verify.add_argument("--ref", required=True)
    verify.add_argument("--expect-tree-sha", required=True)
    verify.add_argument("--expect-source-path", required=True)
    verify.add_argument("--expect-image-name", required=True)

    args = parser.parse_args()
    return {"preflight": cmd_preflight, "reuse": cmd_reuse, "verify": cmd_verify}[
        args.command
    ](args)


if __name__ == "__main__":
    raise SystemExit(main())
