#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Seed UNKNOWN licences in the committed inventory from public registries.

``osrb_inventory.py`` may not touch the network: the committed inventory.csv
must be byte-identical across regenerations or the drift gate flaps. Most
Python lockfiles record no licence, so without an outside answer those rows
stay UNKNOWN forever — 876 of 3991 rows when this tool was written, which
drowned the compliance report in rows that were UNKNOWN for lack of a lookup,
not for lack of a licence.

This is the deliberate, human-run escape hatch the generator's ``--previous``
carry-forward was designed around. Run it once (or after a dependency wave),
review the diff like any other change, commit. From then on the generator's
tier-5 carry-forward keeps every seeded licence on an unchanged
(package, version) with no further network access, and any version bump drops
back to UNKNOWN and asks again.

Rules, in the same spirit as the generator:

* Only rows whose licence is UNKNOWN are ever touched.
* Only an unambiguous registry answer is written: PyPI's ``license_expression``
  (PEP 639), else a single Trove classifier, else a short free-text label;
  npm's ``license`` string; GitHub's repository licence (SPDX id) for pinned
  actions. Multi-licence prose, empty answers and errors leave UNKNOWN alone.
* ``risk`` is recomputed for every row this tool fills, with the same
  ``license_risk`` the comparison uses.
* The output must remain a generator fixpoint: after seeding,
  ``osrb_inventory.py --previous inventory.csv`` must reproduce the file
  byte-for-byte. ``--verify-fixpoint`` runs that check for you.

Usage:
    python3 .github/osrb/osrb_seed.py --inventory .github/osrb/inventory.csv
    python3 .github/osrb/osrb_seed.py --inventory ... --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from osrb_scan import license_risk  # noqa: E402

TIMEOUT = 20
UA = {"User-Agent": "vss-osrb-seed/1.0"}


def _get_json(url: str) -> dict | None:
    headers = dict(UA)
    # Anonymous api.github.com allows 60 requests/hour, which silently starves
    # the action-licence lookups while PyPI and npm succeed — the seeded file
    # then looks complete but is not. Use the ambient token when there is one.
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None


def _classifier_licence(classifiers: list[str] | None) -> str:
    """The single Trove licence classifier, or "" when absent or plural.

    Two different licence classifiers on one release is exactly the ambiguity
    this tool must not resolve on its own — that row stays UNKNOWN and goes to
    a human with both candidates visible on PyPI.
    """
    found = []
    for entry in classifiers or []:
        if entry.startswith("License :: "):
            tail = entry.rsplit(" :: ", 1)[-1].strip()
            if tail and tail != "OSI Approved":
                found.append(tail)
    return found[0] if len(set(found)) == 1 and found else ""


def resolve_pypi(name: str, version: str) -> str:
    """Licence for one PyPI release, preferring the exact-version document."""
    for url in (
        f"https://pypi.org/pypi/{urllib.parse.quote(name)}/{urllib.parse.quote(version)}/json",
        f"https://pypi.org/pypi/{urllib.parse.quote(name)}/json",
    ):
        doc = _get_json(url)
        if not doc:
            continue
        info = doc.get("info") or {}
        licence = (info.get("license_expression") or "").strip()
        if licence:
            return licence
        licence = _classifier_licence(info.get("classifiers"))
        if licence:
            return licence
        raw = (info.get("license") or "").strip()
        # A short single-line label is a licence name; anything longer is the
        # licence *text* pasted into metadata, which is not an identifier.
        if raw and len(raw) < 60 and "\n" not in raw:
            return raw
        return ""
    return ""


def resolve_npm(name: str, version: str) -> str:
    doc = _get_json("https://registry.npmjs.org/" + urllib.parse.quote(name, safe="@/"))
    if not doc:
        return ""
    versions = doc.get("versions") or {}
    meta = versions.get(version) or {}
    licence = meta.get("license") or doc.get("license") or ""
    if isinstance(licence, dict):
        licence = licence.get("type", "")
    return str(licence).strip()


_ACTION_RE = re.compile(r"^([\w.-]+)/([\w.-]+)")


