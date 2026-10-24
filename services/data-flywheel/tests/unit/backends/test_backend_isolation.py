# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Backend-isolation guard (build-time switchable backends).

A build ships only the selected backend's deps — e.g. a DEFT image has no
``nemo_platform`` installed. For that to not crash, **no module loaded on the
generic/DEFT path may eagerly import a backend SDK or another backend's
package**. Backend selection is lazy (inside ``factory._build`` / handlers), so
only *module-level* imports are a problem.

This test AST-scans the source (no runtime import needed) and fails if any
module outside the NMP-specific locations eagerly imports the NMP SDK or the
``src.backends.nmp`` package. It's the guard that keeps the lazy-load isolation
from silently regressing.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[3] / "src"

# Locations that ARE a backend and may import that backend's code directly.
# The NMP deployment adapter (lib/deployment/adapters/nmp/) is intentionally NOT
# listed: it imports DMSClient lazily inside _get_dms_client, so it must stay free
# of eager NMP imports and is held to the same rule as generic code.
ALLOWED_PREFIXES = ("backends/nmp/", "backends/deft/")

# Backend SDK top-level package names that must not be imported eagerly elsewhere.
SDK_ROOTS = {"nemo_platform"}
# Backend packages that must not be imported eagerly (only lazily, by kind) — a
# build ships one backend, so generic/other-backend code must not pull one in.
# Both the absolute path and the dot-stripped relative form (a relative import
# `from ..backends.nmp import x` normalizes to `backends.nmp`) are listed so
# relative imports of the backend package can't bypass the check.
BACKEND_PKG_PREFIXES = (
    "src.backends.nmp",
    "backends.nmp",
    "src.backends.deft",
    "backends.deft",
)


def _module_level_imports(path: Path) -> list[str]:
    """Eager (module-scope) imports.

    Walks the module body *and* module-level compound statements (``if`` / ``try``
    / ``for`` / ``with`` / ``match`` …) since imports inside them still execute at
    module load. Does NOT descend into function/class bodies — those are lazy and
    fine. Relative imports are included too (``from .x import y`` is eager); the
    leading dots are preserved so the caller can normalize them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods: list[str] = []

    def _is_type_checking(test: ast.expr) -> bool:
        # `if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:` — these bodies never run
        # at runtime, so imports inside them are type-only and NOT eager.
        if isinstance(test, ast.Name):
            return test.id == "TYPE_CHECKING"
        if isinstance(test, ast.Attribute):
            return test.attr == "TYPE_CHECKING"
        return False

    def walk_module_scope(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            # Local scopes are lazy by definition for this test — skip them.
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            if isinstance(node, ast.Import):
                mods.extend(alias.name for alias in node.names)
                continue
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.append(("." * node.level) + node.module)
                continue
            # `if TYPE_CHECKING:` body is not eager — only walk its runtime `else`.
            if isinstance(node, ast.If) and _is_type_checking(node.test):
                walk_module_scope(node.orelse)
                continue
            # Recurse into the bodies of other module-level compound statements.
            # "cases" covers `match`/`case` (each case is an ast.match_case whose
            # body is reached via the ExceptHandler-style branch below).
            for field in ("body", "orelse", "finalbody", "handlers", "cases"):
                child = getattr(node, field, None)
                if isinstance(child, list):
                    for sub in child:
                        if isinstance(sub, ast.stmt):
                            walk_module_scope([sub])
                        else:  # e.g. ast.ExceptHandler — walk its body
                            walk_module_scope(getattr(sub, "body", []) or [])

    walk_module_scope(tree.body)
    return mods


def test_no_eager_backend_import_outside_backend_package():
    offenders: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        rel = py.relative_to(SRC).as_posix()
        if any(rel.startswith(p) for p in ALLOWED_PREFIXES):
            continue
        for mod in _module_level_imports(py):
            normalized = mod.lstrip(".")  # relative imports -> bare dotted path
            root = normalized.split(".")[0] if normalized else ""
            if root in SDK_ROOTS or normalized.startswith(BACKEND_PKG_PREFIXES):
                offenders.append(f"{rel}: eager import of {mod!r}")

    assert not offenders, (
        "A backend SDK / backend package is imported eagerly outside the NMP "
        "backend — this breaks build-time backend isolation (a DEFT image with "
        "no nemo_platform would crash at import). Move the import behind the lazy "
        "factory / backend handler:\n  " + "\n  ".join(offenders)
    )
