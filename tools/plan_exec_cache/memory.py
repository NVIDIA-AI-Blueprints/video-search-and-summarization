"""Store reusable Markdown procedures."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from core import (MemoryMiss, ProceduralMemoryError, contains_credential,
                  repo_root, resolve_source, sha256, source_hash, source_record,
                  state_home, validate_key)


INPUT_RE = re.compile(r"\{\{input\.[^}]+\}\}")
UNSAFE_RE = re.compile(r"(?m)(?:^|[;&|]\s*)eval\b")
ACTION_RE = re.compile(r"(?ms)^```(bash|sh|tool)\s*\n(.*?)^```\s*$")
VARIABLE_RE = re.compile(r"\$(?:\{)?([A-Z][A-Z0-9_]*)(?:\})?")
ASSIGNMENT_RE = re.compile(
    r"(?m)^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)\s*="
)


def write_atomic(path: Path, text: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", text=True,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def write_json(path: Path, value: dict) -> None:
    write_atomic(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def section(text: str, name: str) -> str:
    lines = text.splitlines()
    start = level = None
    fence: tuple[str, int] | None = None
    for index, line in enumerate(lines):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token[0], len(token)
            elif token[0] == fence[0] and len(token) >= fence[1]:
                fence = None
            continue
        if fence:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not heading:
            continue
        if start is not None:
            if len(heading.group(1)) <= level:
                return "\n".join(lines[start:index]).strip()
        elif heading.group(2).strip().casefold() == name.casefold():
            start = index + 1
            level = len(heading.group(1))
    return "\n".join(lines[start:]).strip() if start is not None else ""


def validate_procedure(text: str) -> str:
    text = text.strip() + "\n"
    for required in (
        "Description", "Preconditions and constraints", "Request binding",
        "Runtime values", "Source compliance", "Steps", "Verification",
    ):
        if not section(text, required):
            raise ProceduralMemoryError(f"procedure needs a {required} section")
    if not re.search(
        r"(?mi)^\s*-\s*Required:\s*\S", section(text, "Source compliance")
    ):
        raise ProceduralMemoryError(
            "Source compliance needs at least one Required: mapping"
        )
    binding = section(text, "Request binding")
    bound_variables = set(VARIABLE_RE.findall(binding))
    if not bound_variables and not re.search(
        r"(?mi)^\s*-\s*None\s*;", binding
    ):
        raise ProceduralMemoryError(
            "Request binding must name each request-derived $VARIABLE or "
            "declare None"
        )
    if contains_credential(text):
        raise ProceduralMemoryError("procedure contains a credential value")
    if str(repo_root()) in text:
        raise ProceduralMemoryError(
            "procedure contains the current workspace path; use a runtime "
            "value instead"
        )
    if UNSAFE_RE.search(text):
        raise ProceduralMemoryError("procedure cannot use eval on discovered text")
    if INPUT_RE.search(text):
        raise ProceduralMemoryError(
            "procedure must resolve request values during execution, "
            "not use input placeholders"
        )
    actions = ACTION_RE.findall(section(text, "Steps"))
    if not actions:
        raise ProceduralMemoryError(
            "procedure Steps need a complete bash, sh, or tool block"
        )
    declared_variables = bound_variables | set(
        VARIABLE_RE.findall(section(text, "Runtime values"))
    )
    action_variables = set()
    assigned_variables = set()
    for kind, body in actions:
        action_variables.update(VARIABLE_RE.findall(body))
        if kind in {"bash", "sh"}:
            assigned_variables.update(ASSIGNMENT_RE.findall(body))
    undeclared = action_variables - declared_variables - assigned_variables
    if undeclared:
        names = ", ".join(f"${name}" for name in sorted(undeclared))
        raise ProceduralMemoryError(
            f"procedure uses undeclared runtime values: {names}"
        )
    for index, (kind, body) in enumerate(actions, start=1):
        assigned = bound_variables.intersection(ASSIGNMENT_RE.findall(body))
        if assigned:
            names = ", ".join(f"${name}" for name in sorted(assigned))
            raise ProceduralMemoryError(
                f"procedure action {index} assigns request-bound {names}; "
                "bind request values only at execution time"
            )
        if kind in {"bash", "sh"}:
            checked = subprocess.run(
                ["bash", "-n"], input=body, capture_output=True, text=True,
            )
            if checked.returncode:
                raise ProceduralMemoryError(
                    f"procedure shell block {index} is invalid: "
                    f"{checked.stderr.strip()}"
                )
            continue
        try:
            call = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ProceduralMemoryError(
                f"procedure tool block {index} is invalid JSON: {exc}"
            ) from exc
        if (not isinstance(call, dict)
                or set(call) != {"name", "input"}
                or not isinstance(call["name"], str)
                or not isinstance(call["input"], dict)):
            raise ProceduralMemoryError(
                f"procedure tool block {index} needs string name and object input"
            )
    return text


def dependency_records(sources: list[str]) -> list[dict]:
    if not sources:
        raise ProceduralMemoryError(
            "procedure needs an instruction source for invalidation"
        )
    records = {record["path"]: record for record in map(source_record, sources)}
    return [records[path] for path in sorted(records)]


def valid_dependencies(value: object) -> bool:
    return (isinstance(value, list) and bool(value)
            and all(isinstance(item, dict)
                    and set(item) == {"path", "sha256"}
                    and all(isinstance(item[name], str)
                            for name in ("path", "sha256"))
                    for item in value))


class ProcedureStore:
    def __init__(self, home: Path | None = None):
        self.home = home or state_home()

    def procedure_dir(self, key: str) -> Path:
        return self.home / "memories" / validate_key(key)

    def remember(self, key: str, text: str,
                 sources: list[str]) -> Path:
        """Store the final procedure that the agent executed and verified."""
        text = validate_procedure(text)
        key = validate_key(key)
        metadata = {
            "action_key": key,
            "description": re.sub(r"\s+", " ", section(text, "Description")),
            "procedure_sha256": sha256(text),
            "sources": dependency_records(sources),
        }
        target = self.procedure_dir(key)
        target.mkdir(parents=True, exist_ok=True)
        write_atomic(target / "procedure.md", text)
        write_json(target / "metadata.json", metadata)
        return target

    def load(self, key: str) -> tuple[str, dict]:
        target = self.procedure_dir(key)
        try:
            text = (target / "procedure.md").read_text(encoding="utf-8")
            metadata = json.loads(
                (target / "metadata.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise MemoryMiss(f"no valid reusable procedure for {key}") from exc
        if (not isinstance(metadata, dict)
                or set(metadata) != {
                    "action_key", "description", "procedure_sha256", "sources"
                }
                or metadata.get("action_key") != key
                or not isinstance(metadata.get("description"), str)
                or metadata.get("procedure_sha256") != sha256(text)
                or not valid_dependencies(metadata.get("sources"))):
            raise MemoryMiss(f"stale reusable procedure for {key}")
        try:
            validate_procedure(text)
        except ProceduralMemoryError as exc:
            raise MemoryMiss(f"invalid reusable procedure for {key}") from exc
        for record in metadata["sources"]:
            path = resolve_source(record)
            if not path.exists() or source_hash(path) != record["sha256"]:
                raise MemoryMiss(f"dependency changed: {record['path']}")
        return text, metadata

    def inventory(self) -> list[tuple[str, dict]]:
        root = self.home / "memories"
        result = []
        for path in sorted(root.iterdir()) if root.is_dir() else []:
            if not path.is_dir():
                continue
            try:
                key = validate_key(path.name)
                _text, metadata = self.load(key)
            except (MemoryMiss, ProceduralMemoryError):
                continue
            result.append((key, metadata))
        return result
