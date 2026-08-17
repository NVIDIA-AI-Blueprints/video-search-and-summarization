#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Release-set tooling for the VSS container flow.

A *release set* is one complete, immutable inventory of container images for a
source commit: every first-party image resolves to an immutable reference, and
unchanged components are explicitly carried forward instead of being silently
absent. The manifest — not a moving tag — is what acceptance tests and the
weekly promotion point at
(deploy/docker/release-set.schema.json documents the contract).

Subcommands
===========

closure
    Validate that every first-party ``image:`` reference under deploy/docker
    is classified in deploy/docker/container-inventory.json. Run in CI so a
    new first-party compose reference cannot be silently out of scope.

fragment
    Emit one validated release-set fragment for an image the build workflow
    just pushed (called from build-dev-images.yml, one fragment per image).

assemble
    Merge fragments into a complete release set: every in-scope inventory
    image is present either from a fragment (freshly built/mirrored) or as an
    explicit ``reuse-pinned`` entry at its current committed coordinate.
    Computes the immutable ``release_set_id`` and validates the result.

validate
    Re-validate an existing release-set file (schema semantics + id).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import (  # noqa: E402
    DEPLOY_DIR,
    IMAGE_LINE_RE,
    discover_compose_files,
    discover_env_files,
    image_name,
    read_env_file,
    strip_quotes,
)
from compose_image_golden import load_containers_env, resolve_nested  # noqa: E402

INVENTORY_FILE = DEPLOY_DIR / "container-inventory.json"

DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TREE_RE = re.compile(r"^[0-9a-f]{40}$")

STRATEGIES = ("build", "mirror", "reuse-pinned")
# Inventory strategies that must be represented in every release set.
IN_SCOPE_STRATEGIES = ("build", "mirror")


def load_inventory(repo_root: Path) -> dict:
    return json.loads((repo_root / INVENTORY_FILE).read_text())


def inventory_by_name(inventory: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in inventory["images"]}


def inventory_by_compose_name(inventory: dict) -> dict[str, dict]:
    mapping: dict[str, dict] = {}
    for entry in inventory["images"]:
        for compose_name in entry.get("compose_image_names", [entry["name"]]):
            mapping[compose_name] = entry
    return mapping


def first_party_refs(repo_root: Path, inventory: dict) -> list[tuple[str, str]]:
    """``(compose_rel_path, resolved_ref)`` for every image ref whose
    committed defaults resolve into a first-party registry root."""
    roots = tuple(inventory["first_party_registry_roots"])
    by_compose_name = inventory_by_compose_name(inventory)
    containers_env_path = repo_root / DEPLOY_DIR / "containers.env"
    base_env = (
        load_containers_env(containers_env_path)
        if containers_env_path.exists()
        else {}
    )
    env_variants = [base_env]
    env_variants.extend(
        {**base_env, **read_env_file(path)}
        for path in discover_env_files(repo_root)
        if path != containers_env_path
    )
    refs: set[tuple[str, str]] = set()
    for compose_file in discover_compose_files(repo_root):
        rel = compose_file.relative_to(repo_root).as_posix()
        for line in compose_file.read_text().splitlines():
            match = IMAGE_LINE_RE.match(line)
            if not match:
                continue
            raw_ref = strip_quotes(match.group("ref"))
            candidates = {resolve_nested(raw_ref, {})}
            provisional = resolve_nested(raw_ref, base_env)
            entry = by_compose_name.get(image_name(provisional))
            if entry and entry.get("strategy") in IN_SCOPE_STRATEGIES:
                candidates.update(resolve_nested(raw_ref, env) for env in env_variants)
            concrete = {ref for ref in candidates if "${" not in ref}
            for resolved in concrete or candidates:
                if resolved.startswith(roots):
                    refs.add((rel, resolved))
    return sorted(refs)


# ---------------------------------------------------------------------------
# closure
# ---------------------------------------------------------------------------


