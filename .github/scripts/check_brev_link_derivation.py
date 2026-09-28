#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Make the deployment read a Brev secure link rather than compose one.

A Brev secure link is three independent facts -- a label, a domain, and the host
port the link forwards to -- and all three are chosen when the environment is
created. None of them follows from ``BREV_ENV_ID``. A real instance serves
``jupyter-<id>.gobrev.dev`` on 443 forwarding to host port 8888: the label is a
name and not a port, the domain is not one a template would have offered, and
the host port is not the gateway's default.

So any ``<prefix>-<id>.<domain>`` template is a guess, and a guess here is worse
than declining to answer. ``VSS_PUBLIC_HOST`` feeds the gateway's Host
**allowlist**. A wrong value does not degrade the deployment, it rejects every
request off-host with ``x-vss-gateway-deny: unknown-host`` -- long after the
deploy script exited reporting success, and with nothing in the symptom pointing
back at the hostname that caused it. That is the failure this lint exists to
prevent, and it is why the honest behaviour is a loud failure naming the
override rather than a plausible-looking hostname.

Two things are therefore forbidden in the deployment scripts and the committed
profile config, and neither of them bakes a URL scheme into CI:

  * **A hardcoded Brev link domain.** In a deploy script or a profile env file a
    Brev domain literal can only be a fallback guess, because the real value is
    per-environment. This includes ``gobrev.dev`` -- pinning the domain this
    defect was found on would repeat the defect, just with fresher data.
  * **Choosing the domain by probing ``netbird``.** NetBird describes the
    overlay mesh; it knows nothing about how secure links are named. Asking it
    which public DNS domain to use is a category error that can only ever
    produce a guess, however healthy the client looks.

And one thing is required: a script that sets ``VSS_PUBLIC_HOST`` for a Brev
environment must consult the environment context file, which is the only
authoritative record of the link set on the instance.

Docs and the agent's URL-*parsing* helpers are deliberately out of scope. They
name known Brev domains to recognise or explain a hostname, which is the
opposite of inventing one.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Brev has served secure links from all of these. The list is not a scheme to
# match against -- it is the set of literals that must never be a default.
BREV_LINK_DOMAINS = (
    "brevlab.com",
    "apps.run.brev.nvidia.com",
    "gobrev.dev",
)

# Where a Brev domain literal could only ever be a guess: the scripts that stand
# a deployment up, and the profile config they read.
SCRIPT_DIRS = ("deploy/docker/scripts",)

# Test and proof harnesses are excluded on purpose. Pinning a hostname is how you
# drive the override path deterministically, and standing in for a Brev origin is
# the whole job of the gateway harness -- neither is a deployment deriving a link.
EXCLUDED_DIR_NAMES = frozenset({"test-scripts", "gateway-harness"})
PROFILE_DIRS = (
    "deploy/docker/developer-profiles",
    "deploy/docker/industry-profiles",
)

SHELL_SUFFIXES = (".sh", ".bash")
ENV_SUFFIX = ".env"

DOMAIN_RE = re.compile("|".join(re.escape(domain) for domain in BREV_LINK_DOMAINS))
NETBIRD_RE = re.compile(r"\bnetbird\b")
CONTEXT_RE = re.compile(r"BREV_ENVIRONMENT_CONTEXT_PATH|environment-context\.json")
PUBLIC_HOST_ASSIGN_RE = re.compile(r"""VSS_PUBLIC_HOST["']?\s""")
BREV_ENV_ID_RE = re.compile(r"\bBREV_ENV_ID\b")


def strip_comment(line: str) -> str:
    """Drop a trailing ``#`` comment.

    Both shell and env files explain the rule in prose beside the code, so a lint
    that read comments would fail on the explanation of what it enforces.
    """
    quoted = False
    quote_char = ""
    for index, char in enumerate(line):
        if quoted:
            if char == quote_char:
                quoted = False
            continue
        if char in "\"'":
            quoted = True
            quote_char = char
        elif char == "#":
            return line[:index]
    return line


def is_env_file(path: Path) -> bool:
    """Match env files including a bare ``.env``, whose ``Path.suffix`` is empty."""
    return path.suffix == ENV_SUFFIX or path.name == ENV_SUFFIX


def is_excluded(path: Path) -> bool:
    """True when *path* sits under a test or proof harness directory."""
    return any(part in EXCLUDED_DIR_NAMES for part in path.parts)


def default_paths() -> list[Path]:
    """Return the deploy scripts and profile env files this lint covers."""
    paths: list[Path] = []
    for directory in SCRIPT_DIRS:
        root = ROOT / directory
        if not root.is_dir():
            continue
        paths.extend(
            path
            for path in sorted(root.rglob("*"))
            if path.is_file() and path.suffix in SHELL_SUFFIXES and not is_excluded(path)
        )
    for directory in PROFILE_DIRS:
        root = ROOT / directory
        if not root.is_dir():
            continue
        paths.extend(path for path in sorted(root.rglob("*")) if path.is_file() and is_env_file(path))
    return paths


def scan_paths(paths: Iterable[Path]) -> list[str]:
    """Return one message per violation, empty when the tree is clean."""
    failures: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        try:
            label: str = str(path.relative_to(ROOT))
        except ValueError:
            label = str(path)

        derives_public_host = False
        for number, raw in enumerate(text.splitlines(), start=1):
            line = strip_comment(raw)
            if not line.strip():
                continue

            domain = DOMAIN_RE.search(line)
            if domain:
                failures.append(
                    f"{label}:{number}: hardcodes the Brev link domain "
                    f"{domain.group(0)!r}. A secure link's domain is chosen per "
                    f"environment, so a literal here is a fallback guess; read it "
                    f"from the Brev environment context instead."
                )

            if NETBIRD_RE.search(line) and DOMAIN_RE.search(text):
                failures.append(
                    f"{label}:{number}: selects a Brev link domain by probing "
                    f"netbird. NetBird describes the overlay mesh, not secure-link "
                    f"naming, so it cannot answer this; read the Brev environment "
                    f"context instead."
                )

            if PUBLIC_HOST_ASSIGN_RE.search(line) and BREV_ENV_ID_RE.search(text):
                derives_public_host = True

        if derives_public_host and not CONTEXT_RE.search(text):
            failures.append(
                f"{label}: sets VSS_PUBLIC_HOST for a Brev environment without "
                f"consulting the Brev environment context, which is the only "
                f"authoritative record of the instance's link set. A composed "
                f"hostname lands on the gateway's Host allowlist and fails every "
                f"request with 'x-vss-gateway-deny: unknown-host'."
            )

    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="files to check (default: the deploy scripts and profile env files)",
    )
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures = scan_paths(paths)
    for failure in failures:
        print(failure, file=sys.stderr)
    if failures:
        print(
            f"\n{len(failures)} Brev link derivation problem(s). "
            f"A Brev secure link must be read from the environment context, never composed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
