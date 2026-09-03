#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the gateway's two Host allowlists from drifting apart.

``haproxy.cfg.template`` declares the deployment's origins twice, and the two
lists do different jobs:

  known_host  gates the ``http-request deny`` that answers an unrecognised
              origin with 404 and ``x-vss-gateway-deny: unknown-host``.
  h_main      gates every ``use_backend`` in the frontend, so it decides
              whether an admitted request reaches a service at all.

They are written out longhand rather than shared, because HAProxy has no way to
alias one ACL to another. That makes them a matched pair maintained by hand, and
the failure mode when they diverge is genuinely nasty:

  * An origin in ``known_host`` but missing from ``h_main`` is admitted and then
    routed nowhere. HAProxy answers **503 with no diagnostic header at all** --
    indistinguishable from a backend being down, and the ``x-vss-gateway-deny``
    header that exists to name this class of problem is absent precisely
    because the request was not denied. Meanwhile every other origin keeps
    working, so the breakage is invisible unless someone tests the new name.
  * An origin in ``h_main`` but missing from ``known_host`` is denied 404 before
    routing ever runs, so the ``h_main`` entry is dead code that reads as
    support for an origin the gateway rejects.

Neither shows up on a single-host deployment, where the operator only ever uses
one origin and it happens to be in both lists. The case that finds it is a
deployment reached by a DNS name that is not the box's own address -- which is
the topology this lint exists to protect.

Also asserted here, for the same "an operator following the runbook succeeds"
reason:

  * Every declared origin appears both bare and with an explicit port, because
    a client may or may not send the default port in ``Host``.
  * Every ``use_backend`` in the frontend is gated on ``h_main``. An ungated
    route would serve any Host whatsoever, quietly punching a hole in the
    allowlist.
  * Every variable the ACLs read is named in the runbook section that tells
    operators which origins are declared, so documentation cannot drift away
    from behaviour.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

TEMPLATE = ROOT / "deploy/docker/services/infra/haproxy/haproxy.cfg.template"
README = ROOT / "deploy/docker/README.md"

# The runbook heading under which the declared origins are listed. Matched on
# the heading rather than a line number so ordinary edits above it do not break
# this lint.
README_SECTION = "### The origin callers use has to be declared"

ACL_LINE = re.compile(r"^\s*acl\s+(?P<name>known_host|h_main)\s+hdr\(host\)\s+-i\s+(?P<value>\S.*?)\s*$")
USE_BACKEND = re.compile(r"^\s*use_backend\s+(?P<backend>\S+)(?P<rest>.*)$")
INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def unquote(value: str) -> str:
    """Strip the double quotes HAProxy needs for ``${VAR}`` expansion."""
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def has_port(value: str) -> bool:
    """True when the origin carries an explicit port.

    ``${VSS_PUBLIC_HOST}:${VSS_PUBLIC_PORT}`` does; ``${VSS_PUBLIC_HOST}`` does
    not. Interpolations are blanked first so a colon inside one is not mistaken
    for the separator.
    """
    return ":" in INTERPOLATION.sub("", value)


def variables(value: str) -> set[str]:
    """Return the environment variables an origin expression reads."""
    return {match[2:-1] for match in INTERPOLATION.findall(value)}


def scan_template(path: Path) -> tuple[list[str], set[str]]:
    """Return diagnostics for the Host ACLs, plus the variables they read."""
    failures: list[str] = []
    text = path.read_text()
    try:
        display = path.relative_to(ROOT)
    except ValueError:
        display = path

    declared: dict[str, list[str]] = {"known_host": [], "h_main": []}
    for line in text.splitlines():
        match = ACL_LINE.match(line)
        if match:
            declared[match.group("name")].append(unquote(match.group("value")))

    # A lint that matches nothing passes forever.
    if not declared["known_host"] or not declared["h_main"]:
        failures.append(
            f"{display}: found {len(declared['known_host'])} known_host and "
            f"{len(declared['h_main'])} h_main entries; the Host allowlists were "
            "not recognised, so this lint is not checking anything"
        )
        return failures, set()

    only_known = sorted(set(declared["known_host"]) - set(declared["h_main"]))
    only_main = sorted(set(declared["h_main"]) - set(declared["known_host"]))
    for value in only_known:
        failures.append(
            f"{display}: {value!r} is declared for known_host but not for "
            "h_main; the gateway would admit that Host and then match no "
            "use_backend, answering 503 with no x-vss-gateway-deny header -- "
            "which reads as a dead backend rather than a half-declared origin"
        )
    for value in only_main:
        failures.append(
            f"{display}: {value!r} is declared for h_main but not for "
            "known_host; the deny rule rejects that Host before routing runs, "
            "so the h_main entry is dead code"
        )

    for name, values in declared.items():
        bare = {value for value in values if not has_port(value)}
        ported = {value.rsplit(":", 1)[0] for value in values if has_port(value)}
        for value in sorted(bare - ported):
            failures.append(
                f"{display}: {name} declares {value!r} without a ported form; a "
                "client that sends the port in Host would be refused"
            )
        for value in sorted(ported - bare):
            failures.append(
                f"{display}: {name} declares {value!r} only with a port; a "
                "client that omits the default port in Host would be refused"
            )

    # Every route must be gated on the allowlist. An ungated use_backend
    # serves any Host at all, which is the allowlist's whole point undone.
    in_frontend = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("frontend "):
            in_frontend = True
            continue
        if line and not line[0].isspace():
            in_frontend = False
        if not in_frontend:
            continue
        match = USE_BACKEND.match(line)
        if match and not re.search(r"\bif\s+h_main\b", match.group("rest")):
            failures.append(
                f"{display}:{number}: use_backend {match.group('backend')} is not "
                "gated on h_main, so it would route a request whose Host the "
                "allowlist never declared"
            )

    used: set[str] = set()
    for values in declared.values():
        for value in values:
            used |= variables(value)
    return failures, used


def scan_readme(path: Path, used: set[str]) -> list[str]:
    """Every variable the ACLs read must be named in the runbook."""
    if not used:
        return []
    failures: list[str] = []
    try:
        display = path.relative_to(ROOT)
    except ValueError:
        display = path
    text = path.read_text()
    if README_SECTION not in text:
        return [
            f"{display}: section {README_SECTION!r} is missing; the runbook has "
            "to tell operators which origins the gateway declares"
        ]
    section = text.split(README_SECTION, 1)[1].split("\n## ", 1)[0]
    # HAPROXY_PORT is the listener port rather than an origin, and the prose
    # names it as "the port" instead of by variable. Everything that selects an
    # origin has to be named outright.
    for name in sorted(used - {"HAPROXY_PORT"}):
        if name not in section:
            failures.append(
                f"{display}: the Host allowlist reads {name} but the runbook "
                f"section {README_SECTION!r} never names it, so an operator "
                "cannot tell which value admits the origin they use"
            )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--readme", type=Path, default=README)
    args = parser.parse_args(argv)

    failures, used = scan_template(args.template)
    failures += scan_readme(args.readme, used)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Gateway Host allowlist lint passed ({len(used)} origin variables).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
