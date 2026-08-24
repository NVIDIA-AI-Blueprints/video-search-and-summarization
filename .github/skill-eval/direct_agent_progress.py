#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Privacy-safe progress monitoring for direct OpenShell agent runs."""
from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import Awaitable, Callable


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

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_HASH_RE = re.compile(r"^[a-f0-9]{64}$")
_STATES = frozenset({
    "created", "running", "paused", "restarting", "removing",
    "exited", "dead", "unknown",
})
_HEALTH = frozenset({"healthy", "unhealthy", "starting", "none", "unknown"})
_PROGRESS_CATEGORIES = frozenset({
    "file_mutation",
    "compose_validated",
    "compose_phase",
    "container_transition",
    "image_activity",
    "image_activity_heartbeat",
})
_EVENT_FIELDS = {
    "watchdog_started": frozenset({
        "hard_ceiling_sec", "cold_start_grace_sec", "idle_timeout_sec",
    }),
    "tool": frozenset({"tool_category"}),
    "file_mutation": frozenset({"mutation_kind", "target_kind"}),
    "expected_services": frozenset({"services", "compose_sha256"}),
    "compose_validated": frozenset({"service_count", "compose_sha256"}),
    "compose_phase": frozenset({"phase"}),
    "container_transition": frozenset({
        "service", "previous_state", "state", "health", "exit_code",
        "restart_count",
    }),
    "image_activity": frozenset({"image_count", "delta"}),
    "image_activity_heartbeat": frozenset({"phase"}),
    "timeout": frozenset({
        "reason", "last_progress_category", "last_progress_elapsed_sec",
    }),
    "finished": frozenset({"outcome"}),
}
_ENUM_FIELDS = {
    "tool_category": frozenset({
        "shell", "read", "edit", "write", "search", "other",
    }),
    "mutation_kind": frozenset({"edit", "write"}),
    "target_kind": frozenset({"compose", "config", "source", "other"}),
    "phase": frozenset({"config", "pull", "build", "up", "none"}),
    "reason": frozenset({"idle", "hard-ceiling"}),
    "outcome": frozenset({"success", "failure", "cancelled"}),
    "last_progress_category": _PROGRESS_CATEGORIES | frozenset({"startup"}),
}


class DirectAgentWatchdogExpired(RuntimeError):
    """The direct-run hard or idle watchdog fired."""


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
        self._wall_clock = wall_clock or (
            lambda: datetime.datetime.now(datetime.timezone.utc)
        )
        self._started = monotonic()
        self._max_events = max_events
        try:
            self._events = min(
                max_events,
                len(path.read_text(encoding="utf-8").splitlines()),
            )
        except OSError:
            self._events = 0

    def record(self, category: str, **fields: object) -> bool:
        allowed = _EVENT_FIELDS.get(category)
        if allowed is None:
            raise ValueError(f"unsupported progress category: {category}")
        unexpected = set(fields) - allowed
        if unexpected:
            raise ValueError(
                f"unsupported fields for {category}: {sorted(unexpected)}"
            )
        for name, allowed_values in _ENUM_FIELDS.items():
            if name in fields and fields[name] not in allowed_values:
                raise ValueError(f"unsafe {name} value")
        for name in ("service", "previous_state", "state", "health"):
            if name in fields and name == "service":
                if not _SAFE_NAME_RE.fullmatch(str(fields[name])):
                    raise ValueError("unsafe service value")
            elif name in fields and name in {"previous_state", "state"}:
                if fields[name] not in _STATES:
                    raise ValueError(f"unsafe {name} value")
            elif name in fields and fields[name] not in _HEALTH:
                raise ValueError("unsafe health value")
        if "services" in fields:
            services = fields["services"]
            if (
                not isinstance(services, list)
                or len(services) > MAX_DIAGNOSTIC_SERVICES
                or any(not _SAFE_NAME_RE.fullmatch(str(item)) for item in services)
            ):
                raise ValueError("unsafe services value")
        if "compose_sha256" in fields and not _SAFE_HASH_RE.fullmatch(
            str(fields["compose_sha256"])
        ):
            raise ValueError("unsafe compose_sha256 value")
        if (
            self._events >= self._max_events
            and category not in {"timeout", "finished"}
        ):
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

    def progress(self, category: str) -> None:
        if category not in _PROGRESS_CATEGORIES:
            return
        self.last_progress = self._monotonic()
        self.last_progress_category = category

    def expiration(self) -> tuple[str, str, float] | None:
        now = self._monotonic()
        elapsed = now - self.started
        idle = now - self.last_progress
        if elapsed >= self.hard_ceiling_sec:
            return "hard-ceiling", self.last_progress_category, idle
        if (
            elapsed >= self.cold_start_grace_sec
            and idle >= self.idle_timeout_sec
        ):
            return "idle", self.last_progress_category, idle
        return None


