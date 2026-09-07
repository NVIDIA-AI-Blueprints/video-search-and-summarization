#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Check the four developer profiles against the shared ingress route table.

What this asserts, and nothing more:

  1. every mounted path and its pathType come from the shared table
     (deploy/helm/services/common/templates/_ingress-routes.tpl);
  2. no profile mounts a backend at the origin root -- the `/v1` mounts that
     made the VLM endpoint profile-dependent;
  3. the path-rewrite annotation covers exactly the mounted rewriting routes,
     with the destination the table specifies;
  4. every mount `vss configure` probes (vss_cli/config.py:INGRESS_SERVICES)
     exists in the table, so one configured CLI resolves on any profile;
  5. disabling a component removes its route instead of leaving an Ingress
     pointed at a Service that was never created;
  6. the host-less east-west rule is absent by default, and carries only the
     RTVI mounts when global.rtviInternalIngress.enabled turns it on;
  7. the hand-applied vss-ingress-example*.yaml pair still describes the same
     mounts the chart renders.

It does NOT check parity with the Docker edge (haproxy.cfg.template): that
config is aligned to this table separately and still carries Docker-only routes.

Usage: python3 deploy/helm/scripts/verify-ingress-routes.py [--verbose]
(location-independent: all paths resolve relative to this file)
Requires: helm, PyYAML, and `helm dependency build` already run per profile.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent  # deploy/helm/scripts
HELM = HERE.parent
REPO = HELM.parent.parent
PROFILES_DIR = HELM / "developer-profiles"
TABLE = HELM / "services" / "common" / "templates" / "_ingress-routes.tpl"
CLI_CONFIG = REPO / "services/agent/packages/vss_cli/src/vss_cli/config.py"

# Profiles whose vssIngress is off by default need it switched on to render.
ON = ["--set", "vssIngress.enabled=true"]
PROFILES = {
    "dev-profile-base": ON,
    "dev-profile-alerts": [],
    "dev-profile-lvs": ON,
    "dev-profile-search": [],
}
HOST = "10.0.0.1"
EAST_WEST = {"/rtvi-cv", "/rtvi-embed"}
DEDICATED_HOSTS = {"kibana", "phoenix", "streamer"}

# (profile, values override, path that must disappear). One per gating shape:
# an umbrella dependency, a leaf component, and the legacy alias keys.
DISABLED_CASES = [
    ("dev-profile-lvs", "vss-summarization.enabled=false", "/lvs"),
    ("dev-profile-lvs", "infra.enabled=false", "/elasticsearch"),
    ("dev-profile-base", "rtvi.vss-rtvi-vlm.enabled=false", "/rtvi-vlm"),
    ("dev-profile-base", "vssIngress.vlm.enabled=false", "/rtvi-vlm"),
    ("dev-profile-alerts", "rtvi.vss-rtvi-cv.enabled=false", "/rtvi-cv"),
    ("dev-profile-search", "infra.elasticsearch.enabled=false", "/elasticsearch"),
]


def canonical_table() -> list[dict]:
    """The rows of the shared table, read from the template itself."""
    body = TABLE.read_text()
    start = body.index('{{- define "vss.ingress.routeTable" -}}')
    end = body.index("{{- end -}}", start)
    rows = yaml.safe_load(body[start:end].split("-}}", 1)[1])
    assert rows, "no rows parsed from the canonical table"
    return rows


