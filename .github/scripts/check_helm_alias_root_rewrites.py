#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every rewriting Helm ingress mount must also rewrite its bare root.

``haproxy.org/path-rewrite`` rules are written as pairs, and both halves are
load-bearing because the two forms are disjoint:

    ^/storage/(.*) /vst/storage/\\1     matches /storage/clip.mp4, not /storage
    ^/storage$     /vst/storage        matches /storage, nothing else

A mount that declares only the first form still *routes* its bare root -- the
HAProxy Ingress controller trims the trailing slash off a ``pathType: Prefix``
path and writes two map rows, an exact one for ``/storage`` and a ``map_beg``
one for ``/storage/`` (``pkg/route/route.go::MapRows``). So ``GET /storage``
selects the backend and then arrives **unrewritten**, still carrying the public
prefix the backend has never heard of, and the backend answers 404.

That is a worse failure than a missing route: the request is admitted, reaches a
healthy service, and comes back as though the media it asked for did not exist.
Nothing in the ingress or the backend logs says "prefix not stripped".

The Docker edge answers those bare roots -- every mount there is a two-line ACL,
``acl p_storage path /storage`` beside ``acl p_storage path_beg /storage/`` --
so a chart missing the pair does not merely serve a 404, it serves a *different
answer than Compose does for the same request*. Keeping the two edges agreeing
is the whole point of the shared route table, and `vss configure` records one
set of paths for both.

The canonical table in ``deploy/helm/services/common/templates/_ingress-routes.tpl``
cannot get this wrong: ``vss.ingress.pathRewriteRows`` emits ``^<path>/(.*)`` and
``^<path>$`` from one row, so a generated manifest always has both. This lint is
for the hand-written manifests that do not go through it -- the warehouse
industry profiles and the ``vss-ingress-example-rewrites.yaml`` files operators
apply directly -- where the pair is maintained by hand and half of it has gone
missing before.

Two rules are checked:

* **Root pairing.** Every ``^/X/(.*)`` rule has a ``^/X$`` rule in the same
  annotation, rewriting to the same place with the ``/\\1`` tail resolved. The
  target is asserted, not just the presence, so a root rule pointing somewhere
  else is caught too.
* **Canonical spelling.** A ``path:`` that the canonical table also declares is
  spelled the way the table spells it. This one is not a routing bug -- the
  controller trims the slash, so ``/vios/`` and ``/vios`` produce identical map
  rows -- but ``path: /vios/`` reads as "the bare root is not served here" and
  has twice been reported as one. Agreeing with the table costs a character and
  removes the trap.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HELM_ROOT = ROOT / "deploy/helm"
ROUTE_TABLE = HELM_ROOT / "services/common/templates/_ingress-routes.tpl"

ANNOTATION = "haproxy.org/path-rewrite:"

# `^/storage/(.*) /vst/storage/\1` -- a concrete rule, both halves literal.
# Rules the route table generates start `^{{ $row.path }}` and are skipped:
# they are correct by construction, and there is nothing to pair up until the
# template is rendered.
RULE = re.compile(r"^(?P<source>\^/\S*)\s+(?P<replacement>\S+)$")
PREFIX_SOURCE = re.compile(r"^\^(?P<mount>/\S*?)/\(\.\*\)$")
ROOT_SOURCE = re.compile(r"^\^(?P<mount>/\S*?)\$$")

# `- path: /vios` / `  path: /vios` in an Ingress `paths:` list.
PATH_LINE = re.compile(r"^\s*-?\s*path:\s*(?P<path>/\S*)\s*$")
# `  path: /vios` in the canonical route table's YAML rows.
TABLE_PATH = re.compile(r"^\s*path:\s*(?P<path>/\S*)\s*$")


