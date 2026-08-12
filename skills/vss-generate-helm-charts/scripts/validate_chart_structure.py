#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Perform deterministic, offline structural checks for Helm chart sources."""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover - exercised through the CLI error path
    yaml = None


SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
FILES_GET_RE = re.compile(r"\.Files\.Get\s+(?:\(\s*)?[\"']([^\"']+)[\"']")
FILES_GLOB_RE = re.compile(r"\.Files\.Glob\s+(?:\(\s*)?[\"']([^\"']+)[\"']")
COMPOSE_TOKEN_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::[-+?][^}]*)?\}")


@dataclass(frozen=True)
class Finding:
    severity: str
    chart: str
    message: str
    file: str | None = None
    line: int | None = None


class DuplicateKeyError(ValueError):
    pass


if yaml is not None:
    class UniqueKeyLoader(yaml.SafeLoader):
        pass


    def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        explicit_keys: dict[Any, int] = {}
        for key_node, _value_node in node.value:
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in explicit_keys
            except TypeError as exc:
                raise DuplicateKeyError(
                    f"unhashable mapping key at line {key_node.start_mark.line + 1}"
                ) from exc
            if duplicate:
                raise DuplicateKeyError(
                    f"duplicate key {key!r} at line {key_node.start_mark.line + 1}"
                )
            explicit_keys[key] = key_node.start_mark.line + 1

        loader.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise DuplicateKeyError(
                    f"unhashable mapping key at line {key_node.start_mark.line + 1}"
                ) from exc
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping


    UniqueKeyLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )


def relative(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def load_yaml(path: Path) -> Any:
    if yaml is None:
        raise RuntimeError("PyYAML is required for offline chart validation")
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError, DuplicateKeyError) as exc:
        raise ValueError(str(exc)) from exc


def get_value(values: Any, dotted_path: str) -> tuple[bool, Any]:
    current = values
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def is_exact_version(version: str) -> bool:
    return bool(SEMVER_RE.fullmatch(version))


def add(
    findings: list[Finding],
    severity: str,
    chart: str,
    message: str,
    file: str | None = None,
    line: int | None = None,
) -> None:
    findings.append(Finding(severity, chart, message, file, line))


def parse_yaml_file(
    repo_root: Path,
    chart_name: str,
    path: Path,
    findings: list[Finding],
) -> Any | None:
    try:
        return load_yaml(path)
    except (RuntimeError, ValueError) as exc:
        add(
            findings,
            "ERROR",
            chart_name,
            f"YAML parse failed: {exc}",
            relative(repo_root, path),
        )
        return None


