#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Privacy-safe progress monitoring for direct OpenShell agent runs."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import hmac
import json
import os
import re
import selectors
import shlex
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

DEFAULT_HARD_CEILING_SEC = 7200
# A clean direct OpenShell trial deletes model volumes; alerts/LVS cold starts
# spend about 20 minutes downloading weights before stable container health.
# Twenty-five minutes covers that observed floor without consuming the 2h cap.
DEFAULT_COLD_START_GRACE_SEC = 25 * 60
# Eighteen minutes tolerates slow registry/build transitions while terminating
# a genuinely motionless run well before the unchanged 7200-second ceiling.
DEFAULT_IDLE_TIMEOUT_SEC = 18 * 60
DEFAULT_POLL_SEC = 30
DEFAULT_ACTIVITY_HEARTBEAT_SEC = 5 * 60
MAX_JOURNAL_EVENTS = 512
MAX_DIAGNOSTIC_SERVICES = 64
MAX_EXPECTED_SERVICES = 64
MAX_REQUIRED_LOCAL_IMAGES = 32
MAX_CLEANUP_CONTAINERS = 256
MAX_PROGRESS_KEYS = 256
MAX_SEEN_IMAGE_IDS = 4096
# A fixed five-minute heartbeat is rate-bounded but may cover the entire
# legitimate pull/build window. The immutable 7200-second hard ceiling still
# wins before another idle extension could carry an active phase beyond it.
MAX_PHASE_HEARTBEATS = 21
MAX_SPEC_BYTES = 1024 * 1024
MAX_COMPOSE_BYTES = 4 * 1024 * 1024
MAX_COMMAND_OUTPUT_BYTES = 1024 * 1024
MAX_INNER_READ_BYTES = 1024 * 1024

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,255}$"
)
_SAFE_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_STATES = frozenset(
    {
        "created",
        "running",
        "paused",
        "restarting",
        "removing",
        "exited",
        "dead",
        "unknown",
    }
)
_HEALTH = frozenset({"healthy", "unhealthy", "starting", "none", "unknown"})
_PROGRESS_CATEGORIES = frozenset(
    {
        "file_mutation",
        "compose_validated",
        "compose_phase",
        "container_transition",
        "image_activity",
        "image_activity_heartbeat",
    }
)
_EVENT_FIELDS = {
    "watchdog_started": frozenset(
        {
            "hard_ceiling_sec",
            "cold_start_grace_sec",
            "idle_timeout_sec",
        }
    ),
    "tool": frozenset({"tool_category"}),
    "file_mutation": frozenset({"mutation_kind", "target_kind"}),
    "expected_services": frozenset({"service_count", "compose_sha256"}),
    "compose_validated": frozenset({"service_count", "compose_sha256"}),
    "compose_phase": frozenset({"phase"}),
    "container_transition": frozenset(
        {
            "service_index",
            "previous_state",
            "state",
            "health",
            "exit_code",
            "restart_count",
        }
    ),
    "image_activity": frozenset({"image_count", "delta"}),
    "image_activity_heartbeat": frozenset({"phase"}),
    "timeout": frozenset(
        {
            "reason",
            "last_progress_category",
            "last_progress_elapsed_sec",
        }
    ),
    "cleanup": frozenset({"outcome"}),
    "finished": frozenset({"outcome"}),
}
_ENUM_FIELDS = {
    "tool_category": frozenset(
        {
            "shell",
            "read",
            "edit",
            "write",
            "search",
            "other",
        }
    ),
    "mutation_kind": frozenset({"edit", "write"}),
    "target_kind": frozenset({"compose", "config", "source", "other"}),
    "phase": frozenset({"config", "pull", "build", "up", "none"}),
    "reason": frozenset({"idle", "hard-ceiling"}),
    "outcome": frozenset({"success", "failure", "cancelled"}),
    "last_progress_category": _PROGRESS_CATEGORIES | frozenset({"startup"}),
}


class DirectAgentWatchdogExpired(RuntimeError):  # noqa: N818
    """The direct-run hard or idle watchdog fired."""


class BoundedCommandResult(NamedTuple):
    returncode: int
    stdout: str
    truncated: bool


def _read_bytes_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ValueError("input exceeds watchdog size limit")
    return data


def _read_json_bounded(path: Path, limit: int = MAX_SPEC_BYTES) -> object:
    return json.loads(_read_bytes_bounded(path, limit))


