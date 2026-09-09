#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every gateway route carries an exposure tier, and internal-only is enforced.

FR-29 of the Docker Service Location Indirection SRD
(docs/docker-service-location-indirection-srd.md):

    Each gateway route shall be classified as internal-only,
    remote-agent-accessible, or public-user-accessible. The default
    classification shall be internal-only unless a route is required by browser
    clients or remote agents.

and again in the security review table of that document's section 5:

    Route exposure review | Each gateway route shall be reviewed and classified
    as internal-only, remote-agent-accessible, or public-user-accessible.

A classification nobody can check is a comment, so this lint is what makes the
requirement a property of the repository rather than a paragraph in a document.
It holds three things.

**1. Every route is classified, on both edges.** The tier lives next to the
route: an `exposure:` field on the row of the canonical table
(deploy/helm/services/common/templates/_ingress-routes.tpl), and an
`# exposure:` marker above the route in the Docker edge
(deploy/docker/services/infra/haproxy/haproxy.cfg.template). Two records rather
than one shared source because the two edges have no shared source -- the same
reason the Host allowlists are a hand-maintained matched pair -- so rule 2
exists to keep them honest.

**2. The two edges agree.** A mount classified one way in Kubernetes and
another way in Docker is worse than an unclassified one, because each edge looks
correct on its own and the deployment's actual exposure depends on which one an
operator ran. Checked in both directions for every mount both edges carry.

**3. internal-only is actually restricted.** This is the part that is not
bookkeeping. The tier claims a route is unreachable from outside the deployment,
and the two edges enforce it by different mechanisms because they have different
primitives:

  * *Kubernetes*: an Ingress rule under the public host IS the exposure, so the
    enforcement is not to render the path at all. There is nothing to gate --
    the controller would have to route the request before any predicate could
    refuse it. Hence `vss.ingress.publiclyMountable` skips internal-only rows,
    and this lint requires any row that is still published to name a
    `publicMountException` with a written reason.
  * *Docker*: HAProxy can inspect the request, so an internal-only route is
    gated on `h_internal` AND `gw_internal_src` -- an internal Host and a
    private source range -- and carries an explicit `deny` for each negation.
    Both gates are required together because each is insufficient alone: Host
    is client-controlled, and RFC 1918 covers a remote agent on a corporate
    network. Asserted here so a route cannot be classified internal-only while
    being served to anyone who asks.

Also asserted, because each is a way the above passes while meaning nothing:

  * `h_internal` must not name any of the deployment's PUBLISHED origins
    (`VSS_PUBLIC_HOST`, `EXTERNAL_IP`, `HOST_IP`, `HOST_INTERNAL_ALIAS`,
    `VSS_GATEWAY_HOST`). Adding one would make the internal Host test true for
    exactly the callers the tier excludes, and every internal-only route would
    quietly become public while this lint still passed.
  * `gw_internal_src` must actually test `src`. A marker ACL keyed on something
    else would gate nothing.
  * The runbook must name the three tiers and list the internal-only routes, so
    an operator who gets a 403 can find out why.
  * The tripwires the other gateway lints carry: if no routes or no rows were
    recognised, the parse broke and the lint is asserting nothing, which is
    reported as a failure rather than a clean run. This repository has shipped
    guards that passed while checking nothing -- an ingress verifier reading
    stale vendored charts, an endpoint lint skipping a missing host -- and a
    green run is not evidence on its own.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TABLE = ROOT / "deploy/helm/services/common/templates/_ingress-routes.tpl"
TEMPLATE = ROOT / "deploy/docker/services/infra/haproxy/haproxy.cfg.template"
README = ROOT / "deploy/docker/README.md"

README_SECTION = "### Who each route is exposed to"

TIERS = ("internal-only", "remote-agent-accessible", "public-user-accessible")

#: The ACLs that together admit an internal-only route, and what each is for.
INTERNAL_HOST_ACL = "h_internal"
INTERNAL_SRC_ACL = "gw_internal_src"

#: Variables naming an origin the deployment PUBLISHES. None may appear in the
#: internal Host allowlist -- see rule in the docstring.
PUBLIC_ORIGIN_VARS = (
    "VSS_PUBLIC_HOST",
    "EXTERNAL_IP",
    "HOST_IP",
    "HOST_INTERNAL_ALIAS",
    "VSS_GATEWAY_HOST",
)

#: Docker frontend routes with no mount path to compare against the canonical
#: table, and why comparing them is not possible rather than not done. They
#: still require an `# exposure:` marker; only the parity check is skipped.
NO_CANONICAL_MOUNT = {
    "p_vst_prefixed": "path_reg on an embedded host:port, not a mount prefix",
}

