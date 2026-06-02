#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compute a *build-input-scoped* content hash for a service source folder.

The container-source gate compares a folder's tree SHA against the image's
``com.nvidia.vss.source_tree_sha``. Historically that SHA was the raw git tree
object of the whole folder (``git rev-parse <commit>:services/agent``), so any
change to a non-shipped file — README, AGENTS.md, tests, CI metadata — changed
the SHA and forced a needless container re-spin.

``build_input_tree_sha`` instead hashes only the files that actually influence
the image: the git-tracked files under the folder, **minus** anything excluded
by that folder's ``.dockerignore`` (mirroring the docker build context), but
**always** including the Dockerfile (it shapes the image even though docker
strips it from the context). The result is a sha256 over the sorted
``<blob_sha> <path>`` lines, so it depends only on tracked content + paths and
is reproducible identically at build time and check time.

IMPORTANT: the build-stamp side (ci-vss-oss ``ci/tools/create_manifest.py``)
must compute the stamp with this exact algorithm for the two to agree. Vendor
this module there, or reimplement it byte-for-byte.

The ``.dockerignore`` matcher implements the subset of docker's syntax used in
this repo: ``#`` comments, blank lines, ``**`` (any depth), ``*`` / ``?``
(within a path segment), leading ``/`` anchoring, trailing ``/`` directory
patterns, and ``!`` negation (last match wins).
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path


# A compiled ignore rule is a ``(negated, regex)`` pair.

def _pattern_to_regex(pattern):
    """Translate a single .dockerignore pattern to an anchored full-path regex."""
    anchored = pattern.startswith("/")
    body = pattern[1:] if anchored else pattern
    dir_only = body.endswith("/")
    body = body.rstrip("/")

    # Tokenize into path segments and translate each.
    segments = body.split("/")
    parts: list[str] = []
    for seg in segments:
        if seg == "**":
            parts.append("(?:.*/)?")  # zero or more path components
            continue
        out = []
        i = 0
        while i < len(seg):
            ch = seg[i]
            if ch == "*":
                out.append("[^/]*")
            elif ch == "?":
                out.append("[^/]")
            else:
                out.append(re.escape(ch))
            i += 1
        parts.append("".join(out) + "/")

    regex = "".join(parts).rstrip("/")
    # Collapse the artifact of a leading "**" segment producing "(?:.*/)?/...".
    regex = regex.replace("(?:.*/)?/", "(?:.*/)?")

    prefix = "" if anchored else r"(?:.*/)?"
    # A non-anchored, single-segment pattern (e.g. "Dockerfile", "*.md") may
    # match at any depth; an anchored one only at the folder root.
    if anchored:
        prefix = ""
    suffix = "(?:/.*)?$" if dir_only else r"(?:/.*)?$"
    return re.compile("^" + prefix + regex + suffix)


def load_dockerignore(text):
    """Parse .dockerignore text into a list of ``(negated, regex)`` rules."""
    rules = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:]
        line = line.strip()
        if not line:
            continue
        rules.append((negated, _pattern_to_regex(line)))
    return rules


def is_ignored(rel, rules):
    """Return True if ``rel`` is excluded, honoring negation (last match wins)."""
    ignored = False
    for negated, regex in rules:
        if regex.match(rel):
            ignored = not negated
    return ignored


def is_dockerfile(rel: str) -> bool:
    base = rel.rsplit("/", 1)[-1]
    return base == "Dockerfile" or base.endswith(".Dockerfile") or base.startswith("Dockerfile.")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _read_dockerignore(repo: Path, commit: str, source_path: str) -> list[IgnoreRule]:
    show = subprocess.run(
        ["git", "-C", str(repo), "show", f"{commit}:{source_path}/.dockerignore"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if show.returncode != 0:
        return []
    return load_dockerignore(show.stdout)


def build_input_files(repo: Path, commit: str, source_path: str) -> dict[str, str]:
    """Return ``{path: blob_sha}`` for the files that influence the image."""
    rules = _read_dockerignore(repo, commit, source_path)
    listing = _git(repo, "ls-tree", "-r", commit, "--", f"{source_path}/")
    kept: dict[str, str] = {}
    for line in listing.splitlines():
        if not line.strip():
            continue
        meta, _, path = line.partition("\t")
        fields = meta.split()
        if len(fields) < 3 or fields[1] != "blob":
            continue  # skip submodules/trees
        blob_sha = fields[2]
        rel = path[len(source_path) + 1 :]
        if rel == ".dockerignore":
            continue  # the ignore file itself is not part of the image
        if is_dockerfile(rel):
            kept[path] = blob_sha  # always influences the image
            continue
        if is_ignored(rel, rules):
            continue
        kept[path] = blob_sha
    return kept


def build_input_tree_sha(repo: Path, commit: str, source_path: str) -> str:
    """Deterministic sha256 over the build-input file set of ``source_path``."""
    kept = build_input_files(repo, commit, source_path)
    blob = "\n".join(f"{kept[p]} {p}" for p in sorted(kept))
    return hashlib.sha256(blob.encode()).hexdigest()