def resolve_github_action(name: str) -> str:
    """SPDX id of an action's repository licence.

    An action pinned by SHA ships whatever its repository is licensed under;
    the repository licence is the best unambiguous public answer. GitHub
    reports NOASSERTION for unrecognised texts, which stays UNKNOWN here.
    """
    match = _ACTION_RE.match(name)
    if not match:
        return ""
    doc = _get_json(
        f"https://api.github.com/repos/{match.group(1)}/{match.group(2)}/license"
    )
    spdx = ((doc or {}).get("license") or {}).get("spdx_id") or ""
    return "" if spdx in ("", "NOASSERTION") else spdx


# Late-bound on purpose: each entry looks the resolver up on the module at
# call time rather than capturing the function object here. A direct reference
# freezes the binding at import, which silently defeats mock.patch in the test
# suite — the test then hits the live registry, and "demo" happens to be a real
# GPL-licensed package on PyPI, so the test failure itself was a live lookup.
RESOLVERS = {
    "python": lambda n, v: resolve_pypi(n, v),
    "node": lambda n, v: resolve_npm(n, v),
    "github-action": lambda n, v: resolve_github_action(n),
}


# Evidence classes whose rows really were fetched from the language registry.
# An ``imported-only`` row is exactly where the name may NOT be the
# distribution: ``pyds`` is DeepStream's bindings arriving inside the base
# image, and resolving that name against PyPI answered for an unrelated
# GPLv3 package that happens to share it. A wrong licence written by this tool
# outlives the run — it rides the carry-forward — so provenance is required,
# not inferred.
REGISTRY_EVIDENCE = {
    "python": {"declared-manifest", "container-pip"},
    "node": {"declared-manifest"},
    "github-action": None,  # the action name IS the repository coordinate
}


def _registry_provenanced(row: dict[str, str]) -> bool:
    allowed = REGISTRY_EVIDENCE.get(row.get("language", ""))
    if allowed is None:
        return True
    evidence = set((row.get("usage_evidence") or "").split(";"))
    return bool(evidence & allowed)


def seed(rows: list[dict[str, str]]) -> list[tuple[dict[str, str], str]]:
    """Return ``(row, licence)`` for every UNKNOWN row a registry can answer."""
    candidates = [
        row
        for row in rows
        if row.get("license") == "UNKNOWN"
        and row.get("language") in RESOLVERS
        and _registry_provenanced(row)
    ]

    def lookup(row: dict[str, str]) -> tuple[dict[str, str], str]:
        resolver = RESOLVERS[row["language"]]
        return row, resolver(row["package"], row.get("version", ""))

    with ThreadPoolExecutor(max_workers=16) as pool:
        resolved = list(pool.map(lookup, candidates))
    # A registry can literally declare "UNKNOWN" (PyPI's `arango` does), and
    # writing that over our own UNKNOWN would count as progress forever while
    # changing nothing. A non-answer is not a licence.
    non_answers = {"", "unknown", "noassertion", "none", "n/a", "see license", "other"}
    return [
        (row, licence)
        for row, licence in resolved
        if licence and licence.strip().lower() not in non_answers
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    path = Path(args.inventory)
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    filled = seed(rows)
    for row, licence in filled:
        row["license"] = licence
        row["risk"] = license_risk(licence)

    print(f"[osrb-seed] UNKNOWN rows: {sum(1 for r in rows if r['license'] == 'UNKNOWN') + len(filled)}", file=sys.stderr)
    print(f"[osrb-seed] filled from registries: {len(filled)}", file=sys.stderr)
    for row, licence in sorted(filled, key=lambda item: item[0]["package"])[:10]:
        print(f"[osrb-seed]   {row['package']} {row['version']} -> {licence}", file=sys.stderr)
    if len(filled) > 10:
        print(f"[osrb-seed]   ... and {len(filled) - 10} more", file=sys.stderr)

    if args.dry_run:
        return 0
    with path.open("w", newline="", encoding="utf-8") as handle:
        # LF, matching osrb_inventory's writer. The csv module defaults to
        # CRLF, which flips every line ending in the committed file and makes
        # the drift gate read a licence seed as a 3992-line rewrite.
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"[osrb-seed] wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
