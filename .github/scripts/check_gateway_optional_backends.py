#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep "not deployed" distinguishable from "deployed and unwell" at the gateway.

Consolidating every service onto one origin destroyed a signal the transport
used to supply for free, and this lint exists because the loss is invisible:

  Addressed directly on Docker DNS, a service a profile does not deploy has no
  listener, so the connection is REFUSED. Only a running service can answer
  ``503`` at all. So ``ConnectError`` meant absent and ``503`` meant
  present-and-failing, and a caller that treats an optional service as
  skippable could just catch the transport error.

  Addressed through HAProxy, the connection always succeeds -- to HAProxy -- and
  a route whose backend is DOWN answers ``503``. Absent and unwell became the
  same three digits from the same origin.

That is not a hypothetical. PR #1983 moved ``RTVI_CV_ENDPOINT`` from
``http://vss-rtvi-cv:9000`` to ``${VSS_GATEWAY_ORIGIN}/rtvi-cv``, and
``_register_with_rtvi_cv`` -- which documents rt-cv as optional and catches
``httpx.ConnectError`` / ``httpx.TimeoutException`` -- started raising 502 on
every upload run by a profile that does not deploy rt-cv. Seventy-one CI checks
passed with that defect present, because no CI job deploys a profile that omits
an optional service and then exercises a path through it. Hence a static lint.

``haproxy.cfg.template`` restores the distinction: a route whose backend has no
usable server (``nbsrv() eq 0``) gets a 503 the gateway synthesises itself,
carrying ``x-vss-gateway-unavailable``. The service never saw the request, so it
cannot have produced that reply -- and an unmarked 503 therefore still means
present-and-failing, which stays an error for every caller. The gateway strips
the header from backend-origin responses so a service cannot forge it.

What is checked, and why each rule is here rather than left to review:

1. **Every route is marked.** A new mount added without a marker is the next
   occurrence of this bug, and it will not fail any test: the endpoint works
   fine on a profile that deploys the service. The exceptions are named in
   :data:`UNMARKED_BACKENDS` with a reason, so skipping one is a decision
   somebody wrote down.
2. **Each marker names its own route's backend.** ``nbsrv()`` takes a literal
   backend name, so a copy-paste that leaves the neighbour's name behind
   produces a marker that reports the wrong service absent -- or never fires.
   Checked in both directions, so a marker for a route that no longer exists
   also fails.
3. **The header value names the route.** Derived from the route's own path ACL,
   so ``/rtvi-cv`` cannot come back saying ``rtvi-vlm``.
4. **One header name, not two.** The template and
   ``vss_agents/utils/gateway.py`` are a matched pair with no shared source; a
   rename on one side would silently stop the tolerance working, which looks
   exactly like the original bug.
5. **The forgery strip exists.** Without ``http-response del-header``, any
   backend could claim to be absent and have its callers skip it.
6. **The runbook says so**, because an operator debugging a 503 needs to know
   the header decides what it means.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE = ROOT / "deploy/docker/services/infra/haproxy/haproxy.cfg.template"
README = ROOT / "deploy/docker/README.md"
AGENT_HELPER = ROOT / "services/agent/packages/vss_agents/src/vss_agents/utils/gateway.py"

README_SECTION = "### A 503 from the gateway is not the same as a 503 from a service"

#: Frontend backends that are deliberately NOT marked, and why. Every one is
#: either the deployment's own front door or its media plane -- nothing treats
#: them as optional -- and every one is reached through overlapping path ACLs
#: (``/api`` is a prefix of ``/api/chat``, ``/vst/api/v1/storage`` of ``/vst``,
#: ``/storage`` and ``/vios`` rewrite into VST's namespace, and the UI's
#: catch-all has no path ACL at all). A marker keyed on one of those ACLs would
#: fire on a request that routes to its neighbour, reporting a service absent
#: because an unrelated one is down. That is a worse failure than the bare 503
#: they answer today.
UNMARKED_BACKENDS = {
    "bk_vss_ui": "browser front door; reached by the catch-all `if h_main`, which has no path ACL",
    "bk_vss_agent": "the caller of these routes, not an optional callee; `p_api` overlaps `p_api_chat`",
    "bk_vst_ingress": "media plane; `/vst` overlaps `/vst/api/v1/storage`",
    "bk_vst_storage_compat": "media plane; `/storage` rewrites into VST's namespace",
    "bk_vst_storage_api_direct": "media plane; `/vst/api/v1/storage` overlaps `/vst`",
    "bk_vst_prefixed_compat": "media plane; repairs legacy host:port media URLs, matched by path_reg",
    "bk_vios_rewrite": "media plane; `/vios` is the `/vst` alias and rewrites into it",
}

