"""Shared state, validation, and hashing."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path


KEY_RE = re.compile(
    r"^[a-z0-9]+(?:[_-][a-z0-9]+)*(?:\.[a-z0-9]+(?:[_-][a-z0-9]+)*)*$"
)
SECRET_RE = re.compile(
    r"\b(?:nvapi-|hf_|sk-|ghp_|gho_|github_pat_)[A-Za-z0-9_-]{8,}"
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(?P<name>[A-Za-z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD)"
    r"[A-Za-z0-9_]*)\s*(?:=|:(?!-))\s*(?P<value>[^\s]+)", re.IGNORECASE,
)
TOKEN_COUNT_RE = re.compile(
    r"^(?:max|min|num|input|output|total|cache|prompt|completion|context|reasoning)"
    r"(?:_[a-z0-9]+)*_tokens?$", re.IGNORECASE,
)
VARIABLE_REFERENCE_RE = re.compile(
    r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*(?::-)?\})"
)


class ProceduralMemoryError(RuntimeError):
    pass


class MemoryMiss(ProceduralMemoryError):
    pass


def contains_credential(text: str) -> bool:
    """Detect credential values without treating numeric token limits as secrets."""
    if SECRET_RE.search(text):
        return True
    for match in SECRET_ASSIGNMENT_RE.finditer(text):
        name = match.group("name")
        value = match.group("value").rstrip(",;}").strip("\"'")
        if TOKEN_COUNT_RE.fullmatch(name) and re.fullmatch(r"\d+(?:\.\d+)?", value):
            continue
        if VARIABLE_REFERENCE_RE.fullmatch(value):
            continue
        return True
    return False


def enabled() -> bool:
    return os.environ.get("PLAN_EXECUTE_CACHE", "").strip().lower() in {
        "1", "true", "yes", "on"
    }


def sha256(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def validate_key(key: str) -> str:
    if len(key) > 120 or not KEY_RE.fullmatch(key):
        raise ProceduralMemoryError(f"invalid procedure key: {key!r}")
    return key


def repo_root() -> Path:
    override = os.environ.get("PLAN_EXECUTE_CACHE_REPO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    result = subprocess.run(
        ["git", "-C", str(Path(__file__).parent), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=True,
    )
    return Path(result.stdout.strip()).resolve()


def state_home() -> Path:
    return Path(
        os.environ.get("PLAN_EXECUTE_CACHE_HOME", "~/.plan-execute-cache")
    ).expanduser()


def source_hash(path: Path) -> str:
    if path.is_file():
        return sha256(path.read_bytes())
    digest = hashlib.sha256()
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        relative = child.relative_to(path)
        if "__pycache__" in relative.parts or child.suffix in {".pyc", ".pyo"}:
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_record(source: str) -> dict:
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = repo_root() / path
    path = path.resolve()
    if not path.is_file() and not path.is_dir():
        raise ProceduralMemoryError(f"procedure source does not exist: {source}")
    try:
        stored = str(path.relative_to(repo_root()))
    except ValueError:
        stored = str(path)
    return {"path": stored, "sha256": source_hash(path)}


def resolve_source(record: dict) -> Path:
    path = Path(record["path"])
    return path if path.is_absolute() else repo_root() / path