def validate_chart(repo_root: Path, chart_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    chart_name = relative(repo_root, chart_root)
    chart_file = chart_root / "Chart.yaml"
    values_file = chart_root / "values.yaml"

    if not chart_file.is_file():
        add(findings, "ERROR", chart_name, "Chart.yaml is missing")
        return findings
    metadata = parse_yaml_file(repo_root, chart_name, chart_file, findings)
    if not isinstance(metadata, dict):
        return findings

    for key in ("apiVersion", "name", "version"):
        if not metadata.get(key):
            add(
                findings,
                "ERROR",
                chart_name,
                f"Chart.yaml is missing non-empty {key}",
                relative(repo_root, chart_file),
            )
    if metadata.get("apiVersion") != "v2":
        add(
            findings,
            "ERROR",
            chart_name,
            "Chart apiVersion must be v2",
            relative(repo_root, chart_file),
        )
    version = metadata.get("version")
    if version is not None and not SEMVER_RE.fullmatch(str(version)):
        add(
            findings,
            "ERROR",
            chart_name,
            f"Chart version is not SemVer: {version!r}",
            relative(repo_root, chart_file),
        )

    values: Any = {}
    if not values_file.is_file():
        add(findings, "ERROR", chart_name, "values.yaml is missing")
    else:
        values = parse_yaml_file(repo_root, chart_name, values_file, findings)
        if values is not None and not isinstance(values, dict):
            add(
                findings,
                "ERROR",
                chart_name,
                "values.yaml root must be a mapping",
                relative(repo_root, values_file),
            )
            values = {}

    for extra_values in sorted(chart_root.glob("values*.yaml")):
        if extra_values == values_file:
            continue
        loaded = parse_yaml_file(repo_root, chart_name, extra_values, findings)
        if loaded is not None and not isinstance(loaded, dict):
            add(
                findings,
                "ERROR",
                chart_name,
                "Values file root must be a mapping",
                relative(repo_root, extra_values),
            )

    dependencies = metadata.get("dependencies", [])
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list):
        add(
            findings,
            "ERROR",
            chart_name,
            "Chart dependencies must be a list",
            relative(repo_root, chart_file),
        )
        dependencies = []

    valid_dependencies: list[dict[str, Any]] = []
    for index, dependency in enumerate(dependencies):
        if not isinstance(dependency, dict):
            add(
                findings,
                "ERROR",
                chart_name,
                f"Dependency #{index + 1} must be a mapping",
                relative(repo_root, chart_file),
            )
            continue
        valid_dependencies.append(dependency)
        for key in ("name", "version", "repository"):
            if not dependency.get(key):
                add(
                    findings,
                    "ERROR",
                    chart_name,
                    f"Dependency #{index + 1} is missing {key}",
                    relative(repo_root, chart_file),
                )
        condition = dependency.get("condition")
        if isinstance(condition, str) and values_file.is_file():
            alternatives = [item.strip() for item in condition.split(",") if item.strip()]
            if alternatives and not any(get_value(values, item)[0] for item in alternatives):
                add(
                    findings,
                    "ERROR",
                    chart_name,
                    f"Dependency condition is absent from values.yaml: {condition}",
                    relative(repo_root, chart_file),
                )

        repository = dependency.get("repository")
        if not isinstance(repository, str) or not repository.startswith("file://"):
            continue
        child_root = (chart_root / repository[len("file://") :]).resolve()
        child_chart = child_root / "Chart.yaml"
        if not child_chart.is_file():
            add(
                findings,
                "ERROR",
                chart_name,
                f"Local dependency path has no Chart.yaml: {repository}",
                relative(repo_root, chart_file),
            )
            continue
        child_metadata = parse_yaml_file(repo_root, chart_name, child_chart, findings)
        if not isinstance(child_metadata, dict):
            continue
        dependency_name = dependency.get("name")
        if dependency_name and dependency_name != child_metadata.get("name"):
            add(
                findings,
                "ERROR",
                chart_name,
                f"Dependency name {dependency_name!r} does not match child chart name {child_metadata.get('name')!r}",
                relative(repo_root, chart_file),
            )
        dependency_version = str(dependency.get("version", ""))
        child_version = str(child_metadata.get("version", ""))
        if is_exact_version(dependency_version) and dependency_version != child_version:
            add(
                findings,
                "ERROR",
                chart_name,
                f"Dependency {dependency_name!r} version {dependency_version} does not match child {child_version}",
                relative(repo_root, chart_file),
            )

    validate_lock(repo_root, chart_root, chart_name, valid_dependencies, findings)
    validate_templates(repo_root, chart_root, chart_name, valid_dependencies, findings)

    service_root = (repo_root / "deploy/helm/services").resolve()
    try:
        chart_root.resolve().relative_to(service_root)
        is_service_chart = True
    except ValueError:
        is_service_chart = False
    if is_service_chart and isinstance(values, dict) and "enabled" not in values:
        add(
            findings,
            "WARN",
            chart_name,
            "Service chart values.yaml has no top-level enabled gate",
            relative(repo_root, values_file) if values_file.exists() else None,
        )
    return findings


