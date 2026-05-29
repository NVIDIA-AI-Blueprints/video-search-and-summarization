#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Patch OpenClaw's OpenAI chat-completions provider to avoid streaming tools.

Some OpenAI-compatible NVIDIA endpoints support tool calls only in
non-streaming mode. Current OpenClaw builds hardcode `stream: true` in
`buildOpenAICompletionsParams`, which prevents NemoClaw from invoking MCP
tools during headless CI. This helper applies a narrow compatibility patch to
the installed OpenClaw bundle until the upstream runtime exposes a config knob.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOTS = (
    Path("/usr/local/lib/node_modules/openclaw/dist"),
    Path.home() / ".local/lib/node_modules/openclaw/dist",
)
FUNCTION_RE = re.compile(
    r"""
    (?:function\s+buildOpenAICompletionsParams\s*\()
    |
    (?:(?:const|let|var)\s+buildOpenAICompletionsParams\s*=\s*(?:async\s*)?(?:function\s*)?\(?)
    |
    (?:buildOpenAICompletionsParams\s*:\s*(?:async\s*)?(?:function\s*)?\(?)
    """,
    re.VERBOSE,
)
STREAM_TRUE_RE = re.compile(r"\bstream:\s*true\b")
STREAM_FALSE_RE = re.compile(r"\bstream:\s*false\b")


def _drop_stream_options(lines: list[str], start: int, limit: int) -> tuple[list[str], bool]:
    output: list[str] = []
    dropped = False
    index = 0
    while index < len(lines):
        line = lines[index]
        in_patch_window = start <= index <= limit
        if in_patch_window and re.match(r"^\s*stream_options\s*:", line):
            dropped = True
            balance = line.count("{") - line.count("}")
            index += 1
            while index < len(lines) and balance > 0:
                balance += lines[index].count("{") - lines[index].count("}")
                index += 1
            continue
        output.append(line)
        index += 1
    return output, dropped


def patch_source(source: str) -> tuple[str, bool, bool]:
    """Return `(source, found_target, changed)` for one JS bundle."""
    lines = source.splitlines(keepends=True)
    for idx, line in enumerate(lines):
        if not FUNCTION_RE.search(line):
            continue
        limit = min(len(lines) - 1, idx + 250)
        window = "".join(lines[idx : limit + 1])
        if STREAM_FALSE_RE.search(window):
            patched_lines, dropped = _drop_stream_options(lines, idx, limit)
            return "".join(patched_lines), True, dropped
        for stream_idx in range(idx, limit + 1):
            if STREAM_TRUE_RE.search(lines[stream_idx]):
                lines[stream_idx] = STREAM_TRUE_RE.sub("stream: false", lines[stream_idx], count=1)
                lines, _ = _drop_stream_options(lines, idx, limit)
                return "".join(lines), True, True
        return source, True, False
    return source, False, False


def patch_path(path: Path) -> tuple[bool, bool]:
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False, False
    updated, found_target, changed = patch_source(original)
    if changed:
        path.write_text(updated, encoding="utf-8")
    return found_target, changed


def iter_js_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix == ".js":
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.js")))
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "roots",
        nargs="*",
        type=Path,
        default=list(DEFAULT_ROOTS),
        help="OpenClaw dist roots or JS files to patch",
    )
    args = parser.parse_args(argv)

    found = 0
    changed = 0
    for file_path in iter_js_files(args.roots):
        found_target, file_changed = patch_path(file_path)
        found += int(found_target)
        changed += int(file_changed)
        if file_changed:
            print(f"patched {file_path}")

    if found == 0:
        print("WARN: no buildOpenAICompletionsParams target found; OpenClaw layout may have changed", file=sys.stderr)
        return 0
    print(f"OpenClaw streaming compatibility targets={found} changed={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