def load_expected_services(spec_path: Path) -> tuple[str, ...]:
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    raw = data.get("expected_services") or []
    if not isinstance(raw, list):
        raise ValueError("expected_services must be a JSON list")
    services = tuple(dict.fromkeys(_safe_name(item, "") for item in raw))
    if any(not item for item in services):
        raise ValueError("expected_services contains an unsafe service name")
    return services


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
    return bool(re.search(r"(?:^|[;&|]\s*)docker\s+compose\b[^;&|]*\bup\b", command))


def _compose_phase(command: str) -> str | None:
    if re.search(r"\bdocker\s+(?:compose\b[^;&|]*\s+)?pull\b", command):
        return "pull"
    if re.search(r"\bdocker\s+(?:compose\b[^;&|]*\s+)?build\b", command):
        return "build"
    if _is_compose_up(command):
        return "up"
    if re.search(r"\bdocker\s+compose\b[^;&|]*\bconfig\b", command):
        return "config"
    return None


def validate_compose_services(
    compose_file: Path,
    expected_services: tuple[str, ...],
) -> tuple[tuple[str, ...], str]:
    digest = hashlib.sha256(compose_file.read_bytes()).hexdigest()
    proc = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "config", "--services"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("resolved Compose validation failed")
    actual = {
        line.strip() for line in proc.stdout.splitlines()
        if _SAFE_NAME_RE.fullmatch(line.strip())
    }
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
        self.tracker = tracker or ProgressTracker(monotonic=monotonic)
        self.journal = journal or ProgressJournal(
            results_root / "progress-journal.jsonl", monotonic=monotonic
        )
        self._monotonic = monotonic
        self.poll_sec = poll_sec
        self.activity_heartbeat_sec = activity_heartbeat_sec
        self.active_phase: str | None = None
        self.active_tool_id: str | None = None
        self.last_activity_heartbeat = self.tracker.started
        self.compose_file: Path | None = None
        self.compose_sha256: str | None = None
        self._container_snapshot: dict[str, tuple[str, str, int, int]] = {}
        self._image_ids: set[str] | None = None
        self._inner_journal_offset = 0
        self.inner_journal_path = Path("/logs/agent/direct-progress.jsonl")
        self.journal.record(
            "watchdog_started",
            hard_ceiling_sec=self.tracker.hard_ceiling_sec,
            cold_start_grace_sec=self.tracker.cold_start_grace_sec,
            idle_timeout_sec=self.tracker.idle_timeout_sec,
        )

    def _record_progress(self, category: str, **fields: object) -> None:
        self.journal.record(category, **fields)
        self.tracker.progress(category)

    async def pre_tool(self, input_data, tool_use_id, context):  # noqa: ANN001
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
            self._record_progress(
                "file_mutation",
                mutation_kind=tool.lower(),
                target_kind=_target_kind(tool_input),
            )
        if tool != "Bash":
            return {}

        command = str(tool_input.get("command") or "")
        phase = _compose_phase(command)
        if phase:
            self.active_phase = phase
            self.active_tool_id = str(tool_use_id)
            self._record_progress("compose_phase", phase=phase)
        generated = _generated_resolved_compose(command, self.repo_root)
        if generated is not None:
            # Internal pointer only. The path is never journaled or printed.
            self.compose_file = generated

        if self.expected_services and _is_compose_up(command):
            candidates = _resolved_compose_candidates(command, self.repo_root)
            if not candidates and self.compose_file is not None:
                candidates = [self.compose_file]
            if not candidates:
                return self._deny(
                    "deployment blocked: resolved Compose file was not observed"
                )
            compose_file = candidates[-1]
            try:
                missing, digest = validate_compose_services(
                    compose_file, self.expected_services
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                return self._deny("deployment blocked: resolved Compose validation failed")
            self.compose_file = compose_file
            self.compose_sha256 = digest
            self.journal.record(
                "expected_services",
                services=list(self.expected_services),
                compose_sha256=digest,
            )
            if missing:
                return self._deny(
                    "deployment blocked: missing expected services: "
                    + ", ".join(missing)
                )
            self._record_progress(
                "compose_validated",
                service_count=len(self.expected_services),
                compose_sha256=digest,
            )
        return {}

    async def post_tool(self, input_data, tool_use_id, context):  # noqa: ANN001
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
        proc = subprocess.run(
            ["docker", "images", "--no-trunc", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            return
        image_ids = {
            item.strip().removeprefix("sha256:")
            for item in proc.stdout.splitlines()
            if _SAFE_HASH_RE.fullmatch(item.strip().removeprefix("sha256:"))
        }
        if self._image_ids is not None and image_ids != self._image_ids:
            self._record_progress(
                "image_activity",
                image_count=len(image_ids),
                delta=len(image_ids) - len(self._image_ids),
            )
        self._image_ids = image_ids

    def _compose_ps(self) -> list[dict]:
        if self.compose_file is None:
            return []
        proc = subprocess.run(
            [
                "docker", "compose", "-f", str(self.compose_file),
                "ps", "-a", "--format", "json",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
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
            rows.append({
                "service": service,
                "state": state,
                "health": health,
                "exit_code": exit_code,
                "restart_count": restart_count,
            })
            if len(rows) >= MAX_DIAGNOSTIC_SERVICES:
                break
        return rows

    def sample(self) -> None:
        self._sample_inner_journal()
        try:
            self._sample_images()
        except (OSError, subprocess.SubprocessError):
            pass
        try:
            rows = self._safe_service_rows()
        except (OSError, subprocess.SubprocessError):
            rows = []
        current = {
            row["service"]: (
                row["state"], row["health"],
                row["exit_code"], row["restart_count"],
            )
            for row in rows
        }
        for service, value in current.items():
            previous = self._container_snapshot.get(service)
            if previous != value:
                self._record_progress(
                    "container_transition",
                    service=service,
                    previous_state=previous[0] if previous else "unknown",
                    state=value[0],
                    health=value[1],
                    exit_code=value[2],
                    restart_count=value[3],
                )
        self._container_snapshot = current

        now = self._monotonic()
        if (
            self.active_phase in {"pull", "build", "up"}
            and now - self.last_activity_heartbeat >= self.activity_heartbeat_sec
        ):
            self.last_activity_heartbeat = now
            self._record_progress(
                "image_activity_heartbeat", phase=self.active_phase
            )

    def _sample_inner_journal(self) -> None:
        """Merge only closed-schema inner-agent events into the artifact."""
        try:
            state = json.loads(_INNER_STATE.read_text(encoding="utf-8"))
            candidate = Path(state.get("compose_file", "")).resolve()
            candidate.relative_to(self.repo_root.resolve())
            if candidate.is_file():
                self.compose_file = candidate
        except (AttributeError, OSError, TypeError, ValueError):
            pass
        try:
            with self.inner_journal_path.open(encoding="utf-8") as handle:
                handle.seek(self._inner_journal_offset)
                lines = handle.readlines(MAX_JOURNAL_EVENTS * 2048)
                self._inner_journal_offset = handle.tell()
        except OSError:
            return
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
            self.tracker.progress(str(category))

    def archive_timeout(self, expiration: tuple[str, str, float]) -> None:
        reason, last_category, idle_sec = expiration
        self.journal.record(
            "timeout",
            reason=reason,
            last_progress_category=last_category,
            last_progress_elapsed_sec=round(idle_sec, 3),
        )
        rows = self._safe_service_rows()
        summaries = {
            "schema": 1,
            "reason": reason,
            "last_progress_category": last_category,
            "last_progress_elapsed_sec": round(idle_sec, 3),
            "compose_sha256": self.compose_sha256,
            "services": rows[:MAX_DIAGNOSTIC_SERVICES],
            "phase_summary": {
                "active": self.active_phase or "none",
                "image_count": len(self._image_ids or ()),
            },
        }
        self.results_root.mkdir(parents=True, exist_ok=True)
        (self.results_root / "timeout-diagnostics.json").write_text(
            json.dumps(summaries, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

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

    async def monitor() -> int:
        nonlocal expiration_result
        expiration_result = await progress.wait_for_expiry()
        return 0

    watchdog_task = asyncio.create_task(monitor())
    try:
        done, _ = await asyncio.wait(
            {agent_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if agent_task in done:
            watchdog_task.cancel()
            await asyncio.gather(watchdog_task, return_exceptions=True)
            return agent_task.result()
        watchdog_task.result()
        agent_task.cancel()
        await asyncio.gather(agent_task, return_exceptions=True)
        assert expiration_result is not None
        expiration = expiration_result
        reason, category, idle_sec = expiration
        raise DirectAgentWatchdogExpired(
            f"{reason}; last progress={category} {int(idle_sec)}s ago"
        )
    finally:
        for task in (agent_task, watchdog_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(agent_task, watchdog_task, return_exceptions=True)


_INNER_JOURNAL = Path("/logs/agent/direct-progress.jsonl")
_INNER_STATE = Path("/logs/agent/direct-progress-state.json")
_INNER_CONFIG = Path("/logs/agent/direct-progress-config.json")


def _read_inner_state() -> dict:
    try:
        state = json.loads(_INNER_STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
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
        config = json.loads(_INNER_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    raw = config.get("expected_services") if isinstance(config, dict) else []
    if not isinstance(raw, list):
        return ()
    safe = tuple(_safe_name(item, "") for item in raw)
    return safe if all(safe) else ()


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
    phase = _compose_phase(command)
    if phase:
        state["active_phase"] = phase
        state["active_tool_id"] = str(payload.get("tool_use_id") or "")
        journal.record("compose_phase", phase=phase)
    generated = _generated_resolved_compose(
        command, Path.home() / "video-search-and-summarization"
    )
    if generated is not None:
        state["compose_file"] = str(generated)
    _write_inner_state(state)

    expected = _inner_expected_services()
    if not expected or not _is_compose_up(command):
        return 0
    candidates = _resolved_compose_candidates(
        command, Path.home() / "video-search-and-summarization"
    )
    if not candidates and state.get("compose_file"):
        candidates = [Path(state["compose_file"])]
    if not candidates:
        print(
            "deployment blocked: resolved Compose file was not observed",
            file=sys.stderr,
        )
        return 2
    try:
        missing, digest = validate_compose_services(candidates[-1], expected)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        print(
            "deployment blocked: resolved Compose validation failed",
            file=sys.stderr,
        )
        return 2
    journal.record(
        "expected_services",
        services=list(expected),
        compose_sha256=digest,
    )
    if missing:
        print(
            "deployment blocked: missing expected services: "
            + ", ".join(missing),
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
    except Exception:  # noqa: BLE001 - hooks fail closed without raw details
        print("direct progress hook failed safely", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_hook_main(sys.argv[1:]))
