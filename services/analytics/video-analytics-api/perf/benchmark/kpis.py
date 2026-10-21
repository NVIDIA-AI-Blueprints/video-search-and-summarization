# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load API KPI YAML and compare a result against its thresholds."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

DEFAULT_KPIS_PATH = Path(__file__).resolve().parents[1] / "configs" / "kpis.yaml"
_MAX_FIELDS = (
    ("avg_s", "max_avg_s"),
    ("p50_s", "max_p50_s"),
    ("p95_s", "max_p95_s"),
    ("p99_s", "max_p99_s"),
    ("max_s", "max_max_s"),
)


def load_kpis(path: Path | None = None) -> dict[str, Any]:
    """Load kpis.yaml. Uses PyYAML when present, else a small subset parser."""
    kpis_path = path or DEFAULT_KPIS_PATH
    text = kpis_path.read_text()
    try:
        import yaml  # type: ignore
    except ImportError:
        data = _load_simple_yaml(text)
    else:
        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{kpis_path} did not parse as a mapping")
    return data


def resolved_required_endpoints(spec: Mapping[str, Any]) -> list[str]:
    """Expand required_endpoints. `spec_gets` means the OpenAPI dump-backed GET matrix."""
    required = [str(item) for item in (spec.get("required_endpoints") or [])]
    if required == ["spec_gets"]:
        from openapi_matrix import spec_get_names

        return spec_get_names()
    return required


def _limits_for(spec: Mapping[str, Any], name: str) -> dict[str, Any]:
    default = spec.get("default") if isinstance(spec.get("default"), Mapping) else {}
    dedicated = spec.get(name) if isinstance(spec.get(name), Mapping) else {}
    merged = dict(default)
    merged.update(dedicated)
    return merged


def evaluate_kpis(result: Mapping[str, Any], kpis: Mapping[str, Any]) -> dict[str, Any]:
    """Return pass/fail plus violation strings for one API result."""
    scenario = str(result.get("scenario") or "")
    config_id = str(result.get("config_id") or "")
    spec = ((kpis.get("scenarios") or {}).get(scenario) or {}).get(config_id)
    if not isinstance(spec, Mapping):
        error = f"no KPI spec for {scenario}/{config_id}"
        return {"passed": False, "violations": [error], "error": error}

    violations: list[str] = []
    endpoints = result.get("endpoints") or {}
    required = resolved_required_endpoints(spec)
    for name in required:
        measured = endpoints.get(name)
        if not isinstance(measured, Mapping) or not measured.get("present"):
            violations.append(f"{name} missing")
            continue
        limits = _limits_for(spec, name)
        min_count = limits.get("min_count")
        ok_count = float(measured.get("ok_count") or 0)
        if min_count is not None and ok_count < float(min_count):
            violations.append(f"{name}.ok_count {ok_count} < {min_count}")
        min_ok_ratio = limits.get("min_ok_ratio")
        if min_ok_ratio is not None:
            ratio = measured.get("ok_ratio")
            if ratio is None:
                violations.append(f"{name}.ok_ratio missing")
            elif float(ratio) < float(min_ok_ratio):
                violations.append(f"{name}.ok_ratio {ratio} < {min_ok_ratio}")
        for value_field, limit_field in _MAX_FIELDS:
            limit = limits.get(limit_field)
            if limit is None:
                continue
            if measured.get(value_field) is None:
                violations.append(f"{name}.{value_field} missing")
                continue
            value = float(measured[value_field])
            if value > float(limit):
                violations.append(f"{name}.{value_field} {value} > {limit}")

    error = f"KPI violations: {'; '.join(violations)}" if violations else None
    return {"passed": not violations, "violations": violations, "error": error}


def _load_simple_yaml(text: str) -> dict[str, Any]:
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        rows.append((indent, stripped.strip()))
    value, index = _parse_mapping(rows, 0, 0)
    if index != len(rows):
        raise ValueError("kpis.yaml has leftover lines the subset parser cannot read")
    if not isinstance(value, dict):
        raise ValueError("kpis.yaml root must be a mapping")
    return value


def _parse_mapping(
    rows: list[tuple[int, str]], index: int, indent: int
) -> tuple[Any, int]:
    mapping: dict[str, Any] = {}
    sequence: list[Any] | None = None
    while index < len(rows):
        row_indent, content = rows[index]
        if row_indent < indent:
            break
        if row_indent > indent:
            raise ValueError(f"unexpected indent in kpis.yaml: {content}")
        if content.startswith("- "):
            if mapping:
                raise ValueError(f"mixed mapping and list at {content}")
            if sequence is None:
                sequence = []
            sequence.append(_parse_scalar(content[2:].strip()))
            index += 1
            continue
        if sequence is not None:
            break
        key, sep, rest = content.partition(":")
        if not sep:
            raise ValueError(f"expected key: value in kpis.yaml: {content}")
        key = key.strip()
        rest = rest.strip()
        index += 1
        if rest:
            mapping[key] = _parse_scalar(rest)
            continue
        if index >= len(rows) or rows[index][0] <= indent:
            mapping[key] = {}
            continue
        child, index = _parse_mapping(rows, index, rows[index][0])
        mapping[key] = child
    return (sequence if sequence is not None else mapping), index


def _strip_comment(line: str) -> str:
    if line.lstrip().startswith("#"):
        return ""
    in_quote = ""
    chars: list[str] = []
    for char in line:
        if in_quote:
            chars.append(char)
            if char == in_quote:
                in_quote = ""
            continue
        if char in "\"'":
            in_quote = char
            chars.append(char)
            continue
        if char == "#":
            break
        chars.append(char)
    return "".join(chars)


def _parse_scalar(text: str) -> Any:
    if text[:1] in "\"'" and text.endswith(text[:1]) and len(text) >= 2:
        return text[1:-1]
    lowered = text.lower()
    if lowered in ("true", "yes"):
        return True
    if lowered in ("false", "no"):
        return False
    if lowered in ("null", "~", "none"):
        return None
    try:
        if any(char in text for char in ".eE"):
            return float(text)
        return int(text)
    except ValueError:
        return text
