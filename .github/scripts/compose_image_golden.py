#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Golden test for the container coordinates resolved from ``deploy/docker``.

Two guarantees, both meant to make the ``containers.env`` single source of
truth a *no-behavior-change* layer:

1. **Golden refs.** Every ``image:`` reference under ``deploy/docker``,
   resolved with an EMPTY environment (i.e. only the inline
   ``${VAR:-default}`` literals apply, exactly what a clean clone gets),
   must match the checked-in golden file
   ``deploy/docker/test-scripts/compose-images.golden``. Any change to a
   registry, image name, or default tag shows up as an explicit golden diff
   in review instead of hiding inside a compose refactor.

2. **No drift.** Resolving the same references WITH ``containers.env``
   sourced must produce byte-identical results. The SSOT defaults and the
   inline compose defaults are duplicated on purpose during the migration;
   this check is what makes the duplication safe.

Run from the repository root:

    python3 .github/scripts/compose_image_golden.py           # verify
    python3 .github/scripts/compose_image_golden.py --update  # regenerate
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_container_tag_source import (  # noqa: E402
    DEPLOY_DIR,
    IMAGE_LINE_RE,
    discover_compose_files,
    strip_quotes,
)

CONTAINERS_ENV = DEPLOY_DIR / "containers.env"
GOLDEN_FILE = DEPLOY_DIR / "test-scripts" / "compose-images.golden"

GOLDEN_HEADER = """\
# Golden resolved image references for deploy/docker (defaults only, no env).
# Regenerate with:  python3 .github/scripts/compose_image_golden.py --update
# A diff in this file means a registry, image name, or default tag changed.
"""

_VAR_START_RE = re.compile(r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<op>:?[-?])?")
SHARED_COORDINATE_VARS = ("${VSS_CONTAINER_REGISTRY", "${VSS_CONTAINER_TAG")


def uses_shared_coordinate(text: str) -> bool:
    return any(marker in text for marker in SHARED_COORDINATE_VARS)


def resolve_nested(text: str, env: dict[str, str]) -> str:
    """Resolve ``${VAR}`` / ``${VAR:-default}`` with brace-aware nesting.

    Unlike ``check_container_tag_source.resolve_compose_vars`` (regex-based,
    cannot see past the first ``}``), this handles defaults that themselves
    contain substitutions, e.g. ``${A:-${B}/name}`` as used in
    ``containers.env``. Variables that are unset and have no default are kept
    literally so the output stays deterministic.
    """
    result: list[str] = []
    i = 0
    while i < len(text):
        match = _VAR_START_RE.match(text, i)
        if not match:
            result.append(text[i])
            i += 1
            continue
        name, op = match.group("name"), match.group("op")
        j = match.end()
        if op is None:
            # ${VAR} — find the closing brace (no default, no nesting).
            if j < len(text) and text[j] == "}":
                value = env.get(name)
                result.append(value if value is not None else text[i : j + 1])
                i = j + 1
                continue
            result.append(text[i])
            i += 1
            continue
        # ${VAR:-default} / ${VAR-default} / ${VAR:?msg} — scan the default
        # with brace counting so nested ${...} survive.
        depth = 1
        k = j
        while k < len(text) and depth:
            if text.startswith("${", k):
                depth += 1
                k += 2
            elif text[k] == "}":
                depth -= 1
                k += 1
            else:
                k += 1
        if depth:
            result.append(text[i])
            i += 1
            continue
        default_raw = text[j : k - 1]
        value = env.get(name)
        use_default = value is None or (op.startswith(":") and value == "")
        if op.endswith("?"):
            # ${VAR:?msg}: keep literally when unset, like resolve_compose_vars.
            result.append(value if not use_default else text[i:k])
        else:
            result.append(resolve_nested(default_raw, env) if use_default else value)
        i = k
    return "".join(result)


def load_containers_env(path: Path) -> dict[str, str]:
    """Load ``containers.env`` top-down, resolving each value against the
    entries above it (the file is written so this converges in one pass)."""
    values: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        values[key] = resolve_nested(strip_quotes(value), values)
    return values


def collect_lines(repo_root: Path) -> tuple[list[str], list[str]]:
    """Return ``(golden_lines, drift_errors)`` for every compose ``image:``
    ref under ``deploy/docker``."""
    containers_env = load_containers_env(repo_root / CONTAINERS_ENV)
    golden: list[str] = []
    drift: list[str] = []
    for compose_file in discover_compose_files(repo_root):
        rel = compose_file.relative_to(repo_root).as_posix()
        for line in compose_file.read_text().splitlines():
            match = IMAGE_LINE_RE.match(line)
            if not match:
                continue
            raw = strip_quotes(match.group("ref"))
            defaults_only = resolve_nested(raw, {})
            golden.append(f"{rel} {defaults_only}")
            with_ssot = resolve_nested(raw, containers_env)
            # The managed agent/UI/alert set intentionally has one mutable SSOT
            # tag in containers.env. A tested-coordinate bot updates that single
            # line without rewriting duplicated inline fallbacks.
            shared_coordinate = uses_shared_coordinate(raw)
            if with_ssot != defaults_only and not shared_coordinate:
                drift.append(
                    f"{rel}: containers.env resolves {raw!r}\n"
                    f"    to   {with_ssot!r}\n"
                    f"    but the inline defaults give {defaults_only!r}"
                )
    return sorted(golden), drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--update", action="store_true", help="regenerate the golden file"
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()

    golden_lines, drift = collect_lines(repo_root)

    if drift:
        print("FAIL: containers.env drifted from the inline compose defaults:\n")
        print("\n".join(drift))
        return 1

    golden_path = repo_root / GOLDEN_FILE
    content = GOLDEN_HEADER + "\n".join(golden_lines) + "\n"

    if args.update:
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(content)
        print(f"Wrote {len(golden_lines)} refs to {GOLDEN_FILE.as_posix()}.")
        return 0

    if not golden_path.exists():
        print(f"FAIL: golden file {GOLDEN_FILE.as_posix()} is missing; run --update.")
        return 1

    expected = golden_path.read_text()
    if expected == content:
        print(
            f"OK: {len(golden_lines)} resolved image refs match the golden file, "
            "and containers.env matches the inline defaults."
        )
        return 0

    print("FAIL: resolved image refs differ from the golden file:\n")
    sys.stdout.writelines(
        difflib.unified_diff(
            expected.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=GOLDEN_FILE.as_posix(),
            tofile="resolved from working tree",
        )
    )
    print("\nIf the change is intentional, regenerate with --update.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
