#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Inventory Docker changes, Helm targets, and comment directives without deploying.

The script reads repository files and Git metadata only. It never invokes Docker,
Helm, kubectl, or a Kubernetes API.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - exercised through the CLI error path
    yaml = None


DOCKER_PREFIX = "deploy/docker"
HELM_PREFIX = "deploy/helm"
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".conf",
    ".dockerfile",
    ".env",
    ".ini",
    ".json",
    ".md",
    ".properties",
    ".sh",
    ".text",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
COMPOSE_NAMES = {
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
}
EXCLUDED_CONTEXT_NAMES = {"generated.env", "resolved.yml", ".DS_Store"}
DIRECTIVE_RE = re.compile(
    r"#\s*helm-sync\s*:\s*(compose-only|helm-only|replace)\s*\|\s*(\S.*)\s*$",
    re.IGNORECASE,
)
DEPLOYMENT_COMMENT_RE = re.compile(
    r"\bhelm\b|\bkubernetes\b|\bk8s\b|\bcompose[- ]only\b|\bdocker[- ]only\b",
    re.IGNORECASE,
)
ENV_TOKEN_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")


SERVICE_TARGETS = {
    "agent": ["deploy/helm/services/agent"],
    "alert": ["deploy/helm/services/alert"],
    "analytics": ["deploy/helm/services/analytics"],
    "auto-calibration": [
        "deploy/helm/services/calibration-toolkit",
        "deploy/helm/services/calibration-import",
    ],
    "configurators": ["deploy/helm/services/bp-configurator"],
    "infra": ["deploy/helm/services/infra"],
    "monitoring": ["deploy/helm/services/monitoring"],
    "nim": ["deploy/helm/services/nims"],
    "rtvi": ["deploy/helm/services/rtvi"],
    "ui": ["deploy/helm/services/ui"],
    "video-summarization": ["deploy/helm/services/video-summarization"],
    "vios": ["deploy/helm/services/vios"],
}


if yaml is not None:
    class ComposeLoader(yaml.SafeLoader):
        """Safe loader that preserves values behind Compose's local YAML tags."""


    def construct_compose_tag(loader: Any, _suffix: str, node: Any) -> Any:
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node, deep=True)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        return loader.construct_scalar(node)


    ComposeLoader.add_multi_constructor("!", construct_compose_tag)


class InventoryError(RuntimeError):
    """A deterministic inventory error suitable for concise CLI output."""


def run_git(repo_root: Path, arguments: list[str]) -> bytes:
    command = ["git", "-C", str(repo_root), *arguments]
    process = subprocess.run(command, capture_output=True, check=False)
    if process.returncode:
        stderr = process.stderr.decode("utf-8", errors="replace").strip()
        raise InventoryError(f"Git command failed: {' '.join(command)}\n{stderr}")
    return process.stdout