FRONTEND = re.compile(r"^frontend\s+\S+")
USE_BACKEND = re.compile(r"^\s*use_backend\s+(?P<backend>\S+)\s+if\s+(?P<conds>.+?)\s*$")
EXPOSURE_MARKER = re.compile(r"^\s*#\s*exposure:\s*(?P<tier>\S+)\s*$")
# `acl p_x path /mount` / `acl p_x path_beg /mount/`. path_reg is matched so the
# ACL is known to exist without claiming a mount for it.
ACL_PATH = re.compile(r"^\s*acl\s+(?P<name>\S+)\s+path(?P<kind>_beg|_reg)?\s+(?P<value>\S.*?)\s*$")
ACL_HOST = re.compile(r"^\s*acl\s+(?P<name>\S+)\s+hdr\(host\)\s+-i\s+(?P<value>\S.*?)\s*$")
ACL_SRC = re.compile(r"^\s*acl\s+(?P<name>\S+)\s+src\s+(?P<value>\S.*?)\s*$")
DENY = re.compile(r"^\s*http-request\s+deny\b.*?\bif\s+(?P<conds>.+?)\s*$")
INTERPOLATION = re.compile(r"\$\{([^}]*)\}")

# Rows of the canonical table. Parsed with regexes rather than a YAML loader:
# these lints run under a bare `python3` in the compose-golden job, which
# installs nothing, and the block is Helm template source rather than a YAML
# document anyway.
ROW_KEY = re.compile(r"^-\s*key:\s*(?P<key>\S+)\s*$")
ROW_FIELD = re.compile(r"^\s+(?P<field>path|pathType|rewrite|exposure|publicMountException):\s*(?P<value>.*?)\s*$")


