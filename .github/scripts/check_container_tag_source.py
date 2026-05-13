#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Check that a deploy image tag points at the current source subtree.

This mirrors the ci-vss-oss rebuild decision: resolve the image tag from the
deploy compose files, extract the source commit suffix from the tag, and compare
that commit's subtree hash with the current checkout's source subtree hash.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


TAG_COMMIT_RE = re.compile(r"(?:^|[-_/])(?P<sha>[0-9a-f]{7,40})(?:$|[+._-])", re.IGNORECASE)
IMAGE_LINE_RE = re.compile(r"^\s*image:\s*(?P<ref>\S+)\s*(?:#.*)?$")
COMPOSE_VAR_RE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?:(?P<op>:?[-?])(?P<value>[^}]*))?\}"
)


@dataclass(frozen=True)
class ImageConfig:
    image_name: str
    source_path: Path


IMAGE_CONFIGS = {
    "vss-agent": ImageConfig(image_name="vss-agent", source_path=Path("services/agent")),
    "vss-agent-ui": ImageConfig(image_name="vss-agent-ui", source_path=Path("services/ui")),
}

DEPLOY_DIR = Path("deploy/docker")


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_stdout(repo: Path, *args: str) -> str:
    return run_git(repo, *args).stdout.strip()


def strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = strip_quotes(value)
    return values


def image_name(ref: str) -> str:
    ref_without_digest = ref.split("@", 1)[0]
    last_component = ref_without_digest.rsplit("/", 1)[-1]
    return last_component.split(":", 1)[0]


def image_tag(ref: str) -> str | None:
    ref_without_digest = ref.split("@", 1)[0]
    slash_index = ref_without_digest.rfind("/")
    colon_index = ref_without_digest.rfind(":")
    if colon_index <= slash_index:
        return None
    return ref_without_digest[colon_index + 1 :]


def commit_prefix_from_tag(tag: str | None) -> str | None:
    if not tag:
        return None
    matches = list(TAG_COMMIT_RE.finditer(tag))
    if not matches:
        return None
    return matches[-1].group("sha").lower()


def find_image_refs(compose_file: Path, expected_image_name: str) -> list[str]:
    refs: list[str] = []
    for line in compose_file.read_text().splitlines():
        match = IMAGE_LINE_RE.match(line)
        if not match:
            continue
        ref = strip_quotes(match.group("ref"))
        if image_name(ref) == expected_image_name and ref not in refs:
            refs.append(ref)
    return refs