def dependency_key(dependency: dict[str, Any]) -> tuple[str, str]:
    return str(dependency.get("name", "")), str(dependency.get("repository", ""))


def validate_lock(
    repo_root: Path,
    chart_root: Path,
    chart_name: str,
    dependencies: list[dict[str, Any]],
    findings: list[Finding],
) -> None:
    lock_file = chart_root / "Chart.lock"
    if not dependencies:
        if lock_file.exists():
            add(
                findings,
                "WARN",
                chart_name,
                "Chart.lock exists but Chart.yaml declares no dependencies",
                relative(repo_root, lock_file),
            )
        return
    if not lock_file.is_file():
        add(
            findings,
            "ERROR",
            chart_name,
            "Chart.yaml has dependencies but Chart.lock is missing",
        )
        return
    lock = parse_yaml_file(repo_root, chart_name, lock_file, findings)
    if not isinstance(lock, dict):
        return
    locked = lock.get("dependencies", [])
    if not isinstance(locked, list):
        add(
            findings,
            "ERROR",
            chart_name,
            "Chart.lock dependencies must be a list",
            relative(repo_root, lock_file),
        )
        return
    chart_by_key = {dependency_key(item): item for item in dependencies}
    lock_by_key = {
        dependency_key(item): item for item in locked if isinstance(item, dict)
    }
    if set(chart_by_key) != set(lock_by_key):
        missing = sorted(set(chart_by_key) - set(lock_by_key))
        extra = sorted(set(lock_by_key) - set(chart_by_key))
        add(
            findings,
            "ERROR",
            chart_name,
            f"Chart.lock dependency set differs; missing={missing}, extra={extra}",
            relative(repo_root, lock_file),
        )
    for key in sorted(set(chart_by_key) & set(lock_by_key)):
        expected = str(chart_by_key[key].get("version", ""))
        actual = str(lock_by_key[key].get("version", ""))
        if is_exact_version(expected) and expected != actual:
            add(
                findings,
                "ERROR",
                chart_name,
                f"Chart.lock version for {key[0]!r} is {actual}, expected {expected}",
                relative(repo_root, lock_file),
            )
        elif not is_exact_version(expected) and not is_exact_version(actual):
            add(
                findings,
                "WARN",
                chart_name,
                f"Cannot verify locked version {actual!r} against dependency constraint {expected!r}",
                relative(repo_root, lock_file),
            )
    if not lock.get("digest"):
        add(
            findings,
            "ERROR",
            chart_name,
            "Chart.lock has no digest; regenerate it with Helm",
            relative(repo_root, lock_file),
        )
    if not lock.get("generated"):
        add(
            findings,
            "WARN",
            chart_name,
            "Chart.lock has no generated timestamp",
            relative(repo_root, lock_file),
        )