FRONTEND = re.compile(r"^frontend\s+\S+")
USE_BACKEND = re.compile(r"^\s*use_backend\s+(?P<backend>\S+)\s+if\s+(?P<conds>.+?)\s*$")
MARKER = re.compile(
    r"^\s*http-request\s+set-var\(txn\.gw_absent\)\s+str\((?P<name>[^)]*)\)\s+"
    r"if\s+h_main\s+(?P<acl>\S+)\s+\{\s*nbsrv\((?P<backend>[^)]+)\)\s+eq\s+0\s*\}\s*$"
)
RETURN_503 = re.compile(r"^\s*http-request\s+return\s+status\s+503\s+hdr\s+(?P<header>[\w-]+)\b.*\bif\s+(?P<conds>.+?)\s*$")
DEL_HEADER = re.compile(r"^\s*http-response\s+del-header\s+(?P<header>[\w-]+)\s*$")
ACL_PATH = re.compile(r"^\s*acl\s+(?P<name>\S+)\s+path(?P<kind>_beg)?\s+/(?P<mount>[\w.-]*)/?\s*$")
HELPER_CONSTANT = re.compile(r"^GATEWAY_UNAVAILABLE_HEADER\s*=\s*[\"'](?P<header>[^\"']+)[\"']\s*$")


def display(path: Path) -> str:
    """Path relative to the repository root where possible, for diagnostics."""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def helper_header(path: Path) -> str | None:
    """The header name the agent looks for, read from its own module."""
    if not path.is_file():
        return None
    for line in path.read_text().splitlines():
        match = HELPER_CONSTANT.match(line)
        if match:
            return match.group("header")
    return None


def parse_template(path: Path) -> dict[str, object]:
    """Pull the routing table, the markers and the header rules out of the template."""
    routes: list[tuple[int, str, str]] = []  # (line, backend, path acl)
    catch_all: list[tuple[int, str]] = []
    markers: list[tuple[int, str, str, str]] = []  # (line, name, acl, backend)
    mounts: dict[str, str] = {}  # path acl -> mount name
    returns: list[tuple[int, str, str]] = []  # (line, header, conds)
    deletes: list[tuple[int, str]] = []

    in_frontend = False
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if FRONTEND.match(line):
            in_frontend = True
            continue
        if line and not line[0].isspace():
            in_frontend = False
        if not in_frontend:
            continue

        acl_match = ACL_PATH.match(line)
        if acl_match and acl_match.group("mount"):
            # `path /x` and `path_beg /x/` declare the same mount twice; either
            # spelling is enough to name it.
            mounts.setdefault(acl_match.group("name"), acl_match.group("mount"))

        marker_match = MARKER.match(line)
        if marker_match:
            markers.append(
                (number, marker_match.group("name"), marker_match.group("acl"), marker_match.group("backend"))
            )
            continue

        return_match = RETURN_503.match(line)
        if return_match:
            returns.append((number, return_match.group("header"), return_match.group("conds")))
            continue

        delete_match = DEL_HEADER.match(line)
        if delete_match:
            deletes.append((number, delete_match.group("header")))
            continue

        use_match = USE_BACKEND.match(line)
        if use_match:
            conds = use_match.group("conds").split()
            others = [name for name in conds if name != "h_main" and not name.startswith("!")]
            if others:
                for acl in others:
                    routes.append((number, use_match.group("backend"), acl))
            else:
                catch_all.append((number, use_match.group("backend")))

    return {
        "routes": routes,
        "catch_all": catch_all,
        "markers": markers,
        "mounts": mounts,
        "returns": returns,
        "deletes": deletes,
    }