def parse_name_status_z(payload: bytes, origin: str) -> list[dict[str, str]]:
    fields = payload.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[dict[str, str]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        code = status[0]
        if code in {"R", "C"}:
            if index + 1 >= len(fields):
                raise InventoryError("Unexpected truncated Git rename/copy output")
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
            changes.append(
                {
                    "status": status,
                    "path": new_path,
                    "old_path": old_path,
                    "origin": origin,
                }
            )
        else:
            if index >= len(fields):
                raise InventoryError("Unexpected truncated Git name-status output")
            path = fields[index]
            index += 1
            changes.append({"status": status, "path": path, "origin": origin})
    return changes


def git_changes(repo_root: Path, changed_from: str | None) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    if changed_from:
        changes.extend(
            parse_name_status_z(
                run_git(
                    repo_root,
                    [
                        "diff",
                        "--name-status",
                        "-z",
                        "--find-renames",
                        f"{changed_from}...HEAD",
                        "--",
                        DOCKER_PREFIX,
                    ],
                ),
                f"{changed_from}...HEAD",
            )
        )
    changes.extend(
        parse_name_status_z(
            run_git(
                repo_root,
                [
                    "diff",
                    "--name-status",
                    "-z",
                    "--find-renames",
                    "HEAD",
                    "--",
                    DOCKER_PREFIX,
                ],
            ),
            "worktree",
        )
    )
    untracked = run_git(
        repo_root,
        ["ls-files", "--others", "--exclude-standard", "-z", "--", DOCKER_PREFIX],
    )
    for path in untracked.decode("utf-8", errors="surrogateescape").split("\0"):
        if path:
            changes.append({"status": "?", "path": path, "origin": "worktree"})
    return deduplicate_changes(changes)


def deduplicate_changes(changes: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[tuple[str, str | None], dict[str, str]] = {}
    for item in changes:
        key = (item["path"], item.get("old_path"))
        if key not in merged:
            merged[key] = dict(item)
            continue
        origins = set(merged[key]["origin"].split(","))
        origins.add(item["origin"])
        merged[key]["origin"] = ",".join(sorted(origins))
        if item["status"] != merged[key]["status"]:
            merged[key]["status"] = f"{merged[key]['status']}+{item['status']}"
    return sorted(merged.values(), key=lambda entry: (entry["path"], entry["status"]))


def repo_relative(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def explicit_changes(repo_root: Path, raw_paths: list[str]) -> list[dict[str, str]]:
    docker_root = (repo_root / DOCKER_PREFIX).resolve()
    results: list[dict[str, str]] = []
    for raw_path in raw_paths:
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(docker_root)
        except ValueError as exc:
            raise InventoryError(
                f"Explicit path must be under {DOCKER_PREFIX}: {raw_path}"
            ) from exc
        if not resolved.exists():
            raise InventoryError(f"Explicit path does not exist: {raw_path}")
        if resolved.is_dir():
            files = sorted(path for path in resolved.rglob("*") if path.is_file())
        else:
            files = [resolved]
        for path in files:
            results.append(
                {
                    "status": "explicit",
                    "path": repo_relative(repo_root, path),
                    "origin": "explicit",
                }
            )
    return deduplicate_changes(results)


def component_root(relative_path: str) -> str:
    parts = Path(relative_path).parts
    if len(parts) < 3 or parts[:2] != ("deploy", "docker"):
        return DOCKER_PREFIX
    if len(parts) == 3:
        return DOCKER_PREFIX
    family = parts[2]
    if family == "services":
        if len(parts) >= 5:
            return Path(*parts[:4]).as_posix()
        return "deploy/docker/services"
    if family == "developer-profiles":
        if len(parts) >= 5:
            return Path(*parts[:4]).as_posix()
        return "deploy/docker/developer-profiles"
    if family == "industry-profiles":
        if len(parts) < 5:
            return "deploy/docker/industry-profiles"
        if parts[3] == "warehouse-operations" and len(parts) >= 6:
            if parts[4] in {
                "warehouse-2d-app",
                "warehouse-3d-app",
                "warehouse-mv3dt-app",
            }:
                return Path(*parts[:5]).as_posix()
            return "deploy/docker/industry-profiles/warehouse-operations"
        return Path(*parts[:4]).as_posix()
    return DOCKER_PREFIX


def files_below(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    return sorted(
        candidate
        for candidate in path.rglob("*")
        if candidate.is_file() and candidate.name not in EXCLUDED_CONTEXT_NAMES
    )


def build_source_context(repo_root: Path, changes: list[dict[str, str]]) -> tuple[list[str], list[str]]:
    roots = sorted(
        {
            component_root(path)
            for item in changes
            for path in (item["path"], item.get("old_path"))
            if path
        }
    )
    context: set[str] = set()
    for root in roots:
        for path in files_below(repo_root / root):
            context.add(repo_relative(repo_root, path))

        parts = Path(root).parts
        context.add("deploy/docker/compose.yml")
        if len(parts) >= 3 and parts[2] in {"services", "developer-profiles", "industry-profiles"}:
            aggregate = repo_root / "deploy" / "docker" / parts[2] / "compose.yml"
            if aggregate.exists():
                context.add(repo_relative(repo_root, aggregate))

    for item in changes:
        if (repo_root / item["path"]).exists():
            context.add(item["path"])
    return roots, sorted(path for path in context if (repo_root / path).exists())


def candidate_targets(relative_path: str) -> list[str]:
    parts = Path(relative_path).parts
    if len(parts) < 4 or parts[:2] != ("deploy", "docker"):
        return [HELM_PREFIX]
    family = parts[2]
    if family == "services":
        if len(parts) == 4 or parts[3] == "compose.yml":
            return ["deploy/helm/services"]
        return SERVICE_TARGETS.get(parts[3], [f"deploy/helm/services/{parts[3]}"])
    if family == "developer-profiles":
        if len(parts) == 4 or parts[3] == "compose.yml":
            return ["deploy/helm/developer-profiles"]
        return [f"deploy/helm/developer-profiles/{parts[3]}"]
    if family == "industry-profiles":
        if len(parts) == 4 or parts[3] == "compose.yml":
            return ["deploy/helm/industry-profiles"]
        if parts[3] == "warehouse-operations" and len(parts) >= 5:
            if parts[4] in {
                "warehouse-2d-app",
                "warehouse-3d-app",
                "warehouse-mv3dt-app",
            }:
                return [f"deploy/helm/industry-profiles/warehouse-operations/{parts[4]}"]
            return ["deploy/helm/industry-profiles/warehouse-operations"]
        return [f"deploy/helm/industry-profiles/{parts[3]}"]
    return [
        "deploy/helm/services",
        "deploy/helm/developer-profiles",
        "deploy/helm/industry-profiles",
    ]


def safe_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise InventoryError(
            "PyYAML is required to resolve Helm dependency consumers. "
            "Install it in the authoring environment or inspect Chart.yaml files manually."
        )
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InventoryError(f"Cannot parse {path}: {exc}") from exc
    return loaded if isinstance(loaded, dict) else {}


def discover_charts(repo_root: Path) -> tuple[dict[Path, dict[str, Any]], dict[Path, set[Path]]]:
    charts: dict[Path, dict[str, Any]] = {}
    reverse: dict[Path, set[Path]] = defaultdict(set)
    helm_root = repo_root / HELM_PREFIX
    for chart_file in sorted(helm_root.rglob("Chart.yaml")):
        root = chart_file.parent.resolve()
        charts[root] = safe_yaml(chart_file)
    for parent, metadata in charts.items():
        dependencies = metadata.get("dependencies", [])
        if not isinstance(dependencies, list):
            continue
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            repository = dependency.get("repository")
            if not isinstance(repository, str) or not repository.startswith("file://"):
                continue
            child = (parent / repository[len("file://") :]).resolve()
            reverse[child].add(parent)
    return charts, reverse


def charts_for_targets(
    repo_root: Path, targets: list[str]
) -> tuple[list[str], list[str], list[str]]:
    charts, reverse = discover_charts(repo_root)
    primary: set[Path] = set()
    missing: list[str] = []
    for target in targets:
        target_path = (repo_root / target).resolve()
        if not target_path.exists():
            missing.append(target)
            continue
        if target_path in charts:
            primary.add(target_path)
        for chart_root in charts:
            try:
                chart_root.relative_to(target_path)
            except ValueError:
                continue
            primary.add(chart_root)

    consumers: set[Path] = set()
    queue: deque[Path] = deque(sorted(primary))
    visited = set(primary)
    while queue:
        child = queue.popleft()
        for parent in sorted(reverse.get(child, set())):
            if parent in visited:
                continue
            visited.add(parent)
            consumers.add(parent)
            queue.append(parent)

    def relative_chart(path: Path) -> str:
        return path.relative_to(repo_root.resolve()).as_posix()

    return (
        sorted(relative_chart(path) for path in primary),
        sorted(relative_chart(path) for path in consumers),
        sorted(set(missing)),
    )


def is_text_candidate(path: Path) -> bool:
    name = path.name.lower()
    return path.suffix.lower() in TEXT_SUFFIXES or name.startswith("dockerfile")


def read_text(path: Path) -> str | None:
    if not is_text_candidate(path):
        return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    if b"\0" in payload:
        return None
    return payload.decode("utf-8", errors="replace")


def next_directive_target(lines: list[str], start_index: int) -> tuple[int | None, str | None]:
    index = start_index + 1
    if index >= len(lines):
        return None, None
    stripped = lines[index].strip()
    if not stripped or stripped.startswith("#"):
        return None, None
    return index + 1, stripped


def scan_comments(repo_root: Path, source_files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    directives: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    comments: list[dict[str, Any]] = []
    for relative in source_files:
        source_path = repo_root / relative
        if source_path.suffix.lower() == ".md":
            continue
        text = read_text(source_path)
        if text is None:
            continue
        formal_directives_allowed = (
            source_path.suffix.lower() in {".yml", ".yaml"}
            and "compose" in source_path.name.lower()
        )
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if "helm-sync" in line.lower() and formal_directives_allowed:
                match = DIRECTIVE_RE.search(line)
                if not match:
                    malformed.append(
                        {
                            "file": relative,
                            "line": index + 1,
                            "text": line.strip(),
                            "error": "Expected '# helm-sync: ACTION | non-empty requirement'",
                        }
                    )
                    continue
                prefix = line[: match.start()].strip()
                if prefix:
                    target_line, target = index + 1, prefix
                else:
                    target_line, target = next_directive_target(lines, index)
                if target_line is None:
                    malformed.append(
                        {
                            "file": relative,
                            "line": index + 1,
                            "text": line.strip(),
                            "error": "Standalone directive has no following YAML target",
                        }
                    )
                    continue
                directives.append(
                    {
                        "file": relative,
                        "line": index + 1,
                        "action": match.group(1).lower(),
                        "requirement": match.group(2).strip(),
                        "target_line": target_line,
                        "target": target,
                    }
                )
                continue

            hash_index = line.find("#")
            if hash_index < 0:
                continue
            comment = line[hash_index + 1 :].strip()
            if comment and DEPLOYMENT_COMMENT_RE.search(comment):
                comments.append(
                    {"file": relative, "line": index + 1, "text": comment}
                )
    key = lambda item: (item["file"], item["line"])
    return sorted(directives, key=key), sorted(malformed, key=key), sorted(comments, key=key)


def service_line_numbers(text: str) -> dict[str, int]:
    numbers: dict[str, int] = {}
    in_services = False
    for number, line in enumerate(text.splitlines(), start=1):
        content = line.split("#", 1)[0].rstrip()
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        stripped = content.strip()
        if indent == 0:
            in_services = stripped == "services:"
        elif in_services and indent == 2:
            match = re.match(r"([A-Za-z0-9_.-]+):(?:\s.*)?$", stripped)
            if match:
                numbers.setdefault(match.group(1), number)
    return numbers


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return str(value)


def load_compose(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise InventoryError("PyYAML is required to extract Compose service facts")
    try:
        loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=ComposeLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise InventoryError(str(exc)) from exc
    return loaded if isinstance(loaded, dict) else {}


def environment_names(raw_environment: Any) -> list[str]:
    names: set[str] = set()
    if isinstance(raw_environment, dict):
        names.update(str(key) for key in raw_environment)
    elif isinstance(raw_environment, list):
        for item in raw_environment:
            if isinstance(item, str):
                names.add(item.split("=", 1)[0])
    return sorted(name for name in names if name)


def summarize_service(
    relative: str, name: str, definition: Any, line: int | None
) -> dict[str, Any]:
    if not isinstance(definition, dict):
        return {
            "file": relative,
            "line": line,
            "name": name,
            "fields": [],
            "parse_note": "Service definition is not a mapping",
        }
    important_fields = (
        "image",
        "build",
        "container_name",
        "profiles",
        "entrypoint",
        "command",
        "working_dir",
        "user",
        "ports",
        "expose",
        "env_file",
        "volumes",
        "configs",
        "secrets",
        "healthcheck",
        "depends_on",
        "restart",
        "deploy",
        "gpus",
        "devices",
        "runtime",
        "network_mode",
        "networks",
        "extra_hosts",
        "dns",
        "ipc",
        "pid",
        "privileged",
        "cap_add",
        "cap_drop",
        "security_opt",
        "read_only",
        "shm_size",
        "ulimits",
        "stop_grace_period",
        "stop_signal",
    )
    summary: dict[str, Any] = {
        "file": relative,
        "line": line,
        "name": name,
        "fields": sorted(str(key) for key in definition),
        "environment_names": environment_names(definition.get("environment")),
    }
    for field in important_fields:
        if field in definition:
            summary[field] = json_safe(definition[field])
    return summary


def compose_declared_references(document: dict[str, Any]) -> list[str]:
    references: set[str] = set()

    def add_values(raw: Any) -> None:
        if isinstance(raw, str):
            references.add(raw)
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, str):
                    references.add(item)
                elif isinstance(item, dict):
                    if isinstance(item.get("path"), str):
                        references.add(item["path"])
                    add_values(item.get("env_file"))
        elif isinstance(raw, dict):
            path = raw.get("path")
            if isinstance(path, str):
                references.add(path)
            add_values(raw.get("env_file"))

    add_values(document.get("include"))
    services = document.get("services", {})
    if isinstance(services, dict):
        for definition in services.values():
            if not isinstance(definition, dict):
                continue
            extends = definition.get("extends")
            if isinstance(extends, dict) and isinstance(extends.get("file"), str):
                references.add(extends["file"])
            add_values(definition.get("env_file"))
    return sorted(references)


def compose_facts(
    repo_root: Path, source_files: list[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    services_inventory: list[dict[str, Any]] = []
    parse_errors: list[dict[str, Any]] = []
    for relative in source_files:
        path = repo_root / relative
        if path.name.lower() not in COMPOSE_NAMES:
            continue
        text = read_text(path)
        if text is None:
            continue
        line_numbers = service_line_numbers(text)
        services: list[str] = []
        in_services = False
        references: list[str] = []
        for line in text.splitlines():
            content = line.split("#", 1)[0].rstrip()
            if not content.strip():
                continue
            indent = len(content) - len(content.lstrip(" "))
            stripped = content.strip()
            if indent == 0:
                in_services = stripped == "services:"
            elif in_services and indent == 2:
                match = re.match(r"([A-Za-z0-9_.-]+):(?:\s.*)?$", stripped)
                if match:
                    services.append(match.group(1))
            ref_match = re.match(
                r"-?\s*(?:path|file|env_file):\s*(.+?)\s*$", stripped
            )
            if ref_match:
                references.append(ref_match.group(1).strip("'\""))
        facts.append(
            {
                "file": relative,
                "services": sorted(set(services)),
                "environment_tokens": sorted(set(ENV_TOKEN_RE.findall(text))),
                "declared_references": sorted(set(references)),
            }
        )
        try:
            loaded = load_compose(path)
        except InventoryError as exc:
            parse_errors.append({"file": relative, "error": str(exc)})
            continue
        facts[-1]["declared_references"] = sorted(
            set(facts[-1]["declared_references"])
            | set(compose_declared_references(loaded))
        )
        services = loaded.get("services", {})
        if services is None:
            services = {}
        if not isinstance(services, dict):
            parse_errors.append(
                {"file": relative, "error": "Top-level services must be a mapping"}
            )
            continue
        for name, definition in services.items():
            service_name = str(name)
            services_inventory.append(
                summarize_service(
                    relative, service_name, definition, line_numbers.get(service_name)
                )
            )
    service_key = lambda item: (item["file"], item.get("line") or 0, item["name"])
    return facts, sorted(services_inventory, key=service_key), parse_errors


def environment_files(repo_root: Path, source_files: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    assignment_re = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for relative in source_files:
        path = repo_root / relative
        if path.suffix != ".env" and not path.name.endswith(".env"):
            continue
        text = read_text(path)
        if text is None:
            continue
        names = []
        for line in text.splitlines():
            match = assignment_re.match(line)
            if match:
                names.append(match.group(1))
        results.append({"file": relative, "variables": sorted(set(names))})
    return results


def service_profiles(service_inventory: list[dict[str, Any]]) -> list[str]:
    profiles: set[str] = set()
    for service in service_inventory:
        raw = service.get("profiles")
        if isinstance(raw, str):
            profiles.add(raw)
        elif isinstance(raw, list):
            profiles.update(str(item) for item in raw if item is not None)
    return sorted(profile for profile in profiles if profile)


ENV_ASSIGNMENT_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"
)
ENV_REFERENCE_RE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}"
)


def available_profile_roots(repo_root: Path) -> list[str]:
    roots: set[str] = set()
    developer_root = repo_root / "deploy/docker/developer-profiles"
    if developer_root.is_dir():
        for child in developer_root.iterdir():
            if child.is_dir():
                roots.add(repo_relative(repo_root, child))
    industry_root = repo_root / "deploy/docker/industry-profiles"
    if industry_root.is_dir():
        for child in industry_root.iterdir():
            if child.is_dir():
                roots.add(repo_relative(repo_root, child))
    return sorted(roots)


def parse_env_assignments(paths: list[Path]) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for path in paths:
        text = read_text(path)
        if text is None:
            continue
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            match = ENV_ASSIGNMENT_RE.match(line)
            if not match:
                continue
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            assignments[match.group(1)] = value
    return assignments


def expand_env_expression(value: str, assignments: dict[str, str]) -> str:
    expanded = value
    for _ in range(12):
        changed = False

        def replace(match: re.Match[str]) -> str:
            nonlocal changed
            name, default = match.group(1), match.group(2)
            if name in assignments:
                replacement = assignments[name]
            elif default is not None:
                replacement = default
            else:
                return match.group(0)
            changed = True
            return replacement

        next_value = ENV_REFERENCE_RE.sub(replace, expanded)
        expanded = next_value
        if not changed:
            break
    return expanded


def expression_matches_profile(expression: str, profile: str) -> bool:
    for candidate in expression.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        pieces: list[str] = []
        position = 0
        for match in ENV_REFERENCE_RE.finditer(candidate):
            pieces.append(re.escape(candidate[position : match.start()]))
            pieces.append(r"[^,\s]+")
            position = match.end()
        pieces.append(re.escape(candidate[position:]))
        if re.fullmatch("".join(pieces), profile):
            return True
    return False


def discover_profile_consumers(repo_root: Path, profiles: list[str]) -> list[str]:
    if not profiles:
        return []
    consumers: list[str] = []
    for root in available_profile_roots(repo_root):
        env_paths = sorted(
            path
            for path in (repo_root / root).rglob("*.env")
            if path.name not in EXCLUDED_CONTEXT_NAMES
        )
        assignments = parse_env_assignments(env_paths)
        expressions = [
            expand_env_expression(value, assignments)
            for name, value in assignments.items()
            if name.startswith("COMPOSE_PROFILES")
        ]
        if any(
            expression_matches_profile(expression, profile)
            for expression in expressions
            for profile in profiles
        ):
            consumers.append(root)
    return consumers


def helm_target_for_profile_root(profile_root: str) -> str:
    parts = Path(profile_root).parts
    if parts[:3] == ("deploy", "docker", "developer-profiles") and len(parts) >= 4:
        return f"deploy/helm/developer-profiles/{parts[3]}"
    if parts[:3] == ("deploy", "docker", "industry-profiles") and len(parts) >= 4:
        if parts[3] == "warehouse-operations":
            return "deploy/helm/industry-profiles/warehouse-operations"
        return f"deploy/helm/industry-profiles/{parts[3]}"
    return "deploy/helm"


def make_report(repo_root: Path, changes: list[dict[str, str]]) -> dict[str, Any]:
    roots, source_files = build_source_context(repo_root, changes)
    targets = sorted(
        {
            target
            for item in changes
            for path in (item["path"], item.get("old_path"))
            if path
            for target in candidate_targets(path)
        }
    )
    initial_compose_files, initial_services, initial_parse_errors = compose_facts(
        repo_root, source_files
    )
    related_profile_roots = discover_profile_consumers(
        repo_root, service_profiles(initial_services)
    )
    expanded_source_files = set(source_files)
    for profile_root in related_profile_roots:
        for path in files_below(repo_root / profile_root):
            expanded_source_files.add(repo_relative(repo_root, path))
        parts = Path(profile_root).parts
        if len(parts) >= 3:
            aggregate = repo_root / "deploy" / "docker" / parts[2] / "compose.yml"
            if aggregate.is_file():
                expanded_source_files.add(repo_relative(repo_root, aggregate))
    source_files = sorted(expanded_source_files)

    primary, consumers, missing = charts_for_targets(repo_root, targets)
    directives, malformed, comments = scan_comments(repo_root, source_files)
    if related_profile_roots:
        compose_files, service_inventory, compose_parse_errors = compose_facts(
            repo_root, source_files
        )
    else:
        compose_files = initial_compose_files
        service_inventory = initial_services
        compose_parse_errors = initial_parse_errors
    profile_helm_targets = sorted(
        {helm_target_for_profile_root(root) for root in related_profile_roots}
    )
    missing_profile_helm_targets = [
        target for target in profile_helm_targets if not (repo_root / target).exists()
    ]
    return {
        "repo_root": str(repo_root.resolve()),
        "selected_changes": changes,
        "component_roots": roots,
        "docker_profile_consumers": related_profile_roots,
        "profile_helm_targets": profile_helm_targets,
        "missing_profile_helm_targets": missing_profile_helm_targets,
        "source_context_files": source_files,
        "compose_files": compose_files,
        "service_inventory": service_inventory,
        "compose_parse_errors": compose_parse_errors,
        "environment_files": environment_files(repo_root, source_files),
        "candidate_helm_targets": targets,
        "primary_charts": primary,
        "transitive_consumer_charts": consumers,
        "missing_candidate_targets": missing,
        "directives": directives,
        "malformed_directives": malformed,
        "deployment_comments": comments,
    }


def markdown_list(values: list[str], empty: str = "None") -> list[str]:
    return [f"- `{value}`" for value in values] if values else [f"- {empty}"]


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Compose-to-Helm context", "", "## Selected Docker changes", ""]
    if report["selected_changes"]:
        for item in report["selected_changes"]:
            rename = f" (from `{item['old_path']}`)" if item.get("old_path") else ""
            lines.append(
                f"- `{item['status']}` `{item['path']}`{rename} — {item['origin']}"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Component roots", ""])
    lines.extend(markdown_list(report["component_roots"]))
    lines.extend(["", "## Docker profile consumers", ""])
    lines.extend(markdown_list(report["docker_profile_consumers"]))
    lines.extend(["", "## Corresponding Helm profile targets", ""])
    lines.extend(markdown_list(report["profile_helm_targets"]))
    if report["missing_profile_helm_targets"]:
        lines.extend(["", "### Missing Helm profile targets", ""])
        lines.extend(markdown_list(report["missing_profile_helm_targets"]))
    lines.extend(["", "## Candidate Helm targets", ""])
    lines.extend(markdown_list(report["candidate_helm_targets"]))
    if report["missing_candidate_targets"]:
        lines.extend(["", "### Missing candidate targets", ""])
        lines.extend(markdown_list(report["missing_candidate_targets"]))
    lines.extend(["", "## Primary charts", ""])
    lines.extend(markdown_list(report["primary_charts"]))
    lines.extend(["", "## Transitive consumer charts", ""])
    lines.extend(markdown_list(report["transitive_consumer_charts"]))

    lines.extend(["", "## Compose facts", ""])
    if not report["compose_files"]:
        lines.append("- None")
    for item in report["compose_files"]:
        services = ", ".join(f"`{name}`" for name in item["services"]) or "none detected"
        env = ", ".join(f"`{name}`" for name in item["environment_tokens"]) or "none"
        references = ", ".join(
            f"`{name}`" for name in item["declared_references"]
        ) or "none"
        lines.append(
            f"- `{item['file']}` — services: {services}; env tokens: {env}; "
            f"declared references: {references}"
        )

    lines.extend(["", "## Service ledger scaffold", ""])
    if not report["service_inventory"]:
        lines.append("- None")
    for item in report["service_inventory"]:
        location = f"{item['file']}:{item['line']}" if item.get("line") else item["file"]
        fields = ", ".join(f"`{name}`" for name in item["fields"]) or "none"
        environment = ", ".join(
            f"`{name}`" for name in item.get("environment_names", [])
        ) or "none"
        lines.append(
            f"- `{location}` **{item['name']}** — fields: {fields}; env: {environment}"
        )

    lines.extend(["", "## Compose parse errors", ""])
    if not report["compose_parse_errors"]:
        lines.append("- None")
    for item in report["compose_parse_errors"]:
        lines.append(f"- `{item['file']}`: {item['error']}")

    lines.extend(["", "## Environment layers", ""])
    if not report["environment_files"]:
        lines.append("- None")
    for item in report["environment_files"]:
        variables = ", ".join(f"`{name}`" for name in item["variables"]) or "none"
        lines.append(f"- `{item['file']}` — variables: {variables}")

    lines.extend(["", "## Helm-sync directives", ""])
    if not report["directives"]:
        lines.append("- None")
    for item in report["directives"]:
        lines.append(
            f"- `{item['file']}:{item['line']}` **{item['action']}** → "
            f"`{item['file']}:{item['target_line']}`: {item['requirement']}"
        )

    lines.extend(["", "## Malformed directives", ""])
    if not report["malformed_directives"]:
        lines.append("- None")
    for item in report["malformed_directives"]:
        lines.append(
            f"- `{item['file']}:{item['line']}`: {item['error']} — `{item['text']}`"
        )

    lines.extend(["", "## Other deployment-related comments", ""])
    if not report["deployment_comments"]:
        lines.append("- None")
    for item in report["deployment_comments"]:
        lines.append(f"- `{item['file']}:{item['line']}`: {item['text']}")

    lines.extend(["", "## Source context files", ""])
    lines.extend(markdown_list(report["source_context_files"]))
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only Docker-to-Helm change and directive inventory"
    )
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument(
        "--changed-from",
        help="Git base ref; inventories BASE...HEAD plus current worktree changes",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="Explicit file/directory under deploy/docker (repeatable)",
    )
    parser.add_argument(
        "--format", choices=("markdown", "json"), default="markdown"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    repo_root = Path(args.repo_root).resolve()
    if not (repo_root / DOCKER_PREFIX).is_dir() or not (repo_root / HELM_PREFIX).is_dir():
        print(
            f"ERROR: {repo_root} must contain {DOCKER_PREFIX} and {HELM_PREFIX}",
            file=sys.stderr,
        )
        return 4
    try:
        changes = (
            explicit_changes(repo_root, args.path)
            if args.path
            else git_changes(repo_root, args.changed_from)
        )
        if not changes:
            print(
                "ERROR: no Docker source changes found; pass --path or --changed-from",
                file=sys.stderr,
            )
            return 3
        report = make_report(repo_root, changes)
    except InventoryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4

    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 2 if report["malformed_directives"] or report["compose_parse_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
