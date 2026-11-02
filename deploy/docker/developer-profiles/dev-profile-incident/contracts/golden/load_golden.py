# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Load a request golden and check a producer's request body against it (standard library only).

Eval and the agent load this file by path (the agent test as ``sync.py`` is loaded); the console has the same
rules in ``incident-console-v2/tests/support/golden.mjs``. What a golden is: [README](README.md).
"""

from __future__ import annotations

import difflib
import hashlib
import json
from pathlib import Path
from typing import Any

GOLDEN_DIR = Path(__file__).resolve().parent
CONTRACTS_DIR = GOLDEN_DIR.parent
IMAGE_DATA_PREFIX = "data:image/jpeg;base64,"
IMAGE_PLACEHOLDER = IMAGE_DATA_PREFIX + "<jpeg>"
PRODUCERS = frozenset({"agent", "console", "eval"})


class GoldenError(ValueError):
    """A golden file that cannot be read: an unknown case or producer, a missing file, or a stale hash."""


def _contract_ref(ref: dict[str, Any]) -> Any:
    """The contract file a ``$contract_text`` or ``$contract_json`` reference names, after checking its hash."""
    kind = "$contract_text" if "$contract_text" in ref else "$contract_json"
    path = (CONTRACTS_DIR / ref[kind]).resolve()
    if CONTRACTS_DIR.resolve() not in path.parents:
        raise GoldenError(f"{ref[kind]!r} is outside contracts/")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != ref.get("sha256"):
        raise GoldenError(
            f"contracts/{ref[kind]} changed (sha256 {digest}, golden has {ref.get('sha256')}): a deliberate "
            "contract change updates the golden's sha256 in the same PR"
        )
    return raw.decode("utf-8") if kind == "$contract_text" else json.loads(raw)


def expand(value: Any) -> Any:
    """``value`` with every contract reference replaced by the file it names."""
    if isinstance(value, dict):
        if set(value) in ({"$contract_text", "sha256"}, {"$contract_json", "sha256"}):
            return _contract_ref(value)
        return {key: expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [expand(item) for item in value]
    return value


def normalize(value: Any) -> Any:
    """``value`` with every inline JPEG replaced by :data:`IMAGE_PLACEHOLDER` (frames differ by encoder)."""
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str) and value.startswith(IMAGE_DATA_PREFIX):
        return IMAGE_PLACEHOLDER
    return value


def load(name: str) -> dict[str, Any]:
    """The golden file ``<name>.json``, unexpanded."""
    path = GOLDEN_DIR / f"{name}.json"
    if not path.is_file():
        raise GoldenError(f"no golden named {name!r} in {GOLDEN_DIR}")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def case(name: str, case_name: str) -> dict[str, Any]:
    """One case of a golden, its body expanded: ``{"producers", "inputs", "transport", "body", ...}``."""
    cases = load(name)["cases"]
    if case_name not in cases:
        raise GoldenError(f"golden {name!r} has no case {case_name!r} (cases: {', '.join(sorted(cases))})")
    raw = cases[case_name]
    unknown = set(raw["producers"]) - PRODUCERS
    if unknown:
        raise GoldenError(f"{name}/{case_name}: unknown producers {sorted(unknown)}")
    return {**raw, "body": expand(raw["body"]), "transport": raw.get("transport", {})}


def _same(a: Any, b: Any) -> bool:
    """Equal as JSON values: numbers by value (``0`` equals ``0.0``), but a boolean is never a number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[key], b[key]) for key in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return type(a) is type(b) and a == b


def _dump(value: Any) -> list[str]:
    return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False).splitlines()


def mismatch(name: str, case_name: str, produced: dict[str, Any], producer: str) -> str | None:
    """Why ``produced`` (``producer``'s body for the case) differs from the golden, or ``None`` when it matches.

    Keys in the case's ``transport`` (such as the agent's ``"stream": false``) may be present or absent, but
    when present must have exactly the golden's value; every other key must equal the golden body.
    """
    golden = case(name, case_name)
    if producer not in golden["producers"]:
        return f"{name}/{case_name}: {producer!r} is not a producer of this case ({golden['producers']})"
    body = normalize(produced)
    for key, value in golden["transport"].items():
        if key in body:
            if not _same(body[key], value):
                return f"{name}/{case_name}: transport key {key!r} is {body[key]!r}, the golden allows only {value!r}"
            body = {k: v for k, v in body.items() if k != key}
    if _same(body, golden["body"]):
        return None
    diff = difflib.unified_diff(_dump(golden["body"]), _dump(body), "golden", producer, lineterm="", n=2)
    lines = list(diff)
    shown = "\n".join(lines[:80]) + (f"\n... {len(lines) - 80} more diff lines" if len(lines) > 80 else "")
    return f"{name}/{case_name}: {producer} body differs from the golden\n{shown}"