def _run_bounded(
    command: list[str],
    *,
    timeout: float,
    output_limit: int = MAX_COMMAND_OUTPUT_BYTES,
) -> BoundedCommandResult:
    """Run a fixed argv with bounded retained output and process-group reap."""
    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    retained = bytearray()
    truncated = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(proc.stdout.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                room = output_limit - len(retained)
                if room > 0:
                    retained.extend(chunk[:room])
                if len(chunk) > room:
                    truncated = True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout)
        returncode = proc.wait(timeout=remaining)
    except BaseException:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                with suppress(OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
        raise
    finally:
        selector.close()
        proc.stdout.close()
    return BoundedCommandResult(
        returncode,
        retained.decode("utf-8", errors="replace"),
        truncated,
    )


def _safe_name(value: object, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if _SAFE_NAME_RE.fullmatch(text) else default


def _safe_state(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _STATES else "unknown"


def _safe_health(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in _HEALTH else "unknown"


class ProgressJournal:
    """Bounded JSONL writer with a closed event/field vocabulary."""

    def __init__(
        self,
        path: Path,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime.datetime] | None = None,
        max_events: int = MAX_JOURNAL_EVENTS,
    ):
        self.path = path
        self._monotonic = monotonic
        self._wall_clock = wall_clock or (lambda: datetime.datetime.now(datetime.UTC))
        self._started = monotonic()
        self._max_events = max_events
        try:
            with path.open("rb") as handle:
                existing = handle.read(MAX_INNER_READ_BYTES + 1)
            self._events = (
                max_events if len(existing) > MAX_INNER_READ_BYTES else min(max_events, len(existing.splitlines()))
            )
        except OSError:
            self._events = 0

    def record(self, category: str, **fields: object) -> bool:
        allowed = _EVENT_FIELDS.get(category)
        if allowed is None:
            raise ValueError(f"unsupported progress category: {category}")
        unexpected = set(fields) - allowed
        if unexpected:
            raise ValueError(f"unsupported fields for {category}: {sorted(unexpected)}")
        for name, allowed_values in _ENUM_FIELDS.items():
            if name in fields and fields[name] not in allowed_values:
                raise ValueError(f"unsafe {name} value")
        for name in ("previous_state", "state", "health"):
            if name in fields and name in {"previous_state", "state"}:
                if fields[name] not in _STATES:
                    raise ValueError(f"unsafe {name} value")
            elif name in fields and fields[name] not in _HEALTH:
                raise ValueError("unsafe health value")
        for name in (
            "service_index",
            "service_count",
            "image_count",
            "delta",
            "exit_code",
            "restart_count",
        ):
            value = fields.get(name)
            if value is not None and (not isinstance(value, int) or abs(value) > 1_000_000):
                raise ValueError(f"unsafe {name} value")
        if "compose_sha256" in fields and not _SAFE_HASH_RE.fullmatch(str(fields["compose_sha256"])):
            raise ValueError("unsafe compose_sha256 value")
        if self._events >= self._max_events and category not in {
            "timeout",
            "cleanup",
            "finished",
        }:
            return False
        event = {
            "schema": 1,
            "timestamp": self._wall_clock().isoformat(),
            "elapsed_sec": round(self._monotonic() - self._started, 3),
            "category": category,
            **fields,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        self._events += 1
        return True


class ProgressTracker:
    """Monotonic hard/idle deadline state with an initial cold-start grace."""

    def __init__(
        self,
        *,
        hard_ceiling_sec: float = DEFAULT_HARD_CEILING_SEC,
        cold_start_grace_sec: float = DEFAULT_COLD_START_GRACE_SEC,
        idle_timeout_sec: float = DEFAULT_IDLE_TIMEOUT_SEC,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        if min(hard_ceiling_sec, cold_start_grace_sec, idle_timeout_sec) <= 0:
            raise ValueError("watchdog durations must be positive")
        self.hard_ceiling_sec = hard_ceiling_sec
        self.cold_start_grace_sec = cold_start_grace_sec
        self.idle_timeout_sec = idle_timeout_sec
        self._monotonic = monotonic
        self.started = monotonic()
        self.last_progress = self.started
        self.last_progress_category = "startup"
        self._seen_progress: set[tuple[str, object]] = set()

    def progress(self, category: str, token: object | None = None) -> bool:
        if category not in _PROGRESS_CATEGORIES:
            return False
        key = (category, category if token is None else token)
        if key in self._seen_progress or len(self._seen_progress) >= MAX_PROGRESS_KEYS:
            return False
        self._seen_progress.add(key)
        self.last_progress = self._monotonic()
        self.last_progress_category = category
        return True

    def expiration(self) -> tuple[str, str, float] | None:
        now = self._monotonic()
        elapsed = now - self.started
        idle = now - self.last_progress
        if elapsed >= self.hard_ceiling_sec:
            return "hard-ceiling", self.last_progress_category, idle
        if elapsed >= self.cold_start_grace_sec and idle >= self.idle_timeout_sec:
            return "idle", self.last_progress_category, idle
        return None


def load_expected_services(spec_path: Path) -> tuple[str, ...]:
    try:
        data = _read_json_bounded(spec_path)
    except (OSError, TypeError, ValueError):
        return ()
    if not isinstance(data, dict):
        return ()
    raw = data.get("expected_services") or []
    if not isinstance(raw, list):
        raise ValueError("expected_services must be a JSON list")
    if len(raw) > MAX_EXPECTED_SERVICES:
        raise ValueError("expected_services exceeds watchdog service limit")
    services = tuple(dict.fromkeys(_safe_name(item, "") for item in raw))
    if any(not item for item in services):
        raise ValueError("expected_services contains an unsafe service name")
    return services


def load_required_local_images(spec_path: Path) -> tuple[str, ...]:
    try:
        data = _read_json_bounded(spec_path)
    except (OSError, TypeError, ValueError):
        return ()
    if not isinstance(data, dict):
        return ()
    raw = data.get("required_local_images") or []
    if not isinstance(raw, list):
        raise ValueError("required_local_images must be a JSON list")
    if len(raw) > MAX_REQUIRED_LOCAL_IMAGES:
        raise ValueError("required_local_images exceeds watchdog image limit")
    images = tuple(dict.fromkeys(str(item).strip() for item in raw))
    if any(not _SAFE_IMAGE_RE.fullmatch(item) for item in images):
        raise ValueError("required_local_images contains an unsafe image name")
    return images


def missing_required_local_images(
    required_images: tuple[str, ...],
) -> tuple[str, ...]:
    image_ids = required_local_image_ids(required_images)
    return tuple(
        image for image in required_images if image not in image_ids
    )


def required_local_image_ids(
    required_images: tuple[str, ...],
) -> dict[str, str]:
    image_ids: dict[str, str] = {}
    for image in required_images:
        result = _run_bounded(
            ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
            timeout=20,
            output_limit=256,
        )
        image_id = result.stdout.strip().removeprefix("sha256:")
        if (
            result.returncode != 0
            or result.truncated
            or not _SAFE_HASH_RE.fullmatch(image_id)
        ):
            continue
        image_ids[image] = image_id
    return image_ids


def _rebuilds_required_image(
    command: str,
    required_images: tuple[str, ...],
) -> bool:
    """Detect mutation of immutable local tags without retaining raw argv."""
    if not required_images:
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # A malformed shell command cannot be classified safely.
        return any(
            cue in command
            for cue in ("docker", "compose", "build", "pull", "tag")
        )

    required = set(required_images)
    required_services = {
        image.rsplit("/", 1)[-1].split(":", 1)[0]
        for image in required_images
    }

    def option_values(
        values: list[str],
        names: set[str],
    ) -> set[str]:
        found: set[str] = set()
        for index, value in enumerate(values):
            if value in names and index + 1 < len(values):
                found.add(values[index + 1])
            for name in names:
                if value.startswith(f"{name}="):
                    found.add(value.removeprefix(f"{name}="))
                elif (
                    len(name) == 2
                    and value.startswith(name)
                    and value != name
                ):
                    found.add(value.removeprefix(name).removeprefix("="))
        return found

    def selected_services(values: list[str]) -> set[str]:
        options_with_values = {
            "-f", "--file", "-p", "--project-name", "--profile",
            "--env-file", "--project-directory", "--parallel", "--progress",
            "--build-arg", "--builder", "-m", "--memory", "--provenance",
            "--sbom", "--ssh", "--policy", "--scale", "--timeout",
            "--wait-timeout", "--pull",
        }
        selected: set[str] = set()
        skip_next = False
        for value in values:
            if skip_next:
                skip_next = False
                continue
            if value in options_with_values:
                skip_next = True
                continue
            if value.startswith("-"):
                continue
            if _SAFE_NAME_RE.fullmatch(value):
                selected.add(value)
        return selected

    def inspect_compose(values: list[str]) -> bool:
        global_options_with_values = {
            "-f", "--file", "-p", "--project-name", "--profile",
            "--env-file", "--project-directory", "--parallel", "--progress",
        }
        action_index = None
        skip_next = False
        for index, value in enumerate(values):
            if skip_next:
                skip_next = False
                continue
            if value in global_options_with_values:
                skip_next = True
                continue
            if value in {"build", "pull", "up"}:
                action_index = index
                break
        if action_index is None:
            return False
        action = values[action_index]
        tail = values[action_index + 1 :]
        selected = selected_services(tail)
        selects_required = bool(selected & required_services)
        if action == "build":
            return (
                not selected
                or selects_required
                or "--with-dependencies" in tail
            )
        if action == "pull":
            return not selected or selects_required
        build_values = option_values(tail, {"--build"})
        build_requested = "--build" in tail or bool(
            build_values - {"0", "false", "never"}
        )
        pull_values = option_values(tail, {"--pull"})
        pull_required = bool(pull_values & {"always"})
        return build_requested or pull_required

    def inspect_docker(values: list[str]) -> bool:
        global_options_with_values = {
            "--config", "-c", "--context", "-H", "--host", "-l",
            "--log-level",
        }
        while values:
            if values[0] in global_options_with_values:
                values = values[2:]
            elif values[0].startswith("-"):
                values = values[1:]
            else:
                break
        if not values:
            return False
        if values[0] == "compose":
            return inspect_compose(values[1:])
        if values[0] == "buildx" and len(values) > 1:
            if values[1] == "bake":
                return True
            if values[1] == "build":
                values = values[1:]
        elif values[0] == "image" and len(values) > 1:
            values = values[1:]
        action = values[0]
        tail = values[1:]
        if action == "build":
            tags = option_values(tail, {"-t", "--tag"})
            return bool(tags & required) or any(
                "$" in tag or "`" in tag
                for tag in tags
            )
        if action in {"tag", "commit", "import"}:
            target = tail[-1] if tail else ""
            return (
                target in required
                or "$" in target
                or "`" in target
            )
        if action in {"pull", "rmi", "rm"}:
            return bool(set(tail) & required) or any(
                "$" in value or "`" in value
                for value in tail
            )
        # Archive tags cannot be known before loading. Fail closed only for
        # this inherently ambiguous mutating operation.
        return action == "load"

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in {";", "&&", "||", "|", "&"}:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)

    for segment in segments:
        if segment and segment[0] == "sudo":
            segment = segment[1:]
            sudo_options_with_values = {
                "-u", "--user", "-g", "--group", "-h", "--host",
                "-p", "--prompt", "-C", "--close-from", "-T",
                "--command-timeout", "-D", "--chdir", "-R", "--chroot",
            }
            while segment and segment[0].startswith("-"):
                option = segment[0]
                segment = segment[1:]
                if (
                    option in sudo_options_with_values
                    and segment
                ):
                    segment = segment[1:]
        if segment and segment[0] == "env":
            segment = segment[1:]
            env_options_with_values = {
                "-u", "--unset", "-C", "--chdir", "-S", "--split-string",
            }
            while segment:
                option = segment[0]
                if option in env_options_with_values:
                    segment = segment[2:]
                elif option.startswith("-") or "=" in option:
                    segment = segment[1:]
                else:
                    break
        while (
            segment
            and "=" in segment[0]
            and not segment[0].startswith("-")
        ):
            segment = segment[1:]
        while segment:
            wrapper = Path(segment[0]).name
            if wrapper in {"command", "exec", "nohup"}:
                segment = segment[1:]
                while segment and segment[0].startswith("-"):
                    segment = segment[1:]
                continue
            if wrapper == "time":
                segment = segment[1:]
                while segment and segment[0].startswith("-"):
                    option = segment[0]
                    segment = segment[1:]
                    if option in {"-f", "--format", "-o", "--output"}:
                        segment = segment[1:]
                continue
            if wrapper == "nice":
                segment = segment[1:]
                if segment and segment[0] in {"-n", "--adjustment"}:
                    segment = segment[2:]
                continue
            if wrapper == "timeout":
                segment = segment[1:]
                while segment and segment[0].startswith("-"):
                    option = segment[0]
                    segment = segment[1:]
                    if option in {"-k", "--kill-after", "-s", "--signal"}:
                        segment = segment[1:]
                if segment:
                    segment = segment[1:]
                continue
            break
        if not segment:
            continue
        executable = Path(segment[0]).name
        if executable in {"sh", "bash", "dash", "zsh"}:
            try:
                command_index = segment.index("-c")
                wrapped = segment[command_index + 1]
            except (ValueError, IndexError):
                continue
            if _rebuilds_required_image(wrapped, required_images):
                return True
            if any(
                marker in wrapped
                for marker in ("$(", "${", "`", "$DOCKER", "$COMPOSE")
            ) and any(
                cue in wrapped
                for cue in ("build", "compose", "docker", "pull", "tag")
            ):
                return True
            continue
        if executable == "docker":
            if inspect_docker(segment[1:]):
                return True
            continue
        if executable == "docker-compose":
            if inspect_compose(segment[1:]):
                return True
            continue
        if executable == "xargs":
            for index, value in enumerate(segment[1:], start=1):
                wrapped_executable = Path(value).name
                if wrapped_executable == "docker":
                    if inspect_docker(segment[index + 1 :]):
                        return True
                    break
                if wrapped_executable == "docker-compose":
                    if inspect_compose(segment[index + 1 :]):
                        return True
                    break
            continue
        normalized_segment = " ".join(segment).lower()
        mentions_required = (
            any(
                image.lower() in normalized_segment
                for image in required_images
            )
            or any(
                service.lower() in normalized_segment
                for service in required_services
            )
        )
        wrapper_is_ambiguous = any(
            cue in normalized_segment
            for cue in ("build", "compose", "docker", "pull", "tag", "bake")
        ) and any(
            marker in normalized_segment
            for marker in ("$", "`")
        )
        if mentions_required or wrapper_is_ambiguous:
            if any(
                cue in normalized_segment
                for cue in (
                    "build", "compose", "docker", "pull", "tag", "bake",
                )
            ):
                return True
    return False


def validate_resolved_compose(
    compose_file: Path,
    repo_root: Path,
    required_images: tuple[str, ...] = (),
) -> None:
    _read_bytes_bounded(compose_file, MAX_COMPOSE_BYTES)
    validator = (
        repo_root
        / "skills/vss-build-vision-agent/scripts/validate_resolved_yml.py"
    )
    if not validator.is_file():
        raise RuntimeError("resolved Compose validator is unavailable")
    result = _run_bounded(
        [
            str(validator),
            str(compose_file),
            "--repo-root",
            str(repo_root),
            *[
                argument
                for image in required_images
                for argument in ("--required-local-image", image)
            ],
        ],
        timeout=90,
    )
    if result.returncode != 0 or result.truncated:
        raise RuntimeError("resolved Compose safety validation failed")


def _resolved_compose_candidates(command: str, repo_root: Path) -> list[Path]:
    """Extract only local compose file paths; never execute shell fragments."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []
    candidates: list[Path] = []
    for index, token in enumerate(tokens):
        value = None
        if token in {"-f", "--file"} and index + 1 < len(tokens):
            value = tokens[index + 1]
        elif token.startswith("--file="):
            value = token.partition("=")[2]
        if not value or "$" in value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = repo_root / path
        try:
            resolved = path.resolve()
            resolved.relative_to(repo_root.resolve())
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved not in candidates:
            candidates.append(resolved)
    return candidates


def _generated_resolved_compose(command: str, repo_root: Path) -> Path | None:
    """Resolve a local ``config > resolved.yml`` target without shell eval."""
    match = re.search(
        r">\s*(?P<path>[A-Za-z0-9_./-]*resolved\.ya?ml)(?:\s|$)",
        command,
    )
    if match is None:
        return None
    cwd = repo_root
    cd_match = re.search(
        r"(?:^|[;&|]\s*)cd\s+(?P<path>[A-Za-z0-9_./-]+)\s*&&",
        command,
    )
    if cd_match is not None:
        candidate_cwd = Path(cd_match.group("path"))
        cwd = candidate_cwd if candidate_cwd.is_absolute() else repo_root / candidate_cwd
    target = Path(match.group("path"))
    target = target if target.is_absolute() else cwd / target
    try:
        resolved = target.resolve()
        resolved.relative_to(repo_root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def _is_compose_up(command: str) -> bool:
    return bool(
        re.search(
            r"(?:^|[;&|]\s*)docker(?:\s+compose|-compose)\b"
            r"[^;&|]*\bup\b",
            command,
        )
    )


def _compose_phase(command: str) -> str | None:
    if re.search(
        r"\bdocker(?:(?:\s+compose|-compose)\b[^;&|]*\s+|\s+)pull\b",
        command,
    ):
        return "pull"
    if re.search(
        r"\bdocker(?:(?:\s+compose|-compose)\b[^;&|]*\s+|\s+)build\b",
        command,
    ):
        return "build"
    if _is_compose_up(command):
        return "up"
    if re.search(
        r"\bdocker(?:\s+compose|-compose)\b[^;&|]*\bconfig\b",
        command,
    ):
        return "config"
    return None


def validate_compose_services(
    compose_file: Path,
    expected_services: tuple[str, ...],
) -> tuple[tuple[str, ...], str]:
    compose_bytes = _read_bytes_bounded(compose_file, MAX_COMPOSE_BYTES)
    digest = hashlib.sha256(compose_bytes).hexdigest()
    proc = _run_bounded(
        ["docker", "compose", "-f", str(compose_file), "config", "--services"],
        timeout=60,
    )
    if proc.returncode != 0 or proc.truncated:
        raise RuntimeError("resolved Compose validation failed")
    actual = {line.strip() for line in proc.stdout.splitlines() if _SAFE_NAME_RE.fullmatch(line.strip())}
    missing = tuple(name for name in expected_services if name not in actual)
    return missing, digest


def _target_kind(tool_input: dict) -> str:
    path = str(tool_input.get("file_path") or tool_input.get("path") or "")
    suffix = Path(path).suffix.lower()
    if suffix in {".yml", ".yaml"}:
        return "compose"
    if suffix in {".env", ".json", ".toml"}:
        return "config"
    if suffix in {".py", ".sh", ".js", ".ts"}:
        return "source"
    return "other"


class DirectAgentProgress:
    """Tool hooks, Docker sampling, idle detection, and timeout diagnostics."""

    def __init__(
        self,
        *,
        results_root: Path,
        spec_path: Path,
        repo_root: Path,
        tracker: ProgressTracker | None = None,
        journal: ProgressJournal | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        poll_sec: float = DEFAULT_POLL_SEC,
        activity_heartbeat_sec: float = DEFAULT_ACTIVITY_HEARTBEAT_SEC,
    ):
        self.results_root = results_root
        self.repo_root = repo_root
        self.expected_services = load_expected_services(spec_path)
        self.required_local_images = load_required_local_images(spec_path)
        self.required_local_image_ids = required_local_image_ids(
            self.required_local_images
        )
        self.tracker = tracker or ProgressTracker(monotonic=monotonic)
        self.journal = journal or ProgressJournal(results_root / "progress-journal.jsonl", monotonic=monotonic)
        self._monotonic = monotonic
        self.poll_sec = poll_sec
        self.activity_heartbeat_sec = activity_heartbeat_sec
        self.active_phase: str | None = None
        self.active_tool_id: str | None = None
        self.last_activity_heartbeat = self.tracker.started
        self._phase_heartbeat_count = 0
        self.compose_file: Path | None = None
        self.compose_sha256: str | None = None
        self._container_snapshot: dict[str, tuple[str, str, int, int]] = {}
        self._image_ids: set[str] | None = None
        self._seen_image_ids: set[str] | None = None
        self._image_tracking_saturated = False
        self._pseudonym_salt = os.urandom(32)
        self._inner_journal_offset = 0
        self.inner_journal_path = Path("/logs/agent/direct-progress.jsonl")
        self.journal.record(
            "watchdog_started",
            hard_ceiling_sec=self.tracker.hard_ceiling_sec,
            cold_start_grace_sec=self.tracker.cold_start_grace_sec,
            idle_timeout_sec=self.tracker.idle_timeout_sec,
        )

    def _record_progress(
        self,
        category: str,
        *,
        token: object | None = None,
        **fields: object,
    ) -> None:
        self.journal.record(category, **fields)
        self.tracker.progress(category, token)

    async def pre_tool(self, input_data, tool_use_id, _context):
        tool = str(input_data.get("tool_name") or "unknown")
        tool_input = input_data.get("tool_input") or {}
        category = {
            "Bash": "shell",
            "Read": "read",
            "Edit": "edit",
            "Write": "write",
            "Glob": "search",
            "Grep": "search",
        }.get(tool, "other")
        self.journal.record("tool", tool_category=category)
        if tool in {"Edit", "Write"}:
            target_kind = _target_kind(tool_input)
            self._record_progress(
                "file_mutation",
                token=(tool.lower(), target_kind),
                mutation_kind=tool.lower(),
                target_kind=target_kind,
            )
        if tool != "Bash":
            return {}

        command = str(tool_input.get("command") or "")
        if _rebuilds_required_image(command, self.required_local_images):
            return self._deny(
                "deployment blocked: declared prebuilt image rebuild attempted"
            )
        phase = _compose_phase(command)
        if phase:
            if phase != self.active_phase:
                self._phase_heartbeat_count = 0
            self.active_phase = phase
            self.active_tool_id = str(tool_use_id)
            self._record_progress("compose_phase", token=phase, phase=phase)
        generated = _generated_resolved_compose(command, self.repo_root)
        if generated is not None:
            # Internal pointer only. The path is never journaled or printed.
            self.compose_file = generated

        if (
            self.expected_services or self.required_local_images
        ) and _is_compose_up(command):
            candidates = _resolved_compose_candidates(command, self.repo_root)
            if not candidates and self.compose_file is not None:
                candidates = [self.compose_file]
            if not candidates:
                return self._deny("deployment blocked: resolved Compose file was not observed")
            compose_file = candidates[-1]
            try:
                validate_resolved_compose(
                    compose_file,
                    self.repo_root,
                    self.required_local_images,
                )
                current_image_ids = required_local_image_ids(
                    self.required_local_images
                )
                if (
                    len(current_image_ids) != len(self.required_local_images)
                    or current_image_ids != self.required_local_image_ids
                ):
                    return self._deny(
                        "deployment blocked: required local image missing or changed"
                    )
                missing, digest = validate_compose_services(compose_file, self.expected_services)
            except (
                OSError,
                RuntimeError,
                ValueError,
                subprocess.SubprocessError,
            ):
                return self._deny("deployment blocked: resolved Compose validation failed")
            self.compose_file = compose_file
            self.compose_sha256 = digest
            self.journal.record(
                "expected_services",
                service_count=len(self.expected_services),
                compose_sha256=digest,
            )
            if missing:
                return self._deny(f"deployment blocked: {len(missing)} expected service(s) missing")
            self._record_progress(
                "compose_validated",
                token=digest,
                service_count=len(self.expected_services),
                compose_sha256=digest,
            )
        return {}

    async def post_tool(self, _input_data, tool_use_id, _context):
        if str(tool_use_id) == self.active_tool_id:
            self.active_phase = None
            self.active_tool_id = None
        return {}

    @staticmethod
    def _deny(reason: str) -> dict:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    def _sample_images(self) -> None:
        proc = _run_bounded(
            ["docker", "images", "--no-trunc", "--format", "{{.ID}}"],
            timeout=15,
        )
        if proc.returncode != 0 or proc.truncated:
            return
        image_ids = {
            item.strip().removeprefix("sha256:")
            for item in proc.stdout.splitlines()
            if _SAFE_HASH_RE.fullmatch(item.strip().removeprefix("sha256:"))
        }
        if self._seen_image_ids is None:
            baseline = sorted(image_ids)
            self._seen_image_ids = set(baseline[:MAX_SEEN_IMAGE_IDS])
            self._image_tracking_saturated = (
                len(baseline) > MAX_SEEN_IMAGE_IDS
            )
        new_image_ids = (
            image_ids - self._seen_image_ids
            if not self._image_tracking_saturated
            else set()
        )
        remaining = MAX_SEEN_IMAGE_IDS - len(self._seen_image_ids)
        if len(new_image_ids) > remaining:
            new_image_ids = set(sorted(new_image_ids)[:remaining])
            self._image_tracking_saturated = True
        if new_image_ids:
            self._record_progress(
                "image_activity",
                token=hashlib.sha256(
                    "\n".join(sorted(new_image_ids)).encode()
                ).hexdigest(),
                image_count=len(image_ids),
                delta=len(new_image_ids),
            )
            self._seen_image_ids.update(new_image_ids)
        self._image_ids = image_ids

    def _compose_ps(self) -> list[dict]:
        if self.compose_file is None:
            return []
        proc = _run_bounded(
            [
                "docker",
                "compose",
                "-f",
                str(self.compose_file),
                "ps",
                "-a",
                "--format",
                "json",
            ],
            timeout=20,
        )
        if proc.returncode != 0 or proc.truncated:
            return []
        text = proc.stdout.strip()
        if not text:
            return []
        try:
            decoded = json.loads(text)
            rows = decoded if isinstance(decoded, list) else [decoded]
        except ValueError:
            rows = []
            for line in text.splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    rows.append(item)
        return [row for row in rows if isinstance(row, dict)]

    def _safe_service_rows(self) -> list[dict]:
        allowed = set(self.expected_services)
        rows: list[dict] = []
        for row in self._compose_ps():
            service = _safe_name(row.get("Service") or row.get("Name"), "")
            if not service or (allowed and service not in allowed):
                continue
            state = _safe_state(row.get("State"))
            health = _safe_health(row.get("Health") or "none")
            try:
                exit_code = int(row.get("ExitCode") or 0)
                restart_count = int(row.get("RestartCount") or 0)
            except (TypeError, ValueError):
                exit_code, restart_count = 0, 0
            rows.append(
                {
                    "service": service,
                    "state": state,
                    "health": health,
                    "exit_code": exit_code,
                    "restart_count": restart_count,
                }
            )
            if len(rows) >= MAX_DIAGNOSTIC_SERVICES:
                break
        return rows

    def sample(self) -> None:
        self._sample_inner_journal()
        with suppress(OSError, subprocess.SubprocessError):
            self._sample_images()
        try:
            rows = self._safe_service_rows()
        except (OSError, subprocess.SubprocessError):
            rows = []
        current = {
            row["service"]: (
                row["state"],
                row["health"],
                row["exit_code"],
                row["restart_count"],
            )
            for row in rows
        }
        for service_index, (service, value) in enumerate(current.items()):
            previous = self._container_snapshot.get(service)
            if previous != value:
                self._record_progress(
                    "container_transition",
                    # Restart counters can grow forever for a crash loop. Keep
                    # them as bounded diagnostics, but coalesce progress on the
                    # finite lifecycle/health state instead.
                    token=(service, value[:3]),
                    service_index=service_index,
                    previous_state=previous[0] if previous else "unknown",
                    state=value[0],
                    health=value[1],
                    exit_code=value[2],
                    restart_count=value[3],
                )
        self._container_snapshot = current

        now = self._monotonic()
        if (
            self.active_phase in {"pull", "build"}
            and self._phase_heartbeat_count < MAX_PHASE_HEARTBEATS
            and now - self.last_activity_heartbeat >= self.activity_heartbeat_sec
        ):
            self.last_activity_heartbeat = now
            self._phase_heartbeat_count += 1
            self._record_progress(
                "image_activity_heartbeat",
                token=(self.active_phase, self._phase_heartbeat_count),
                phase=self.active_phase,
            )

    def _sample_inner_journal(self) -> None:
        """Merge only closed-schema inner-agent events into the artifact."""
        try:
            state = _read_json_bounded(_INNER_STATE)
            if not isinstance(state, dict):
                raise ValueError("inner state must be an object")
            candidate = Path(state.get("compose_file", "")).resolve()
            candidate.relative_to(self.repo_root.resolve())
            if candidate.is_file():
                self.compose_file = candidate
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        try:
            with self.inner_journal_path.open("rb") as handle:
                start = self._inner_journal_offset
                handle.seek(start)
                chunk = handle.read(MAX_INNER_READ_BYTES)
        except OSError:
            return
        if chunk and not chunk.endswith(b"\n"):
            last_newline = chunk.rfind(b"\n")
            if last_newline < 0:
                # An overlong or concurrently replaced line is not a valid
                # closed-schema event. Discard this bounded chunk.
                self._inner_journal_offset = start + len(chunk)
                return
            chunk = chunk[: last_newline + 1]
        self._inner_journal_offset = start + len(chunk)
        lines = chunk.splitlines()[:MAX_JOURNAL_EVENTS]
        for line in lines[:MAX_JOURNAL_EVENTS]:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            category_value = event.get("category")
            if not isinstance(category_value, str):
                continue
            category = category_value
            allowed = _EVENT_FIELDS.get(category)
            if allowed is None or category in {"watchdog_started", "timeout"}:
                continue
            fields = {name: event[name] for name in allowed if name in event}
            try:
                self.journal.record(category, **fields)
            except (TypeError, ValueError):
                continue
            if category == "compose_phase":
                phase = fields.get("phase")
                self.active_phase = None if phase == "none" else str(phase)
            elif category in {"expected_services", "compose_validated"}:
                self.compose_sha256 = str(fields.get("compose_sha256") or "") or None
            token = tuple(sorted((name, json.dumps(value, sort_keys=True)) for name, value in fields.items()))
            self.tracker.progress(str(category), token)

    def archive_timeout(self, expiration: tuple[str, str, float]) -> None:
        reason, last_category, idle_sec = expiration
        with suppress(OSError, ValueError):
            self.journal.record(
                "timeout",
                reason=reason,
                last_progress_category=last_category,
                last_progress_elapsed_sec=round(idle_sec, 3),
            )
        try:
            rows = self._safe_service_rows()
        except (OSError, subprocess.SubprocessError):
            rows = []
        nonhealthy_rows = [
            row
            for row in rows
            if not (
                (
                    row["state"] == "running"
                    and row["health"] in {"healthy", "none"}
                )
                or (
                    row["state"] == "exited"
                    and row["exit_code"] == 0
                )
            )
        ]
        safe_rows = [
            {
                "service_id": "svc-"
                + hmac.new(
                    self._pseudonym_salt,
                    row["service"].encode(),
                    hashlib.sha256,
                ).hexdigest()[:12],
                "state": row["state"],
                "health": row["health"],
                "exit_code": row["exit_code"],
                "restart_count": row["restart_count"],
            }
            for row in nonhealthy_rows[:MAX_DIAGNOSTIC_SERVICES]
        ]
        summaries = {
            "schema": 1,
            "reason": reason,
            "last_progress_category": last_category,
            "last_progress_elapsed_sec": round(idle_sec, 3),
            "compose_sha256": self.compose_sha256,
            "services": safe_rows,
            "phase_summary": {
                "active": self.active_phase or "none",
                "image_count": len(self._image_ids or ()),
            },
        }
        with suppress(OSError):
            self.results_root.mkdir(parents=True, exist_ok=True)
            (self.results_root / "timeout-diagnostics.json").write_text(
                json.dumps(summaries, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

    def cleanup_after_timeout(self) -> bool:
        """Reconcile only the observed Compose project after agent reap."""
        if self.compose_file is None:
            with suppress(OSError, ValueError):
                self.journal.record("cleanup", outcome="success")
            return True
        success = False
        try:
            result = _run_bounded(
                [
                    "docker",
                    "compose",
                    "-f",
                    str(self.compose_file),
                    "down",
                    "--remove-orphans",
                    "--volumes",
                    "--timeout",
                    "30",
                ],
                timeout=60,
            )
            success = result.returncode == 0
            if not success:
                ids_result = _run_bounded(
                    [
                        "docker",
                        "compose",
                        "-f",
                        str(self.compose_file),
                        "ps",
                        "-aq",
                    ],
                    timeout=20,
                )
                container_ids = [
                    value for value in ids_result.stdout.splitlines() if re.fullmatch(r"[a-f0-9]{12,64}", value)
                ][:MAX_CLEANUP_CONTAINERS]
                if container_ids:
                    remove_result = _run_bounded(
                        ["docker", "rm", "-f", "-v", *container_ids],
                        timeout=60,
                    )
                    success = remove_result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            success = False
        with suppress(OSError, ValueError):
            self.journal.record("cleanup", outcome="success" if success else "failure")
        return success

    async def wait_for_expiry(
        self,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> tuple[str, str, float]:
        while True:
            await sleep(self.poll_sec)
            self.sample()
            expiration = self.tracker.expiration()
            if expiration is not None:
                self.archive_timeout(expiration)
                return expiration


async def run_with_progress_watchdog(
    agent: Awaitable[int],
    progress: DirectAgentProgress,
) -> int:
    """Cancel and reap the agent coroutine when the progress watchdog fires."""
    agent_task: asyncio.Future[int] = asyncio.ensure_future(agent)
    expiration_result: tuple[str, str, float] | None = None
    agent_completed = False
    cleanup_attempted = False

    async def monitor() -> int:
        nonlocal expiration_result
        expiration_result = await progress.wait_for_expiry()
        return 0

    watchdog_task = asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait({agent_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED)
        if agent_task in done:
            watchdog_task.cancel()
            await asyncio.gather(watchdog_task, return_exceptions=True)
            result = agent_task.result()
            agent_completed = True
            return result
        watchdog_task.result()
        agent_task.cancel()
        await asyncio.gather(agent_task, return_exceptions=True)
        cleanup_attempted = True
        progress.cleanup_after_timeout()
        assert expiration_result is not None
        expiration = expiration_result
        reason, category, idle_sec = expiration
        raise DirectAgentWatchdogExpired(f"{reason}; last progress={category} {int(idle_sec)}s ago")
    finally:
        for task in (agent_task, watchdog_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(agent_task, watchdog_task, return_exceptions=True)
        if not agent_completed and not cleanup_attempted:
            progress.cleanup_after_timeout()


_INNER_JOURNAL = Path("/logs/agent/direct-progress.jsonl")
_INNER_STATE = Path("/logs/agent/direct-progress-state.json")
_INNER_CONFIG = Path("/logs/agent/direct-progress-config.json")


def _read_inner_state() -> dict:
    try:
        state = _read_json_bounded(_INNER_STATE)
    except (OSError, TypeError, ValueError):
        state = {}
    return state if isinstance(state, dict) else {}


def _write_inner_state(state: dict) -> None:
    _INNER_STATE.parent.mkdir(parents=True, exist_ok=True)
    _INNER_STATE.write_text(
        json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _inner_expected_services() -> tuple[str, ...]:
    try:
        config = _read_json_bounded(_INNER_CONFIG)
    except (OSError, TypeError, ValueError):
        return ()
    raw = config.get("expected_services") if isinstance(config, dict) else []
    if not isinstance(raw, list) or len(raw) > MAX_EXPECTED_SERVICES:
        return ()
    safe = tuple(_safe_name(item, "") for item in raw)
    return safe if all(safe) else ()


def _inner_required_local_images() -> tuple[str, ...]:
    try:
        config = _read_json_bounded(_INNER_CONFIG)
    except (OSError, TypeError, ValueError):
        return ()
    raw = config.get("required_local_images") if isinstance(config, dict) else []
    if not isinstance(raw, list) or len(raw) > MAX_REQUIRED_LOCAL_IMAGES:
        return ()
    images = tuple(str(item).strip() for item in raw)
    return images if all(_SAFE_IMAGE_RE.fullmatch(item) for item in images) else ()


def _inner_required_local_image_ids() -> dict[str, str]:
    try:
        config = _read_json_bounded(_INNER_CONFIG)
    except (OSError, TypeError, ValueError):
        return {}
    raw = (
        config.get("required_local_image_ids")
        if isinstance(config, dict)
        else {}
    )
    if not isinstance(raw, dict) or len(raw) > MAX_REQUIRED_LOCAL_IMAGES:
        return {}
    image_ids = {
        str(image): str(image_id)
        for image, image_id in raw.items()
        if (
            _SAFE_IMAGE_RE.fullmatch(str(image))
            and _SAFE_HASH_RE.fullmatch(str(image_id))
        )
    }
    return image_ids if len(image_ids) == len(raw) else {}


def run_inner_hook(event_name: str, payload: dict) -> int:
    """Claude Code hook entry point; never persists raw hook input."""
    tool = str(payload.get("tool_name") or "unknown")
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    journal = ProgressJournal(_INNER_JOURNAL)
    state = _read_inner_state()
    if event_name == "post":
        if state.get("active_tool_id") == str(payload.get("tool_use_id") or ""):
            state["active_phase"] = None
            state["active_tool_id"] = None
            _write_inner_state(state)
            journal.record("compose_phase", phase="none")
        return 0

    category = {
        "Bash": "shell",
        "Read": "read",
        "Edit": "edit",
        "Write": "write",
        "Glob": "search",
        "Grep": "search",
    }.get(tool, "other")
    journal.record("tool", tool_category=category)
    if tool in {"Edit", "Write"}:
        journal.record(
            "file_mutation",
            mutation_kind=tool.lower(),
            target_kind=_target_kind(tool_input),
        )
    if tool != "Bash":
        return 0

    command = str(tool_input.get("command") or "")
    required_images = _inner_required_local_images()
    if _rebuilds_required_image(command, required_images):
        print(
            "deployment blocked: declared prebuilt image rebuild attempted",
            file=sys.stderr,
        )
        return 2
    phase = _compose_phase(command)
    if phase:
        state["active_phase"] = phase
        state["active_tool_id"] = str(payload.get("tool_use_id") or "")
        journal.record("compose_phase", phase=phase)
    generated = _generated_resolved_compose(command, Path.home() / "video-search-and-summarization")
    if generated is not None:
        state["compose_file"] = str(generated)
    _write_inner_state(state)

    expected = _inner_expected_services()
    if (
        not expected and not required_images
    ) or not _is_compose_up(command):
        return 0
    candidates = _resolved_compose_candidates(command, Path.home() / "video-search-and-summarization")
    if not candidates and state.get("compose_file"):
        candidates = [Path(state["compose_file"])]
    if not candidates:
        print(
            "deployment blocked: resolved Compose file was not observed",
            file=sys.stderr,
        )
        return 2
    try:
        repo_root = Path.home() / "video-search-and-summarization"
        validate_resolved_compose(
            candidates[-1],
            repo_root,
            required_images,
        )
        current_image_ids = required_local_image_ids(required_images)
        if (
            len(current_image_ids) != len(required_images)
            or current_image_ids != _inner_required_local_image_ids()
        ):
            print(
                "deployment blocked: required local image missing or changed",
                file=sys.stderr,
            )
            return 2
        missing, digest = validate_compose_services(candidates[-1], expected)
    except (
        OSError,
        RuntimeError,
        ValueError,
        subprocess.SubprocessError,
    ):
        print(
            "deployment blocked: resolved Compose validation failed",
            file=sys.stderr,
        )
        return 2
    journal.record(
        "expected_services",
        service_count=len(expected),
        compose_sha256=digest,
    )
    if missing:
        print(
            f"deployment blocked: {len(missing)} expected service(s) missing",
            file=sys.stderr,
        )
        return 2
    journal.record(
        "compose_validated",
        service_count=len(expected),
        compose_sha256=digest,
    )
    return 0


def _hook_main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] != "hook" or argv[1] not in {"pre", "post"}:
        return 64
    try:
        payload = json.loads(sys.stdin.read(1024 * 1024))
    except ValueError:
        return 1
    if not isinstance(payload, dict):
        return 1
    try:
        return run_inner_hook(argv[1], payload)
    except Exception:
        print("direct progress hook failed safely", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_hook_main(sys.argv[1:]))