def display(path: Path) -> str:
    """Path relative to the repository root where possible, for diagnostics."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def canonical_rows(path: Path) -> tuple[list[dict], list[str]]:
    """Rows of `vss.ingress.routeTable`, each with its line number."""
    text = path.read_text()
    marker = '{{- define "vss.ingress.routeTable" -}}'
    if marker not in text:
        return [], [f"{display(path)}: {marker} is missing; the canonical route table was not found"]
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if marker in line)
    rows: list[dict] = []
    for number, line in enumerate(lines[start + 1 :], start=start + 2):
        if line.startswith("{{- end -}}"):
            break
        key = ROW_KEY.match(line)
        if key:
            rows.append({"key": key.group("key"), "line": number})
            continue
        field = ROW_FIELD.match(line)
        if field and rows:
            rows[-1][field.group("field")] = field.group("value")
    return rows, []


def parse_docker(path: Path) -> tuple[dict, list[str]]:
    """Routes, exposure markers, mounts and the internal-only ACLs."""
    lines = path.read_text().split("\n")

    routes: list[dict] = []  # one per use_backend
    mounts: dict[str, str] = {}  # path acl -> mount prefix
    regex_acls: set[str] = set()
    host_acls: dict[str, list[str]] = {}
    src_acls: dict[str, list[str]] = {}
    denies: list[tuple[int, list[str]]] = []

    in_frontend = False
    pending: tuple[int, str] | None = None  # most recent exposure marker
    for number, line in enumerate(lines, start=1):
        if FRONTEND.match(line):
            in_frontend = True
            continue
        if line and not line[0].isspace():
            in_frontend = False
        if not in_frontend:
            continue

        marker = EXPOSURE_MARKER.match(line)
        if marker:
            pending = (number, marker.group("tier"))
            continue

        acl_host = ACL_HOST.match(line)
        if acl_host:
            value = acl_host.group("value")
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            host_acls.setdefault(acl_host.group("name"), []).append(value)
            continue

        acl_src = ACL_SRC.match(line)
        if acl_src:
            src_acls.setdefault(acl_src.group("name"), []).append(acl_src.group("value"))
            continue

        acl_path = ACL_PATH.match(line)
        if acl_path:
            name = acl_path.group("name")
            if acl_path.group("kind") == "_reg":
                regex_acls.add(name)
            else:
                # `path /x` and `path_beg /x/` name the same mount; either is enough.
                mounts.setdefault(name, acl_path.group("value").rstrip("/") or "/")
            continue

        deny = DENY.match(line)
        if deny:
            denies.append((number, deny.group("conds").split()))
            continue

        use = USE_BACKEND.match(line)
        if use:
            conds = use.group("conds").split()
            path_acls = [
                c for c in conds
                if c not in {"h_main", INTERNAL_HOST_ACL, INTERNAL_SRC_ACL}
                and not c.startswith("!")
                and not c.startswith("{")
            ]
            routes.append(
                {
                    "line": number,
                    "backend": use.group("backend"),
                    "conds": conds,
                    "path_acls": path_acls,
                    "exposure": pending[1] if pending else None,
                    "marker_line": pending[0] if pending else None,
                }
            )
            # Consumed, so the NEXT route cannot inherit this marker. Silent
            # inheritance is the failure mode this lint exists to prevent: an
            # unmarked route would pick up its neighbour's tier and read as
            # classified, which is how the UI catch-all came out
            # remote-agent-accessible on the first run of this rule.
            pending = None
            continue

    # The catch-all `use_backend bk_vss_ui if h_main` has no path ACL; it serves
    # the origin root, which is the canonical table's `/` row.
    for route in routes:
        if not route["path_acls"]:
            route["mount"] = "/"
        else:
            found = [mounts[a] for a in route["path_acls"] if a in mounts]
            route["mount"] = found[0] if found else None

    return {
        "routes": routes,
        "mounts": mounts,
        "regex_acls": regex_acls,
        "host_acls": host_acls,
        "src_acls": src_acls,
        "denies": denies,
    }, []


def scan(table: Path, template: Path) -> tuple[list[str], dict]:
    """Diagnostics for the exposure contract, plus what was classified."""
    failures: list[str] = []
    rows, row_failures = canonical_rows(table)
    failures += row_failures
    parsed, parse_failures = parse_docker(template)
    failures += parse_failures
    routes = parsed["routes"]

    # A lint that matches nothing passes forever.
    if not rows or not routes:
        failures.append(
            f"{display(table)}: parsed {len(rows)} canonical rows and "
            f"{len(routes)} Docker routes from {display(template)}; the route "
            "tables were not recognised, so this lint is not checking anything"
        )
        return failures, {}

    # 1a. Every canonical row carries a valid tier.
    helm_tiers: dict[str, str] = {}
    for row in rows:
        where = f"{display(table)}:{row['line']}"
        path = row.get("path")
        tier = row.get("exposure")
        if not path:
            failures.append(f"{where}: route row for key {row['key']!r} has no path")
            continue
        if not tier:
            failures.append(
                f"{where}: route {path} has no `exposure:` field. FR-29 requires every "
                f"gateway route to be classified as one of {', '.join(TIERS)}; add the "
                "field to this row so the classification cannot drift from the route"
            )
            continue
        if tier not in TIERS:
            failures.append(
                f"{where}: route {path} has exposure {tier!r}, which is not one of "
                f"{', '.join(TIERS)}"
            )
            continue
        helm_tiers[path] = tier

        # 3a. Kubernetes enforcement: an internal-only row must not be published.
        if tier == "internal-only":
            reason = row.get("publicMountException")
            if reason is not None and not reason.strip():
                failures.append(
                    f"{where}: route {path} is internal-only and carries an empty "
                    "`publicMountException`. An exception has to state why the mount is "
                    "still published on the public Ingress rule; an empty one is a "
                    "silent opt-out"
                )

    # 1b. Every Docker route carries a valid tier.
    docker_tiers: dict[str, str] = {}
    for route in routes:
        where = f"{display(template)}:{route['line']}"
        tier = route["exposure"]
        if not tier:
            failures.append(
                f"{where}: use_backend {route['backend']} has no `# exposure:` marker "
                f"above it. FR-29 requires every gateway route to be classified as one "
                f"of {', '.join(TIERS)}; add `# exposure: <tier>` to this route block"
            )
            continue
        if tier not in TIERS:
            failures.append(
                f"{where}: use_backend {route['backend']} is marked exposure {tier!r}, "
                f"which is not one of {', '.join(TIERS)}"
            )
            continue
        mount = route["mount"]
        if mount:
            docker_tiers.setdefault(mount, tier)

    # 2. The two edges agree about every mount they both carry.
    for route in routes:
        mount, tier = route["mount"], route["exposure"]
        if not tier or tier not in TIERS:
            continue
        if mount is None:
            acls = ", ".join(route["path_acls"]) or "(none)"
            if not any(a in NO_CANONICAL_MOUNT for a in route["path_acls"]):
                failures.append(
                    f"{display(template)}:{route['line']}: use_backend "
                    f"{route['backend']} is gated on {acls}, which declares no mount "
                    "path, so its tier cannot be compared with the canonical table. "
                    "Name it in NO_CANONICAL_MOUNT with a reason if that is intended"
                )
            continue
        expected = helm_tiers.get(mount)
        if expected is not None and expected != tier:
            failures.append(
                f"{display(template)}:{route['line']}: {mount} is marked {tier!r} on the "
                f"Docker edge but {expected!r} in {display(table)}. The two edges must "
                "classify a mount identically -- each reads correct alone, and the "
                "deployment's real exposure would depend on which edge was deployed"
            )

    # 3b. Docker enforcement: internal-only routes are gated and denied.
    internal_routes = [r for r in routes if r["exposure"] == "internal-only"]
    for route in internal_routes:
        where = f"{display(template)}:{route['line']}"
        conds = route["conds"]
        for acl in (INTERNAL_HOST_ACL, INTERNAL_SRC_ACL):
            if acl not in conds:
                failures.append(
                    f"{where}: use_backend {route['backend']} is classified internal-only "
                    f"but is not gated on {acl}; add it to the condition. Without both "
                    f"{INTERNAL_HOST_ACL} and {INTERNAL_SRC_ACL} the route is served to "
                    "any caller the Host allowlist admits, which is every caller the "
                    "tier is supposed to exclude"
                )
        # An explicit deny for each negation, so a refused request does not fall
        # through to the UI catch-all and collect a 200 with an HTML page.
        for acl in (INTERNAL_HOST_ACL, INTERNAL_SRC_ACL):
            negation = "!" + acl
            if not any(
                negation in deny_conds and set(route["path_acls"]) & set(deny_conds)
                for _, deny_conds in parsed["denies"]
            ):
                failures.append(
                    f"{where}: use_backend {route['backend']} is classified internal-only "
                    f"but no `http-request deny ... if ... {negation}` covers it. Without "
                    "the deny, a request from outside matches no use_backend and falls "
                    "through to the UI catch-all, answering 200 with the UI's HTML "
                    "instead of refusing the route"
                )

    # A tier that is declared but never used is not a failure; a tier that is
    # enforced nowhere while routes claim it is.
    if helm_tiers and not any(t == "internal-only" for t in helm_tiers.values()) and not internal_routes:
        failures.append(
            f"{display(template)}: no route on either edge is classified internal-only, so "
            "the enforcement rules below are unexercised and this lint is not checking "
            "them. FR-29 makes internal-only the DEFAULT tier -- if that is genuinely "
            "correct for every route, say so here"
        )

    # 4. The internal Host allowlist must not name a published origin.
    host_values = parsed["host_acls"].get(INTERNAL_HOST_ACL)
    if internal_routes and not host_values:
        failures.append(
            f"{display(template)}: routes are classified internal-only but no "
            f"`acl {INTERNAL_HOST_ACL} hdr(host)` is declared, so the gate they are "
            "supposed to be behind does not exist"
        )
    for value in host_values or []:
        for name in INTERPOLATION.findall(value):
            if name in PUBLIC_ORIGIN_VARS:
                failures.append(
                    f"{display(template)}: {INTERNAL_HOST_ACL} declares {value!r}, which "
                    f"reads {name} -- an origin the deployment PUBLISHES. That makes the "
                    "internal Host test true for exactly the callers the internal-only "
                    "tier excludes, so every internal-only route would be reachable from "
                    "outside while this lint still passed"
                )

    # 5. The source ACL has to test `src`.
    if internal_routes and INTERNAL_SRC_ACL not in parsed["src_acls"]:
        failures.append(
            f"{display(template)}: routes are classified internal-only but "
            f"`acl {INTERNAL_SRC_ACL} src ...` is not declared, so the source gate is "
            "either missing or keyed on something other than the client address"
        )

    classified = {
        "helm": helm_tiers,
        "docker": docker_tiers,
        "internal": sorted({r["mount"] for r in internal_routes if r["mount"]}),
        "exceptions": {
            row["path"]: row["publicMountException"]
            for row in rows
            if row.get("exposure") == "internal-only" and row.get("publicMountException")
        },
    }
    return failures, classified


def scan_readme(path: Path, classified: dict) -> list[str]:
    """The runbook has to explain the tiers and name the restricted routes."""
    if not classified:
        return []
    where = display(path)
    text = path.read_text()
    if README_SECTION not in text:
        return [
            f"{where}: section {README_SECTION!r} is missing; an operator who gets a 403 "
            "from an internal-only route has no way to learn the tier decided it"
        ]
    section = text.split(README_SECTION, 1)[1].split("\n## ", 1)[0]
    failures: list[str] = []
    for tier in TIERS:
        if tier not in section:
            failures.append(
                f"{where}: section {README_SECTION!r} never names the {tier!r} tier, so "
                "the classification an operator is subject to is not documented"
            )
    for mount in classified["internal"]:
        if mount not in section:
            failures.append(
                f"{where}: {mount} is classified internal-only and refused to outside "
                f"callers, but section {README_SECTION!r} does not list it"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table", type=Path, default=TABLE)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)

    failures, classified = scan(args.table, args.template)
    if classified:
        failures += scan_readme(args.readme, classified)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    helm, docker = classified["helm"], classified["docker"]
    print(
        f"Gateway route exposure lint passed "
        f"({len(helm)} canonical rows, {len(docker)} Docker mounts classified)."
    )
    for tier in TIERS:
        mounts = sorted({m for m, t in helm.items() if t == tier})
        print(f"  {tier}: {', '.join(mounts) or '(none)'}")
    print(f"  internal-only enforced at the Docker edge: {', '.join(classified['internal'])}")
    for mount, reason in sorted(classified["exceptions"].items()):
        print(f"  public-mount exception: {mount} -- {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
