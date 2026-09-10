#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Keep every redirect an nginx service in this tree emits relative.

nginx absolutises a ``Location`` that begins with ``/`` into
``scheme://host:port/...`` and the port it substitutes is the listener's own,
not the one the client reached.  For the VIOS ingress -- which listens on 30888
and is reached through the gateway on 7777 -- that turned
``location = /vst { return 301 /vst/; }`` into
``Location: http://<Host>:30888/vst/``: off the gateway origin and onto an
internal service port.  Where TLS terminates outside the deployment the same
header is additionally plain http from an https page, so a browser blocks it as
mixed content as well as being unable to reach it.

``absolute_redirect off;`` makes the header relative, which resolves against the
request URL (RFC 7231 7.1.2) and is therefore correct on every origin without
the service being told the external scheme, host or port.

Two things this lint has to get right, because a simpler version passes
vacuously:

  * **Scope.**  ``absolute_redirect`` is inherited, so it counts only when it
    sits on the block that emits the redirect or on one of its ancestors.  A
    copy parked in an unrelated ``location`` governs nothing.
  * **Comments.**  The configs explain the fix in prose that quotes
    ``return 301 /vst/`` and ``absolute_redirect``, so a substring search finds
    both in a file that has neither in force.  Comments are stripped first.

Files are discovered by content rather than from a list, so a new nginx config
that redirects is covered the day it lands instead of the day someone
remembers to add it here.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Go template actions are removed before braces are counted: the Helm variant of
# the VIOS config is a text/template, and `{{- if ... }}` would otherwise read
# as an nginx block opening.
GO_TEMPLATE_ACTION = re.compile(r"\{\{.*?\}\}", re.DOTALL)

DIRECTIVE = re.compile(r"^(?P<name>[a-z_]+)\b\s*(?P<args>.*?)\s*;?$")
BLOCK_OPEN = re.compile(r"^(?P<head>[^{}]*?)\s*\{$")

# A redirect nginx generates itself, as opposed to one proxied from a backend.
REDIRECT_STATUSES = {"301", "302", "303", "307", "308"}

# `alias` and `root` make nginx serve a filesystem tree, and a request for a
# directory without the trailing slash gets nginx's own directory-slash
# redirect -- the same absolutised Location as an explicit `return`, from a
# directive that never mentions redirecting.
FILESYSTEM_DIRECTIVES = {"alias", "root"}


def strip_comments(text: str) -> str:
    """Drop nginx ``#`` comments, honouring single and double quoting.

    nginx ends a comment at the newline and does not recognise ``#`` inside a
    quoted string, which matters here: ``log_format`` and the CORS maps in the
    Helm template carry quoted values.
    """
    kept: list[str] = []
    for line in text.splitlines():
        quote: str | None = None
        cut = len(line)
        for index, char in enumerate(line):
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in "\"'":
                quote = char
                continue
            if char == "#":
                cut = index
                break
        kept.append(line[:cut])
    return "\n".join(kept)


def looks_like_nginx(text: str) -> bool:
    """An nginx server config, not a logstash/redis/postgres file that ends .conf."""
    return bool(re.search(r"\bserver\s*\{", text)) and "listen" in text


def analyse(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Return the redirect-emitting scopes and the scopes that disable absolutising.

    Scopes are rendered as ``/``-joined block heads, e.g.
    ``http > server > location = /vst``, so a diagnostic names the block a
    reviewer has to look at.
    """
    body = GO_TEMPLATE_ACTION.sub("", strip_comments(text))

    stack: list[str] = []
    emitters: list[tuple[str, str]] = []
    disabled_scopes: list[str] = []
    enabled_scopes: list[str] = []

    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue

        # A line may close several blocks and/or open one.
        while line.startswith("}"):
            if stack:
                stack.pop()
            line = line[1:].strip()
        if not line:
            continue

        block = BLOCK_OPEN.match(line)
        if block:
            stack.append(" ".join(block.group("head").split()))
            continue

        scope = " > ".join(stack) or "(top level)"
        directive = DIRECTIVE.match(line)
        if not directive:
            continue
        name, args = directive.group("name"), directive.group("args")

        if name == "return":
            fields = args.split()
            # `return <code> <url>` with a code that redirects. A bare
            # `return 200 'ok'` sets no Location and is not a redirect.
            if fields and fields[0] in REDIRECT_STATUSES:
                emitters.append((scope, line))
        elif name == "rewrite" and args.split()[-1:] in (["permanent"], ["redirect"]):
            emitters.append((scope, line))
        elif name in FILESYSTEM_DIRECTIVES:
            emitters.append((scope, line))
        elif name == "absolute_redirect":
            if args.split()[:1] == ["off"]:
                disabled_scopes.append(scope)
            else:
                enabled_scopes.append(scope)

    return emitters, [*disabled_scopes, *(f"!{s}" for s in enabled_scopes)]


def governed(scope: str, disabled: Iterable[str]) -> bool:
    """Is ``scope`` covered by an ``absolute_redirect off`` on it or an ancestor?

    Inheritance runs downwards only, so an ancestor's setting applies and a
    sibling's does not.  An explicit ``absolute_redirect on`` deeper than the
    ``off`` would re-enable absolutising, so it is reported rather than ignored.
    """
    for entry in disabled:
        if entry.startswith("!"):
            continue
        if scope == entry or scope.startswith(f"{entry} > "):
            return True
    return False


def default_paths() -> list[Path]:
    """Every nginx config in the tree, template variants included."""
    paths: list[Path] = []
    for pattern in ("*.conf", "*.conf.template"):
        for path in ROOT.rglob(pattern):
            if ".git" in path.parts or "node_modules" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if looks_like_nginx(text):
                paths.append(path)
    return sorted(paths)


def scan_paths(paths: Iterable[Path]) -> tuple[list[str], int]:
    """Return actionable diagnostics plus the number of files that redirect."""
    failures: list[str] = []
    checked = 0
    for path in paths:
        try:
            display_path = path.relative_to(ROOT)
        except ValueError:
            display_path = path
        text = path.read_text(encoding="utf-8")
        emitters, disabled = analyse(text)
        if not emitters:
            continue
        checked += 1

        for scope in [entry[1:] for entry in disabled if entry.startswith("!")]:
            failures.append(
                f"{display_path}: `absolute_redirect on` in `{scope}` re-enables "
                "absolutising below it; nginx would resume substituting its own "
                "listener port into Location"
            )

        ungoverned = [(s, d) for s, d in emitters if not governed(s, disabled)]
        if ungoverned:
            scope, directive = ungoverned[0]
            failures.append(
                f"{display_path}: `{directive}` in `{scope}` can make nginx emit "
                "an absolute Location, and no `absolute_redirect off;` governs "
                f"that block ({len(ungoverned)} such directive(s) in this file). "
                "Add `absolute_redirect off;` to the enclosing `http {}` so the "
                "Location stays relative and correct on every origin."
            )
    return failures, checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args(argv)

    paths = args.paths or default_paths()
    failures, checked = scan_paths(paths)
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print(
        f"nginx relative-redirect lint passed "
        f"({checked} redirect-emitting config(s) of {len(paths)} nginx config(s))."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
