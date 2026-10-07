#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Stamp every hand-readable version field with the repository's one version.

The agent and the CLI get their version from hatch-vcs: ``git describe --tags
--match 'v[0-9]*'`` off the nearest release tag. Three other places have to
say the same thing and are plain text, so nothing derives them at install
time -- this script writes them from the same tag, so they are never typed by
hand again:

* ``skills/**/SKILL.md`` -- ``metadata.version`` in the frontmatter (what a
  reader, a harness image and the ``requires-vss`` checker see);
* ``deploy/docker/containers.env`` -- the ``VSS_VERSION="..."`` line the
  Compose HAProxy edge answers ``GET /api/v1/version`` with;
* ``values.yaml`` of every chart that owns a ``templates/vss-ingress.yaml`` --
  the top-level ``vssVersion: "..."`` the Helm HAProxy ingress answers with.

The stamp is the *release line* rendered as SemVer -- ``3.3.0-rc0`` off the
tag ``v3.3.0rc0``, ``3.3.0`` off ``v3.3.0`` -- with no commit distance, sha or
tree: a deployment's identity is the commit it ships from, which the images
already record, and a distance would make these files change on every merge.
The number only moves when the line does.

No bot writes these files: a person runs this script and lands the result in
an ordinary reviewed PR. ``--check`` is the merge gate (ci.yml), and it is
deliberately one-sided so the release order works:

* every field must declare the *same* version -- a hand edit to one file, or a
  new skill or chart that copied an old number, fails;
* that version must be *equal to or newer than* the nearest ``v*`` tag's
  (SemVer precedence) -- so after a new tag lands, the gate fails until someone
  lands the stamp PR, and a release PR stamping ``3.3.0`` passes while develop
  is still on ``v3.3.0rc0``, which is what lets the tag be cut on a commit that
  already agrees with it.

    stamp_versions.py                  rewrite every field to the nearest tag's version
    stamp_versions.py --version 3.3.0  rewrite every field to 3.3.0 (the release PR)
    stamp_versions.py --check          exit 1 unless the fields agree and are >= the tag

A file that should carry a version and has no field to rewrite is an error
(exit 2), never a field invented: a chart with an ingress template must
declare ``vssVersion``, a skill must declare ``metadata.version``.

Standard library only; ``pep440_to_semver`` is imported from libs/vss/core so
the rendering cannot drift from the endpoint's.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "libs" / "vss" / "core" / "src"))

from vss_core.version import SEMVER_PATTERN, pep440_to_semver  # noqa: E402

# The same match every pyproject's hatch-vcs uses: release tags only, so the
# nightly-*, dev-* and pr* tags the repo also carries never become a version.
DESCRIBE = ("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*")

# Skills: only the frontmatter block is touched (first `---` fence to the next);
# metadata.version is the only indented `version:` key a SKILL.md carries, and
# the quote style already in the file is kept.
FRONTMATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)
VERSION_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]+)version:[ \t]*(?P<quote>[\"']?)(?P<value>[^\"'\n]*)(?P=quote)[ \t]*$",
    re.MULTILINE,
)
# Compose edge: a plain assignment, deliberately not `${VSS_VERSION:-...}`, so a
# shell variable cannot override the stamped value.
CONTAINERS_ENV = Path("deploy/docker/containers.env")
ENV_LINE_RE = re.compile(r'^VSS_VERSION="(?P<value>[^"$\n]*)"[ \t]*$', re.MULTILINE)
# Helm edge: a top-level key (no indent) in the ingress-owning chart's values.
VALUES_KEY_RE = re.compile(r'^vssVersion:[ \t]*"(?P<value>[^"\n]*)"[ \t]*$', re.MULTILINE)
INGRESS_TEMPLATE = Path("templates/vss-ingress.yaml")


def run_git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def derive_version(repo_root: Path) -> str:
    """The release line of ``HEAD`` as SemVer: the nearest ``v*`` tag, rendered.

    Raises ``RuntimeError`` when no release tag is reachable (a shallow clone)
    or the tag is not a bare release/pre-release: a wrong stamp is worse than
    no stamp, so this never guesses.
    """
    try:
        tag = run_git(repo_root, *DESCRIBE)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "no v* tag is reachable from HEAD (shallow clone? push one, e.g. v3.3.0rc0)"
        ) from exc
    version = pep440_to_semver(tag.removeprefix("v"))
    if version is None or "+" in version or ".dev." in version:
        raise RuntimeError(f"tag {tag!r} is not a bare release or pre-release (vX.Y.Z, vX.Y.ZrcN)")
    return version


def semver_key(version: str) -> tuple:
    """Sort key giving SemVer 2.0.0 precedence (section 11); build metadata ignored.

    A release outranks its pre-releases; pre-release identifiers compare
    numerically when numeric, lexically otherwise, numeric below alphanumeric,
    and a longer list outranks its own prefix: 3.3.0-rc0 < 3.3.0-rc0.dev.20 < 3.3.0.
    """
    core, _, _build = version.partition("+")
    release, _, prerelease = core.partition("-")
    major, minor, patch = (int(part) for part in release.split("."))
    if not prerelease:
        return (major, minor, patch, 1, ())
    identifiers = tuple(
        (0, int(ident), "") if ident.isdigit() else (1, 0, ident) for ident in prerelease.split(".")
    )
    return (major, minor, patch, 0, identifiers)


# --- targets -----------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """One kind of file that carries the version: how to find it, read it, write it."""

    name: str
    paths: Callable[[Path], list[Path]]
    current: Callable[[str], str | None]
    stamp: Callable[[str, str], str | None]


