#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep the public browser origin and the in-deployment gateway origin distinct.

The deployment has two origins and they are not interchangeable:

  VSS_PUBLIC_*        what a browser or an off-host caller uses. Where the
                      platform terminates TLS outside the stack -- a Brev secure
                      link is https on 443 forwarding plain HTTP to 7777 -- this
                      is https and 443.
  VSS_GATEWAY_ORIGIN  HAProxy's own listener, always plain HTTP on HAPROXY_PORT.
                      This is what containers call each other on.

Two ways to confuse them, and both fail in production rather than at startup:

  * Deriving a browser-facing URL from the gateway origin hands the browser
    ``http://vss.local:7777/...`` -- a name that does not resolve off-host, and
    mixed content when the page itself came over https.
  * Deriving the gateway origin from the public values hands containers
    ``https://vss.local:443`` -- a listener that does not exist, so every
    service-to-service call fails.

Neither shows up in a single-host http deployment, because there the two origins
happen to coincide. This lint is what keeps them from being wired together in
the profile where nobody is looking.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_SUFFIX = ".env"

# Variables a browser, a report link or `vss configure --base-url` consumes.
# Each must be built from the public origin.
BROWSER_FACING = (
    "VST_EXTERNAL_URL",
    "VST_BASE_URL",
    "VSS_AGENT_EXTERNAL_URL",
    "VSS_AGENT_REPORTS_BASE_URL",
    "VSS_PUBLIC_URL",
)

# Variables in-deployment callers consume. Each must stay on the gateway origin.
GATEWAY_FACING = ("VSS_GATEWAY_ORIGIN", "VSS_GATEWAY_HOST", "VSS_GATEWAY_PORT")

PUBLIC_TOKEN = re.compile(r"\$\{?VSS_PUBLIC_")
GATEWAY_TOKEN = re.compile(r"\$\{?VSS_GATEWAY_")
ASSIGNMENT = re.compile(r"^\s*(?P<name>[A-Z0-9_]+)\s*=\s*(?P<value>.*)$")


def is_env_file(path: Path) -> bool:
    """Match env files including a bare ``.env``, whose ``Path.suffix`` is empty."""
    return path.suffix == ENV_SUFFIX or path.name == ENV_SUFFIX


def default_paths() -> list[Path]:
    """Return the profile env files that define the two origins."""
    roots = (
        ROOT / "deploy/docker/developer-profiles",
        ROOT / "deploy/docker/industry-profiles",
    )
    paths: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            # Only a profile's own top-level env files declare the origins;
            # generated.env is a runtime artefact and is not checked in.
            if (
                is_env_file(path)
                and len(path.relative_to(root).parts) == 2
                and path.name != "generated.env"
            ):
                paths.append(path)
    return sorted(paths)


def scan_paths(paths: Iterable[Path]) -> list[str]:
    """Return actionable diagnostics for origins wired to the wrong source."""
    failures: list[str] = []
    for path in paths:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        for line_number, line in enumerate(path.read_text().splitlines(), start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            match = ASSIGNMENT.match(line)
            if not match:
                continue
            name, value = match.group("name"), match.group("value")

            if name in BROWSER_FACING and GATEWAY_TOKEN.search(value):
                failures.append(
                    f"{display_path}:{line_number}: {name} is derived from "
                    f"VSS_GATEWAY_* ({value.strip()!r}); browser-facing URLs must "
                    "come from VSS_PUBLIC_* or the link is unreachable off-host"
                )
            if name in GATEWAY_FACING and PUBLIC_TOKEN.search(value):
                failures.append(
                    f"{display_path}:{line_number}: {name} is derived from "
                    f"VSS_PUBLIC_* ({value.strip()!r}); the gateway origin is "
                    "HAProxy's own listener and must not inherit the public "
                    "scheme or port"
                )
            # An empty origin renders "/elasticsearch" and fails later inside an
            # HTTP client as a malformed request instead of here as a missing
            # configuration value.
            if name in GATEWAY_FACING and value.strip() in {"", '""', "''"}:
                failures.append(
                    f"{display_path}:{line_number}: {name} is empty; an empty "
                    "origin renders a relative URL that fails as a malformed "
                    "request rather than as a configuration error"
                )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures = scan_paths(paths)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(f"Public/gateway origin split lint passed ({len(paths)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
