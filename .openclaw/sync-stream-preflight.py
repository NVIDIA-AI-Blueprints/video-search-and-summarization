# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import re
import sys
from pathlib import Path


HEADING = "## Live-stream summarization and report preflight"
SECTION = re.compile(r"^" + re.escape(HEADING) + r"\n.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)


def sync_preflight(template, target):
    match = SECTION.search(template)
    if match is None:
        raise ValueError("Template is missing the stream preflight")
    section = match.group().rstrip() + "\n\n"
    existing = target.read_text() if target.exists() else template
    remaining = SECTION.sub("", existing)
    first_section = re.search(r"^## ", remaining, re.MULTILINE)
    insertion = first_section.start() if first_section else len(remaining)
    prefix = remaining[:insertion]
    if not prefix or prefix.endswith("\n\n"):
        separator = ""
    elif prefix.endswith("\n"):
        separator = "\n"
    else:
        separator = "\n\n"
    updated = prefix + separator + section + remaining[insertion:]
    if updated != existing or not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(updated)


if __name__ == "__main__":
    sync_preflight(sys.argv[1], Path(sys.argv[2]))