def display(path: Path) -> str:
    """Repo-relative path when possible, so messages are copy-pasteable."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def root_replacement(replacement: str) -> str:
    """The bare-root target implied by a ``^/X/(.*)`` rule's replacement.

    Mirrors ``{{ $to | default "/" }}`` in the canonical template: drop the
    ``/\\1`` tail the prefix form appends, and a strip (which leaves nothing)
    becomes ``/``.
    """
    stripped = re.sub(r"/\\1$", "", replacement)
    return stripped or "/"


def rewrite_blocks(path: Path) -> list[tuple[int, list[tuple[int, str, str]]]]:
    """Return ``(annotation_line, rules)`` for each path-rewrite annotation.

    A rule is ``(line_number, source, replacement)``. Go-template control lines
    are skipped rather than ending the block, because the warehouse charts wrap
    individual rules in ``{{- if $analyticsEnabled }}``.
    """
    blocks: list[tuple[int, list[tuple[int, str, str]]]] = []
    lines = path.read_text().splitlines()
    for index, line in enumerate(lines):
        if ANNOTATION not in line or line.lstrip().startswith("#"):
            continue
        annotation_indent = len(line) - len(line.lstrip())
        rules: list[tuple[int, str, str]] = []
        for offset, candidate in enumerate(lines[index + 1 :], start=index + 2):
            stripped = candidate.lstrip()
            indent = len(candidate) - len(stripped)
            if stripped and not stripped.startswith("{{") and indent <= annotation_indent:
                break
            match = RULE.fullmatch(stripped)
            if match:
                rules.append((offset, match.group("source"), match.group("replacement")))
        blocks.append((index + 1, rules))
    return blocks


def canonical_paths(path: Path) -> dict[str, str]:
    """Map ``normalised mount -> canonical spelling`` from the route table."""
    if not path.is_file():
        return {}
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        match = TABLE_PATH.match(line)
        if match:
            mount = match.group("path")
            found[mount.rstrip("/") or "/"] = mount
    return found


def scan_root_pairing(path: Path) -> tuple[list[str], int]:
    """Every ``^/X/(.*)`` rule needs its ``^/X$`` twin, pointing to the same place."""
    failures: list[str] = []
    checked = 0
    for annotation_line, rules in rewrite_blocks(path):
        roots = {
            match.group("mount"): (number, replacement)
            for number, source, replacement in rules
            if (match := ROOT_SOURCE.fullmatch(source))
        }
        for number, source, replacement in rules:
            match = PREFIX_SOURCE.fullmatch(source)
            if not match:
                continue
            mount = match.group("mount")
            checked += 1
            expected = root_replacement(replacement)
            if mount not in roots:
                failures.append(
                    f"{display(path)}:{number}: the {mount} mount rewrites "
                    f"{source!r} but the annotation opened at line "
                    f"{annotation_line} has no {f'^{mount}$'!r} rule, so a "
                    f"request for the bare root {mount} routes to the backend "
                    f"still carrying the {mount} prefix and comes back 404. "
                    f"Add {f'^{mount}$'} {expected}"
                )
                continue
            root_number, actual = roots[mount]
            if actual != expected:
                failures.append(
                    f"{display(path)}:{root_number}: {f'^{mount}$'} rewrites to "
                    f"{actual!r}, but {source!r} rewrites to {replacement!r}, "
                    f"whose root form is {expected!r}. The two halves of one "
                    f"mount would send the bare root and its subpaths to "
                    f"different places"
                )
    return failures, checked


def scan_canonical_spelling(path: Path, canonical: dict[str, str]) -> tuple[list[str], int]:
    """A mount the canonical table declares is spelled the table's way."""
    if not canonical:
        return [], 0
    failures: list[str] = []
    checked = 0
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        match = PATH_LINE.match(line)
        if not match:
            continue
        declared = match.group("path")
        expected = canonical.get(declared.rstrip("/") or "/")
        if expected is None:
            continue
        checked += 1
        if declared != expected:
            failures.append(
                f"{display(path)}:{number}: path {declared!r} diverges from the "
                f"canonical route table, which spells this mount {expected!r} "
                f"({display(ROUTE_TABLE)}). The HAProxy controller trims the "
                f"trailing slash so both route identically, but the spelling "
                f"with it reads as though the bare root were unserved and has "
                f"been reported as a routing bug twice"
            )
    return failures, checked


def manifests(helm_root: Path) -> list[Path]:
    """Helm manifests that could carry an ingress route, route table excluded."""
    return sorted(
        candidate
        for candidate in helm_root.rglob("*.yaml")
        if candidate.resolve() != ROUTE_TABLE.resolve()
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--helm-root", type=Path, default=HELM_ROOT)
    parser.add_argument("--route-table", type=Path, default=ROUTE_TABLE)
    args = parser.parse_args(argv)

    canonical = canonical_paths(args.route_table)
    if not canonical:
        print(
            f"{display(args.route_table)}: no canonical route rows found, so the "
            "spelling rule is not checking anything",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []
    rewrites = 0
    paths = 0
    for manifest in manifests(args.helm_root):
        pairing_failures, checked = scan_root_pairing(manifest)
        failures += pairing_failures
        rewrites += checked
        spelling_failures, seen = scan_canonical_spelling(manifest, canonical)
        failures += spelling_failures
        paths += seen

    # A lint that matches nothing passes forever.
    if not rewrites or not paths:
        print(
            f"{display(args.helm_root)}: found {rewrites} prefix rewrite rules and "
            f"{paths} canonical paths; the ingress manifests were not recognised, "
            "so this lint is not checking anything",
            file=sys.stderr,
        )
        return 1

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(
        f"Helm alias root rewrite lint passed ({rewrites} rewriting mounts, "
        f"{paths} canonical paths)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
