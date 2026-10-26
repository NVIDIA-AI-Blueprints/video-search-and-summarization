#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Map a Fern publish URL to the version that holds the PR checkout.

Fern's unversioned site URL is the first listed version (the latest
release). A develop-targeted PR is the Dev version at ``<url>/dev``.
A release-targeted PR is inserted as the default version, so the
unversioned URL is already that preview.

Usage: preview_docs_href.py <published-url> [release]
  release empty    -> <published-url>/dev
  release X.Y.Z    -> <published-url>
Writes the href to stdout.
"""

from __future__ import annotations

import sys

DEV_SLUG = "dev"


def preview_docs_href(published_url: str, release: str | None = None) -> str:
    base = published_url.rstrip("/")
    if not base:
        raise ValueError("published URL is empty")
    if release:
        return base
    return f"{base}/{DEV_SLUG}"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or len(args) > 2:
        print(
            "usage: preview_docs_href.py <published-url> [release]",
            file=sys.stderr,
        )
        return 2
    url, release = args[0], args[1] if len(args) == 2 else ""
    try:
        print(preview_docs_href(url, release or None))
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
