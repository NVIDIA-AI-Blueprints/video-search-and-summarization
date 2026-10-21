# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load playback KPI YAML and compare a result against its thresholds."""

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


def evaluate_kpis(result: Mapping[str, Any], kpis: Mapping[str, Any]) -> dict[str, Any]:
    """Return pass/fail plus violation strings for one playback result."""
    scenario = str(result.get("scenario") or "")
    config_id = str(result.get("config_id") or "")
    spec = ((kpis.get("scenarios") or {}).get(scenario) or {}).get(config_id)
    if not isinstance(spec, Mapping):
        error = f"no KPI spec for {scenario}/{config_id}"
        return {"passed": False, "violations": [error], "error": error}

    violations: list[str] = []
    required = list(spec.get("required_topics") or [])
    for topic in required:
        if topic == "raw":
            raw = result.get("raw") or {}
            if not raw or float(raw.get("count") or 0) <= 0:
                violations.append("raw missing")
            continue
        entry = (result.get("topics") or {}).get(topic) or {}
        if not entry.get("present"):
            violations.append(f"{topic} missing")

    for topic, limits in spec.items():
        if topic == "required_topics" or not isinstance(limits, Mapping):
            continue
        measured, label = _topic_values(result, topic)
        if measured is None:
            if topic in required:
                continue
            violations.append(f"{topic} missing")
            continue
        min_count = limits.get("min_count")
        if min_count is not None and float(measured.get("count") or 0) < float(min_count):
            violations.append(
                f"{label}.count {measured.get('count')} < {min_count}"
            )
        for value_field, limit_field in _MAX_FIELDS:
            limit = limits.get(limit_field)
            if limit is None:
                continue
            if measured.get(value_field) is None:
                violations.append(f"{label}.{value_field} missing")
                continue
            value = float(measured[value_field])
            if value > float(limit):
                violations.append(f"{label}.{value_field} {value} > {limit}")

    error = f"KPI violations: {'; '.join(violations)}" if violations else None
    return {"passed": not violations, "violations": violations, "error": error}


def _topic_values(
    result: Mapping[str, Any], topic: str
) -> tuple[Mapping[str, Any] | None, str]:
    if topic == "raw":
        raw = result.get("raw")
        if not isinstance(raw, Mapping):
            return None, "raw"
        return raw, "raw"
    entry = (result.get("topics") or {}).get(topic) or {}
    if not entry.get("present"):
        return None, topic
    values = dict(entry.get("corrected") or {})
    total = entry.get("total") or {}
    if "count" in total:
        values["count"] = total["count"]
    return values, f"{topic}.corrected"


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
