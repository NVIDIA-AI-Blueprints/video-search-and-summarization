#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the loopback-bound host publishes loopback-bound, and unused from inside.

``deploy/docker/services/infra/compose.yml`` publishes Elasticsearch and Phoenix
as ``${X_HOST_BIND:-127.0.0.1}:${X_HOST_PORT:-<port>}:<port>``. Both back ends
are unauthenticated -- Elasticsearch runs with ``xpack.security.enabled: false``
and Phoenix's OTLP receiver accepts any span offered -- so a wildcard bind
publishes a read *and* write surface to every interface the host has, and
bypasses the ``/elasticsearch`` allowlist the gateway enforces, which is
otherwise the only authorisation control in front of the cluster.

Narrowing a publish only breaks what was reaching it *through* the publish, and
inside a Compose deployment nothing needs to: a container on the project network
resolves ``elasticsearch``/``phoenix`` by name, which no bind address affects.
That is what makes the loopback default safe, and it is an invariant rather than
an observation -- so it is checked, in both directions.

**A caller that dials the publish is the regression.** The smartcities profile's
Kibana dashboard importer set ``ES_URL: http://${HOST_IP}:${ELASTICSEARCH_HOST_PORT:-9200}``
on the stated grounds that host networking made the service names unresolvable.
Nothing set ``network_mode: host`` for it; it ran on the project network, where
``elasticsearch`` resolved fine and ``${HOST_IP}`` -- a LAN address, from ``ip
route get 1.1.1.1`` in ``dev-profile.sh`` -- did not answer once the publish went
loopback. The readiness probe exhausted its ten attempts, the container exited 1,
and the ITS dashboard was silently never imported. Nothing else failed, which is
why this is a lint: the deployment comes up and only a dashboard is missing.

**A profile that sets the bind wide is the other regression.** The variable
exists so an *operator* can widen the publish for their own host, deliberately,
from the environment. A profile setting ``X_HOST_BIND=0.0.0.0`` restores the
exposure for every deployment of that profile while looking like configuration.

**And the premise has to hold.** Both halves above are only worth checking while
the publish is actually narrow. Fold the bind back into the port variable, or
default it to ``0.0.0.0``, and this lint would be guarding nothing while
reporting success -- so the publish shape is checked too, and a guarded service
that disappears from the Compose file is a finding rather than a skip.

Scoped to what configures containers: Compose files, env files and shell under
``deploy/docker``. Notebooks and Markdown are excluded because they print host
URLs for a human to open in a browser from wherever they are, which is a
documentation question and not a service reaching a dependency. ``test-scripts``
is excluded because it asserts *about* these values rather than setting them.

**Overlays are not skipped here**, unlike in ``check_gateway_host_acls.py``.
That lint skips a directory the ``include:`` graph does not reach -- smartcities
-- because it reads *env files*, and an overlay's env file is merged into a
profile's before deployment, so judging it alone reports variables the merge
supplies. A ``compose.yml`` is not merged: the smartcities launchable copies its
own over ``dev-profile-alerts/compose.yml``, so every service definition in an
overlay reaches a real deployment verbatim. The two rules disagree because the
inputs do.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

INFRA_COMPOSE = Path("deploy") / "docker" / "services" / "infra" / "compose.yml"


class Guarded:
    """A host publish that is deliberately bound to loopback."""

    def __init__(self, service: str, prefix: str, port: int) -> None:
        self.service = service
        self.bind_var = f"{prefix}_HOST_BIND"
        self.port_var = f"{prefix}_HOST_PORT"
        self.port = port


GUARDED = (
    Guarded("elasticsearch", "ELASTICSEARCH", 9200),
    Guarded("phoenix", "PHOENIX", 6006),
)

# The deployment's handles on a host address. Any of them in front of a guarded
# port means the caller is going out to the host publish rather than across the
# project network. `localhost` is deliberately absent: a host-side health probe
# on localhost is exactly what a loopback bind still serves.
HOST_ADDRESS_TOKENS = ("HOST_IP", "EXTERNAL_IP", "VSS_PUBLIC_HOST")

# A bind that publishes past loopback. `*` and an empty value are Docker's own
# spellings for "every interface".
WIDE_BINDS = ("0.0.0.0", "::", "*", "")

LOOPBACK_BINDS = ("127.0.0.1", "localhost", "::1")

# `NAME=value` in an env file or a Compose `environment:` list, and `NAME: value`
# in a Compose `environment:` mapping. `${NAME:-default}` does not match either,
# so an interpolation is never read as an assignment.
ASSIGNMENT = re.compile(
    r"^\s*-?\s*(?P<name>[A-Z_][A-Z0-9_]*)\s*(?:=|:\s)\s*(?P<value>.*?)\s*$"
)

SCANNED_SUFFIXES = (".yml", ".yaml", ".env", ".sh")


def references_host_address(value: str) -> bool:
    """True when *value* interpolates one of the host-address variables."""
    return any(
        f"${{{token}" in value or f"${token}" in value for token in HOST_ADDRESS_TOKENS
    )


def references_port(value: str, guarded: Guarded) -> bool:
    """True when *value* names the guarded service's host-published port."""
    return guarded.port_var in value or f":{guarded.port}" in value