def render(profile: str, extra: list[str]) -> list[dict]:
    out = subprocess.run(
        [
            "helm",
            "template",
            "vss",
            "./" + profile,
            "--set",
            "global.externalHost=" + HOST,
            "--show-only",
            "templates/vss-ingress.yaml",
            *PROFILES[profile],
            *extra,
        ],
        cwd=PROFILES_DIR,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        sys.exit(f"FAIL {profile}: helm template failed\n{out.stderr}")
    return [
        d for d in yaml.safe_load_all(out.stdout) if d and d.get("kind") == "Ingress"
    ]


def mounted_paths(ingresses: list[dict]) -> set[str]:
    """Paths on named hosts, ignoring the dedicated single-service hosts."""
    found: set[str] = set()
    for ing in ingresses:
        for rule in ing["spec"]["rules"]:
            if "host" not in rule:
                continue
            paths = [p["path"] for p in rule["http"]["paths"]]
            if paths == ["/"] and rule["host"].split(".")[0] in DEDICATED_HOSTS:
                continue
            found |= set(paths)
    return found


def check_profile(profile: str, rows: list[dict], verbose: bool) -> list[str]:
    fails: list[str] = []
    by_path = {r["path"]: r for r in rows}
    strips = {r["path"] for r in rows if r.get("rewrite", "none") != "none"}
    ingresses = render(profile, [])

    for ing in ingresses:
        annotations = ing["metadata"].get("annotations") or {}
        # Each rewriting route contributes a pair: the capture form and the
        # bare form. Both destinations matter -- a wrong one silently sends the
        # backend a path it does not serve.
        rewrites = {}
        for line in annotations.get("haproxy.org/path-rewrite", "").splitlines():
            if line.strip():
                src, dst = line.split()
                rewrites[src] = dst
        mounted_strip = set()

        for rule in ing["spec"]["rules"]:
            paths = [p["path"] for p in rule["http"]["paths"]]
            if "host" not in rule:
                fails.append(
                    f"{profile}: renders a host-less rule by default. That rule belongs "
                    f"to global.rtviInternalIngress and answers on every Host reaching "
                    f"the controller, external listener included"
                )
                continue
            if paths == ["/"] and rule["host"].split(".")[0] in DEDICATED_HOSTS:
                continue
            for entry in rule["http"]["paths"]:
                path = entry["path"]
                row = by_path.get(path)
                if row is None:
                    fails.append(
                        f"{profile}: mounts {path}, which the shared table does not define"
                    )
                    continue
                if entry["pathType"] != row["pathType"]:
                    fails.append(
                        f"{profile}: mounts {path} as {entry['pathType']}, "
                        f"table says {row['pathType']}"
                    )
                if re.fullmatch(r"/v\d+(/.*)?", path):
                    fails.append(
                        f"{profile}: mounts {path} at the origin root -- the mount a "
                        f"caller cannot resolve without knowing the profile"
                    )
                if path in strips:
                    mounted_strip.add(path)
                    to = "" if row["rewrite"] == "strip" else row["rewrite"]
                    anchor = "^" if row.get("anchored", False) else ""
                    for src, want in (
                        (f"{anchor}{path}/(.*)", f"{to}/\\1"),
                        (f"{anchor}{path}", to or "/"),
                    ):
                        got = rewrites.get(src)
                        if got is None:
                            fails.append(
                                f"{profile}: {path} mounted but {src!r} is not rewritten"
                            )
                        elif got != want:
                            fails.append(
                                f"{profile}: {src!r} rewrites to {got!r}, table says {want!r}"
                            )

        surplus = {
            src.removeprefix("^").replace("/(.*)", "") for src in rewrites
        } - mounted_strip
        if surplus:
            fails.append(f"{profile}: rewritten but not mounted: {sorted(surplus)}")

        if verbose:
            print(f"\n{profile}")
            for rule in ing["spec"]["rules"]:
                print(f"  host: {rule.get('host', '(any -- east-west)')}")
                for entry in rule["http"]["paths"]:
                    svc = entry["backend"]["service"]
                    print(
                        f"    {entry['path']:<22} -> {svc['name']}:{svc['port']['number']}"
                    )

    # The east-west rule, switched on. It exists so an in-cluster caller matches
    # a rule at all; it must not quietly carry the rest of the table onto every
    # Host the controller answers.
    if EAST_WEST & mounted_paths(ingresses):
        with_rii = render(profile, ["--set", "global.rtviInternalIngress.enabled=true"])
        hostless = [r for i in with_rii for r in i["spec"]["rules"] if "host" not in r]
        if not hostless:
            fails.append(
                f"{profile}: rtviInternalIngress enabled but no host-less rule rendered, "
                f"so the agent's in-cluster RTVI calls match nothing"
            )
        for rule in hostless:
            stray = {p["path"] for p in rule["http"]["paths"]} - EAST_WEST
            if stray:
                fails.append(f"{profile}: host-less rule carries {sorted(stray)}")

    return fails


def check_disabled(verbose: bool) -> list[str]:
    """A disabled component must lose its route, not keep a dangling backend."""
    fails = []
    for profile, override, path in DISABLED_CASES:
        if path in mounted_paths(render(profile, ["--set", override])):
            fails.append(
                f"{profile}: --set {override} still mounts {path}. `default true` on a "
                f"boolean hands back the default for an explicit false -- gate with "
                f"vss.ingress.enabled instead"
            )
        elif verbose:
            print(f"  disabled case ok: {profile} --set {override} drops {path}")
    return fails


def check_cli_parity(rows: list[dict]) -> list[str]:
    """Every mount `vss configure` probes has to exist in the table."""
    if not CLI_CONFIG.exists():
        return [f"cannot read {CLI_CONFIG} to check CLI parity"]
    wanted = set(re.findall(r'mount="([^"]+)"', CLI_CONFIG.read_text()))
    if not wanted:
        return [f"no INGRESS_SERVICES mounts parsed from {CLI_CONFIG}"]
    missing = wanted - {r["path"] for r in rows}
    if missing:
        return [
            f"vss configure probes {sorted(missing)}, which the shared table does not mount"
        ]
    return []


def check_examples(main_paths: dict[str, set]) -> list[str]:
    """The hand-applied examples must describe the same mounts as the chart."""
    fails = []
    for profile, rendered in main_paths.items():
        files = sorted((PROFILES_DIR / profile).glob("vss-ingress-example*.yaml"))
        if not files:
            continue  # not every profile ships a manual example
        documented: set = set()
        for f in files:
            for doc in yaml.safe_load_all(f.read_text()):
                if doc and doc.get("kind") == "Ingress":
                    for rule in doc["spec"]["rules"]:
                        documented |= {p["path"] for p in rule["http"]["paths"]}
        if documented != rendered:
            fails.append(
                f"{profile}: vss-ingress-example*.yaml is stale -- missing "
                f"{sorted(rendered - documented)}, extra {sorted(documented - rendered)}. "
                f"Regenerate from `helm template` (see the header in those files)."
            )
    return fails


def main() -> int:
    verbose = "--verbose" in sys.argv
    rows = canonical_table()
    failures = check_cli_parity(rows)
    main_paths: dict[str, set] = {}

    for profile in PROFILES:
        failures += check_profile(profile, rows, verbose)
        main_paths[profile] = mounted_paths(render(profile, []))

    failures += check_disabled(verbose)
    failures += check_examples(main_paths)

    if failures:
        print("\n".join("FAIL " + f for f in failures))
        return 1
    distinct = len({p for paths in main_paths.values() for p in paths})
    print(
        f"OK  {len(PROFILES)} profiles render from one table ({distinct} distinct mounts); "
        f"CLI probe mounts present; {len(DISABLED_CASES)} disabled-component cases drop "
        f"their route; examples current"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
