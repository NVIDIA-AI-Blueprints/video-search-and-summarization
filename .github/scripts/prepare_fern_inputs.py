# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Build an isolated Fern input tree while keeping authored examples runnable."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from collections.abc import Collection, Mapping
from pathlib import Path
from typing import Any

import check_fern_substitutions as check

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI_PATH = Path(
    "services/analytics/video-analytics-api/src/app/specification/openapi.json"
)
COPY_DIRECTORIES = (Path("docs"), Path("fern"))
MARKDOWN_SUFFIXES = frozenset({".md", ".mdx"})


class PreparationError(RuntimeError):
    """An authored or prepared Fern input is unsafe."""


def escape_substitutions(
    text: str,
    allowed_substitutions: Collection[str] = check.ALLOWED_SUBSTITUTIONS,
    environment: Mapping[str, str] = os.environ,
) -> str:
    """Prepare placeholders while keeping authored shell defaults runnable."""

    def replace_defaulted(match: Any) -> str:
        name = match.group("name")
        if name not in allowed_substitutions:
            return match.group(0)
        if environment.get(name):
            return f"${{{name}}}"
        return match.group("default")

    def replace(match: Any) -> str:
        name = match.group("name")
        if name in allowed_substitutions:
            return match.group(0)
        return r"\$\{" + name + r"\}"

    defaulted = check.DEFAULTED_SUBSTITUTION_PATTERN.sub(replace_defaulted, text)
    return check.SUBSTITUTION_PATTERN.sub(replace, defaulted)


def transform_json_value(
    value: Any,
    allowed_substitutions: Collection[str],
    environment: Mapping[str, str],
) -> Any:
    """Escape placeholders recursively in decoded JSON string values."""
    if isinstance(value, str):
        return escape_substitutions(value, allowed_substitutions, environment)
    if isinstance(value, list):
        return [
            transform_json_value(item, allowed_substitutions, environment)
            for item in value
        ]
    if isinstance(value, dict):
        return {
            key: transform_json_value(item, allowed_substitutions, environment)
            for key, item in value.items()
        }
    return value


def iter_tree_inputs(root: Path):
    """Yield the Fern inputs from a repository-shaped tree."""
    yield from check.iter_inputs(
        (root / "docs", root / "fern"),
        (root / OPENAPI_PATH,),
    )


def find_authored_escape_issues(root: Path) -> list[str]:
    """Reject generated Fern literal syntax in committed source inputs."""
    issues: list[str] = []
    for path in iter_tree_inputs(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in check.ESCAPE_PREFIX_PATTERN.finditer(line):
                name = match.group("name")
                issues.append(
                    f"{path.relative_to(root)}:{line_number}: generated Fern escape "
                    f"\\$\\{{{name}\\}} must not be committed; write ${{{name}}}"
                )
    return issues


def copy_inputs(source_root: Path, output_root: Path) -> None:
    """Copy only inputs Fern consumes while preserving relative paths and symlinks."""
    output_root.mkdir(parents=True)
    ignored = shutil.ignore_patterns(".fern", "node_modules")
    for relative in COPY_DIRECTORIES:
        shutil.copytree(
            source_root / relative,
            output_root / relative,
            symlinks=True,
            ignore=ignored,
        )

    openapi_output = output_root / OPENAPI_PATH
    openapi_output.parent.mkdir(parents=True)
    shutil.copy2(source_root / OPENAPI_PATH, openapi_output)


def transform_inputs(
    output_root: Path,
    allowed_substitutions: Collection[str],
    environment: Mapping[str, str],
) -> int:
    """Transform copied Markdown and JSON inputs in place."""
    changed = 0
    for path in iter_tree_inputs(output_root):
        suffix = path.suffix.lower()
        if suffix in MARKDOWN_SUFFIXES:
            original = path.read_text(encoding="utf-8")
            prepared = escape_substitutions(
                original, allowed_substitutions, environment
            )
        elif suffix == ".json":
            original = path.read_text(encoding="utf-8")
            value = json.loads(original)
            transformed = transform_json_value(
                value, allowed_substitutions, environment
            )
            if transformed == value:
                continue
            prepared = json.dumps(transformed, indent=2, ensure_ascii=False) + "\n"
        else:
            continue

        if prepared != original:
            path.write_text(prepared, encoding="utf-8")
            changed += 1
    return changed


def find_prepared_issues(
    root: Path,
    allowed_substitutions: Collection[str],
) -> list[str]:
    """Reject any non-allowlisted substitution that reached the prepared tree."""
    issues = check.find_allowlist_issues(allowed_substitutions)
    for path in iter_tree_inputs(root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        issues.extend(
            check.find_file_issues(
                path.relative_to(root),
                text,
                allowed_substitutions,
            )
        )
    return issues


def prepare_tree(
    source_root: Path,
    output_root: Path,
    allowed_substitutions: Collection[str] = check.ALLOWED_SUBSTITUTIONS,
    environment: Mapping[str, str] = os.environ,
) -> int:
    """Create and validate an isolated, repository-shaped Fern input tree."""
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if output_root.exists():
        raise PreparationError(f"output path already exists: {output_root}")
    if output_root == source_root or source_root in output_root.parents:
        raise PreparationError("output path must be outside the source repository")

    source_issues = check.find_allowlist_issues(allowed_substitutions)
    source_issues.extend(find_authored_escape_issues(source_root))
    if source_issues:
        raise PreparationError("\n".join(source_issues))

    copy_inputs(source_root, output_root)
    changed = transform_inputs(output_root, allowed_substitutions, environment)
    prepared_issues = find_prepared_issues(output_root, allowed_substitutions)
    if prepared_issues:
        raise PreparationError("\n".join(prepared_issues))
    return changed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    try:
        changed = prepare_tree(args.source, args.output)
    except (OSError, ValueError, json.JSONDecodeError, PreparationError) as error:
        print(f"Fern input preparation failed: {error}", file=sys.stderr)
        return 1

    print(f"Prepared Fern input tree at {args.output.resolve()} ({changed} files transformed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
