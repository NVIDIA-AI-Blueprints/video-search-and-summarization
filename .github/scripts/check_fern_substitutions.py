# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reject unintended Fern environment-variable substitutions in docs inputs."""

from __future__ import annotations

import json
import re
import sys
from argparse import ArgumentParser
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

TEXT_SUFFIXES = frozenset({".json", ".md", ".mdx", ".yaml", ".yml"})
JSON_SUFFIXES = frozenset({".json"})
YAML_SUFFIXES = frozenset({".yaml", ".yml"})

# Intentionally empty for the substitution-enablement migration. Add future
# build-time documentation variables explicitly; use a VSS_DOCS_ prefix.
ALLOWED_SUBSTITUTIONS: frozenset[str] = frozenset()
ALLOWED_SUBSTITUTION_PREFIX = "VSS_DOCS_"

# These mirror Fern's documented/implemented forms:
#   substitution: ${NAME}
#   literal:      \$\{NAME\}
SUBSTITUTION_PATTERN = re.compile(r"\$\{(?P<name>\w+)\}")
ESCAPE_PREFIX_PATTERN = re.compile(r"\\\$\\\{(?P<name>\w+)(?P<close>\\?\})")
YAML_HEX_ESCAPE_LENGTHS = {"x": 2, "u": 4, "U": 8}
YAML_SIMPLE_ESCAPES = {
    "0": "\0",
    "a": "\a",
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "v": "\v",
    "f": "\f",
    "r": "\r",
    "e": "\x1b",
    " ": " ",
    '"': '"',
    "/": "/",
    "\\": "\\",
    "N": "\x85",
    "_": "\xa0",
    "L": "\u2028",
    "P": "\u2029",
}


def iter_inputs(
    scan_roots: Iterable[Path], extra_inputs: Iterable[Path]
) -> Iterable[Path]:
    """Yield Fern text inputs without following directory symlinks."""
    for root in scan_roots:
        for path in sorted(root.rglob("*")):
            if (
                path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() in TEXT_SUFFIXES
            ):
                yield path
    for path in extra_inputs:
        if path.is_file():
            yield path


def find_allowlist_issues(allowed_substitutions: Collection[str]) -> list[str]:
    """Reject allowlisted names outside the documentation-only namespace."""
    return [
        f"allowlisted Fern substitution ${{{name}}} must use the "
        f"{ALLOWED_SUBSTITUTION_PREFIX} prefix"
        for name in sorted(allowed_substitutions)
        if not name.startswith(ALLOWED_SUBSTITUTION_PREFIX)
    ]


def find_issues(
    path: Path,
    text: str,
    allowed_substitutions: Collection[str] = ALLOWED_SUBSTITUTIONS,
    line_offset: int = 0,
) -> list[str]:
    """Return actionable errors for one Fern input file."""
    issues: list[str] = []

    for line_number, line in enumerate(text.splitlines(), start=1 + line_offset):
        for match in SUBSTITUTION_PATTERN.finditer(line):
            name = match.group("name")
            if name not in allowed_substitutions:
                issues.append(
                    f"{path}:{line_number}: unintended Fern substitution ${{{name}}}; "
                    f"write \\$\\{{{name}\\}} for a literal"
                )

        for match in ESCAPE_PREFIX_PATTERN.finditer(line):
            if match.group("close") != r"\}":
                name = match.group("name")
                issues.append(
                    f"{path}:{line_number}: malformed Fern literal escape for {name}; "
                    f"write \\$\\{{{name}\\}}"
                )

    return issues


def iter_string_values(value: Any) -> Iterable[str]:
    """Yield decoded string values from a JSON-compatible object."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from iter_string_values(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from iter_string_values(item)


def find_json_issues(
    path: Path,
    text: str,
    allowed_substitutions: Collection[str] = ALLOWED_SUBSTITUTIONS,
) -> list[str]:
    """Check decoded JSON strings, matching Fern's object-level substitution."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        return [f"{path}:{error.lineno}: cannot inspect invalid JSON: {error.msg}"]

    issues: list[str] = []
    for string_value in iter_string_values(value):
        issues.extend(find_issues(path, string_value, allowed_substitutions))
    return issues