def cmd_closure(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    inventory = load_inventory(repo_root)
    by_compose_name = inventory_by_compose_name(inventory)

    refs = first_party_refs(repo_root, inventory)
    unclassified: list[str] = []
    used: set[str] = set()
    for rel, resolved in refs:
        basename = image_name(resolved)
        entry = by_compose_name.get(basename)
        if entry is None:
            unclassified.append(f"{rel}: {resolved} (basename {basename!r})")
        else:
            used.add(entry["name"])

    if unclassified:
        print(
            "FAIL: first-party image references not classified in "
            f"{INVENTORY_FILE.as_posix()}:\n"
        )
        print("\n".join(f"  - {item}" for item in unclassified))
        print(
            "\nAdd an inventory entry (strategy build/mirror/external-pin) so the"
            " image is explicitly in or out of release-set scope."
        )
        return 1

    unused = sorted(
        entry["name"] for entry in inventory["images"] if entry["name"] not in used
    )
    if unused:
        print(
            "note: inventory entries with no compose reference (stale?): "
            + ", ".join(unused)
        )

    print(
        f"OK: {len(refs)} first-party compose refs are all classified in the "
        f"inventory ({len(used)} logical images in use)."
    )
    return 0


# ---------------------------------------------------------------------------
# fragment
# ---------------------------------------------------------------------------


def build_fragment(
    inventory: dict,
    *,
    name: str,
    image: str,
    tag: str,
    digest: str,
    platforms: list[str],
    strategy: str = "build",
    source_tree_sha: str | None = None,
    upstream_digest: str | None = None,
) -> dict:
    """Validate one built/mirrored image against the inventory and return its
    release-set fragment. Raises ``ValueError`` with the full problem list."""
    problems: list[str] = []
    entry = inventory_by_name(inventory).get(name)
    if entry is None:
        problems.append(f"unknown image name {name!r} (not in the inventory)")
    if strategy not in ("build", "mirror"):
        problems.append(f"fragment strategy must be build|mirror, got {strategy!r}")
    if not DIGEST_RE.match(digest or ""):
        problems.append(f"digest must match sha256:<64 hex>, got {digest!r}")
    if ":" in image.rsplit("/", 1)[-1] or "@" in image:
        problems.append(f"image must be registry/name without tag, got {image!r}")
    if not tag:
        problems.append("tag must be non-empty")

    if entry is not None:
        required = set(entry.get("platforms", []))
        missing = required - set(platforms)
        if missing:
            problems.append(
                f"{name}: platforms {sorted(missing)} required by the inventory "
                f"are missing from the built manifest ({sorted(platforms)})"
            )
        if strategy == "build":
            if entry.get("strategy") != "build":
                problems.append(
                    f"{name}: inventory strategy is {entry.get('strategy')!r}; "
                    "a build fragment is not allowed"
                )
            if not source_tree_sha or not TREE_RE.match(source_tree_sha):
                problems.append(
                    f"{name}: build fragments require source_tree_sha "
                    f"(git rev-parse <commit>:{entry.get('source_path')}), "
                    f"got {source_tree_sha!r}"
                )
        if strategy == "mirror" and not upstream_digest:
            problems.append(f"{name}: mirror fragments require upstream_digest")

    if problems:
        raise ValueError("\n".join(problems))

    assert entry is not None
    return {
        "name": name,
        "strategy": strategy,
        "image": image,
        "tag": tag,
        "digest": digest,
        "platforms": sorted(platforms),
        "source_path": entry.get("source_path"),
        "source_tree_sha": source_tree_sha,
        "upstream_digest": upstream_digest,
    }


def cmd_fragment(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    inventory = load_inventory(repo_root)
    try:
        fragment = build_fragment(
            inventory,
            name=args.name,
            image=args.image,
            tag=args.tag,
            digest=args.digest,
            platforms=[p for p in args.platforms.split(",") if p],
            strategy=args.strategy,
            source_tree_sha=args.source_tree_sha,
            upstream_digest=args.upstream_digest,
        )
    except ValueError as exc:
        print(f"FAIL: invalid fragment:\n{exc}", file=sys.stderr)
        return 1
    output = json.dumps(fragment, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output)
        print(f"Wrote fragment for {args.name} to {args.out}")
    else:
        print(output, end="")
    return 0


# ---------------------------------------------------------------------------
# assemble
# ---------------------------------------------------------------------------


def _split_ref(resolved_ref: str) -> tuple[str, str]:
    """Split a resolved ``registry/name:tag`` ref into (image, tag). A tag
    that is an unresolved ``${VAR}`` expression is preserved as-is."""
    no_digest = resolved_ref.split("@", 1)[0]
    slash = no_digest.rfind("/")
    colon = no_digest.rfind(":")
    if colon > slash:
        return no_digest[:colon], no_digest[colon + 1 :]
    return no_digest, ""


def reuse_entries(
    repo_root: Path, inventory: dict, built_names: set[str]
) -> tuple[list[dict], list[str]]:
    """Explicit ``reuse-pinned`` entries for every in-scope inventory image
    that has no fragment, at its current committed coordinate. Returns
    ``(entries, problems)``."""
    by_compose_name = inventory_by_compose_name(inventory)
    pinned: dict[str, set[tuple[str, str]]] = {}
    for _, resolved in first_party_refs(repo_root, inventory):
        entry = by_compose_name.get(image_name(resolved))
        if entry is None:
            continue
        pinned.setdefault(entry["name"], set()).add(_split_ref(resolved))

    entries: list[dict] = []
    problems: list[str] = []
    for entry in inventory["images"]:
        name = entry["name"]
        if name in built_names or entry.get("strategy") not in IN_SCOPE_STRATEGIES:
            continue
        coordinates = sorted(pinned.get(name, set()))
        if not coordinates:
            problems.append(
                f"{name}: in-scope inventory image has no resolvable compose "
                "coordinate to carry forward"
            )
            continue
        if len(coordinates) > 1:
            problems.append(
                f"{name}: ambiguous pinned coordinates {coordinates}; "
                "normalize the compose references before assembling"
            )
            continue
        image, tag = coordinates[0]
        entries.append(
            {
                "name": name,
                "strategy": "reuse-pinned",
                "image": image,
                "tag": tag,
                "digest": None,
                "platforms": sorted(entry.get("platforms", [])),
                "source_path": entry.get("source_path"),
                "source_tree_sha": None,
                "upstream_digest": None,
            }
        )
    return entries, problems


def compute_release_set_id(release_set: dict) -> str:
    subject = {**release_set, "release_set_id": ""}
    canonical = json.dumps(subject, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def validate_release_set(release_set: dict, inventory: dict) -> list[str]:
    """Schema-semantic validation (stdlib implementation of
    deploy/docker/release-set.schema.json) plus inventory cross-checks."""
    problems: list[str] = []
    if release_set.get("schema_version") != 1:
        problems.append("schema_version must be 1")

    source = release_set.get("source") or {}
    if not source.get("repository"):
        problems.append("source.repository is required")
    if not COMMIT_RE.match(source.get("commit") or ""):
        problems.append("source.commit must be a 40-hex commit SHA")

    names = inventory_by_name(inventory)
    seen: set[str] = set()
    for item in release_set.get("images", []):
        name = item.get("name", "<missing>")
        if name in seen:
            problems.append(f"{name}: duplicate entry")
        seen.add(name)
        if name not in names:
            problems.append(f"{name}: not in the inventory")
        if item.get("strategy") not in STRATEGIES:
            problems.append(f"{name}: invalid strategy {item.get('strategy')!r}")
        tag = item.get("tag")
        if not tag:
            problems.append(f"{name}: tag is required")
        elif "${" in tag:
            problems.append(f"{name}: tag contains unresolved variable {tag!r}")
        image = item.get("image") or ""
        if "${" in image:
            problems.append(f"{name}: image contains unresolved variable {image!r}")
        digest = item.get("digest")
        if item.get("strategy") in ("build", "mirror"):
            if not DIGEST_RE.match(digest or ""):
                problems.append(
                    f"{name}: strategy {item.get('strategy')} requires an "
                    f"immutable sha256 digest, got {digest!r}"
                )
        elif digest is not None and not DIGEST_RE.match(digest):
            problems.append(f"{name}: malformed digest {digest!r}")
        if item.get("strategy") == "build":
            tree = item.get("source_tree_sha")
            if not tree or not TREE_RE.match(tree):
                problems.append(f"{name}: build entries require source_tree_sha")

    missing = [
        entry["name"]
        for entry in inventory["images"]
        if entry.get("strategy") in IN_SCOPE_STRATEGIES and entry["name"] not in seen
    ]
    if missing:
        problems.append(
            "incomplete set — in-scope inventory images absent: " + ", ".join(missing)
        )

    if not release_set.get("images"):
        problems.append("images must be non-empty")

    expected_id = compute_release_set_id(release_set)
    if release_set.get("release_set_id") != expected_id:
        problems.append(
            f"release_set_id mismatch: recorded {release_set.get('release_set_id')!r}, "
            f"computed {expected_id!r}"
        )
    return problems


def cmd_assemble(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    inventory = load_inventory(repo_root)

    fragments: list[dict] = []
    if args.fragments and args.fragments.is_dir():
        for path in sorted(args.fragments.glob("**/*.json")):
            fragments.append(json.loads(path.read_text()))
    built_names = {fragment["name"] for fragment in fragments}

    reused, problems = reuse_entries(repo_root, inventory, built_names)
    if problems:
        print("FAIL: cannot assemble a complete release set:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    release_set = {
        "schema_version": 1,
        "release_set_id": "",
        "source": {
            key: value
            for key, value in {
                "repository": args.repository,
                "commit": args.commit,
                "ref": args.ref,
                "workflow_run": args.workflow_run,
            }.items()
            if value
        },
        "images": sorted(fragments + reused, key=lambda item: item["name"]),
    }
    release_set["release_set_id"] = compute_release_set_id(release_set)

    errors = validate_release_set(release_set, inventory)
    if errors:
        print("FAIL: assembled release set is invalid:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    output = json.dumps(release_set, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output)
        print(
            f"Wrote release set {release_set['release_set_id']} "
            f"({len(release_set['images'])} images, {len(fragments)} built, "
            f"{len(reused)} reused) to {args.out}"
        )
    else:
        print(output, end="")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    inventory = load_inventory(repo_root)
    release_set = json.loads(args.file.read_text())
    errors = validate_release_set(release_set, inventory)
    if errors:
        print(f"FAIL: {args.file} is invalid:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(f"OK: {args.file} is a valid, complete release set.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("closure", help="validate inventory closure over deploy/docker")

    fragment = sub.add_parser("fragment", help="emit one validated fragment")
    fragment.add_argument("--name", required=True)
    fragment.add_argument("--image", required=True, help="registry/name, no tag")
    fragment.add_argument("--tag", required=True)
    fragment.add_argument("--digest", required=True)
    fragment.add_argument(
        "--platforms",
        required=True,
        help="comma-separated, e.g. linux/amd64,linux/arm64",
    )
    fragment.add_argument("--strategy", choices=("build", "mirror"), default="build")
    fragment.add_argument("--source-tree-sha")
    fragment.add_argument("--upstream-digest")
    fragment.add_argument("--out", type=Path)

    assemble = sub.add_parser("assemble", help="assemble a complete release set")
    assemble.add_argument("--fragments", type=Path, help="directory of fragment JSONs")
    assemble.add_argument("--repository", required=True)
    assemble.add_argument("--commit", required=True)
    assemble.add_argument("--ref")
    assemble.add_argument("--workflow-run")
    assemble.add_argument("--out", type=Path)

    validate = sub.add_parser("validate", help="validate an existing release set")
    validate.add_argument("--file", type=Path, required=True)

    args = parser.parse_args()
    return {
        "closure": cmd_closure,
        "fragment": cmd_fragment,
        "assemble": cmd_assemble,
        "validate": cmd_validate,
    }[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