def _tracked(repo_root: Path, *patterns: str) -> list[Path]:
    listed = run_git(repo_root, "ls-files", "--", *patterns)
    return sorted({repo_root / line for line in listed.splitlines() if line})


def skill_files(repo_root: Path) -> list[Path]:
    """Every tracked ``SKILL.md`` under ``skills/``."""
    return _tracked(repo_root, "skills/**/SKILL.md", "skills/*/SKILL.md")


def skill_current(text: str) -> str | None:
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter is None:
        return None
    match = VERSION_LINE_RE.search(frontmatter.group("body"))
    return match.group("value") if match else None


def skill_stamp(text: str, version: str) -> str | None:
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter is None or VERSION_LINE_RE.search(frontmatter.group("body")) is None:
        return None

    def replace(match: re.Match[str]) -> str:
        quote = match.group("quote") or '"'
        return f"{match.group('indent')}version: {quote}{version}{quote}"

    body, count = VERSION_LINE_RE.subn(replace, frontmatter.group("body"), count=1)
    assert count == 1
    return text[: frontmatter.start("body")] + body + text[frontmatter.end("body") :]


def containers_env_files(repo_root: Path) -> list[Path]:
    return [repo_root / CONTAINERS_ENV] if (repo_root / CONTAINERS_ENV).exists() else []


def chart_values_files(repo_root: Path) -> list[Path]:
    """``values.yaml`` of every chart that owns a ``templates/vss-ingress.yaml``.

    Discovered from the ingress template, not from the key: a chart that
    renders the version route but declares no ``vssVersion`` is reported as
    broken rather than silently skipped.
    """
    templates = _tracked(repo_root, f"deploy/helm/**/{INGRESS_TEMPLATE.as_posix()}")
    return sorted(template.parent.parent / "values.yaml" for template in templates)


def _regex_target(pattern: re.Pattern[str], render: Callable[[str], str]) -> tuple[
    Callable[[str], str | None], Callable[[str, str], str | None]
]:
    def current(text: str) -> str | None:
        match = pattern.search(text)
        return match.group("value") if match else None

    def stamp(text: str, version: str) -> str | None:
        if pattern.search(text) is None:
            return None
        new_text, count = pattern.subn(render(version), text, count=1)
        assert count == 1
        return new_text

    return current, stamp


_env_current, _env_stamp = _regex_target(ENV_LINE_RE, lambda v: f'VSS_VERSION="{v}"')
_values_current, _values_stamp = _regex_target(VALUES_KEY_RE, lambda v: f'vssVersion: "{v}"')

TARGETS: tuple[Target, ...] = (
    Target("skill", skill_files, skill_current, skill_stamp),
    Target("compose edge", containers_env_files, _env_current, _env_stamp),
    Target("helm edge", chart_values_files, _values_current, _values_stamp),
)


# --- main --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--check", action="store_true", help="report disagreements; write nothing")
    parser.add_argument(
        "--version",
        help="stamp this SemVer instead of deriving it from git (release order: stamp, then tag)",
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()

    if args.version is not None:
        if not SEMVER_PATTERN.fullmatch(args.version):
            print(f"error: {args.version!r} is not strict SemVer", file=sys.stderr)
            return 2
        version = args.version
    else:
        try:
            version = derive_version(repo_root)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    declared_by: dict[str, list[tuple[Target, Path]]] = {}
    broken: list[tuple[Target, Path]] = []
    for target in TARGETS:
        for path in target.paths(repo_root):
            text = path.read_text(encoding="utf-8")
            declared = target.current(text)
            if declared is None:
                broken.append((target, path))
                continue
            declared_by.setdefault(declared, []).append((target, path))
            if not args.check and declared != version:
                stamped = target.stamp(text, version)
                assert stamped is not None
                path.write_text(stamped, encoding="utf-8")

    for target, path in broken:
        print(
            f"error: {path.relative_to(repo_root)}: no version field to stamp ({target.name})",
            file=sys.stderr,
        )
    if not args.check:
        restamped = [entry for value, entries in declared_by.items() if value != version for entry in entries]
        for target, path in restamped:
            print(f"{path.relative_to(repo_root)}: stamped {version} ({target.name})")
        if not restamped and not broken:
            print(f"every version field already declares {version}")
        return 2 if broken else 0

    if broken:
        return 2
    fix = "run `python3 .github/scripts/stamp_versions.py` (or `--version X.Y.Z` for a release) and commit the result"
    if len(declared_by) > 1:
        # Name the minority values file by file; the majority is the reference.
        majority = max(declared_by, key=lambda value: len(declared_by[value]))
        print(f"error: version fields disagree; most declare {majority}; {fix}", file=sys.stderr)
        for value, entries in sorted(declared_by.items()):
            if value == majority:
                continue
            for target, path in entries:
                print(f"  {path.relative_to(repo_root)}: {value} ({target.name})", file=sys.stderr)
        return 1
    (stamped_version,) = declared_by or {version: []}
    if not SEMVER_PATTERN.fullmatch(stamped_version) or semver_key(stamped_version) < semver_key(version):
        print(
            f"error: every version field declares {stamped_version}, older than the nearest "
            f"v* tag's {version}; {fix}",
            file=sys.stderr,
        )
        return 1
    ahead = "" if stamped_version == version else f" (ahead of the nearest v* tag's {version}: a release stamp)"
    print(f"every version field declares {stamped_version}{ahead}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