def iter_yaml_double_quoted_scalars(text: str) -> Iterable[tuple[int, str, str]]:
    """Yield actual YAML double-quoted scalars decoded without a dependency.

    Fern parses YAML before applying substitutions. CI intentionally runs this
    guard with the Python standard library only, so this lexer distinguishes
    double-quoted scalars from quotes in plain, single-quoted, comment, and block
    contexts before decoding the escape forms that can conceal a placeholder.
    """
    single_quoted = False
    double_quoted = False
    plain_scalar = False
    block_scalar_indent: int | None = None
    start_line = 0
    raw: list[str] = []
    decoded: list[str] = []
    strip_double_quoted_indent = False

    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        indentation = len(line) - len(line.lstrip(" "))
        if block_scalar_indent is not None:
            if not line.strip() or indentation > block_scalar_indent:
                continue
            block_scalar_indent = None

        index = 0
        plain_scalar = False
        expect_scalar = True
        if double_quoted and strip_double_quoted_indent:
            while index < len(line) and line[index] in " \t":
                raw.append(line[index])
                index += 1
            strip_double_quoted_indent = False

        while index < len(line):
            character = line[index]

            if double_quoted:
                if character == '"':
                    yield start_line, "".join(raw), "".join(decoded)
                    double_quoted = False
                    raw = []
                    decoded = []
                    expect_scalar = False
                    index += 1
                    continue
                if character != "\\" or index + 1 >= len(line):
                    raw.append(character)
                    decoded.append(character)
                    index += 1
                    continue

                escape = line[index + 1]
                raw.extend((character, escape))
                if escape == "\n":
                    strip_double_quoted_indent = True
                    index += 2
                    continue
                if escape in YAML_SIMPLE_ESCAPES:
                    decoded.append(YAML_SIMPLE_ESCAPES[escape])
                    index += 2
                    continue
                if escape in YAML_HEX_ESCAPE_LENGTHS:
                    width = YAML_HEX_ESCAPE_LENGTHS[escape]
                    digits = line[index + 2 : index + 2 + width]
                    if len(digits) == width and all(
                        char in "0123456789abcdefABCDEF" for char in digits
                    ):
                        raw.append(digits)
                        try:
                            decoded.append(chr(int(digits, 16)))
                        except ValueError:
                            decoded.extend((character, escape, digits))
                        index += 2 + width
                        continue

                decoded.extend((character, escape))
                index += 2
                continue

            if single_quoted:
                if character == "'":
                    if index + 1 < len(line) and line[index + 1] == "'":
                        index += 2
                        continue
                    single_quoted = False
                    expect_scalar = False
                index += 1
                continue

            if plain_scalar:
                if character == "#" and (index == 0 or line[index - 1].isspace()):
                    break
                if character == ":" and (
                    index + 1 == len(line)
                    or line[index + 1].isspace()
                    or line[index + 1] in ",[]{}"
                ):
                    plain_scalar = False
                    expect_scalar = True
                elif character in ",]}":
                    plain_scalar = False
                    expect_scalar = character == ","
                index += 1
                continue

            if character == "#" and (index == 0 or line[index - 1].isspace()):
                break
            if character.isspace():
                index += 1
                continue
            if character == '"' and expect_scalar:
                double_quoted = True
                start_line = line_number
                raw = []
                decoded = []
                index += 1
                continue
            if character == "'" and expect_scalar:
                single_quoted = True
                index += 1
                continue
            if character in "|>" and expect_scalar:
                block_scalar_indent = indentation
                break
            if character in "[{,:" or (
                character in "-?"
                and index + 1 < len(line)
                and line[index + 1].isspace()
            ):
                expect_scalar = True
                index += 1
                continue
            if character in "]}":
                expect_scalar = False
                index += 1
                continue

            plain_scalar = True
            expect_scalar = False
            index += 1


def find_yaml_issues(
    path: Path,
    text: str,
    allowed_substitutions: Collection[str] = ALLOWED_SUBSTITUTIONS,
) -> list[str]:
    """Check raw YAML and decoded double-quoted scalar values."""
    issues = find_issues(path, text, allowed_substitutions)
    for start_line, raw, decoded in iter_yaml_double_quoted_scalars(text):
        if decoded != raw:
            issues.extend(
                find_issues(
                    path,
                    decoded,
                    allowed_substitutions,
                    line_offset=start_line - 1,
                )
            )
    return issues


def find_file_issues(
    path: Path,
    text: str,
    allowed_substitutions: Collection[str] = ALLOWED_SUBSTITUTIONS,
) -> list[str]:
    """Check one Fern input using the decoding Fern applies to its file type."""
    suffix = path.suffix.lower()
    if suffix in JSON_SUFFIXES:
        return find_json_issues(path, text, allowed_substitutions)
    if suffix in YAML_SUFFIXES:
        return find_yaml_issues(path, text, allowed_substitutions)
    return find_issues(path, text, allowed_substitutions)


def find_tree_issues(root: Path) -> tuple[list[Path], list[str]]:
    """Return files and issues from a repository-shaped Fern input tree."""
    issues = find_allowlist_issues(ALLOWED_SUBSTITUTIONS)
    scan_roots = (root / "docs", root / "fern")
    extra_inputs = (
        root
        / "services"
        / "analytics"
        / "video-analytics-api"
        / "src"
        / "app"
        / "specification"
        / "openapi.json",
    )
    files = list(iter_inputs(scan_roots, extra_inputs))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        issues.extend(find_file_issues(path.relative_to(root), text))
    return files, issues


def main() -> int:
    parser = ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="repository-shaped prepared input tree to validate",
    )
    args = parser.parse_args()
    files, issues = find_tree_issues(args.root.resolve())

    if issues:
        print(
            "Fern environment-variable substitution safety check failed:",
            file=sys.stderr,
        )
        for issue in issues:
            print(f"  {issue}", file=sys.stderr)
        print(
            "Escape documentation literals as \\$\\{NAME\\}. "
            "Actual build-time variables must be explicitly allowlisted.",
            file=sys.stderr,
        )
        return 1

    print(f"Fern substitution safety check passed ({len(files)} text inputs scanned).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