def validate_templates(
    repo_root: Path,
    chart_root: Path,
    chart_name: str,
    dependencies: list[dict[str, Any]],
    findings: list[Finding],
) -> None:
    templates_root = chart_root / "templates"
    template_files = (
        sorted(path for path in templates_root.rglob("*") if path.is_file())
        if templates_root.is_dir()
        else []
    )
    if not template_files and not dependencies:
        add(findings, "ERROR", chart_name, "Leaf chart has no template files")
        return
    if template_files and not (templates_root / "_helpers.tpl").is_file():
        add(
            findings,
            "WARN",
            chart_name,
            "Chart templates have no _helpers.tpl",
        )

    for template in template_files:
        try:
            text = template.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            add(
                findings,
                "ERROR",
                chart_name,
                f"Cannot read template: {exc}",
                relative(repo_root, template),
            )
            continue
        glob_matches = list(FILES_GLOB_RE.finditer(text))
        optional_globs: set[str] = set()
        for match in glob_matches:
            line_start = text.rfind("\n", 0, match.start()) + 1
            prefix = text[line_start : match.start()]
            if re.search(r"{{-?\s*(?:if|with)\b", prefix):
                optional_globs.add(match.group(1))

        for match in FILES_GET_RE.finditer(text):
            referenced = chart_root / match.group(1)
            guarded = any(
                fnmatch.fnmatch(match.group(1), pattern) for pattern in optional_globs
            )
            if not referenced.is_file() and not guarded:
                add(
                    findings,
                    "ERROR",
                    chart_name,
                    f".Files.Get references missing file: {match.group(1)}",
                    relative(repo_root, template),
                    text.count("\n", 0, match.start()) + 1,
                )
        for match in glob_matches:
            pattern = match.group(1)
            matches = [
                path
                for path in chart_root.rglob("*")
                if path.is_file()
                and fnmatch.fnmatch(path.relative_to(chart_root).as_posix(), pattern)
            ]
            if not matches and pattern not in optional_globs:
                add(
                    findings,
                    "WARN",
                    chart_name,
                    f".Files.Glob currently matches no files: {pattern}",
                    relative(repo_root, template),
                    text.count("\n", 0, match.start()) + 1,
                )

    for values_file in sorted(chart_root.glob("values*.yaml")):
        try:
            lines = values_file.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        block_indent: int | None = None
        for number, line in enumerate(lines, start=1):
            stripped = line.strip()
            indent = len(line) - len(line.lstrip(" "))
            if block_indent is not None:
                if stripped and indent <= block_indent:
                    block_indent = None
                else:
                    continue
            if re.search(r"[:>-]\s*[|>]\s*(?:#.*)?$", line):
                block_indent = indent
                continue
            content = line.split("#", 1)[0]
            match = COMPOSE_TOKEN_RE.search(content)
            if match:
                add(
                    findings,
                    "WARN",
                    chart_name,
                    f"Values file contains Compose-style interpolation; verify it is intentional: {match.group(0)}",
                    relative(repo_root, values_file),
                    number,
                )


def discover_requested_charts(repo_root: Path, raw_charts: list[str], recursive: bool) -> list[Path]:
    charts: set[Path] = set()
    for raw in raw_charts:
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        candidate = candidate.resolve()
        if candidate.is_file() and candidate.name == "Chart.yaml":
            candidate = candidate.parent
        if not candidate.exists():
            raise ValueError(f"Chart path does not exist: {raw}")
        if (candidate / "Chart.yaml").is_file():
            charts.add(candidate)
        if recursive:
            for chart_file in candidate.rglob("Chart.yaml"):
                charts.add(chart_file.parent.resolve())
    if not charts:
        raise ValueError("No Chart.yaml found under requested paths")
    return sorted(charts)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline Helm chart source validator")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument(
        "--chart", action="append", required=True, help="Chart file/directory (repeatable)"
    )
    parser.add_argument("--recursive", action="store_true", help="Validate nested charts")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument(
        "--strict-warnings", action="store_true", help="Return failure when warnings exist"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    try:
        charts = discover_requested_charts(repo_root, args.chart, args.recursive)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    findings: list[Finding] = []
    for chart in charts:
        findings.extend(validate_chart(repo_root, chart))
    findings.sort(key=lambda item: (item.severity != "ERROR", item.chart, item.file or "", item.line or 0, item.message))

    errors = sum(item.severity == "ERROR" for item in findings)
    warnings = sum(item.severity == "WARN" for item in findings)
    if args.format == "json":
        print(
            json.dumps(
                {
                    "charts": [relative(repo_root, item) for item in charts],
                    "errors": errors,
                    "warnings": warnings,
                    "findings": [asdict(item) for item in findings],
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        for item in findings:
            location = item.file or item.chart
            if item.line:
                location = f"{location}:{item.line}"
            print(f"[{item.severity}] {location}: {item.message}")
        print(
            f"Validated {len(charts)} chart(s): {errors} error(s), {warnings} warning(s)"
        )
    if errors or (args.strict_warnings and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