def scan_paths(paths: Iterable[Path]) -> tuple[list[str], int]:
    """Return actionable diagnostics plus the number of references checked."""
    failures: list[str] = []
    checked = 0

    for path in paths:
        try:
            display = path.relative_to(ROOT)
        except ValueError:
            display = path

        for number, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1
        ):
            if line.lstrip().startswith("#"):
                continue

            where = f"{display}:{number}"
            assignment = ASSIGNMENT.match(line)

            for guarded in GUARDED:
                if assignment and assignment.group("name") == guarded.bind_var:
                    checked += 1
                    bind = assignment.group("value").strip("\"'")
                    if bind in WIDE_BINDS:
                        failures.append(
                            f"{where}: {guarded.bind_var}={bind or '<empty>'} "
                            f"publishes {guarded.service} on every interface for "
                            f"everyone who deploys this, and it answers "
                            f"unauthenticated. The variable is there for an "
                            f"operator to widen their own host from the "
                            f"environment -- drop it from the file and let the "
                            f"loopback default stand."
                        )
                    continue

                if not references_port(line, guarded):
                    continue

                checked += 1
                if references_host_address(line):
                    failures.append(
                        f"{where}: reaches {guarded.service} at a host address on "
                        f"its published port. That port is bound to loopback by "
                        f"default ({guarded.bind_var} in {INFRA_COMPOSE}), and a "
                        f"host address here is a LAN address, so the connection is "
                        f"refused. A container on the project network reaches it as "
                        f"'{guarded.service}:{guarded.port}', which no bind address "
                        f"affects; a client that needs the gateway instead uses "
                        f"${{VSS_GATEWAY_ORIGIN}}/{guarded.service}."
                    )

    return failures, checked


def scan_publishes(root: Path | None = None) -> list[str]:
    """Check each guarded publish still splits its bind out and defaults narrow.

    Returns diagnostics, or an empty list when every guarded service publishes
    ``${X_HOST_BIND:-<loopback>}:${X_HOST_PORT:-<port>}:<port>``. This is the
    premise the rest of the lint rests on, so a missing file or a service that
    no longer appears is reported rather than skipped.
    """
    path = (root or ROOT) / INFRA_COMPOSE
    if not path.is_file():
        return [
            f"{INFRA_COMPOSE}: not found, so the loopback publishes this lint "
            f"protects cannot be verified. If the infra Compose file moved, point "
            f"this check at it."
        ]

    text = path.read_text(encoding="utf-8")
    failures: list[str] = []

    for guarded in GUARDED:
        published = [
            line.strip()
            for line in text.splitlines()
            if not line.lstrip().startswith("#")
            and re.search(rf":\s*{guarded.port}\s*$", line)
            and guarded.port_var in line
        ]
        if not published:
            failures.append(
                f"{INFRA_COMPOSE}: no publish of {guarded.service} on "
                f"{guarded.port_var}. This lint exists to keep that publish narrow "
                f"and unused from inside the deployment; if the service moved or "
                f"stopped publishing, update GUARDED rather than leaving the check "
                f"pointed at nothing."
            )
            continue

        for entry in published:
            match = re.search(
                rf"\$\{{{guarded.bind_var}:-(?P<default>[^}}]*)\}}", entry
            )
            if match is None:
                failures.append(
                    f"{INFRA_COMPOSE}: {guarded.service} publishes {entry!r} with "
                    f"no {guarded.bind_var}. Without a bind of its own the port "
                    f"goes to every interface, where it answers unauthenticated. "
                    f"Keep the bind split from the port: every shipped profile "
                    f"sets {guarded.port_var} to a bare '{guarded.port}', so "
                    f"folding the bind into that variable leaves every real "
                    f"deployment wide open."
                )
            elif match.group("default") not in LOOPBACK_BINDS:
                failures.append(
                    f"{INFRA_COMPOSE}: {guarded.service} defaults "
                    f"{guarded.bind_var} to {match.group('default')!r}, which is "
                    f"not loopback. The default is what every deployment that does "
                    f"not think about it gets."
                )

    return failures


def default_paths() -> list[Path]:
    """Every file under ``deploy/docker`` that could configure one of these."""
    base = ROOT / "deploy" / "docker"
    return sorted(
        path
        for path in base.rglob("*")
        if path.is_file()
        and "test-scripts" not in path.parts
        and (path.suffix in SCANNED_SUFFIXES or path.name.endswith(".env"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures, checked = scan_paths(paths)

    # Explicit paths mean a caller is linting specific fixtures, so only the
    # repository-wide run checks the publishes those fixtures are measured
    # against.
    if not args.paths:
        failures = failures + scan_publishes()

    # A lint with nothing to check passes forever. Every profile sets the two
    # port variables, so a repository-wide run that finds no reference at all is
    # this check having lost track of the deployment, not a clean tree.
    if not args.paths and checked == 0:
        print(
            "No reference to a guarded host port found under deploy/docker -- this "
            "lint is checking nothing. Point GUARDED at wherever the loopback "
            "publishes moved.",
            file=sys.stderr,
        )
        return 1

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    extra = (
        ""
        if args.paths
        else f", {len(GUARDED)} loopback publish(es) intact in {INFRA_COMPOSE.name}"
    )
    print(
        f"Loopback host publish lint passed "
        f"({checked} reference(s) in {len(paths)} file(s){extra})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