def resolve_compose_vars(text: str, env: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        op = match.group("op")
        fallback = match.group("value") or ""
        value = env.get(name)

        if op == ":-":
            return value if value else fallback
        if op == "-":
            return value if value is not None else fallback
        if op in (":?", "?"):
            if value:
                return value
            missing.append(name)
            return match.group(0)
        if value is None:
            missing.append(name)
            return match.group(0)
        return value

    resolved = COMPOSE_VAR_RE.sub(replace, text)
    return resolved, tuple(sorted(set(missing)))


def resolve_commit(repo: Path, prefix: str) -> str | None:
    result = run_git(repo, "rev-parse", "--verify", f"{prefix}^{{commit}}", check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def tree_sha(repo: Path, commit: str, source_path: Path) -> str | None:
    result = run_git(repo, "rev-parse", f"{commit}:{source_path.as_posix()}", check=False)
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def discover_compose_files(repo_root: Path) -> list[Path]:
    deploy = repo_root / DEPLOY_DIR
    files: set[Path] = set()
    for pattern in ("**/*.yml", "**/*.yaml"):
        for path in deploy.glob(pattern):
            if path.is_file():
                files.add(path)
    return sorted(files)


def discover_env_files(repo_root: Path) -> list[Path]:
    deploy = repo_root / DEPLOY_DIR
    return sorted(p for p in deploy.glob("**/.env") if p.is_file())


@dataclass(frozen=True)
class ResolvedImage:
    resolved_ref: str
    origins: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class UnresolvedImage:
    compose_rel: str
    env_rel: str | None
    raw_ref: str
    missing: tuple[str, ...]


def collect_resolved_images(
    repo_root: Path,
    config: ImageConfig,
    compose_files: list[Path],
    env_files: list[Path],
) -> tuple[list[ResolvedImage], list[UnresolvedImage]]:
    env_caches = {ef: read_env_file(ef) for ef in env_files}
    by_resolved: dict[str, list[tuple[str, str | None]]] = {}
    unresolved: list[UnresolvedImage] = []

    for compose_file in compose_files:
        raw_refs = find_image_refs(compose_file, config.image_name)
        if not raw_refs:
            continue
        compose_rel = str(compose_file.relative_to(repo_root))
        for raw_ref in raw_refs:
            _, needed = resolve_compose_vars(raw_ref, {})
            if not needed:
                resolved, _ = resolve_compose_vars(raw_ref, dict(os.environ))
                by_resolved.setdefault(resolved, []).append((compose_rel, None))
                continue

            any_applicable = False
            for env_file in env_files:
                env_values = env_caches[env_file]
                if not all(name in env_values for name in needed):
                    continue
                any_applicable = True
                env_rel = str(env_file.relative_to(repo_root))
                resolved, missing = resolve_compose_vars(raw_ref, {**env_values, **os.environ})
                if missing:
                    unresolved.append(UnresolvedImage(compose_rel, env_rel, raw_ref, missing))
                else:
                    by_resolved.setdefault(resolved, []).append((compose_rel, env_rel))

            if not any_applicable:
                unresolved.append(UnresolvedImage(compose_rel, None, raw_ref, tuple(sorted(needed))))

    images = [
        ResolvedImage(resolved_ref=ref, origins=tuple(origins))
        for ref, origins in sorted(by_resolved.items())
    ]
    return images, unresolved


def check_resolved_image(
    repo_root: Path,
    config: ImageConfig,
    item: ResolvedImage,
    current_commit: str,
    current_tree: str,
    idx: int,
    total: int,
) -> bool:
    src = config.source_path.as_posix()
    print(f"[{idx}/{total}] {item.resolved_ref}")
    print(f"  produced by {len(item.origins)} (compose, env) combination(s):")
    for compose_rel, env_rel in item.origins:
        suffix = f"  ←  {env_rel}" if env_rel else "  (no env vars)"
        print(f"    - {compose_rel}{suffix}")

    tag = image_tag(item.resolved_ref)
    prefix = commit_prefix_from_tag(tag)
    print(f"  tag:           {tag or '<missing>'}")
    if not prefix:
        print("  [FAIL] tag does not contain a git commit SHA suffix; cannot verify source.")
        return False

    tag_commit = resolve_commit(repo_root, prefix)
    if not tag_commit:
        print(f"  built from:    {prefix}  (NOT found in this checkout)")
        print("  [FAIL] could not resolve this SHA locally. Try: git fetch --no-tags origin <branch>")
        return False
    print(f"  built from:    {tag_commit}")

    tag_tree = tree_sha(repo_root, tag_commit, config.source_path)
    if not tag_tree:
        print(f"  [FAIL] could not read {src}/ at commit {tag_commit[:12]}.")
        return False

    print(f"  comparing {src}/:")
    print(f"    at HEAD              ({current_commit[:12]}):  {current_tree}")
    print(f"    at container commit  ({tag_commit[:12]}):  {tag_tree}")
    if tag_tree == current_tree:
        print("    → identical")
        print("  [PASS]")
        return True
    print("    → DIFFERENT")
    print(f"  [FAIL] {config.image_name} container does NOT match the current {src}/ source.")
    print(f"         See the diff:  git diff {tag_commit[:12]} HEAD -- {src}")
    print()
    print("  How to fix:")
    print("    1. Find the 'Trigger Downstream Pipeline' job on this PR's CI run.")
    print("       It links to a downstream pipeline that builds + promotes new")
    print(f"       {config.image_name} images from the current source.")
    print("    2. In that downstream pipeline, open the 'promote' job and copy the")
    print(f"       newly promoted {config.image_name} image tag from its output.")
    print(f"    3. Update the {config.image_name} tag in the (compose, env)")
    print("       combination(s) listed above so they reference the new tag,")
    print("       commit, and push.")
    return False


def verify(repo_root: Path, config: ImageConfig) -> int:
    src = config.source_path.as_posix()
    bar = "=" * 78
    print(bar)
    print(f" {config.image_name}  —  check every deployable container tag against {src}/")
    print(bar)
    print()

    current_commit = git_stdout(repo_root, "rev-parse", "HEAD")
    current_branch = git_stdout(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    current_tree = tree_sha(repo_root, "HEAD", config.source_path)
    if not current_tree:
        print(f"ERROR: could not resolve HEAD:{src}", file=sys.stderr)
        return 1

    print("Current source (HEAD)")
    print(f"  branch:  {current_branch}")
    print(f"  commit:  {current_commit}")
    print(f"  folder:  {src}/  (content hash: {current_tree})")
    print()

    compose_files = discover_compose_files(repo_root)
    env_files = discover_env_files(repo_root)
    print(
        f"Scanned {len(compose_files)} compose file(s) and {len(env_files)} .env file(s) "
        f"under {DEPLOY_DIR.as_posix()}/."
    )
    print()

    images, unresolved = collect_resolved_images(repo_root, config, compose_files, env_files)

    if unresolved:
        print(f"WARNING: {len(unresolved)} unresolved image reference(s):")
        for item in unresolved:
            origin = f"{item.compose_rel}" + (f"  ←  {item.env_rel}" if item.env_rel else "")
            print(f"  - {origin}")
            print(f"      raw:      {item.raw_ref}")
            print(f"      missing:  {', '.join(item.missing)}")
        print()

    if not images:
        print(f"ERROR: no resolvable {config.image_name} image references found.", file=sys.stderr)
        return 1

    print(f"Found {len(images)} unique {config.image_name} image reference(s) to check:")
    print()

    failures = 0
    for idx, item in enumerate(images, start=1):
        if not check_resolved_image(repo_root, config, item, current_commit, current_tree, idx, len(images)):
            failures += 1
        print()

    print(bar)
    if failures or unresolved:
        problems = []
        if failures:
            problems.append(f"{failures} failure(s) out of {len(images)} unique ref(s)")
        if unresolved:
            problems.append(f"{len(unresolved)} unresolved ref(s)")
        print(f"Result: {'; '.join(problems)}.")
        return 1
    print(f"Result: all {len(images)} unique ref(s) match.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-name", choices=sorted(IMAGE_CONFIGS), required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    return verify(args.repo_root.resolve(), IMAGE_CONFIGS[args.image_name])


if __name__ == "__main__":
    raise SystemExit(main())