def scan_template(path: Path, agent_helper: Path = AGENT_HELPER) -> tuple[list[str], set[str]]:
    """Return diagnostics for the absent-backend marker contract, plus marked mounts."""
    failures: list[str] = []
    where = display(path)
    parsed = parse_template(path)
    routes: list[tuple[int, str, str]] = parsed["routes"]  # type: ignore[assignment]
    markers: list[tuple[int, str, str, str]] = parsed["markers"]  # type: ignore[assignment]
    mounts: dict[str, str] = parsed["mounts"]  # type: ignore[assignment]
    returns: list[tuple[int, str, str]] = parsed["returns"]  # type: ignore[assignment]
    deletes: list[tuple[int, str]] = parsed["deletes"]  # type: ignore[assignment]

    # A lint that matches nothing passes forever.
    if not routes or not markers:
        failures.append(
            f"{where}: found {len(routes)} gated routes and {len(markers)} absent-backend "
            "markers; the frontend was not recognised, so this lint is not checking anything"
        )
        return failures, set()

    marked = {(acl, backend) for _, _, acl, backend in markers}
    marked_backends = {backend for _, _, _, backend in markers}

    # 1. Every route that is not deliberately exempt has to be marked.
    for number, backend, acl in routes:
        if backend in UNMARKED_BACKENDS:
            continue
        if (acl, backend) not in marked:
            failures.append(
                f"{where}:{number}: use_backend {backend} if h_main {acl} has no "
                f"absent-backend marker. Add `http-request set-var(txn.gw_absent) "
                f"str(<mount>) if h_main {acl} {{ nbsrv({backend}) eq 0 }}`, or name "
                f"{backend} in UNMARKED_BACKENDS with a reason. Without it a profile "
                "that does not deploy this service answers a bare 503, which a caller "
                "cannot tell from the service being deployed and failing"
            )

    # 2. Markers have to describe routes that exist, with the right backend.
    routed = {(acl, backend) for _, backend, acl in routes}
    routed_acls = {acl for _, _, acl in routes}
    for number, _, acl, backend in markers:
        if (acl, backend) in routed:
            continue
        if acl in routed_acls:
            actual = sorted({real for _, real, real_acl in routes if real_acl == acl})
            failures.append(
                f"{where}:{number}: marker for {acl} checks nbsrv({backend}), but {acl} "
                f"routes to {', '.join(actual)}. The marker would report the wrong "
                "service absent, or never fire at all"
            )
        else:
            failures.append(
                f"{where}:{number}: marker keys on {acl}, which gates no use_backend in "
                "this frontend; it can never fire"
            )

    # 3. The header value has to name the route it speaks for.
    for number, name, acl, _ in markers:
        mount = mounts.get(acl)
        if mount and name != mount:
            failures.append(
                f"{where}:{number}: marker for {acl} (mounted at /{mount}) reports "
                f"{name!r}; the header and the log line would name the wrong service"
            )

    # 4/5. Exactly one synthesised reply, and the forgery strip.
    expected = helper_header(agent_helper)
    if expected is None:
        failures.append(
            f"{display(agent_helper)}: GATEWAY_UNAVAILABLE_HEADER is not defined, so "
            "the agent has no marker to look for and the gateway's is unreadable"
        )
    marker_returns = [entry for entry in returns if "gw_absent" in entry[2]]
    if len(marker_returns) != 1:
        failures.append(
            f"{where}: expected exactly one `http-request return status 503` gated on "
            f"gw_absent, found {len(marker_returns)}; the marked reply is what makes an "
            "absent backend distinguishable"
        )
    elif expected is not None and marker_returns[0][1].lower() != expected.lower():
        failures.append(
            f"{where}:{marker_returns[0][0]}: the synthesised 503 carries "
            f"{marker_returns[0][1]!r} but {display(agent_helper)} looks for "
            f"{expected!r}; the tolerance would never fire and an absent optional "
            "service would hard-fail exactly as it did before this was fixed"
        )
    if expected is not None and not any(header.lower() == expected.lower() for _, header in deletes):
        failures.append(
            f"{where}: no `http-response del-header {expected}` in the frontend. "
            "http-response rules run only on backend-origin responses, so this is what "
            "stops a live service claiming to be absent and having its callers skip it"
        )

    return failures, {name for _, name, _, _ in markers}


def scan_readme(path: Path, marked: set[str]) -> list[str]:
    """The runbook has to explain what the marker means to an operator."""
    if not marked:
        return []
    where = display(path)
    text = path.read_text()
    if README_SECTION not in text:
        return [
            f"{where}: section {README_SECTION!r} is missing; an operator debugging a "
            "503 has no way to learn that the header decides what it means"
        ]
    section = text.split(README_SECTION, 1)[1].split("\n## ", 1)[0]
    failures: list[str] = []
    header = helper_header(AGENT_HELPER)
    if header and header not in section:
        failures.append(f"{where}: section {README_SECTION!r} never names {header}")
    for name in sorted(marked):
        if name not in section:
            failures.append(
                f"{where}: /{name} answers a marked 503 when absent, but the runbook "
                f"section {README_SECTION!r} does not list it"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--readme", type=Path, default=README)
    parser.add_argument("--agent-helper", type=Path, default=AGENT_HELPER)
    args = parser.parse_args(argv)

    failures, marked = scan_template(args.template, args.agent_helper)
    failures += scan_readme(args.readme, marked)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Gateway absent-backend marker lint passed ({len(marked)} optional routes marked).")
    print(f"  marked: {', '.join(sorted(marked))}")
    print(f"  unmarked by design: {', '.join(sorted(UNMARKED_BACKENDS))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
