#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Deterministic GitHub CI runner for the NemoClaw VSS skill smoke eval.

This path intentionally does not ask the outer Claude meta-agent to decide what
to run.  It generates one bounded Harbor dataset, locks one existing
``vss-eval-*`` worker, runs Harbor once in the foreground, and exits with a
clear verdict.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, NamedTuple
from urllib.parse import quote

SKILL_EVAL_MODULE_ROOT = str(Path(__file__).resolve().parents[1])
if SKILL_EVAL_MODULE_ROOT not in sys.path:
    sys.path.insert(0, SKILL_EVAL_MODULE_ROOT)
import remote_worker_lock  # noqa: E402
import run_leg as worker_pool  # noqa: E402

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 on some self-hosted runners.
    tomllib = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_EVAL_ROOT = REPO_ROOT / ".github" / "skill-eval"
ADAPTERS_ROOT = SKILL_EVAL_ROOT / "adapters"
SKILLS_ROOT = REPO_ROOT / "skills"
DEFAULT_DATASET_ROOT = Path("/tmp/skill-eval/datasets/nemoclaw")
DEFAULT_RESULTS_ROOT = Path("/tmp/skill-eval/results")
DEFAULT_SKILL = "vss-deploy-profile"
DEFAULT_PROFILE = "base"
DEFAULT_PLATFORM = "RTXPRO6000BW"
SCRATCH_ROOT = Path("/tmp/skill-eval")
KNOWN_VSS_PROFILES = {"base", "alerts", "search", "lvs"}
AGGREGATE_EVAL_SPEC_NAME = "evals.json"
EVAL_ROW_COMPLETION_MARKER = "<!-- nemoclaw-eval-row-complete -->"
ATTEMPT_OWNER_ENV = "NEMOCLAW_ATTEMPT_OWNER_TOKEN"
ATTEMPT_OWNER_FILE = "nemoclaw-attempt-owner"
ATTEMPT_OWNER_PROBE_ATTEMPTS = 3
ATTEMPT_OWNER_PROBE_RETRY_DELAY_S = 3
BREV_POOL_STORAGE_CONTRACT_ENV = "NEMOCLAW_BREV_POOL_STORAGE_CONTRACT"
BREV_POOL_MIN_STORAGE_GB = 110
BREV_POOL_MIN_FREE_STORAGE_GB = 20
BREV_POOL_STORAGE_SCENARIOS = frozenset(
    {
        (
            "vss-ask-video",
            "base_profile_video_understanding",
            "RTXPRO6000BW",
        ),
        (
            "vss-deploy-dense-captioning",
            "alerts_profile_api",
            "RTXPRO6000BW",
        ),
        ("vss-deploy-profile", "base", "RTXPRO6000BW"),
        (
            "vss-generate-video-report",
            "base_profile_report",
            "RTXPRO6000BW",
        ),
        (
            "vss-manage-alerts",
            "alerts_vlm_real_time",
            "RTXPRO6000BW",
        ),
        (
            "vss-query-analytics",
            "query_analytics",
            "RTXPRO6000BW",
        ),
        (
            "vss-setup-behavior-analytics",
            "deploy_search_and_alerts",
            "ANY",
        ),
        ("vss-summarize-video", "lvs_api_ops", "RTXPRO6000BW"),
    }
)
_REGISTERED_WORKERS: set[str] = set()

PLATFORM_TASK = {
    "RTXPRO6000BW": "rtxpro6000bw",
    "L40S": "l40s",
    "H100": "h100",
    "DGX-SPARK": "spark",
    "IGX-THOR": "thor",
    "ANY": "any",
}

PLATFORM_NAME_HINTS = {
    "RTXPRO6000BW": ("rtx",),
    "L40S": ("l40s",),
    "H100": ("h100",),
    "DGX-SPARK": ("spark",),
    "IGX-THOR": ("thor",),
    "ANY": (),
}

PLATFORM_GPU_HINTS = {
    "RTXPRO6000BW": ("RTX", "PRO", "6000"),
    "L40S": ("L40S",),
    "H100": ("H100",),
    "DGX-SPARK": ("GB10", "SPARK"),
    "IGX-THOR": ("THOR",),
    "ANY": (),
}

# A representative matrix row is one bounded spec/platform chain per supported
# skill. Adapters emit one Harbor task per ``expects`` entry, so each limit must
# include the prerequisite prefix through the first substantive target-skill
# task. A blanket limit of one would exercise only deployment prerequisites for
# most multi-step skills.
REPRESENTATIVE_TASK_LIMITS = {
    ("vss-ask-video", "base_profile_video_understanding"): 4,
    ("vss-deploy-dense-captioning", "alerts_profile_api"): 2,
    ("vss-deploy-profile", "base"): 1,
    ("vss-generate-video-report", "base_profile_report"): 4,
    ("vss-manage-alerts", "alerts_vlm_real_time"): 2,
    ("vss-query-analytics", "query_analytics"): 3,
    ("vss-setup-behavior-analytics", "deploy_search_and_alerts"): 1,
    ("vss-summarize-video", "lvs_api_ops"): 2,
}


class CommandResult(NamedTuple):
    returncode: int
    stdout: str
    stderr: str


class NemoClawScenario(NamedTuple):
    skill: str
    spec_name: str
    spec_path: Path
    platform: str
    gpu_count: int
    task_dir: Path
    harbor_path: Path
    task_name: str
    deployment_profile: str | None


class InfrastructureBlocked(RuntimeError):
    """Raised when CI infra capacity prevents the smoke from running."""


class RemoteLockHeartbeat(NamedTuple):
    stop_event: threading.Event
    lost_event: threading.Event
    thread: threading.Thread


class AttemptOwnerStatus(NamedTuple):
    status: str
    reason: str


class WorkerLock(NamedTuple):
    local_fd: int
    local_handle: Any
    remote_owner: str | None
    remote_target: str | None = None
    heartbeat: RemoteLockHeartbeat | None = None
    remote_lease: remote_worker_lock.RemoteWorkerLease | None = None
    remote_executor: remote_worker_lock.RemoteExecutor | None = None


def _task_dir_sort_key(task_dir: Path) -> tuple[str, int, str]:
    name = task_dir.name
    if name.startswith("step-"):
        try:
            return (str(task_dir.parent), int(name.split("-", 1)[1]), name)
        except ValueError:
            pass
    return (str(task_dir.parent), 0, name)


def _scenario_groups(scenarios: list[NemoClawScenario]) -> list[list[NemoClawScenario]]:
    groups: list[list[NemoClawScenario]] = []
    current_key: tuple[Path, str, int] | None = None
    current: list[NemoClawScenario] = []
    for scenario in scenarios:
        key = (scenario.harbor_path, scenario.platform, scenario.gpu_count)
        if current and key != current_key:
            groups.append(current)
            current = []
        current.append(scenario)
        current_key = key
    if current:
        groups.append(current)
    return groups


def _run(cmd: list[str], *, timeout: int = 60, env: dict[str, str] | None = None) -> CommandResult:
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return CommandResult(proc.returncode, proc.stdout, proc.stderr)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_brev_json(raw: str) -> list[dict[str, Any]]:
    """Parse Brev JSON output while tolerating trailing CLI walkthrough text."""
    text = raw.strip()
    if text:
        for start_char, end_char in (("[", "]"), ("{", "}")):
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start < 0 or end < start:
                continue
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                workspaces = parsed.get("workspaces")
                if isinstance(workspaces, list):
                    return [item for item in workspaces if isinstance(item, dict)]
    bracket = raw.rfind("]")
    if bracket < 0:
        return []
    try:
        parsed = json.loads(raw[: bracket + 1])
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _status_ready(status: str) -> bool:
    upper = status.upper()
    return "RUNNING" in upper or "READY" in upper


def _loose_tokens_match(want: tuple[str, ...], have: str) -> bool:
    upper = have.upper().replace("-", " ")
    return all(token.upper() in upper for token in want)


def _instance_candidates(
    instances: list[dict[str, Any]],
    *,
    platform: str,
    gpu_count: int,
) -> list[str]:
    name_hints = PLATFORM_NAME_HINTS.get(platform, ())
    gpu_hints = PLATFORM_GPU_HINTS.get(platform, ())
    candidates: list[tuple[int, int, str]] = []
    for inst in instances:
        name = str(inst.get("name") or "")
        if not name.startswith("vss-eval-"):
            continue
        status_text = " ".join(str(inst.get(key) or "") for key in ("status", "state"))
        if status_text and not _status_ready(status_text):
            continue
        lowered = name.lower()
        registered = bool(inst.get("_registered"))
        gpu_text = " ".join(
            str(inst.get(key) or "") for key in ("gpu", "instance_type", "type")
        )
        if gpu_count == 0 or platform == "ANY":
            candidates.append(
                (0 if registered else 1, len(name), name)
            )
            continue
        name_match = any(hint in lowered for hint in name_hints)
        gpu_match = _loose_tokens_match(gpu_hints, gpu_text) if gpu_hints else True
        if registered:
            if not gpu_match:
                continue
            count_hint = _name_gpu_count_hint(name)
            if count_hint is not None and count_hint < gpu_count:
                continue
        elif not (name_match or gpu_match):
            continue
        # A 1-GPU profile can safely run on a larger 2-GPU warm worker when
        # the 1-GPU pool is stopped; prefer exact partitions below, but do not
        # reject the larger worker as a fallback.
        if gpu_count >= 2 and "-1g" in lowered:
            continue

        score = 0
        count_hint = _name_gpu_count_hint(name)
        if gpu_count > 0 and count_hint == gpu_count:
            score -= 10
        score += len(name)
        candidates.append(
            (0 if registered else 1, score, name)
        )
    return [name for _, _, name in sorted(candidates)]


def _name_gpu_count_hint(name: str) -> int | None:
    """Return the operator pool's encoded GPU count when one is present."""
    return worker_pool._name_gpu_count_hint(name)


def _summarize_instances(instances: list[dict[str, Any]]) -> str:
    rows: list[str] = []
    for inst in instances:
        name = str(inst.get("name") or "")
        if not name.startswith("vss-eval-"):
            continue
        status = " ".join(str(inst.get(key) or "") for key in ("status", "state")).strip()
        gpu = str(inst.get("gpu") or "").strip()
        instance_type = str(inst.get("instance_type") or inst.get("type") or "").strip()
        details = ", ".join(part for part in (status, gpu, instance_type) if part)
        rows.append(f"{name} ({details or 'no metadata'})")
    return "; ".join(rows) if rows else "<no vss-eval-* workers visible>"


def _exec_target_for_instance(instance: dict[str, Any]) -> str:
    """Prefer Brev instance ID for CLI exec while keeping names for reporting."""
    if instance.get("_registered"):
        return str(instance.get("name") or "")
    return str(instance.get("id") or instance.get("workspace_id") or instance.get("name") or "")


def _generate_dataset(profile: str, platform: str, dataset_root: Path) -> None:
    shutil.rmtree(dataset_root, ignore_errors=True)
    env = os.environ.copy()
    env["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    cmd = [
        sys.executable,
        ".github/skill-eval/adapters/vss-deploy-profile/generate.py",
        "--output-dir",
        str(dataset_root),
        "--skill-dir",
        "skills/vss-deploy-profile",
        "--profile",
        profile,
        "--platform",
        platform,
    ]
    print("[nemoclaw-ci] generating dataset:", " ".join(cmd), flush=True)
    result = _run(cmd, timeout=120, env=env)
    print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f"dataset generation failed with exit {result.returncode}")


def _gpu_count_from_spec(profile: str, platform: str) -> int:
    spec_path = REPO_ROOT / "skills" / "vss-deploy-profile" / "evals" / f"{profile}.json"
    if not spec_path.exists():
        raise RuntimeError(f"missing vss-deploy-profile eval spec: {spec_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    platform_spec = (
        spec.get("resources", {})
        .get("platforms", {})
        .get(platform, {})
    )
    try:
        return int(platform_spec["gpu_count"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"missing gpu_count for profile={profile!r}, platform={platform!r} in {spec_path}"
        ) from exc


def _safe_slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-") or "scenario"


def _skill_filters(raw: str) -> tuple[bool, list[str]]:
    value = raw.strip()
    if not value or value == "*":
        return value == "*", []
    skills = [
        item.strip()
        for chunk in value.split(",")
        for item in chunk.split()
        if item.strip()
    ]
    return False, skills


def _adapter_path(skill: str) -> Path:
    return ADAPTERS_ROOT / skill / "generate.py"


def _adapter_help(adapter: Path) -> str:
    result = _run([sys.executable, str(adapter), "--help"], timeout=45)
    return f"{result.stdout}\n{result.stderr}"


def _spec_json(spec_path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_array_spec(spec_path: Path) -> bool:
    try:
        parsed = json.loads(spec_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(parsed, list)


def _has_expects(spec_path: Path) -> bool:
    spec = _spec_json(spec_path)
    expects = spec.get("expects")
    return isinstance(expects, list) and bool(expects)


def _is_standalone_host_docker_spec(spec_path: Path) -> bool:
    """Return True for evals that require raw host Docker/Compose access.

    The current NemoClaw runner intentionally exercises deployment through the
    VSS Orchestrator MCP server. Specs that explicitly require standalone host
    compose workflows are useful coverage, but they need a host-exec MCP/tooling
    path before they can run under NemoClaw.
    """
    spec = _spec_json(spec_path)
    platforms = spec.get("resources", {}).get("platforms", {})
    if isinstance(platforms, dict):
        for platform_spec in platforms.values():
            modes = platform_spec.get("modes") if isinstance(platform_spec, dict) else None
            if isinstance(modes, list) and any(str(mode).lower() == "standalone" for mode in modes):
                return True

    text = json.dumps(spec.get("expects") or [], sort_keys=True).lower()
    host_direct_phrases = (
        "no vss profile is pre-deployed",
        "do not invoke `/vss-deploy-profile`",
        "does not use `/vss-deploy-profile`",
        "does not use /vss-deploy-profile",
        "not use `/vss-deploy-profile`",
        "not deploy a full vss profile",
        "does not deploy a full vss profile",
    )
    if any(phrase in text for phrase in host_direct_phrases) and any(
        marker in text for marker in ("docker", "compose", "container")
    ):
        return True
    if "deploy amc" in text and "docker compose" in text:
        return True
    markers = (
        "docker compose",
        "compose file",
        "compose.yml",
        "standalone compose",
        "not deploy a full vss profile",
        "does not deploy a full vss profile",
    )
    return "standalone" in text and any(marker in text for marker in markers)


def _known_unbounded_nemoclaw_spec(skill: str, spec_path: Path) -> str | None:
    """Return a blocker reason for profile-backed specs not ready for the sweep.

    These are not standalone Docker cases, but they still need additional
    bounded setup before they are safe in the all-skills NemoClaw matrix.
    """
    if skill == "vss-search-archive" and spec_path.stem == "search":
        return (
            "search archive is not yet bounded for the NemoClaw all-skills "
            "sweep; it needs deterministic search-corpus seeding and "
            "multi-step query execution before it can run reliably"
        )
    if (
        skill == DEFAULT_SKILL
        and spec_path.stem == "alerts_cv"
        and not _env_flag("NEMOCLAW_ENABLE_RTCV")
    ):
        return (
            "alerts CV mode requires real RT-CV model artifacts from NGC; "
            "keep it out of the default NemoClaw sweep until those artifacts "
            "are available on the worker or NEMOCLAW_ENABLE_RTCV=1 is set"
        )
    return None


def _spec_priority(skill: str, spec_path: Path) -> tuple[int, str]:
    preferred = {
        DEFAULT_SKILL: ["base", "alerts_vlm", "lvs", "search", "warehouse"],
    }.get(skill, [])
    try:
        return (preferred.index(spec_path.stem), spec_path.stem)
    except ValueError:
        return (len(preferred), spec_path.stem)


def _eval_spec_paths(
    skill_dir: Path,
    *,
    include_aggregate: bool = True,
) -> list[Path]:
    """Return current and legacy JSON eval specs in deterministic order."""
    specs = [
        spec_path
        for eval_dir_name in ("evals", "eval")
        for spec_path in (skill_dir / eval_dir_name).glob("*.json")
        if include_aggregate or spec_path.name != AGGREGATE_EVAL_SPEC_NAME
    ]
    skill = skill_dir.name
    return sorted(
        specs,
        key=lambda path: (
            *_spec_priority(skill, path),
            0 if path.parent.name == "evals" else 1,
            path.name,
        ),
    )


def _canonical_matrix_skills(skills_filter: str) -> list[str]:
    """Return every requested skill family that carries an eval dataset."""
    all_skills, requested = _skill_filters(skills_filter)
    if not all_skills and not requested:
        requested = [DEFAULT_SKILL]
    skill_dirs = (
        sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
        if all_skills
        else [SKILLS_ROOT / skill for skill in requested]
    )
    return [
        skill_dir.name
        for skill_dir in skill_dirs
        if skill_dir.is_dir()
        and _eval_spec_paths(skill_dir)
    ]


def _platforms_for_spec(spec_path: Path, platform_filter: str | None) -> list[str]:
    spec = _spec_json(spec_path)
    platforms = spec.get("resources", {}).get("platforms", {})
    if not isinstance(platforms, dict) or not platforms:
        return [platform_filter or DEFAULT_PLATFORM]
    if platform_filter and platform_filter in PLATFORM_TASK:
        return [platform_filter]
    declared = [str(name) for name in platforms]
    if not platform_filter:
        return declared
    if "ANY" in platforms:
        return ["ANY"]
    return []


def _selected_specs(
    *,
    skills_filter: str,
    profile_filter: str | None,
    platform_filter: str | None,
    spec_filter: str | None = None,
) -> tuple[list[tuple[str, Path, list[str]]], list[str]]:
    all_skills, requested = _skill_filters(skills_filter)
    if not all_skills and not requested:
        requested = [DEFAULT_SKILL]

    if all_skills:
        skill_dirs = sorted(path for path in SKILLS_ROOT.iterdir() if path.is_dir())
    else:
        skill_dirs = [SKILLS_ROOT / skill for skill in requested]

    selected: list[tuple[str, Path, list[str]]] = []
    blockers: list[str] = []
    for skill_dir in skill_dirs:
        skill = skill_dir.name
        specs = _eval_spec_paths(skill_dir)
        if not specs:
            continue
        adapter = _adapter_path(skill)
        if not adapter.exists():
            blockers.append(f"{skill}: missing Harbor adapter at {adapter.relative_to(REPO_ROOT)}")
            continue
        if skill == DEFAULT_SKILL and profile_filter and not all_skills:
            profile_specs = [
                spec_path
                for spec_path in specs
                if spec_path.stem == profile_filter
            ]
            specs = profile_specs or [skill_dir / "evals" / f"{profile_filter}.json"]
        if spec_filter:
            wanted = Path(spec_filter).stem
            specs = [
                spec_path
                for spec_path in specs
                if spec_path.stem == wanted or spec_path.name == spec_filter
            ]
            if not specs:
                blockers.append(f"{skill}: no eval spec matching {spec_filter}")
                continue
        array_specs = [spec_path for spec_path in specs if _is_array_spec(spec_path)]
        dict_specs = [spec_path for spec_path in specs if spec_path not in array_specs]
        if array_specs:
            if spec_filter or not dict_specs:
                for spec_path in array_specs:
                    blockers.append(
                        f"{skill}/{spec_path.name}: array-format skill eval is not a "
                        "NemoClaw live scenario"
                    )
            if dict_specs:
                specs = dict_specs
            else:
                continue
        empty_specs = [spec_path for spec_path in specs if not _has_expects(spec_path)]
        runnable_specs = [spec_path for spec_path in specs if spec_path not in empty_specs]
        if empty_specs:
            if spec_filter or not runnable_specs:
                for spec_path in empty_specs:
                    blockers.append(
                        f"{skill}/{spec_path.name}: eval spec has no runnable expects"
                    )
            if runnable_specs:
                specs = runnable_specs
            else:
                continue
        standalone_specs = [spec_path for spec_path in specs if _is_standalone_host_docker_spec(spec_path)]
        if standalone_specs:
            for spec_path in standalone_specs:
                blockers.append(
                    f"{skill}/{spec_path.name}: standalone host-Docker eval is not "
                    "supported by the NemoClaw MCP-only runner yet"
                )
            specs = [spec_path for spec_path in specs if spec_path not in standalone_specs]
            if not specs:
                continue
        unbounded_specs: list[tuple[Path, str]] = []
        for spec_path in specs:
            reason = _known_unbounded_nemoclaw_spec(skill, spec_path)
            if reason:
                unbounded_specs.append((spec_path, reason))
        if unbounded_specs:
            for spec_path, reason in unbounded_specs:
                blockers.append(f"{skill}/{spec_path.name}: {reason}")
            blocked_paths = {spec_path for spec_path, _ in unbounded_specs}
            specs = [spec_path for spec_path in specs if spec_path not in blocked_paths]
            if not specs:
                continue
        for spec_path in specs:
            if not spec_path.exists():
                blockers.append(f"{skill}: missing eval spec {spec_path.relative_to(REPO_ROOT)}")
                continue
            platforms = _platforms_for_spec(spec_path, platform_filter)
            if not platforms:
                blockers.append(
                    f"{skill}/{spec_path.name}: no platform match for {platform_filter}"
                )
                continue
            selected.append((skill, spec_path, platforms))
    return selected, blockers


def _run_adapter(
    *,
    skill: str,
    spec_path: Path,
    platform: str,
    output_root: Path,
) -> list[Path]:
    adapter = _adapter_path(skill)
    shutil.rmtree(output_root, ignore_errors=True)
    output_root.mkdir(parents=True, exist_ok=True)

    help_text = _adapter_help(adapter)
    cmd = [
        sys.executable,
        str(adapter.relative_to(REPO_ROOT)),
        "--output-dir",
        str(output_root),
        "--skill-dir",
        str((SKILLS_ROOT / skill).relative_to(REPO_ROOT)),
    ]
    if skill == DEFAULT_SKILL:
        cmd.extend(["--profile", spec_path.stem])
    elif "--spec" in help_text:
        cmd.extend(["--spec", str(spec_path.relative_to(REPO_ROOT))])
    else:
        raise RuntimeError(
            f"{skill}: adapter does not expose --spec, cannot target {spec_path.name}"
        )
    if "--platform" in help_text:
        cmd.extend(["--platform", platform])
    dependency_args = {
        "--deploy-skill-dir": "vss-deploy-profile",
        "--video-io-skill-dir": "vss-manage-video-io-storage",
        "--query-analytics-skill-dir": "vss-query-analytics",
    }
    for arg, dependency_skill in dependency_args.items():
        dependency_dir = SKILLS_ROOT / dependency_skill
        if arg in help_text and dependency_dir.exists():
            cmd.extend([arg, str(dependency_dir.relative_to(REPO_ROOT))])

    env = os.environ.copy()
    env["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    print("[nemoclaw-ci] generating dataset:", " ".join(cmd), flush=True)
    result = _run(cmd, timeout=180, env=env)
    print(result.stdout, end="", flush=True)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr, flush=True)
        raise RuntimeError(f"{skill}/{spec_path.name}: adapter exited {result.returncode}")
    return sorted((path.parent for path in output_root.rglob("task.toml")), key=_task_dir_sort_key)


def _read_task_toml(task_dir: Path) -> dict[str, Any]:
    task_toml = task_dir / "task.toml"
    if tomllib is not None:
        try:
            with task_toml.open("rb") as handle:
                parsed = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {"metadata": _read_metadata_fallback(task_toml)}


def _read_metadata_fallback(task_toml: Path) -> dict[str, Any]:
    try:
        lines = task_toml.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    metadata: dict[str, Any] = {}
    in_metadata = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_metadata = line == "[metadata]"
            continue
        if not in_metadata or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        metadata[key.strip()] = _parse_metadata_value(raw_value.strip())
    return metadata


def _parse_metadata_value(raw_value: str) -> Any:
    value = raw_value.split("#", 1)[0].strip()
    if not value:
        return ""
    if value in ("true", "false"):
        return value == "true"
    if value.startswith('"') and value.endswith('"'):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value.strip('"')
    if value.startswith("[") and value.endswith("]"):
        items = []
        for item in value[1:-1].split(","):
            item = item.strip()
            if item:
                items.append(_parse_metadata_value(item))
        return items
    try:
        return int(value)
    except ValueError:
        return value


def _metadata_value(metadata: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = metadata.get(key)
        if value not in (None, ""):
            return value
    return None


def _task_platform(metadata: dict[str, Any], fallback: str) -> str:
    return str(_metadata_value(metadata, "platform", "gpu_type") or fallback)


def _task_gpu_count(metadata: dict[str, Any]) -> int:
    try:
        return int(metadata.get("gpu_count", 1))
    except (TypeError, ValueError):
        return 1


def _normalize_profile(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    if candidate in ("", "standalone"):
        return None
    return candidate if candidate in KNOWN_VSS_PROFILES else None


def _deployment_profile_from_spec(spec_path: Path) -> str | None:
    spec = _spec_json(spec_path)
    expects = spec.get("expects")
    if isinstance(expects, list) and expects:
        query = str(expects[0].get("query", "") if isinstance(expects[0], dict) else "")
        patterns = (
            r"VSS\s+\*\*([A-Za-z0-9_-]+)\*\*\s+profile",
            r"VSS\s+`?([A-Za-z0-9_-]+)`?\s+profile",
        )
        for pattern in patterns:
            match = re.search(pattern, query, flags=re.IGNORECASE)
            if match:
                profile = _normalize_profile(match.group(1))
                if profile:
                    return profile

    stem = spec_path.stem.lower()
    for profile in KNOWN_VSS_PROFILES:
        if stem == profile or stem.startswith(f"{profile}_"):
            return profile
    return None


def _deployment_profile_from_path(task_dir: Path) -> str | None:
    for part in reversed(task_dir.parts):
        profile = _normalize_profile(part)
        if profile:
            return profile
        for known in KNOWN_VSS_PROFILES:
            if part.lower().startswith(f"{known}_"):
                return known
    return None


def _deployment_profile(
    metadata: dict[str, Any],
    *,
    task_dir: Path | None = None,
    spec_path: Path | None = None,
) -> str | None:
    value = _metadata_value(metadata, "deployment_profile", "profile")
    profile = _normalize_profile(value)
    if profile:
        return profile
    if spec_path:
        profile = _deployment_profile_from_spec(spec_path)
        if profile:
            return profile
    if task_dir:
        profile = _deployment_profile_from_path(task_dir)
        if profile:
            return profile
    return None


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    return json.dumps(str(value))


def _upsert_metadata(task_dir: Path, updates: dict[str, Any]) -> None:
    task_toml = task_dir / "task.toml"
    text = task_toml.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = lines.index("[metadata]")
    except ValueError:
        body = [f"{key} = {_toml_value(value)}" for key, value in updates.items()]
        task_toml.write_text(text.rstrip() + "\n\n[metadata]\n" + "\n".join(body) + "\n", encoding="utf-8")
        return

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("[") and lines[idx].endswith("]"):
            end = idx
            break

    update_keys = set(updates)
    kept = []
    for line in lines[start + 1:end]:
        key = line.split("=", 1)[0].strip()
        if key in update_keys:
            continue
        kept.append(line)
    body = [f"{key} = {_toml_value(value)}" for key, value in updates.items()]
    new_lines = lines[: start + 1] + kept + body + lines[end:]
    task_toml.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")


def _headless_launcher_instruction(skill: str, deployment_profile: str | None) -> str:
    wait_arg = f" \\\n  --wait-profile {deployment_profile}" if deployment_profile else ""
    return (
        "This Harbor trial is a thin launcher for NemoClaw/OpenClaw.\n\n"
        "Do not complete the task directly from Claude Code. Use Bash to run the "
        "headless NemoClaw launcher below. The real task prompt lives in "
        "`/tests/nemoclaw_prompt.md` and is executed inside OpenClaw with the "
        f"`/{skill}` skill available.\n\n"
        "```bash\n"
        "python3 .github/skill-eval/nemoclaw/headless_runner.py \\\n"
        "  --prompt-file /tests/nemoclaw_prompt.md \\\n"
        "  --log-dir /logs/artifacts/nemoclaw \\\n"
        "  --launch-mode cli \\\n"
        f"  --expected-skill {skill} \\\n"
        "  --timeout 1500"
        f"{wait_arg}\n"
        "```\n"
    )


def _generic_nemoclaw_prompt(
    skill: str,
    original_instruction: str,
    deployment_profile: str | None,
) -> str:
    profile_guidance = (
        f"The generated eval metadata requires the `{deployment_profile}` VSS profile. "
        "Before exercising the skill, use the VSS Orchestrator MCP server to deploy "
        "or confirm that profile if it is not already healthy. This is a warm CI "
        "worker whose Docker images are preserved between trials, so call "
        "`vss_orchestrator__docker_up` with `pull_always=true` and "
        "`force_recreate=true` to refresh moving `develop-latest` images. Poll "
        "`vss_orchestrator__docker_status` until it returns a terminal `success` "
        "with `running=false`; a `running` operation is never deployment success. "
        "Never infer readiness from container-name presence. If compose reports a "
        "dependency start failure, inspect the structured `docker_list` states and "
        "combined `docker_logs`, remediate, and retry `docker_up`. Proceed only "
        "after the target host's profile-required functional endpoints—including "
        "the Agent API on port 8000 and UI on port 3000—respond successfully.\n\n"
        if deployment_profile
        else ""
    )
    rtsp_probe_guidance = (
        "As your first OpenClaw `exec` call, before registering the "
        "`RTSP_SAMPLE_URL`, run exactly "
        "`test -n \"${RTSP_SAMPLE_URL:-}\" && printf "
        "'RTSP_SAMPLE_URL is set\\n'` through the OpenClaw `exec` tool and "
        "require the sole output `RTSP_SAMPLE_URL is set`. Then call "
        "`vss_orchestrator__rtsp_sample_probe` with no URL argument and require "
        "`status=success`, `has_video=true`, and `video_stream_count>=1`. "
        "The tool probes only the orchestrator host's configured runtime sample; "
        "never pass the URL through MCP or print its value. A timeout, probe "
        "failure, or no-video result is terminal; "
        "do not register a substitute stream.\n\n"
        if skill == "vss-deploy-dense-captioning"
        else ""
    )
    return (
        "You are running inside automated GitHub VSS skill evaluation with "
        "NemoClaw/OpenClaw.\n\n"
        f"Use the `/{skill}` skill as the primary workflow for this task. "
        "Use the VSS Orchestrator MCP server when the task requires a full VSS "
        "deployment or live profile checks. For standalone microservice tasks, "
        "follow the skill's documented standalone workflow.\n\n"
        f"{profile_guidance}"
        f"{rtsp_probe_guidance}"
        "Run autonomously without asking for confirmation. If prerequisites are "
        "needed, prepare them through the available skill instructions and tools "
        "before executing the user task.\n\n"
        "## Task\n\n"
        f"{original_instruction.strip()}\n"
    )


def _gpu_resource_marker(gpu_count: int) -> str:
    if gpu_count <= 0:
        return "This trial reserves no GPUs."
    if gpu_count == 1:
        return "This trial reserves exactly 1 GPU; the only valid device ID is 0."
    return (
        f"This trial reserves exactly {gpu_count} GPUs; valid device IDs are "
        f"0 through {gpu_count - 1}."
    )


def _with_gpu_resource_guidance(prompt: str, gpu_count: int) -> str:
    """Ensure every NemoClaw prompt states the task's hard GPU boundary."""

    marker = _gpu_resource_marker(gpu_count)
    if marker in prompt:
        return prompt
    if "This trial reserves " in prompt:
        raise RuntimeError(
            f"NemoClaw prompt GPU boundary disagrees with task gpu_count={gpu_count}"
        )
    if gpu_count <= 0:
        guidance = (
            f"{marker} Use remote model endpoints and never "
            "request a local GPU device."
        )
    elif gpu_count == 1:
        guidance = (
            f"{marker} "
            "Leave GPU device-ID overrides unset so profile defaults use shared "
            "placement on GPU 0. Never request GPU 1 or another out-of-range device."
        )
    else:
        guidance = (
            f"{marker} Never request an out-of-range device."
        )
    return prompt.rstrip() + "\n\n## GPU resource boundary\n\n" + guidance + "\n"


def _wrap_task_for_nemoclaw(
    *,
    task_dir: Path,
    skill: str,
    spec_path: Path,
    platform: str,
) -> NemoClawScenario:
    parsed = _read_task_toml(task_dir)
    metadata = parsed.get("metadata") if isinstance(parsed.get("metadata"), dict) else {}
    task_platform = _task_platform(metadata, platform)
    gpu_count = _task_gpu_count(metadata)
    deployment_profile = _deployment_profile(
        metadata,
        task_dir=task_dir,
        spec_path=spec_path,
    )
    tests_dir = task_dir / "tests"
    tests_dir.mkdir(exist_ok=True)
    prompt_path = tests_dir / "nemoclaw_prompt.md"
    instruction_path = task_dir / "instruction.md"
    original_instruction = instruction_path.read_text(encoding="utf-8")
    if not prompt_path.exists():
        prompt_path.write_text(
            _generic_nemoclaw_prompt(skill, original_instruction, deployment_profile),
            encoding="utf-8",
        )
    prompt_text = _with_gpu_resource_guidance(
        prompt_path.read_text(encoding="utf-8"),
        gpu_count,
    )
    marker = _gpu_resource_marker(gpu_count)
    if marker not in prompt_text:
        raise RuntimeError(
            f"{skill}/{spec_path.name}/{task_platform}: generated prompt is missing "
            f"the gpu_count={gpu_count} resource boundary"
        )
    prompt_path.write_text(prompt_text, encoding="utf-8")
    prompt_digest = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    print(
        "[nemoclaw-ci] prompt attestation: "
        f"task={task_dir.name} gpu_count={gpu_count} "
        f"bytes={len(prompt_text.encode('utf-8'))} sha256={prompt_digest}",
        flush=True,
    )
    # Normalize adapter-generated launchers. Some adapters already emit a
    # headless_runner.py instruction when SKILLS_EVAL_RUNNER=nemoclaw, but an
    # older launcher may omit --wait-profile. If we preserve it, Harbor can
    # verify before the async OpenClaw deployment is actually ready.
    instruction_path.write_text(
        _headless_launcher_instruction(skill, deployment_profile),
        encoding="utf-8",
    )

    metadata_updates: dict[str, Any] = {
        "runner": "nemoclaw",
        "requires_nemoclaw": True,
        "requires_mcp": True,
        "expected_skill": skill,
    }
    if (
        _env_flag(BREV_POOL_STORAGE_CONTRACT_ENV, default=False)
        and (skill, spec_path.stem, task_platform.upper())
        in BREV_POOL_STORAGE_SCENARIOS
    ):
        metadata_updates.update(
            {
                "min_root_disk_gb": BREV_POOL_MIN_STORAGE_GB,
                "min_root_disk_free_gb": BREV_POOL_MIN_FREE_STORAGE_GB,
            }
        )
    required_mcp_tools: list[str] = []
    if deployment_profile:
        metadata_updates["deployment_profile"] = deployment_profile
        required_mcp_tools.extend(
            [
                "vss_orchestrator__profiles",
                "vss_orchestrator__docker_status",
            ]
        )
    if skill == "vss-deploy-dense-captioning":
        required_mcp_tools.append("vss_orchestrator__rtsp_sample_probe")
    if required_mcp_tools:
        metadata_updates["required_mcp_tools"] = required_mcp_tools
    _upsert_metadata(task_dir, metadata_updates)

    return NemoClawScenario(
        skill=skill,
        spec_name=spec_path.stem,
        spec_path=spec_path,
        platform=task_platform,
        gpu_count=gpu_count,
        task_dir=task_dir,
        harbor_path=task_dir.parent,
        task_name=task_dir.name,
        deployment_profile=deployment_profile,
    )


def _discover_scenarios(
    *,
    skills_filter: str,
    profile_filter: str | None,
    platform_filter: str | None,
    spec_filter: str | None,
    dataset_root: Path,
    task_limit: int | None = None,
) -> tuple[list[NemoClawScenario], list[str]]:
    shutil.rmtree(dataset_root, ignore_errors=True)
    specs, blockers = _selected_specs(
        skills_filter=skills_filter,
        profile_filter=profile_filter,
        platform_filter=platform_filter,
        spec_filter=spec_filter,
    )
    scenarios: list[NemoClawScenario] = []
    for skill, spec_path, platforms in specs:
        for platform in platforms:
            scenario_root = dataset_root / f"{_safe_slug(skill)}__{_safe_slug(spec_path.stem)}__{_safe_slug(platform)}"
            try:
                task_dirs = _run_adapter(
                    skill=skill,
                    spec_path=spec_path,
                    platform=platform,
                    output_root=scenario_root,
                )
            except RuntimeError as exc:
                blockers.append(str(exc))
                continue
            if not task_dirs:
                blockers.append(f"{skill}/{spec_path.name}/{platform}: adapter generated no tasks")
                continue
            if task_limit and task_limit > 0:
                task_dirs = task_dirs[:task_limit]
            for task_dir in task_dirs:
                scenarios.append(
                    _wrap_task_for_nemoclaw(
                        task_dir=task_dir,
                        skill=skill,
                        spec_path=spec_path,
                        platform=platform,
                    )
                )
    return scenarios, blockers


def _adapter_supports_platform(skill: str, platform: str) -> bool:
    adapter = _adapter_path(skill)
    if not adapter.exists():
        return False
    return platform in _adapter_help(adapter)


def _preferred_platform(skill: str, platforms: list[str], platform_filter: str | None) -> str:
    if platform_filter and platform_filter in platforms:
        return platform_filter
    if "ANY" in platforms:
        return "ANY"
    if _adapter_supports_platform(skill, DEFAULT_PLATFORM):
        return DEFAULT_PLATFORM
    if DEFAULT_PLATFORM in platforms:
        return DEFAULT_PLATFORM
    return platforms[0]


def _build_matrix(
    *,
    skills_filter: str,
    profile_filter: str | None,
    platform_filter: str | None,
    spec_filter: str | None,
    representative_per_skill: bool,
    include_blocked_rows: bool = False,
) -> tuple[list[dict[str, str]], list[str]]:
    specs, blockers = _selected_specs(
        skills_filter=skills_filter,
        profile_filter=profile_filter,
        platform_filter=platform_filter,
        spec_filter=spec_filter,
    )
    rows: list[dict[str, str]] = []
    seen_skills: set[str] = set()
    for skill, spec_path, platforms in specs:
        representative_task_limit = 0
        if representative_per_skill:
            if skill in seen_skills:
                continue
            seen_skills.add(skill)
            representative_task_limit = REPRESENTATIVE_TASK_LIMITS.get(
                (skill, spec_path.stem),
                0,
            )
            if representative_task_limit <= 0:
                blockers.append(
                    f"{skill}/{spec_path.name}: no bounded representative "
                    "task prefix is registered for the NemoClaw sweep"
                )
                continue
        selected_platforms = (
            [_preferred_platform(skill, platforms, platform_filter)]
            if representative_per_skill
            else platforms
        )
        for platform in selected_platforms:
            slug = "__".join(
                _safe_slug(part)
                for part in (skill, spec_path.stem, platform)
            )
            rows.append(
                {
                    "kind": "eval",
                    "name": f"{skill}/{spec_path.stem}/{platform}",
                    "skill": skill,
                    "spec_stem": spec_path.stem,
                    "spec_path": str(spec_path.relative_to(REPO_ROOT)),
                    "platform": platform,
                    "slug": slug,
                    "task_limit": str(representative_task_limit),
                }
            )
    if representative_per_skill and include_blocked_rows:
        represented_skills = {row["skill"] for row in rows}
        for skill in _canonical_matrix_skills(skills_filter):
            if skill in represented_skills:
                continue
            reason = next(
                (
                    blocker
                    for blocker in blockers
                    if blocker.startswith(f"{skill}:")
                    or blocker.startswith(f"{skill}/")
                ),
                None,
            )
            if reason is None:
                reason = (
                    f"{skill}: no bounded/runnable NemoClaw scenario is "
                    "available to the MCP-only runner"
                )
                blockers.append(reason)
            rows.append(
                {
                    "kind": "blocked",
                    "name": f"{skill}/blocked",
                    "skill": skill,
                    "spec_stem": "blocked",
                    "spec_path": "",
                    "platform": "",
                    "slug": f"{_safe_slug(skill)}__blocked",
                    "task_limit": "0",
                    "reason": reason,
                }
            )
        # Publish unsupported coverage immediately before long GPU trials.
        # This makes every requested skill visible within minutes while the
        # bounded eval rows continue on the remote worker pool.
        rows.sort(
            key=lambda row: (
                0 if row["kind"] == "blocked" else 1,
                row["skill"],
                row["name"],
            )
        )
    return rows, blockers


def _print_matrix(rows: list[dict[str, str]], blockers: list[str]) -> None:
    for blocker in blockers:
        print(f"[nemoclaw-ci] blocked coverage item: {blocker}", file=sys.stderr, flush=True)
    print(json.dumps({"include": rows}, separators=(",", ":")), flush=True)


def _list_instances() -> list[dict[str, Any]]:
    try:
        instances = worker_pool._list_pool_instances()
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise InfrastructureBlocked(
            f"worker pool inventory failed: {type(exc).__name__}: {exc}"
        ) from exc
    _REGISTERED_WORKERS.clear()
    _REGISTERED_WORKERS.update(
        str(instance.get("name") or "").lower()
        for instance in instances
        if instance.get("_registered") and instance.get("name")
    )
    if not instances:
        raise InfrastructureBlocked(
            "managed and registered worker inventories returned no "
            "eligible pool instances"
        )
    return instances


def _cleanup_results(results_root: Path, run_id: str) -> None:
    """Recreate only this run's tree without touching concurrent runs."""
    results_root.mkdir(parents=True, exist_ok=True)
    current_run = results_root / run_id
    if current_run.is_dir():
        shutil.rmtree(current_run)
    else:
        current_run.unlink(missing_ok=True)
    current_run.mkdir(parents=True)


def _reachability_failure_text(result: CommandResult) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def _log_reachability_failure(instance: str, exec_target: str, result: CommandResult) -> None:
    output = _reachability_failure_text(result)
    tail = output[-800:] if output else "<no output>"
    target_note = f" exec_target={exec_target}" if exec_target != instance else ""
    print(
        f"[nemoclaw-ci] candidate {instance}{target_note} reachability failed "
        f"rc={result.returncode}: {tail}",
        flush=True,
    )


def _worker_remote_executor(
    instance: str,
    exec_target: str | None = None,
) -> remote_worker_lock.RemoteExecutor:
    """Bind commands to Brev exec or a registered node's SSH alias."""
    if instance.lower() in _REGISTERED_WORKERS:
        worker_pool._WORKER_TRANSPORTS[instance.lower()] = "ssh"
        return worker_pool._remote_lock_executor(instance)

    target = exec_target or instance

    def execute_managed(
        command: str,
        timeout: int,
    ) -> CommandResult:
        setattr(execute_managed, "_brev_auth_failure", None)
        result = _run(
            ["brev", "exec", target, command],
            timeout=timeout,
        )
        try:
            worker_pool._raise_if_brev_auth_failure(
                result.stdout,
                result.stderr,
                f"brev exec {instance} (worker selection)",
            )
        except worker_pool.BrevAuthenticationError as exc:
            # The shared remote-lock helper deliberately converts transport
            # exceptions into an unavailable lease. Preserve this terminal
            # classification on the exact executor object so its caller can
            # recover it without substituting a different executor identity.
            setattr(execute_managed, "_brev_auth_failure", exc)
            raise
        return result

    setattr(execute_managed, "_brev_auth_failure", None)
    return execute_managed


def _reachable(instance: str, exec_target: str | None = None) -> bool:
    target = exec_target or instance
    run_remote = _worker_remote_executor(instance, exec_target)
    try:
        result = run_remote("echo harbor-ready", 45)
    except subprocess.TimeoutExpired:
        print(
            f"[nemoclaw-ci] candidate {instance} reachability check timed out",
            flush=True,
        )
        return False
    reachable = (
        result.returncode == 0
        and "harbor-ready" in (result.stdout or "").splitlines()
    )
    if reachable:
        return True

    _log_reachability_failure(instance, target, result)
    if instance.lower() in _REGISTERED_WORKERS:
        return False
    if "Could not resolve hostname" not in _reachability_failure_text(result):
        return False

    print(
        "[nemoclaw-ci] refreshing Brev SSH config after hostname resolution failure",
        flush=True,
    )
    refresh = _run(["brev", "refresh"], timeout=60)
    worker_pool._raise_if_brev_auth_failure(
        refresh.stdout,
        refresh.stderr,
        "brev refresh (worker selection)",
    )
    if refresh.returncode != 0:
        tail = _reachability_failure_text(refresh)[-800:] or "<no output>"
        print(
            f"[nemoclaw-ci] Brev SSH config refresh failed rc={refresh.returncode}: {tail}",
            flush=True,
        )
        return False

    try:
        retry = run_remote("echo harbor-ready", 45)
    except subprocess.TimeoutExpired:
        print(
            f"[nemoclaw-ci] candidate {instance} reachability retry timed out",
            flush=True,
        )
        return False
    retry_reachable = (
        retry.returncode == 0
        and "harbor-ready" in (retry.stdout or "").splitlines()
    )
    if not retry_reachable:
        _log_reachability_failure(instance, target, retry)
    return retry_reachable


def _try_acquire_lock(instance: str, exec_target: str | None = None) -> WorkerLock | None:
    if "/" in instance or instance in {"", ".", ".."}:
        raise ValueError(f"invalid worker name for lock file: {instance!r}")
    lock_dir = Path("/tmp/brev")
    lock_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_dir / f"{instance}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        handle = os.fdopen(fd, "w")
    except BlockingIOError:
        os.close(fd)
        return None

    remote_owner: str | None = None
    remote_lease: remote_worker_lock.RemoteWorkerLease | None = None
    remote_executor = _worker_remote_executor(instance, exec_target)

    try:
        remote_lock_disabled = (
            os.environ.get("NEMOCLAW_DISABLE_REMOTE_WORKER_LOCK", "").strip().lower()
            in {"1", "true", "yes"}
        )
        if not remote_lock_disabled:
            remote_lease = remote_worker_lock.try_acquire_remote_worker_lock(
                remote_executor,
                instance,
            )
            remote_auth_failure = getattr(
                remote_executor,
                "__dict__",
                {},
            ).get("_brev_auth_failure")
            if isinstance(
                remote_auth_failure,
                worker_pool.BrevAuthenticationError,
            ):
                raise remote_auth_failure
            if remote_lease is None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                handle.close()
                return None
            remote_owner = remote_lease.owner
            heartbeat = remote_lease.heartbeat
        else:
            heartbeat = None
        return WorkerLock(
            fd,
            handle,
            remote_owner,
            exec_target,
            heartbeat,
            remote_lease,
            remote_executor,
        )
    except Exception:
        if remote_lease is not None:
            remote_lease.release()
        elif remote_owner:
            try:
                _clear_remote_worker_lock(
                    exec_target or instance,
                    remote_owner,
                )
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve original error.
                print(
                    "[nemoclaw-ci] WARN: failed to clean up remote lock after "
                    f"heartbeat setup error on {instance}: {cleanup_exc!r}",
                    flush=True,
                )
        fcntl.flock(fd, fcntl.LOCK_UN)
        handle.close()
        raise


def _try_acquire_remote_worker_lock(instance: str) -> str | None:
    """Acquire a lock on the Brev worker, not just on this runner host."""
    owner = "__".join(
        _safe_slug(part)
        for part in (
            "v2",
            os.environ.get("GITHUB_RUN_ID", "local"),
            os.environ.get("GITHUB_RUN_ATTEMPT", "0"),
            os.environ.get(
                "NEMOCLAW_LOCK_OWNER_CONTEXT",
                os.environ.get("GITHUB_JOB", "nemoclaw"),
            ),
            str(os.getpid()),
            str(int(time.time())),
            uuid.uuid4().hex,
        )
    )
    command = f"""set -eu
lock_root=/tmp/skill-eval/locks
lock_dir="$lock_root/nemoclaw-worker.lockdir"
owner={shlex.quote(owner)}
now=$(date +%s)
mkdir -p "$lock_root"
if mkdir "$lock_dir" 2>/dev/null; then
  cleanup_incomplete_lock() {{
    if [ ! -s "$lock_dir/owner" ]; then
      rm -rf "$lock_dir"
    fi
  }}
  trap cleanup_incomplete_lock EXIT HUP INT TERM
  printf '%s\n' "$owner" > "$lock_dir/owner"
  printf '%s\n' "$now" > "$lock_dir/created"
  trap - EXIT HUP INT TERM
  exit 0
fi
created=$(cat "$lock_dir/created" 2>/dev/null || stat -c %Y "$lock_dir" 2>/dev/null || printf '%s\n' "$now")
age=$((now - created))
echo "NemoClaw worker is locked by $(cat "$lock_dir/owner" 2>/dev/null || echo unknown) age=${{age}}s"
exit 1
"""
    def attempt() -> tuple[int, str]:
        try:
            result = _run(["brev", "exec", instance, command], timeout=60)
        except subprocess.TimeoutExpired:
            print(f"[nemoclaw-ci] remote lock check timed out on {instance}", flush=True)
            return 124, ""
        tail = ((result.stdout or "") + (result.stderr or ""))[-500:].strip()
        return result.returncode, tail

    rc, tail = attempt()
    if rc == 0:
        return owner
    locked_owner = _remote_lock_owner_from_output(tail)
    if locked_owner == owner:
        print(
            f"[nemoclaw-ci] reconciled remote lock acquisition on {instance}",
            flush=True,
        )
        return owner
    if locked_owner and _remote_lock_owner_is_inactive(locked_owner):
        print(
            f"[nemoclaw-ci] removing remote lock from inactive run: {locked_owner}",
            flush=True,
        )
        if _clear_remote_worker_lock(instance, locked_owner):
            rc, tail = attempt()
            if rc == 0:
                return owner
            locked_owner = _remote_lock_owner_from_output(tail)
    if tail:
        print(f"[nemoclaw-ci] {instance} remote lock unavailable: {tail}", flush=True)
    return None


def _remote_lock_owner_from_output(output: str) -> str | None:
    match = re.search(r"NemoClaw worker is locked by ([^\s]+)", output)
    return match.group(1) if match else None


def _remote_lock_owner_is_inactive(owner: str) -> bool:
    run_id = _github_run_id_from_lock_owner(owner)
    if not run_id:
        return False
    job_identity = _github_job_identity_from_lock_owner(owner)
    if job_identity:
        run_id, run_attempt, job_context = job_identity
        status = _github_job_status(run_id, run_attempt, job_context)
        if status is not None:
            return status not in {
                "queued",
                "in_progress",
                "waiting",
                "requested",
                "pending",
            }
    current_run_id = os.environ.get("GITHUB_RUN_ID", "")
    if run_id == current_run_id:
        return False
    status = _github_run_status(run_id)
    if status is None:
        return False
    return status not in {"queued", "in_progress", "waiting", "requested", "pending"}


def _github_run_id_from_lock_owner(owner: str) -> str | None:
    match = re.match(r"^(?:v2__)?(\d+)__", owner)
    return match.group(1) if match else None


def _github_job_identity_from_lock_owner(
    owner: str,
) -> tuple[str, str, str] | None:
    parts = owner.split("__")
    if (
        len(parts) != 7
        or parts[0] != "v2"
        or not parts[1].isdigit()
        or not parts[2].isdigit()
        or not parts[3]
    ):
        return None
    return parts[1], parts[2], parts[3]


def _github_job_status(
    run_id: str,
    run_attempt: str,
    job_context: str,
) -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        result = _run(
            [
                "gh",
                "api",
                (
                    f"repos/{repo}/actions/runs/{run_id}/attempts/"
                    f"{run_attempt}/jobs?per_page=100"
                ),
            ],
            timeout=30,
            env={**os.environ, "GH_TOKEN": token},
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        tail = ((result.stdout or "") + (result.stderr or ""))[-300:].strip()
        if tail:
            print(
                f"[nemoclaw-ci] could not query GitHub jobs for run {run_id} "
                f"attempt {run_attempt}: {tail}",
                flush=True,
            )
        return None
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list):
        return None
    matches = [
        job
        for job in jobs
        if isinstance(job, dict)
        and _safe_slug(str(job.get("name") or "")).endswith(job_context)
    ]
    if len(matches) != 1:
        return None
    status = str(matches[0].get("status") or "").strip()
    return status or None


def _github_run_status(run_id: str) -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token:
        return None
    try:
        result = _run(
            [
                "gh",
                "run",
                "view",
                run_id,
                "--repo",
                repo,
                "--json",
                "status",
                "--jq",
                ".status",
            ],
            timeout=30,
            env={**os.environ, "GH_TOKEN": token},
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        tail = ((result.stdout or "") + (result.stderr or ""))[-300:].strip()
        if tail:
            print(
                f"[nemoclaw-ci] could not query GitHub run {run_id}: {tail}",
                flush=True,
            )
        return None
    status = (result.stdout or "").strip()
    return status or None


def _clear_remote_worker_lock(instance: str, owner: str) -> bool:
    command = f"""set -eu
lock_dir=/tmp/skill-eval/locks/nemoclaw-worker.lockdir
expected={shlex.quote(owner)}
actual=$(cat "$lock_dir/owner" 2>/dev/null || true)
if [ -d "$lock_dir" ] && [ "$actual" = "$expected" ]; then
  rm -rf "$lock_dir"
  echo "removed NemoClaw worker lock owned by $expected"
  exit 0
fi
echo "NemoClaw worker lock owner changed to ${{actual:-none}}; not removing"
exit 1
"""
    try:
        result = _run(["brev", "exec", instance, command], timeout=60)
    except subprocess.TimeoutExpired:
        print(f"[nemoclaw-ci] remote stale lock cleanup timed out on {instance}", flush=True)
        return False
    tail = ((result.stdout or "") + (result.stderr or ""))[-500:].strip()
    if result.returncode != 0:
        if tail:
            print(f"[nemoclaw-ci] remote stale lock cleanup skipped: {tail}", flush=True)
        return False
    if tail:
        print(f"[nemoclaw-ci] {tail}", flush=True)
    return True


def _refresh_remote_worker_lock(instance: str, owner: str) -> str:
    """Refresh a held lock for legacy age-based contenders.

    Returns ``refreshed`` only after an atomic exact-owner update,
    ``not_owner`` when the lock is missing/replaced, and ``unknown`` for
    transport failures. It never creates or removes a lock.
    """
    command = f"""set -eu
lock_dir=/tmp/skill-eval/locks/nemoclaw-worker.lockdir
expected={shlex.quote(owner)}
not_owner() {{
  echo "NemoClaw worker lock is not owned by $expected"
  exit 3
}}
[ -d "$lock_dir" ] || not_owner
actual=$(cat "$lock_dir/owner" 2>/dev/null || true)
[ "$actual" = "$expected" ] || not_owner
before=$(stat -Lc '%d:%i' "$lock_dir" 2>/dev/null) || not_owner
tmp=$(mktemp "$lock_dir/.created.XXXXXX") || exit 4
trap 'rm -f "$tmp"' EXIT HUP INT TERM
printf '%s\n' "$(date +%s)" > "$tmp"
after=$(stat -Lc '%d:%i' "$lock_dir" 2>/dev/null) || not_owner
actual=$(cat "$lock_dir/owner" 2>/dev/null || true)
[ "$before" = "$after" ] && [ "$actual" = "$expected" ] || not_owner
mv -f "$tmp" "$lock_dir/created"
trap - EXIT HUP INT TERM
echo "refreshed NemoClaw worker lock owned by $expected"
"""
    try:
        timeout_s = min(
            30,
            max(
                5,
                int(
                    os.environ.get(
                        "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_TIMEOUT_SEC",
                        "30",
                    )
                ),
            ),
        )
        result = _run(["brev", "exec", instance, command], timeout=timeout_s)
    except (OSError, ValueError, subprocess.SubprocessError):
        return "unknown"
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode == 0 and "refreshed NemoClaw worker lock" in output:
        return "refreshed"
    if result.returncode == 3 or "lock is not owned by" in output:
        return "not_owner"
    return "unknown"


def _start_remote_worker_lock_heartbeat(
    instance: str,
    owner: str,
) -> RemoteLockHeartbeat:
    stop_event = threading.Event()
    lost_event = threading.Event()
    try:
        configured_interval = int(
            os.environ.get("NEMOCLAW_REMOTE_LOCK_HEARTBEAT_SEC", "180")
        )
    except ValueError:
        configured_interval = 180
    interval_s = min(240, max(30, configured_interval))
    try:
        configured_max_silence = int(
            os.environ.get(
                "NEMOCLAW_REMOTE_LOCK_HEARTBEAT_MAX_SILENCE_SEC",
                "660",
            )
        )
    except ValueError:
        configured_max_silence = 660
    max_silence_s = min(
        660,
        max(
            interval_s * 2,
            configured_max_silence,
        ),
    )

    def heartbeat() -> None:
        last_success = time.monotonic()
        while not stop_event.wait(interval_s):
            try:
                status = _refresh_remote_worker_lock(instance, owner)
            except Exception as exc:  # noqa: BLE001 - heartbeat must fail closed.
                print(
                    "[nemoclaw-ci] WARN: remote worker lock heartbeat "
                    f"raised on {instance}: {exc!r}",
                    flush=True,
                )
                status = "unknown"
            if stop_event.is_set():
                return
            if status == "refreshed":
                last_success = time.monotonic()
                continue
            if status == "not_owner":
                print(
                    f"[nemoclaw-ci] ERROR: lost remote worker lock on {instance}",
                    flush=True,
                )
                lost_event.set()
                return
            silence = time.monotonic() - last_success
            print(
                "[nemoclaw-ci] WARN: remote worker lock heartbeat "
                f"unconfirmed on {instance} ({int(silence)}s since success)",
                flush=True,
            )
            if silence >= max_silence_s:
                print(
                    "[nemoclaw-ci] ERROR: remote worker lock heartbeat exceeded "
                    f"the {max_silence_s}s safety window on {instance}",
                    flush=True,
                )
                lost_event.set()
                return

    thread = threading.Thread(
        target=heartbeat,
        name=f"nemoclaw-lock-heartbeat-{_safe_slug(instance)}",
        daemon=True,
    )
    thread.start()
    return RemoteLockHeartbeat(stop_event, lost_event, thread)


def _stop_remote_worker_lock_heartbeat(
    heartbeat: RemoteLockHeartbeat | None,
) -> None:
    if heartbeat is None:
        return
    heartbeat.stop_event.set()
    heartbeat.thread.join(timeout=32)
    if heartbeat.thread.is_alive():
        print(
            "[nemoclaw-ci] WARN: remote worker lock heartbeat did not stop "
            "before release",
            flush=True,
        )


def _release_lock(instance: str, lock: WorkerLock) -> None:
    try:
        if lock.remote_lease is not None:
            lock.remote_lease.release()
        else:
            _stop_remote_worker_lock_heartbeat(lock.heartbeat)
        if lock.remote_owner and lock.remote_lease is None:
            remote_target = lock.remote_target or instance
            owner = shlex.quote(lock.remote_owner)
            command = f"""set -eu
lock_dir=/tmp/skill-eval/locks/nemoclaw-worker.lockdir
owner={owner}
if [ -d "$lock_dir" ] && [ "$(cat "$lock_dir/owner" 2>/dev/null || true)" = "$owner" ]; then
  rm -rf "$lock_dir"
else
  echo "NemoClaw worker lock not owned by this run; leaving it in place"
fi
"""
            try:
                run_remote = (
                    lock.remote_executor
                    or _worker_remote_executor(instance, remote_target)
                )
                result = run_remote(command, 60)
                if result.returncode != 0:
                    tail = (
                        (result.stdout or "") + (result.stderr or "")
                    )[-500:].strip()
                    print(
                        "[nemoclaw-ci] WARN: failed to release remote lock "
                        f"on {instance}: {tail}",
                        flush=True,
                    )
            except subprocess.TimeoutExpired:
                print(
                    f"[nemoclaw-ci] WARN: remote lock release timed out on {instance}",
                    flush=True,
                )
    finally:
        fcntl.flock(lock.local_fd, fcntl.LOCK_UN)
        lock.local_handle.close()


def _select_and_lock_instance(
    platform: str,
    gpu_count: int,
    explicit: str | None,
    timeout_s: int,
    excluded: set[str] | None = None,
) -> tuple[str, WorkerLock]:
    deadline = time.time() + timeout_s
    observations: list[str] = []
    excluded = excluded or set()

    def remember(message: str) -> None:
        if not message:
            return
        if observations and observations[-1] == message:
            return
        observations.append(message)
        del observations[:-8]

    def details() -> str:
        if not observations:
            return ""
        return "; last observations: " + " | ".join(observations)

    while True:
        if explicit:
            candidates = [explicit]
            instances_by_name: dict[str, dict[str, Any]] = {}
            try:
                instances = _list_instances()
            except InfrastructureBlocked as exc:
                remember(f"explicit worker inventory unavailable: {exc}")
                print(
                    f"[nemoclaw-ci] explicit worker inventory unavailable: {exc}; "
                    "falling back to the worker name",
                    flush=True,
                )
            else:
                instances_by_name = {
                    str(inst.get("name") or ""): inst
                    for inst in instances
                    if inst.get("name")
                }
                explicit_instance = instances_by_name.get(explicit)
                if explicit_instance:
                    remember(
                        "explicit worker inventory: "
                        f"name={explicit} id={explicit_instance.get('id') or '-'} "
                        f"status={explicit_instance.get('status') or '-'} "
                        f"shell={explicit_instance.get('shell_status') or '-'} "
                        f"health={explicit_instance.get('health_status') or '-'}"
                    )
                else:
                    remember(f"explicit worker {explicit} not visible in brev ls inventory")
        else:
            try:
                instances = _list_instances()
            except InfrastructureBlocked as exc:
                reason = str(exc)
                remember(reason)
                if time.time() >= deadline:
                    raise InfrastructureBlocked(
                        "worker inventory unavailable for "
                        f"{platform} after {timeout_s}s: {reason}{details()}"
                    ) from exc
                print(
                    f"[nemoclaw-ci] worker inventory unavailable: {reason}; "
                    "retrying worker selection",
                    flush=True,
                )
                time.sleep(10)
                continue
            all_candidates = _instance_candidates(
                instances,
                platform=platform,
                gpu_count=gpu_count,
            )
            candidates = [
                candidate
                for candidate in all_candidates
                if candidate not in excluded
            ]
            instances_by_name = {
                str(inst.get("name") or ""): inst
                for inst in instances
                if inst.get("name")
            }
            inventory = _summarize_instances(instances)
            if all_candidates and not candidates:
                raise InfrastructureBlocked(
                    "all pool candidates were excluded after prior worker "
                    f"failures for {platform}: {', '.join(sorted(excluded))}"
                )
        print(
            "[nemoclaw-ci] candidate workers:",
            ", ".join(candidates) if candidates else "<none>",
            flush=True,
        )
        if not candidates:
            reason = (
                f"no running vss-eval-* candidate for {platform}; "
                f"visible workers: {inventory}"
            )
            remember(reason)
            if time.time() >= deadline:
                raise InfrastructureBlocked(reason + details())
            print(f"[nemoclaw-ci] {reason}; retrying worker selection", flush=True)
            time.sleep(10)
            continue

        for candidate in candidates:
            instance_record = instances_by_name.get(candidate, {})
            if instance_record.get("_registered"):
                _REGISTERED_WORKERS.add(candidate.lower())
            else:
                _REGISTERED_WORKERS.discard(candidate.lower())
            exec_target = _exec_target_for_instance(instance_record) or candidate
            if instance_record.get("_registered"):
                print(
                    f"[nemoclaw-ci] candidate {candidate} using registered "
                    "SSH transport",
                    flush=True,
                )
            if exec_target != candidate:
                print(
                    f"[nemoclaw-ci] candidate {candidate} using Brev exec target {exec_target}",
                    flush=True,
                )
            if not _reachable(candidate, exec_target):
                remember(f"candidate {candidate} failed reachability check")
                print(f"[nemoclaw-ci] skipping unreachable candidate {candidate}", flush=True)
                continue
            lock = _try_acquire_lock(candidate, exec_target)
            if lock is not None:
                return candidate, lock
            remember(f"candidate {candidate} reachable but worker lock unavailable")
            print(f"[nemoclaw-ci] skipping locked candidate {candidate}", flush=True)

        if time.time() >= deadline:
            if explicit:
                raise InfrastructureBlocked(
                    "lock timeout: explicit worker "
                    f"{explicit} for {platform} was not reachable/unlocked "
                    f"after {timeout_s}s{details()}"
                )
            raise InfrastructureBlocked(
                "lock timeout: no reachable unlocked worker for "
                f"{platform} after {timeout_s}s{details()}"
            )
        if explicit:
            print(f"[nemoclaw-ci] waiting for explicit worker lock: {explicit}", flush=True)
        else:
            print("[nemoclaw-ci] all candidates busy; retrying worker selection", flush=True)
        time.sleep(10)


def _remaining_run_timeout(
    deadline: float,
    cap_s: int,
    phase: str,
) -> int:
    """Clamp one attempt to the shared smoke-run deadline."""
    remaining_s = int(deadline - time.monotonic())
    if remaining_s <= 0:
        raise InfrastructureBlocked(
            f"NemoClaw smoke run budget exhausted before {phase}"
        )
    return min(cap_s, remaining_s)


def _stream_command(
    cmd: list[str],
    *,
    timeout_s: int,
    env: dict[str, str],
    log_path: Path,
    abort_event: threading.Event | None = None,
) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    registry_fd, registry_name = tempfile.mkstemp(
        prefix="skill-eval-transport-pgids-",
    )
    os.close(registry_fd)
    registry_path = Path(registry_name)
    child_env = env.copy()
    child_env[worker_pool.TRANSPORT_PGID_REGISTRY_ENV] = str(registry_path)
    proc: subprocess.Popen[str] | None = None
    pgid: int | None = None
    selector: selectors.BaseSelector | None = None
    cleanup_started = False
    external_signal: int | None = None
    previous_handlers: dict[signal.Signals, object] = {}

    def record_external_signal(signum, _frame):  # noqa: ANN001
        nonlocal external_signal
        external_signal = signum

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[sig] = signal.getsignal(sig)
            signal.signal(sig, record_external_signal)
        except ValueError:
            for installed_sig, previous in previous_handlers.items():
                with contextlib.suppress(ValueError):
                    signal.signal(installed_sig, previous)
            previous_handlers.clear()
            break

    try:
        with log_path.open("w", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                cwd=REPO_ROOT,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=child_env,
                bufsize=1,
                start_new_session=True,
            )
            # start_new_session=True makes the Harbor leader's PID its PGID.
            # Preserve it even if the leader exits before a detached Brev/SSH
            # transport so cancellation can still reap the complete tree.
            pgid = proc.pid
            started = time.time()
            last_heartbeat = started
            assert proc.stdout is not None
            selector = selectors.DefaultSelector()
            selector.register(proc.stdout, selectors.EVENT_READ)
            outcome: int | None = None
            cancellation_reason = ""
            while True:
                for key, _ in selector.select(timeout=1):
                    line = key.fileobj.readline()
                    if line:
                        print(line, end="", flush=True)
                        log.write(line)
                        log.flush()
                now = time.time()
                if now - last_heartbeat >= 60:
                    elapsed = int(now - started)
                    heartbeat = f"[nemoclaw-ci] Harbor still running ({elapsed}s elapsed)\n"
                    print(heartbeat, end="", flush=True)
                    log.write(heartbeat)
                    log.flush()
                    last_heartbeat = now
                if external_signal is not None:
                    signal_name = signal.Signals(external_signal).name
                    cancellation_reason = f"external {signal_name}"
                    outcome = 128 + external_signal
                    break
                if abort_event is not None and abort_event.is_set():
                    message = (
                        "[nemoclaw-ci] aborting Harbor after remote worker "
                        "lock loss\n"
                    )
                    print(message, end="", flush=True)
                    log.write(message)
                    log.flush()
                    cancellation_reason = "remote worker lease lost"
                    outcome = 125
                    break
                if proc.poll() is not None:
                    attached_group_live = worker_pool._process_group_exists(pgid)
                    detached_groups = worker_pool._registered_transport_groups(
                        registry_path
                    )
                    if attached_group_live or detached_groups:
                        groups = ", ".join(map(str, detached_groups)) or "none"
                        message = (
                            "[nemoclaw-ci] Harbor leader exited while child "
                            "processes remained; registered transport groups: "
                            f"{groups}; preserving leader exit "
                            f"{proc.returncode}\n"
                        )
                        print(message, end="", flush=True)
                        log.write(message)
                        log.flush()
                        cancellation_reason = (
                            "Harbor leader exited with live child processes; "
                            "reaping descendants without changing its result"
                        )
                        outcome = proc.returncode or 0
                        break
                    for rest in proc.stdout:
                        print(rest, end="", flush=True)
                        log.write(rest)
                    return proc.returncode or 0
                if time.time() - started > timeout_s:
                    message = (
                        "[nemoclaw-ci] Harbor exceeded the "
                        f"{timeout_s}s timeout; terminating process group\n"
                    )
                    print(message, end="", flush=True)
                    log.write(message)
                    log.flush()
                    cancellation_reason = f"outer timeout after {timeout_s}s"
                    outcome = 124
                    break

            assert outcome is not None
            cleanup_started = True
            cleanup_message = (
                f"[nemoclaw-ci] {cancellation_reason}; requesting graceful "
                "Harbor cancellation with SIGINT\n"
            )
            print(cleanup_message, end="", flush=True)
            log.write(cleanup_message)
            log.flush()
            # Once bounded cleanup owns the process tree, a repeated workflow
            # signal must not skip Harbor's recovery pulls and leave remote
            # transports behind. A later hard SIGKILL remains the CI ceiling.
            for sig in previous_handlers:
                signal.signal(sig, signal.SIG_IGN)
            exited = worker_pool._cancel_process_tree(proc, pgid, registry_path)
            if not exited:
                warning = (
                    "[nemoclaw-ci] Harbor tree could not be fully reaped after "
                    "SIGKILL; preserving the primary outcome\n"
                )
                print(warning, end="", flush=True)
                log.write(warning)
                log.flush()
            return outcome
    finally:
        if selector is not None:
            selector.close()
        if proc is not None and pgid is not None and not cleanup_started:
            if (
                worker_pool._process_group_exists(pgid)
                or worker_pool._registered_transport_groups(registry_path)
            ):
                worker_pool._cancel_process_tree(proc, pgid, registry_path)
        if proc is not None and proc.stdout is not None:
            with contextlib.suppress(OSError):
                proc.stdout.close()
        for sig, previous in previous_handlers.items():
            with contextlib.suppress(ValueError):
                signal.signal(sig, previous)
        registry_path.unlink(missing_ok=True)


def _latest_reward(results_root: Path, run_id: str, *, since: float = 0.0) -> tuple[float | None, Path | None]:
    run_root = results_root / run_id
    rewards = sorted(
        (path for path in run_root.glob("*/*/verifier/reward.txt") if path.stat().st_mtime >= since),
        key=lambda p: p.stat().st_mtime,
    )
    if not rewards:
        return None, None
    path = rewards[-1]
    trial_result_path = path.parent.parent / "result.json"
    trial_result = _read_json(trial_result_path)
    if (
        not trial_result
        or "exception_info" not in trial_result
        or trial_result.get("exception_info") is not None
    ):
        return None, path
    try:
        reward = float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None, path
    if not math.isfinite(reward) or not 0.0 <= reward <= 1.0:
        return None, path
    return reward, path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _latest_trial(results_root: Path, run_id: str, *, since: float = 0.0) -> tuple[Path | None, dict[str, Any]]:
    run_root = results_root / run_id
    results = [
        path
        for path in run_root.rglob("result.json")
        if path.stat().st_mtime >= since
        and (
            (path.parent / "verifier" / "reward.txt").exists()
            or (path.parent / "trial.log").exists()
        )
    ]
    results = sorted(results, key=lambda p: p.stat().st_mtime)
    if not results:
        return None, {}
    result_path = results[-1]
    return result_path.parent, _read_json(result_path)


def _attempt_owner_status(
    results_root: Path,
    run_id: str,
    *,
    since: float,
    expected_token: str,
) -> AttemptOwnerStatus:
    """Detect a post-start replacement of this attempt's artifact epoch.

    A legacy eval running from an older PR head may ignore the remote worker
    lease and wipe ``/logs/artifacts`` while this trial is active. Harbor's
    successful blanket artifact manifest plus a missing or changed owner file
    proves that post-start contamination. Incomplete collection remains
    unavailable rather than being guessed as contamination. A matching marker
    does not prove exclusive worker use: an uncoordinated legacy setup that
    wiped the directory before this marker was written can still overlap.
    """

    if re.fullmatch(r"[0-9a-f]{32}", expected_token) is None:
        return AttemptOwnerStatus("unavailable", "invalid expected owner token")

    trial_dir, result = _latest_trial(results_root, run_id, since=since)
    if trial_dir is None or not result:
        return AttemptOwnerStatus("unavailable", "no current trial result")
    environment_setup = result.get("environment_setup")
    if (
        not isinstance(environment_setup, dict)
        or not environment_setup.get("finished_at")
    ):
        return AttemptOwnerStatus(
            "unavailable",
            "trial environment setup did not finish",
        )

    artifacts_root = trial_dir / "artifacts"
    manifest_path = artifacts_root / "manifest.json"
    try:
        if artifacts_root.is_symlink():
            return AttemptOwnerStatus(
                "unavailable",
                "artifact root is a symlink",
            )
        manifest_stat = manifest_path.lstat()
        if not stat.S_ISREG(manifest_stat.st_mode):
            return AttemptOwnerStatus(
                "unavailable",
                "artifact manifest is not a regular file",
            )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return AttemptOwnerStatus("unavailable", "artifact manifest is missing")
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return AttemptOwnerStatus(
            "unavailable",
            f"artifact manifest could not be read: {exc}",
        )
    if not isinstance(manifest, list):
        return AttemptOwnerStatus("unavailable", "artifact manifest is not a list")
    matches = [
        item
        for item in manifest
        if isinstance(item, dict) and item.get("source") == "/logs/artifacts"
    ]
    if len(matches) != 1:
        return AttemptOwnerStatus(
            "unavailable",
            "artifact manifest does not contain one /logs/artifacts entry",
        )
    entry = matches[0]
    if entry.get("status") != "ok" or entry.get("type") != "directory":
        return AttemptOwnerStatus(
            "unavailable",
            "/logs/artifacts collection was not successful",
        )
    destination_text = entry.get("destination")
    if not isinstance(destination_text, str) or not destination_text:
        return AttemptOwnerStatus(
            "unavailable",
            "artifact manifest destination is missing",
        )
    destination = Path(destination_text)
    if (
        destination.is_absolute()
        or any(part in {"", ".", ".."} for part in destination.parts)
    ):
        return AttemptOwnerStatus(
            "unavailable",
            "artifact manifest destination is unsafe",
        )

    cursor = trial_dir
    try:
        for part in destination.parts:
            cursor /= part
            if cursor.is_symlink():
                return AttemptOwnerStatus(
                    "unavailable",
                    "artifact manifest destination traverses a symlink",
                )
        marker_path = cursor / ATTEMPT_OWNER_FILE
        marker_stat = marker_path.lstat()
    except FileNotFoundError:
        return AttemptOwnerStatus(
            "contaminated",
            "attempt owner marker disappeared after successful artifact collection",
        )
    except OSError as exc:
        return AttemptOwnerStatus(
            "unavailable",
            f"attempt owner marker could not be inspected: {exc}",
        )
    if not stat.S_ISREG(marker_stat.st_mode) or marker_stat.st_size > 64:
        return AttemptOwnerStatus(
            "contaminated",
            "attempt owner marker was replaced with malformed content",
        )
    try:
        content = marker_path.read_bytes()
    except OSError as exc:
        return AttemptOwnerStatus(
            "unavailable",
            f"attempt owner marker could not be read: {exc}",
        )
    if content != f"{expected_token}\n".encode():
        return AttemptOwnerStatus(
            "contaminated",
            "attempt owner marker belongs to another worker consumer",
        )
    return AttemptOwnerStatus("verified", "attempt owner marker verified")


def _live_attempt_owner_status(
    remote_target: str,
    expected_token: str,
    remote_executor: remote_worker_lock.RemoteExecutor | None = None,
) -> AttemptOwnerStatus:
    """Verify the owner marker on the worker after Harbor and its verifier."""

    if re.fullmatch(r"[0-9a-f]{32}", expected_token) is None:
        return AttemptOwnerStatus("unavailable", "invalid expected owner token")
    marker = shlex.quote(f"/logs/artifacts/{ATTEMPT_OWNER_FILE}")
    expected = shlex.quote(expected_token)
    verified_marker = "__NEMOCLAW_ATTEMPT_OWNER_VERIFIED__"
    command = (
        f"owner={marker}; expected={expected}; "
        'if [ -L "$owner" ] || [ ! -f "$owner" ]; then exit 44; fi; '
        'size=$(stat -c %s "$owner" 2>/dev/null) || exit 45; '
        '[ "$size" -eq 33 ] || exit 45; '
        'IFS= read -r actual < "$owner" || exit 45; '
        '[ "$actual" = "$expected" ] || exit 46; '
        f"printf '{verified_marker}\\n'"
    )
    last_failure = "live attempt owner probe did not run"
    run_remote = (
        remote_executor
        or _worker_remote_executor(remote_target, remote_target)
    )
    for attempt in range(1, ATTEMPT_OWNER_PROBE_ATTEMPTS + 1):
        try:
            result = run_remote(command, 45)
        except (OSError, subprocess.SubprocessError) as exc:
            last_failure = (
                f"live attempt owner probe raised {type(exc).__name__}: {exc}"
            )
        else:
            if result.returncode in {44, 45}:
                return AttemptOwnerStatus(
                    "contaminated",
                    "live attempt owner marker disappeared or was malformed",
                )
            if result.returncode == 46:
                return AttemptOwnerStatus(
                    "contaminated",
                    "live attempt owner marker belongs to another worker consumer",
                )
            if result.returncode == 0 and verified_marker in (
                result.stdout or ""
            ).splitlines():
                return AttemptOwnerStatus(
                    "verified",
                    "live attempt owner marker verified",
                )
            if result.returncode == 0:
                last_failure = (
                    "live attempt owner probe returned no verification marker"
                )
            else:
                tail = _reachability_failure_text(result)[-240:] or "<no output>"
                last_failure = (
                    "live attempt owner probe failed "
                    f"rc={result.returncode}: {tail}"
                )
        if attempt < ATTEMPT_OWNER_PROBE_ATTEMPTS:
            print(
                "[nemoclaw-ci] WARN: "
                f"{last_failure}; retrying ({attempt}/"
                f"{ATTEMPT_OWNER_PROBE_ATTEMPTS})",
                flush=True,
            )
            time.sleep(ATTEMPT_OWNER_PROBE_RETRY_DELAY_S)
    return AttemptOwnerStatus(
        "unavailable",
        f"{last_failure} after {ATTEMPT_OWNER_PROBE_ATTEMPTS} attempts",
    )


def _attempt_evidence_owner_status(
    results_root: Path,
    run_id: str,
    *,
    since: float,
    remote_target: str,
    expected_token: str,
    remote_executor: remote_worker_lock.RemoteExecutor | None = None,
) -> AttemptOwnerStatus:
    """Require an unchanged artifact epoch at collection and after verifier."""

    collected = _attempt_owner_status(
        results_root,
        run_id,
        since=since,
        expected_token=expected_token,
    )
    if collected.status == "contaminated":
        return collected
    live = _live_attempt_owner_status(
        remote_target,
        expected_token,
        remote_executor,
    )
    if live.status == "contaminated":
        return live
    if collected.status == "verified" and live.status == "verified":
        return AttemptOwnerStatus(
            "verified",
            "collected and live attempt owner markers verified",
        )
    unavailable = "; ".join(
        status.reason
        for status in (collected, live)
        if status.status != "verified"
    )
    return AttemptOwnerStatus("unavailable", unavailable)


def _discard_contaminated_attempt(
    results_root: Path,
    run_id: str,
    *,
    since: float,
) -> tuple[bool, str]:
    """Remove untrusted Harbor result evidence before results publication.

    The runner's plain-text scratch log is intentionally retained for
    diagnosing the contamination and is never promoted as a skill verdict.
    """

    trial_dir, _result = _latest_trial(results_root, run_id, since=since)
    if trial_dir is None:
        return True, "no local contaminated result tree was produced"
    run_root = results_root / run_id
    source = trial_dir.parent if trial_dir.parent != run_root else trial_dir
    try:
        if run_root.is_symlink() or source.is_symlink():
            return False, "contaminated trial path is symlinked"
        run_root_resolved = run_root.resolve(strict=True)
        source_resolved = source.resolve(strict=True)
        source_resolved.relative_to(run_root_resolved)
    except (FileNotFoundError, OSError, ValueError):
        return False, "contaminated trial path is outside the current run"
    if source_resolved == run_root_resolved:
        return False, "refusing to remove the current run root"
    try:
        shutil.rmtree(source_resolved)
    except OSError as exc:
        return False, f"could not remove contaminated trial: {exc}"
    return True, f"removed untrusted trial tree {source_resolved.name}"


_RETRYABLE_WORKER_SETUP_MESSAGES = (
    "stray-agent reap failed on ",
    "claude task scratch cleanup failed on ",
    "repo sync failed on ",
    "NemoClaw setup failed on ",
    "docker runtime reset failed on ",
    "host data purge failed on ",
    "log-dir reset/setup failed on ",
    "Upload dir failed on ",
)

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])"
)
_SETUP_FAILURE_DETAIL_PATTERNS = (
    re.compile(
        r"Cannot safely release the stale OpenShell gateway: [^\r\n]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"Cannot safely migrate legacy NemoClaw state for this gateway port: "
        r"[^\r\n]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"Could not discard DGX Station Express installer resume state: "
        r"[^\r\n]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"Sandbox '[A-Za-z0-9._-]+' was created but did not become ready "
        r"within [0-9]+s\.",
        re.IGNORECASE,
    ),
    re.compile(
        r"reason=ContainerRestarting Container is restarting after a failure",
        re.IGNORECASE,
    ),
    re.compile(
        r"Active NemoClaw gateway release unavailable: [^\r\n]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"OpenShell gateway port [0-9]+ is still busy after scoped release",
        re.IGNORECASE,
    ),
    re.compile(
        r"Cannot install trusted lsof(?:: [^\r\n]+)?",
        re.IGNORECASE,
    ),
)


def _worker_bound_setup_message(first_line: str, instance: str) -> bool:
    if any(
        first_line.startswith(f"{marker}{instance}:")
        for marker in _RETRYABLE_WORKER_SETUP_MESSAGES
    ):
        return True
    return first_line.startswith(
        (
            f"Brev instance '{instance}' not found ",
            f"Brev instance '{instance}' does not meet task requirements:",
            f"Brev instance '{instance}' root disk is ",
            f"Brev instance '{instance}' root disk could not be determined:",
            f"Brev instance '{instance}' Docker storage filesystem is ",
            f"Brev instance '{instance}' Docker storage filesystem could not be determined:",
            f"Brev instance '{instance}' Docker storage has ",
            f"Brev instance '{instance}' Docker storage free space could not be determined:",
            f"Brev instance '{instance}' has NVIDIA driver ",
            f"Cannot reach Brev instance '{instance}':",
            f"Unexpected response from instance '{instance}':",
        )
    )


def _worker_setup_failure_summary(message: str) -> str:
    """Keep a bounded, actionable cause from a multiline setup exception."""
    lines = [
        " ".join(_ANSI_ESCAPE_RE.sub("", line).split())
        for line in message.splitlines()
    ]
    lines = [line for line in lines if line]
    if not lines:
        return ""
    first_line = lines[0]
    details: list[str] = []
    for line in lines[1:]:
        for pattern in _SETUP_FAILURE_DETAIL_PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            detail = _shorten(match.group(0), 280)
            if detail not in details:
                details.append(detail)
            break
        if len(details) == 4:
            break
    if not details:
        return first_line
    return f"{first_line}; details: {'; '.join(details)}"


def _retryable_worker_setup_failure(
    results_root: Path,
    run_id: str,
    *,
    since: float,
    instance: str,
) -> str | None:
    """Return a reason only for Brev failures proven to precede agent setup."""
    _trial_dir, result = _latest_trial(results_root, run_id, since=since)
    if not result:
        return None

    config = result.get("config")
    if not isinstance(config, dict):
        return None
    environment = config.get("environment")
    if not isinstance(environment, dict):
        return None
    if environment.get("import_path") != "envs.brev_env:BrevEnvironment":
        return None

    environment_setup = result.get("environment_setup")
    if (
        not isinstance(environment_setup, dict)
        or not environment_setup.get("started_at")
    ):
        return None
    post_environment_fields = (
        "agent_setup",
        "agent_execution",
        "verifier",
        "agent_result",
        "verifier_result",
    )
    if any(
        field not in result or result[field] is not None
        for field in post_environment_fields
    ):
        return None

    exception_info = result.get("exception_info")
    if not isinstance(exception_info, dict):
        return None
    if exception_info.get("exception_type") != "RuntimeError":
        return None
    message = str(exception_info.get("exception_message") or "")
    traceback = str(exception_info.get("exception_traceback") or "")
    if "envs/brev_env.py" not in traceback or "in start" not in traceback:
        return None
    first_line = message.splitlines()[0] if message else ""
    if not _worker_bound_setup_message(first_line, instance):
        return None
    return _worker_setup_failure_summary(message)


def _parse_iso(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "-"
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {sec}s"


def _duration_from_result(result: dict[str, Any]) -> tuple[str, str, str]:
    started = (
        result.get("trial_started_at")
        or result.get("started_at")
        or result.get("start_time")
        or result.get("start")
    )
    finished = (
        result.get("trial_finished_at")
        or result.get("finished_at")
        or result.get("end_time")
        or result.get("end")
    )
    start_dt = _parse_iso(str(started)) if started else None
    finish_dt = _parse_iso(str(finished)) if finished else None
    duration = (
        (finish_dt - start_dt).total_seconds()
        if start_dt is not None and finish_dt is not None
        else None
    )
    return str(started or "-"), str(finished or "-"), _format_duration(duration)


def _format_number(value: int | float | None) -> str:
    if value is None:
        return "-"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}"


def _iter_json_objects_from_log(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    decoder = json.JSONDecoder()
    index = 0
    while index < len(text):
        start = text.find("{", index)
        if start < 0:
            break
        try:
            parsed, offset = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            index = start + 1
            continue
        if isinstance(parsed, dict):
            yield parsed
        index = start + max(offset, 1)


def _usage_from_mapping(data: dict[str, Any]) -> tuple[int, int]:
    prompt_tokens = int(
        data.get("inputTokens")
        or data.get("input_tokens")
        or data.get("input")
        or data.get("prompt_tokens")
        or data.get("promptTokens")
        or 0
    )
    cached_tokens = int(
        data.get("cacheReadInputTokens")
        or data.get("cache_read_input_tokens")
        or data.get("cacheRead")
        or 0
    )
    cached_tokens += int(
        data.get("cacheCreationInputTokens")
        or data.get("cache_creation_input_tokens")
        or data.get("cacheWrite")
        or 0
    )
    return prompt_tokens, cached_tokens


def _prompt_chars_from_openclaw_meta(meta: dict[str, Any]) -> int:
    """Estimate the prompt size when OpenClaw omits provider token usage."""
    total_chars = 0
    report = meta.get("systemPromptReport")
    if isinstance(report, dict):
        system_prompt = report.get("systemPrompt")
        if isinstance(system_prompt, dict):
            total_chars += int(system_prompt.get("chars") or 0)
        skills = report.get("skills")
        if isinstance(skills, dict):
            total_chars += int(skills.get("promptChars") or 0)
    final_prompt = meta.get("finalPromptText")
    if isinstance(final_prompt, str):
        total_chars += len(final_prompt)
    return total_chars


def _format_estimated_tokens(chars: int) -> str:
    if chars <= 0:
        return "n/a"
    return f"~{_format_number(math.ceil(chars / 4))}"


def _load_openclaw_log_metrics(trial_dir: Path) -> tuple[str, str, str] | None:
    candidates = [
        trial_dir / "artifacts" / "nemoclaw" / "openclaw-agent.log",
        trial_dir / "artifacts" / "artifacts" / "nemoclaw" / "openclaw-agent.log",
    ]
    turns = 0
    prompt_tokens = 0
    cached_tokens = 0
    estimated_prompt_chars = 0
    saw_usage = False
    for log_path in candidates:
        for event in _iter_json_objects_from_log(log_path):
            role = event.get("role")
            event_type = str(event.get("type") or event.get("event") or "")
            if role == "assistant" or "assistant" in event_type:
                turns += 1

            result = event.get("result")
            if isinstance(result, dict):
                payloads = result.get("payloads")
                if isinstance(payloads, list):
                    turns += len([payload for payload in payloads if isinstance(payload, dict)])
                meta = result.get("meta")
                if isinstance(meta, dict):
                    estimated_prompt_chars = max(
                        estimated_prompt_chars,
                        _prompt_chars_from_openclaw_meta(meta),
                    )
                agent_meta = meta.get("agentMeta") if isinstance(meta, dict) else None
                last_call_usage = (
                    agent_meta.get("lastCallUsage")
                    if isinstance(agent_meta, dict)
                    else None
                )
                if isinstance(last_call_usage, dict):
                    saw_usage = True
                    prompt, cached = _usage_from_mapping(last_call_usage)
                    prompt_tokens += prompt
                    cached_tokens += cached

            usage = event.get("usage")
            if isinstance(usage, dict):
                saw_usage = True
                prompt, cached = _usage_from_mapping(usage)
                prompt_tokens += prompt
                cached_tokens += cached

            model_usage = event.get("modelUsage") or event.get("model_usage")
            if isinstance(model_usage, dict):
                for usage_value in model_usage.values():
                    if not isinstance(usage_value, dict):
                        continue
                    saw_usage = True
                    prompt, cached = _usage_from_mapping(usage_value)
                    prompt_tokens += prompt
                    cached_tokens += cached

    if not turns and not saw_usage:
        return None
    if saw_usage and not prompt_tokens and estimated_prompt_chars:
        return (
            str(turns) if turns else "n/a",
            _format_estimated_tokens(estimated_prompt_chars),
            _format_number(cached_tokens),
        )
    return (
        str(turns) if turns else "n/a",
        _format_number(prompt_tokens) if saw_usage else "n/a",
        _format_number(cached_tokens) if saw_usage else "n/a",
    )


def _nemoclaw_artifact_dir(trial_dir: Path | None) -> Path | None:
    if trial_dir is None:
        return None
    candidates = [
        trial_dir / "artifacts" / "nemoclaw",
        trial_dir / "artifacts" / "artifacts" / "nemoclaw",
    ]
    return next((path for path in candidates if path.is_dir()), None)


def _load_nemoclaw_async_metrics(trial_dir: Path) -> tuple[str, str, str] | None:
    artifact_dir = _nemoclaw_artifact_dir(trial_dir)
    if artifact_dir is None:
        return None
    hooks = _read_json(artifact_dir / "nemoclaw_hooks_response.json")
    response = hooks.get("response") if isinstance(hooks, dict) else None
    body = response.get("body") if isinstance(response, dict) else None
    if not isinstance(body, dict) or body.get("mode") != "cli-async":
        return None
    return "async readiness", "not emitted", "not emitted"


def _metrics_include_usage(metrics: tuple[str, str, str] | None) -> bool:
    return bool(metrics and (metrics[1] != "n/a" or metrics[2] != "n/a"))


def _metrics_are_zero_usage(metrics: tuple[str, str, str] | None) -> bool:
    return bool(metrics and metrics[1] == "0" and metrics[2] == "0")


def _metrics_are_estimated_usage(metrics: tuple[str, str, str] | None) -> bool:
    return bool(metrics and metrics[1].startswith("~"))


def _wait_for_nemoclaw_metrics(trial_dir: Path | None, timeout_s: float = 30.0) -> None:
    if trial_dir is None:
        return
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        metrics = _load_openclaw_log_metrics(trial_dir)
        if metrics is not None:
            return
        if _load_nemoclaw_async_metrics(trial_dir) is not None:
            return
        time.sleep(2)


def _nemoclaw_runtime_details(trial_dir: Path | None) -> list[str]:
    artifact_dir = _nemoclaw_artifact_dir(trial_dir)
    if artifact_dir is None:
        return []
    hooks = _read_json(artifact_dir / "nemoclaw_hooks_response.json")
    response = hooks.get("response") if isinstance(hooks, dict) else None
    body = response.get("body") if isinstance(response, dict) else None
    if not isinstance(body, dict) or body.get("mode") != "cli-async":
        return []

    details = ["- OpenClaw completion mode: `async readiness`"]
    elapsed = hooks.get("elapsed_s")
    if isinstance(elapsed, (int, float)):
        details.append(f"- Readiness wait: `{_format_duration(float(elapsed))}`")

    wait: Any = []
    try:
        wait = json.loads((artifact_dir / "nemoclaw_wait.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        wait = []
    if isinstance(wait, list):
        details.append(f"- Readiness polls: `{len(wait)}`")

    details.append(
        "- OpenClaw turn/token metrics: `not emitted`; the fast path stops after "
        "VSS readiness instead of waiting for OpenClaw's final answer."
    )
    return details


def _load_trajectory_metrics(trial_dir: Path | None, result: dict[str, Any]) -> tuple[str, str, str]:
    openclaw_metrics = _load_openclaw_log_metrics(trial_dir) if trial_dir is not None else None
    agent_result = result.get("agent_result") if isinstance(result, dict) else None
    if isinstance(agent_result, dict):
        prompt = agent_result.get("n_input_tokens")
        cached = agent_result.get("n_cache_tokens")
        if (
            (_metrics_are_zero_usage(openclaw_metrics) or _metrics_are_estimated_usage(openclaw_metrics))
            and ((prompt or 0) or (cached or 0))
        ):
            turns = openclaw_metrics[0] if openclaw_metrics and openclaw_metrics[0] != "n/a" else "n/a"
            return turns, _format_number(prompt), _format_number(cached)
        if not _metrics_include_usage(openclaw_metrics) and (prompt is not None or cached is not None):
            turns = openclaw_metrics[0] if openclaw_metrics and openclaw_metrics[0] != "n/a" else "n/a"
            return turns, _format_number(prompt), _format_number(cached)

    if _metrics_include_usage(openclaw_metrics):
        return openclaw_metrics

    if trial_dir is None:
        return "n/a", "n/a", "n/a"
    trajectory = trial_dir / "agent" / "trajectory.json"
    data = _read_json(trajectory)
    if not data:
        return (
            _load_nemoclaw_async_metrics(trial_dir)
            or openclaw_metrics
            or ("n/a", "n/a", "n/a")
        )

    steps = data.get("steps")
    turns = 0
    prompt_tokens = 0
    cached_tokens = 0
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            message = step.get("message")
            if isinstance(message, str):
                try:
                    message = json.loads(message)
                except json.JSONDecodeError:
                    continue
            if not isinstance(message, dict) or message.get("type") != "assistant":
                continue
            turns += 1
            usage = (message.get("message") or {}).get("usage")
            if isinstance(usage, dict):
                prompt_tokens += int(usage.get("input_tokens") or 0)
                cached_tokens += int(usage.get("cache_read_input_tokens") or 0)
                cached_tokens += int(usage.get("cache_creation_input_tokens") or 0)

    final_metrics = data.get("final_metrics") or {}
    model_usage = final_metrics.get("modelUsage") if isinstance(final_metrics, dict) else None
    if isinstance(model_usage, dict):
        prompt_tokens = 0
        cached_tokens = 0
        for usage in model_usage.values():
            if not isinstance(usage, dict):
                continue
            prompt_tokens += int(usage.get("inputTokens") or 0)
            cached_tokens += int(usage.get("cacheReadInputTokens") or 0)
            cached_tokens += int(usage.get("cacheCreationInputTokens") or 0)

    metrics = (
        openclaw_metrics[0] if openclaw_metrics and openclaw_metrics[0] != "n/a" else str(turns) if turns else "n/a",
        _format_number(prompt_tokens) if prompt_tokens else "n/a",
        _format_number(cached_tokens) if cached_tokens else "n/a",
    )
    if not _metrics_include_usage(metrics):
        return _load_nemoclaw_async_metrics(trial_dir) or openclaw_metrics or metrics
    return metrics


def _judge_details(trial_dir: Path | None, reward: float | None) -> tuple[int | None, int | None, list[str]]:
    if trial_dir is None:
        return None, None, []
    details = _read_json(trial_dir / "verifier" / "judge.json")
    total = details.get("total")
    passed = details.get("passed")
    checks = details.get("checks")
    failures: list[str] = []
    if isinstance(checks, list):
        for idx, check in enumerate(checks, start=1):
            if not isinstance(check, dict) or bool(check.get("pass")):
                continue
            check_text = str(check.get("check") or f"Check {idx}")
            rationale = str(check.get("rationale") or check.get("matched") or "no rationale recorded")
            failures.append(f"**Check {idx}** ({check_text}) - {rationale}")
    if isinstance(total, int) and isinstance(passed, int):
        return passed, total, failures
    if reward is not None and isinstance(total, int):
        return int(round(reward * total)), total, failures
    return None, None, failures


def _md_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _shorten(value: str, limit: int = 500) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _copy_viewer_snapshot(
    *,
    results_root: Path,
    run_id: str,
    scenario: NemoClawScenario,
    trial_dir: Path | None,
) -> str | None:
    brev_env_id = _coordinator_brev_env_id()
    if not brev_env_id or trial_dir is None:
        return None
    run_root = results_root / run_id
    source = trial_dir.parent if trial_dir.parent != run_root else trial_dir
    if not source.exists():
        return None
    viewer_name = f"nemoclaw__{_scenario_id(scenario)}__{run_id}__{source.name}"
    viewer_dir = results_root / "_viewer" / viewer_name
    shutil.rmtree(viewer_dir, ignore_errors=True)
    shutil.copytree(source, viewer_dir)
    return f"https://harbor-{brev_env_id}.brevlab.com/jobs/{quote(viewer_name, safe='')}"


def _coordinator_brev_env_id() -> str:
    value = os.environ.get("BREV_ENV_ID", "").strip()
    if value:
        return value
    try:
        for line in Path("/etc/environment").read_text(encoding="utf-8").splitlines():
            if not line.startswith("BREV_ENV_ID="):
                continue
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        return ""
    return ""


def _github_run_url(run_id: str) -> str | None:
    repo = os.environ.get("PR_REPO") or os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        return None
    return f"https://github.com/{repo}/actions/runs/{run_id}"


def _scenario_id(scenario: NemoClawScenario) -> str:
    return "__".join(
        _safe_slug(part)
        for part in (
            scenario.skill,
            scenario.spec_name,
            scenario.platform,
            scenario.task_name,
        )
    )


def _write_benchmark_input(run_id: str, scenario_id: str, body: str) -> None:
    scratch = SCRATCH_ROOT / run_id
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"pr-nemoclaw-{scenario_id}.md").write_text(body, encoding="utf-8")
    benchmark = scratch / "benchmark.md"
    if benchmark.exists():
        with benchmark.open("a", encoding="utf-8") as handle:
            handle.write("\n---\n\n")
            handle.write(body.rstrip())
            handle.write("\n")
        return
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    benchmark.write_text(
        "# Skills Eval Benchmark - NemoClaw sweep\n\n"
        f"Generated: {generated}\n\n"
        "---\n\n"
        f"{body.rstrip()}\n",
        encoding="utf-8",
    )


def _scenario_label(scenario: NemoClawScenario) -> str:
    return "/".join(
        (
            scenario.skill,
            scenario.spec_name,
            scenario.platform,
            scenario.task_name,
        )
    )


def _append_eval_row_completion(
    *,
    run_id: str,
    planned: int,
    executed: int,
    skipped: list[tuple[str, str]],
    unexecuted_reason: str = "row execution ended before the remaining scenarios ran",
) -> None:
    """Seal a controlled eval return so partial external timeouts fail closed."""

    benchmark = SCRATCH_ROOT / run_id / "benchmark.md"
    if benchmark.is_file() and EVAL_ROW_COMPLETION_MARKER in benchmark.read_text(
        encoding="utf-8"
    ):
        return

    accounted_skips = len(skipped)
    unaccounted = max(0, planned - executed - accounted_skips)
    skipped_total = accounted_skips + unaccounted
    lines = [
        "## NemoClaw row completion",
        "",
        f"- Planned scenarios: `{planned}`",
        f"- Executed scenarios: `{executed}`",
        f"- Skipped scenarios: `{skipped_total}`",
    ]
    if skipped or unaccounted:
        lines.extend(("", "### Skipped scenarios", ""))
        for label, reason in skipped:
            safe_label = label.replace("`", "'")
            safe_reason = " ".join(reason.replace("`", "'").split())
            lines.append(f"- `{safe_label}` — {safe_reason}")
        if unaccounted:
            safe_reason = " ".join(unexecuted_reason.replace("`", "'").split())
            lines.append(
                f"- `{unaccounted} remaining scenario(s)` — {safe_reason}"
            )
    lines.extend(("", EVAL_ROW_COMPLETION_MARKER, ""))
    _write_benchmark_input(run_id, "row-completion", "\n".join(lines))


def _append_harbor_report(
    *,
    scenario: NemoClawScenario,
    instance: str,
    results_root: Path,
    run_id: str,
    reward: float | None,
    harbor_rc: int,
    log_path: Path,
    since: float = 0.0,
) -> None:
    trial_dir, result = _latest_trial(results_root, run_id, since=since)
    started, finished, duration = _duration_from_result(result)
    if started == "-" and trial_dir is not None:
        started = dt.datetime.fromtimestamp(trial_dir.stat().st_mtime, dt.timezone.utc).isoformat()
    if finished == "-" and trial_dir is not None:
        finished = dt.datetime.fromtimestamp(trial_dir.stat().st_mtime, dt.timezone.utc).isoformat()
    _wait_for_nemoclaw_metrics(trial_dir)
    turns, prompt_tokens, cached_tokens = _load_trajectory_metrics(trial_dir, result)
    passed, total, failures = _judge_details(trial_dir, reward)
    trace_url = _copy_viewer_snapshot(
        results_root=results_root,
        run_id=run_id,
        scenario=scenario,
        trial_dir=trial_dir,
    )
    if trace_url:
        trace_cell = f"[trace]({trace_url})"
    elif run_url := _github_run_url(run_id):
        trace_cell = f"[artifacts]({run_url})"
    else:
        trace_cell = "n/a"
    status_ok = reward is not None and reward >= 1.0 and harbor_rc == 0
    result_prefix = "PASS" if status_ok else "FAIL"
    reward_text = f"{reward:.3g}" if reward is not None else "missing"
    if passed is not None and total is not None:
        result_text = f"{result_prefix} {reward_text} ({passed}/{total})"
    else:
        result_text = f"{result_prefix} {reward_text}"

    head_sha = os.environ.get("PR_HEAD_SHA", "")
    head = head_sha[:8] if head_sha else "unknown"
    spec_path = str(scenario.spec_path.relative_to(REPO_ROOT))
    body = [
        f"## Harbor Eval - `{spec_path}`",
        "",
        (
            f"Head: `{head}` - skill `{scenario.skill}` - task `{scenario.task_name}` - "
            f"platform `{scenario.platform}` - instance `{instance}` - runtime `NemoClaw/OpenClaw`"
        ),
        f"First started: `{started}` - Last finished: `{finished}` - Total: `{duration}`",
        "",
        "| Platform | Result | Reward | Duration | Turns | Prompt tok | Cached tok | Trace |",
        "|---|---|---|---|---|---|---|---|",
        (
            f"| {_md_cell(scenario.platform)} | {_md_cell(result_text)} | {_md_cell(reward_text)} | "
            f"{_md_cell(duration)} | {_md_cell(turns)} | {_md_cell(prompt_tokens)} | "
            f"{_md_cell(cached_tokens)} | {trace_cell} |"
        ),
        "",
        "### NemoClaw runtime details",
        "",
        f"- Worker: `{instance}`",
        f"- Skill: `{scenario.skill}`",
        f"- Spec: `{spec_path}`",
        f"- Harbor task: `{scenario.task_name}`",
        "- Runtime path: Harbor launcher -> NemoClaw/OpenClaw -> VSS Orchestrator MCP",
        f"- Harbor exit code: `{harbor_rc}`",
        f"- Harbor log: `{log_path}`",
    ]
    if trial_dir is not None:
        body.append(f"- Trial artifacts: `{trial_dir}`")
    if prompt_tokens.startswith("~"):
        body.append(
            "- Prompt tok source: estimated from OpenClaw prompt-size fields because "
            "provider token usage was not emitted."
        )
    body.extend(_nemoclaw_runtime_details(trial_dir))
    if failures:
        body.extend(["", "### Failing checks", ""])
        body.extend(f"- {_shorten(item)}" for item in failures[:10])
        if len(failures) > 10:
            body.append(f"- ... {len(failures) - 10} additional failing checks omitted")
    body.append("")
    report = "\n".join(body)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)
            handle.write("\n")
    else:
        print(report, flush=True)
    _write_benchmark_input(run_id, _scenario_id(scenario), report)


def _append_blocked_summary(*, reason: str, scenario: str, scenario_id: str = "blocked") -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    body = [
        "## NemoClaw VSS Skill Eval",
        "",
        "- Status: `BLOCKED`",
        f"- Scenario: `{scenario}`",
        f"- Reason: `{reason}`",
        "",
        "This is an infrastructure/capacity blocker, not a skill regression.",
        "",
    ]
    report = "\n".join(body)
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(report)
    else:
        print(report, flush=True)
    _write_benchmark_input(os.environ.get("GITHUB_RUN_ID", "local"), scenario_id, report)


def _harbor_command(scenario: NemoClawScenario, results_root: Path, run_id: str) -> list[str]:
    uvx = _ensure_uvx()
    model = os.environ.get("ANTHROPIC_MODEL", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").rstrip("/")
    env_build_timeout = os.environ.get(
        "NEMOCLAW_ENVIRONMENT_BUILD_TIMEOUT_MULTIPLIER",
        "6.0",
    )
    if not model:
        raise RuntimeError("ANTHROPIC_MODEL is required")
    if not base_url:
        raise RuntimeError("ANTHROPIC_BASE_URL is required")
    api_base = base_url if base_url.endswith("/v1") else f"{base_url}/v1"
    return [
        uvx,
        "--python",
        sys.executable,
        "--from",
        worker_pool.HARBOR_REQUIREMENT,
        "harbor",
        "run",
        "--environment-import-path",
        "envs.brev_env:BrevEnvironment",
        "-p",
        str(scenario.harbor_path),
        "--include-task-name",
        scenario.task_name,
        "-a",
        "claude-code",
        "--model",
        model,
        "--ak",
        f"api_base={api_base}",
        "--ae",
        "CLAUDE_CODE_DISABLE_THINKING=1",
        "--environment-build-timeout-multiplier",
        env_build_timeout,
        "--agent-timeout-multiplier",
        "6.0",
        "--verifier-timeout-multiplier",
        "3.0",
        "--max-retries",
        "0",
        "-n",
        "1",
        "--yes",
        "-o",
        str(results_root / run_id),
    ]


def _ensure_uvx() -> str:
    """Return a usable uvx binary, installing uv into ~/.local/bin if needed."""
    user_bin = str(Path.home() / ".local" / "bin")
    os.environ["PATH"] = f"{user_bin}:{os.environ.get('PATH', '')}"
    found = shutil.which("uvx")
    if found:
        return found
    print("[nemoclaw-ci] uvx not found; installing uv with pip --user", flush=True)
    result = _run(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet", "uv"],
        timeout=180,
        env=os.environ.copy(),
    )
    if result.returncode != 0:
        raise RuntimeError(f"failed to install uv: {result.stderr[-1000:]}")
    found = shutil.which("uvx")
    if not found:
        raise RuntimeError("uv install completed but uvx is still not on PATH")
    return found


def main(argv: list[str] | None = None) -> int:
    global SCRATCH_ROOT
    if sys.version_info[:2] != worker_pool.SKILL_EVAL_PYTHON_VERSION:
        expected = ".".join(map(str, worker_pool.SKILL_EVAL_PYTHON_VERSION))
        found = ".".join(map(str, sys.version_info[:2]))
        print(
            f"FATAL: NemoClaw skill eval requires Python {expected}.x; found {found}",
            file=sys.stderr,
        )
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills", default=os.environ.get("MANUAL_SKILLS_FILTER", DEFAULT_SKILL))
    parser.add_argument("--spec", default=os.environ.get("NEMOCLAW_EVAL_SPEC", ""))
    parser.add_argument("--profile", default=os.environ.get("NEMOCLAW_EVAL_PROFILE", DEFAULT_PROFILE))
    parser.add_argument("--platform", default=os.environ.get("NEMOCLAW_EVAL_PLATFORM", ""))
    parser.add_argument("--gpu-count", type=int, default=None)
    parser.add_argument("--instance", default=os.environ.get("NEMOCLAW_BREV_INSTANCE"))
    parser.add_argument("--lock-timeout", type=int, default=int(os.environ.get("NEMOCLAW_LOCK_TIMEOUT_SEC", "600")))
    parser.add_argument(
        "--harbor-timeout",
        type=int,
        default=int(
            os.environ.get(
                "NEMOCLAW_HARBOR_TIMEOUT_SEC",
                str(worker_pool.DEFAULT_HARBOR_TIMEOUT_SEC),
            )
        ),
    )
    parser.add_argument("--dataset-root", default=str(DEFAULT_DATASET_ROOT))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--scratch-root", default=os.environ.get("NEMOCLAW_SCRATCH_ROOT", str(SCRATCH_ROOT)))
    parser.add_argument("--task-limit", type=int, default=int(os.environ.get("NEMOCLAW_TASK_LIMIT", "0") or "0"))
    parser.add_argument("--print-matrix", action="store_true")
    parser.add_argument(
        "--all-specs",
        action="store_true",
        default=os.environ.get("NEMOCLAW_ALL_SPECS", "").strip().lower() in {"1", "true", "yes"},
        help="For skills='*', include every spec/platform instead of one representative scenario per skill.",
    )
    args = parser.parse_args(argv)

    run_id = os.environ.get("GITHUB_RUN_ID", f"manual-{int(time.time())}")
    dataset_root = Path(args.dataset_root)
    results_root = Path(args.results_root)
    SCRATCH_ROOT = Path(args.scratch_root)

    all_skills, requested_skills = _skill_filters(args.skills)
    spec_filter = args.spec.strip() or None
    default_single_smoke = not spec_filter and not all_skills and requested_skills in ([], [DEFAULT_SKILL])
    platform_filter = args.platform or (DEFAULT_PLATFORM if default_single_smoke else None)
    profile_filter = args.profile if default_single_smoke else None
    representative_matrix = not spec_filter and not args.all_specs

    if args.print_matrix:
        rows, blockers = _build_matrix(
            skills_filter=args.skills,
            profile_filter=profile_filter,
            platform_filter=platform_filter,
            spec_filter=spec_filter,
            representative_per_skill=representative_matrix,
            include_blocked_rows=representative_matrix,
        )
        _print_matrix(rows, blockers)
        return 0 if rows else 2

    try:
        worker_pool.validate_harbor_timeout_sec(args.harbor_timeout)
    except ValueError as exc:
        parser.error(str(exc))

    run_timeout_s = max(
        1,
        int(
            os.environ.get(
                "NEMOCLAW_RUN_TIMEOUT_SEC",
                str(args.lock_timeout + args.harbor_timeout),
            )
        ),
    )
    run_deadline = time.monotonic() + run_timeout_s
    os.environ["SKILLS_EVAL_RUNNER"] = "nemoclaw"
    os.environ["PYTHONPATH"] = f"{SKILL_EVAL_ROOT}:{os.environ.get('PYTHONPATH', '')}"
    SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    _cleanup_results(results_root, run_id)

    scenarios: list[NemoClawScenario] = []
    executed = 0
    skipped_scenarios: list[tuple[str, str]] = []

    def record_remaining_group_skips(
        group: list[NemoClawScenario],
        current_index: int,
        reason: str,
    ) -> int:
        remaining = group[current_index + 1 :]
        skipped_scenarios.extend(
            (_scenario_label(item), reason) for item in remaining
        )
        return len(remaining)

    try:
        scenarios, blockers = _discover_scenarios(
            skills_filter=args.skills,
            profile_filter=profile_filter,
            platform_filter=platform_filter,
            spec_filter=spec_filter,
            dataset_root=dataset_root,
            task_limit=args.task_limit if args.task_limit > 0 else None,
        )
        for blocker in blockers:
            print(f"[nemoclaw-ci] blocked coverage item: {blocker}", flush=True)
            _append_blocked_summary(
                reason=blocker,
                scenario="coverage discovery",
                scenario_id=f"blocked-{_safe_slug(blocker)[:80]}",
            )
        if not scenarios:
            raise RuntimeError("no NemoClaw scenarios were generated")

        groups = _scenario_groups(scenarios)
        print(
            f"[nemoclaw-ci] generated {len(scenarios)} NemoClaw scenario(s) "
            f"in {len(groups)} worker group(s)",
            flush=True,
        )
        failures: list[str] = []
        scenario_index = 0
        configured_setup_failovers = os.environ.get(
            "NEMOCLAW_MAX_WORKER_FAILOVERS",
            "",
        ).strip()
        max_setup_failovers = (
            max(0, int(configured_setup_failovers))
            if configured_setup_failovers
            else None
        )
        worker_failover_window_s = max(
            0,
            int(os.environ.get("NEMOCLAW_WORKER_FAILOVER_WINDOW_SEC", "1200")),
        )
        max_contamination_failovers = max(
            0,
            int(os.environ.get("NEMOCLAW_MAX_CONTAMINATION_FAILOVERS", "1")),
        )
        for group_index, group in enumerate(groups, start=1):
            first = group[0]
            gpu_count = (
                args.gpu_count
                if args.gpu_count is not None
                else int(os.environ["NEMOCLAW_EVAL_GPU_COUNT"])
                if os.environ.get("NEMOCLAW_EVAL_GPU_COUNT")
                else first.gpu_count
            )
            group_steps = ", ".join(scenario.task_name for scenario in group)
            print(
                "[nemoclaw-ci] worker group "
                f"{group_index}/{len(groups)}: {first.skill}/{first.spec_name}/"
                f"{first.platform} gpu_count={gpu_count} steps=[{group_steps}]",
                flush=True,
            )
            excluded_instances: set[str] = set()
            setup_failovers = 0
            setup_failures_by_instance: dict[str, str] = {}

            def with_setup_failure_context(reason: str) -> str:
                if not setup_failures_by_instance:
                    return reason
                failure_details = "; ".join(
                    f"{name}: {_shorten(failure, 240)}"
                    for name, failure in setup_failures_by_instance.items()
                )
                return (
                    f"{reason}; pre-agent setup failures: "
                    f"{failure_details}"
                )

            contamination_failovers = 0
            worker_failover_deadline = (
                time.monotonic() + worker_failover_window_s
            )
            reserved_retry_harbor_budget_s = 0.0
            while True:
                selection_deadline = (
                    run_deadline - reserved_retry_harbor_budget_s
                )
                try:
                    selection_timeout_s = _remaining_run_timeout(
                        selection_deadline,
                        args.lock_timeout,
                        (
                            "replacement worker selection"
                            if reserved_retry_harbor_budget_s
                            else "worker selection"
                        ),
                    )
                    instance, worker_lock = _select_and_lock_instance(
                        first.platform,
                        gpu_count,
                        args.instance,
                        selection_timeout_s,
                        excluded=excluded_instances,
                    )
                except InfrastructureBlocked as exc:
                    if not setup_failures_by_instance:
                        raise
                    raise InfrastructureBlocked(
                        with_setup_failure_context(str(exc))
                    ) from exc
                required_retry_harbor_budget_s = (
                    reserved_retry_harbor_budget_s
                )
                reserved_retry_harbor_budget_s = 0.0
                print(f"[nemoclaw-ci] selected worker: {instance}", flush=True)
                # Use the human-readable Brev name for Harbor itself. Instance
                # IDs are useful for lock/reachability checks on some runners,
                # but they have proven unreliable as long-lived `brev exec`
                # targets during Harbor environment setup.
                brev_instance = instance
                retry_group = False
                try:
                    if (
                        required_retry_harbor_budget_s
                        and run_deadline - time.monotonic()
                        < required_retry_harbor_budget_s
                    ):
                        raise InfrastructureBlocked(
                            with_setup_failure_context(
                                "replacement worker selection consumed the "
                                "Harbor retry budget"
                            )
                        )
                    os.environ["BREV_INSTANCE"] = brev_instance
                    for group_scenario_index, scenario in enumerate(group):
                        print(
                            "[nemoclaw-ci] scenario "
                            f"{scenario_index + 1}/{len(scenarios)}: "
                            f"{scenario.skill}/{scenario.spec_name}/"
                            f"{scenario.platform}/{scenario.task_name}",
                            flush=True,
                        )
                        scenario_started = time.time()
                        scenario_started_monotonic = time.monotonic()
                        log_path = (
                            SCRATCH_ROOT
                            / run_id
                            / "harbor"
                            / (
                                f"{_scenario_id(scenario)}-"
                                f"{_safe_slug(instance)}.log"
                            )
                        )
                        harbor_env = os.environ.copy()
                        harbor_env["BREV_INSTANCE"] = brev_instance
                        attempt_owner_token = uuid.uuid4().hex
                        harbor_env[ATTEMPT_OWNER_ENV] = attempt_owner_token
                        heartbeat_lost_event = (
                            worker_lock.heartbeat.lost_event
                            if worker_lock.heartbeat
                            else None
                        )
                        harbor_timeout_s = _remaining_run_timeout(
                            run_deadline,
                            args.harbor_timeout,
                            "Harbor execution",
                        )
                        if (
                            harbor_timeout_s
                            <= worker_pool.MIN_HARBOR_BACKSTOP_SEC
                        ):
                            reason = (
                                "remaining smoke-run budget cannot safely "
                                "start Harbor: "
                                f"{harbor_timeout_s}s available, must be "
                                "greater than "
                                f"{worker_pool.MIN_HARBOR_BACKSTOP_SEC}s"
                            )
                            print(
                                f"BLOCKED: {reason}",
                                file=sys.stderr,
                                flush=True,
                            )
                            _append_blocked_summary(
                                reason=reason,
                                scenario=(
                                    f"{scenario.skill}/{scenario.spec_name}/"
                                    f"{scenario.platform}/{scenario.task_name}"
                                ),
                                scenario_id=(
                                    f"{_scenario_id(scenario)}-"
                                    "insufficient-harbor-budget"
                                ),
                            )
                            executed += 1
                            scenario_index += 1
                            failures.append(reason)
                            scenario_index += record_remaining_group_skips(
                                group,
                                group_scenario_index,
                                "skipped because the remaining run budget "
                                "cannot safely start Harbor",
                            )
                            break
                        if (
                            required_retry_harbor_budget_s
                            and harbor_timeout_s
                            < math.ceil(required_retry_harbor_budget_s)
                        ):
                            raise InfrastructureBlocked(
                                with_setup_failure_context(
                                    "replacement worker selection left "
                                    f"{harbor_timeout_s}s for Harbor; required "
                                    f"{math.ceil(required_retry_harbor_budget_s)}s"
                                )
                            )
                        required_retry_harbor_budget_s = 0.0
                        cmd = _harbor_command(scenario, results_root, run_id)
                        print(
                            "[nemoclaw-ci] running Harbor:",
                            " ".join(cmd),
                            flush=True,
                        )
                        harbor_rc = _stream_command(
                            cmd,
                            timeout_s=harbor_timeout_s,
                            env=harbor_env,
                            log_path=log_path,
                            abort_event=heartbeat_lost_event,
                        )
                        if (
                            heartbeat_lost_event is not None
                            and heartbeat_lost_event.is_set()
                        ):
                            harbor_rc = 125

                        reward, _reward_path = _latest_reward(
                            results_root,
                            run_id,
                            since=scenario_started,
                        )
                        setup_failure = (
                            _retryable_worker_setup_failure(
                                results_root,
                                run_id,
                                since=scenario_started,
                                instance=instance,
                            )
                            if reward is None
                            else None
                        )
                        if setup_failure is not None:
                            setup_failures_by_instance[instance] = setup_failure
                            # A setup failure can occur before brev_env.start()
                            # resets /logs/artifacts. Treat collected-only
                            # ownership as the publication boundary: unlike the
                            # normal post-agent path, no live remote probe is
                            # needed to decide whether the local Harbor tree is
                            # safe to retain. Any unverified tree is discarded
                            # before failover or workflow artifact collection.
                            setup_owner_status = _attempt_owner_status(
                                results_root,
                                run_id,
                                since=scenario_started,
                                expected_token=attempt_owner_token,
                            )
                            if setup_owner_status.status != "verified":
                                discarded, discard_reason = (
                                    _discard_contaminated_attempt(
                                        results_root,
                                        run_id,
                                        since=scenario_started,
                                    )
                                )
                                if not discarded:
                                    reason = (
                                        "pre-agent worker setup evidence could "
                                        f"not be made safe on {instance}: "
                                        f"{setup_owner_status.reason}; "
                                        f"{discard_reason}; setup failure: "
                                        f"{_shorten(setup_failure)}"
                                    )
                                    print(
                                        f"BLOCKED: {reason}",
                                        file=sys.stderr,
                                        flush=True,
                                    )
                                    _append_blocked_summary(
                                        reason=reason,
                                        scenario=(
                                            f"{scenario.skill}/"
                                            f"{scenario.spec_name}/"
                                            f"{scenario.platform}/"
                                            f"{scenario.task_name}"
                                        ),
                                        scenario_id=(
                                            f"{_scenario_id(scenario)}-"
                                            "setup-evidence-untrusted"
                                        ),
                                    )
                                    executed += 1
                                    scenario_index += 1
                                    failures.append(reason)
                                    scenario_index += record_remaining_group_skips(
                                        group,
                                        group_scenario_index,
                                        "skipped because a prior dependent scenario was blocked",
                                    )
                                    break
                                print(
                                    "[nemoclaw-ci] discarded unverified "
                                    "pre-agent setup evidence on "
                                    f"{instance}: "
                                    f"{setup_owner_status.reason}; "
                                    f"{discard_reason}",
                                    flush=True,
                                )
                        failover_now = time.monotonic()
                        setup_failover_limit_reached = (
                            max_setup_failovers is not None
                            and setup_failovers >= max_setup_failovers
                        )
                        setup_retry_harbor_budget_s = float(
                            args.harbor_timeout
                        )
                        setup_replacement_selection_budget_s = max(
                            0.0,
                            min(
                                float(args.lock_timeout),
                                run_deadline
                                - failover_now
                                - setup_retry_harbor_budget_s,
                            ),
                        )
                        can_fail_over = (
                            setup_failure is not None
                            and group_scenario_index == 0
                            and not args.instance
                            and not setup_failover_limit_reached
                            and failover_now < worker_failover_deadline
                            and failover_now < run_deadline
                            and setup_replacement_selection_budget_s >= 1.0
                        )
                        if can_fail_over:
                            setup_failovers += 1
                            excluded_instances.add(instance)
                            reserved_retry_harbor_budget_s = (
                                setup_retry_harbor_budget_s
                            )
                            retry_group = True
                            failover_limit = (
                                str(max_setup_failovers)
                                if max_setup_failovers is not None
                                else "pool"
                            )
                            print(
                                "[nemoclaw-ci] pre-agent worker setup failed on "
                                f"{instance}: {_shorten(setup_failure)}; "
                                "failing over "
                                f"({setup_failovers}/{failover_limit})",
                                flush=True,
                            )
                            break
                        if setup_failure is not None:
                            if args.instance:
                                stop_reason = "explicit worker is pinned"
                            elif group_scenario_index != 0:
                                stop_reason = (
                                    "cannot restart a multi-step group after "
                                    f"step {group_scenario_index + 1}"
                                )
                            elif setup_failover_limit_reached:
                                stop_reason = "retry limit reached"
                            else:
                                stop_reason = (
                                    "failover window, run budget, or reserved "
                                    "Harbor budget exhausted"
                                )
                            reason = (
                                f"pre-agent worker setup failed on {instance}: "
                                f"{_shorten(setup_failure)}; failover unavailable "
                                f"({stop_reason})"
                            )
                            print(f"BLOCKED: {reason}", file=sys.stderr, flush=True)
                            _append_blocked_summary(
                                reason=reason,
                                scenario=(
                                    f"{scenario.skill}/{scenario.spec_name}/"
                                    f"{scenario.platform}/{scenario.task_name}"
                                ),
                                scenario_id=(
                                    f"{_scenario_id(scenario)}-"
                                    "worker-setup-blocked"
                                ),
                            )
                            executed += 1
                            scenario_index += 1
                            failures.append(reason)
                            scenario_index += record_remaining_group_skips(
                                group,
                                group_scenario_index,
                                "skipped because a prior dependent scenario was blocked",
                            )
                            break

                        owner_status = _attempt_evidence_owner_status(
                            results_root,
                            run_id,
                            since=scenario_started,
                            remote_target=(
                                worker_lock.remote_target or instance
                            ),
                            expected_token=attempt_owner_token,
                            remote_executor=worker_lock.remote_executor,
                        )
                        if owner_status.status == "contaminated":
                            contamination_now = time.monotonic()
                            attempt_elapsed = max(
                                0.0,
                                contamination_now - scenario_started_monotonic,
                            )
                            minimum_retry_budget = min(
                                float(args.harbor_timeout),
                                max(
                                    900.0,
                                    math.ceil(1.25 * attempt_elapsed) + 300.0,
                                ),
                            )
                            remaining_budget = max(
                                0.0,
                                run_deadline - contamination_now,
                            )
                            replacement_selection_budget = max(
                                0.0,
                                min(
                                    float(args.lock_timeout),
                                    remaining_budget - minimum_retry_budget,
                                ),
                            )
                            can_retry_contamination = (
                                group_scenario_index == 0
                                and not args.instance
                                and contamination_failovers
                                < max_contamination_failovers
                                and replacement_selection_budget >= 1.0
                            )
                            discarded, discard_reason = (
                                _discard_contaminated_attempt(
                                    results_root,
                                    run_id,
                                    since=scenario_started,
                                )
                            )
                            can_retry_contamination = (
                                can_retry_contamination and discarded
                            )
                            if can_retry_contamination:
                                contamination_failovers += 1
                                excluded_instances.add(instance)
                                reserved_retry_harbor_budget_s = (
                                    minimum_retry_budget
                                )
                                retry_group = True
                                print(
                                    "[nemoclaw-ci] worker evidence was replaced "
                                    f"on {instance}: {owner_status.reason}; "
                                    f"{discard_reason}; "
                                    "failing over "
                                    f"(contamination {contamination_failovers}/"
                                    f"{max_contamination_failovers})",
                                    flush=True,
                                )
                                break

                            reason = (
                                f"worker evidence integrity failed on {instance}: "
                                f"{owner_status.reason}; {discard_reason}; "
                                "retry unavailable "
                                f"(step={group_scenario_index + 1}, "
                                f"explicit_worker={bool(args.instance)}, "
                                f"contamination_failovers="
                                f"{contamination_failovers}/"
                                f"{max_contamination_failovers}, "
                                f"remaining_budget={int(remaining_budget)}s, "
                                f"required_harbor_budget="
                                f"{int(minimum_retry_budget)}s, "
                                f"replacement_selection_budget="
                                f"{int(replacement_selection_budget)}s)"
                            )
                            print(f"BLOCKED: {reason}", file=sys.stderr, flush=True)
                            _append_blocked_summary(
                                reason=reason,
                                scenario=(
                                    f"{scenario.skill}/{scenario.spec_name}/"
                                    f"{scenario.platform}/{scenario.task_name}"
                                ),
                                scenario_id=(
                                    f"{_scenario_id(scenario)}-"
                                    "evidence-contaminated"
                                ),
                            )
                            executed += 1
                            scenario_index += 1
                            failures.append(reason)
                            scenario_index += record_remaining_group_skips(
                                group,
                                group_scenario_index,
                                "skipped because a prior dependent scenario was blocked",
                            )
                            break

                        if owner_status.status != "verified" and reward is not None:
                            reason = (
                                f"worker evidence ownership unavailable on {instance}: "
                                f"{owner_status.reason}; refusing to accept "
                                f"reward={reward}"
                            )
                            print(f"BLOCKED: {reason}", file=sys.stderr, flush=True)
                            _append_blocked_summary(
                                reason=reason,
                                scenario=(
                                    f"{scenario.skill}/{scenario.spec_name}/"
                                    f"{scenario.platform}/{scenario.task_name}"
                                ),
                                scenario_id=(
                                    f"{_scenario_id(scenario)}-"
                                    "evidence-unavailable"
                                ),
                            )
                            executed += 1
                            scenario_index += 1
                            failures.append(reason)
                            scenario_index += record_remaining_group_skips(
                                group,
                                group_scenario_index,
                                "skipped because a prior dependent scenario was blocked",
                            )
                            break

                        _append_harbor_report(
                            scenario=scenario,
                            instance=instance,
                            results_root=results_root,
                            run_id=run_id,
                            reward=reward,
                            harbor_rc=harbor_rc,
                            log_path=log_path,
                            since=scenario_started,
                        )
                        executed += 1
                        scenario_index += 1
                        if harbor_rc != 0 or reward is None or reward < 1.0:
                            failure = (
                                f"{scenario.skill}/{scenario.spec_name}/{scenario.platform}/"
                                f"{scenario.task_name} (harbor_rc={harbor_rc}, "
                                f"reward={reward if reward is not None else 'missing'})"
                            )
                            failures.append(failure)
                            if _env_flag(
                                "NEMOCLAW_FAIL_FAST_ON_STEP_FAILURE",
                                default=True,
                            ):
                                remaining = len(group) - group_scenario_index - 1
                                if remaining:
                                    print(
                                        "[nemoclaw-ci] failing fast after scenario "
                                        f"failure; skipping {remaining} remaining "
                                        f"step(s) in {scenario.skill}/"
                                        f"{scenario.spec_name}/{scenario.platform}",
                                        flush=True,
                                    )
                                    scenario_index += record_remaining_group_skips(
                                        group,
                                        group_scenario_index,
                                        "skipped because a prior dependent scenario failed",
                                    )
                                break
                finally:
                    _release_lock(instance, worker_lock)
                if retry_group:
                    continue
                break

        if failures:
            _append_eval_row_completion(
                run_id=run_id,
                planned=len(scenarios),
                executed=executed,
                skipped=skipped_scenarios,
            )
            print(
                f"FAILED: {len(failures)}/{executed} NemoClaw scenario(s) failed: "
                + "; ".join(failures[:10]),
                flush=True,
            )
            return 1
        _append_eval_row_completion(
            run_id=run_id,
            planned=len(scenarios),
            executed=executed,
            skipped=skipped_scenarios,
        )
        print(
            f"DONE: {executed} NemoClaw scenario(s) passed"
            + (f"; {len(blockers)} blocked coverage item(s) reported" if blockers else ""),
            flush=True,
        )
        return 0
    except (InfrastructureBlocked, worker_pool.BrevAuthenticationError) as exc:
        reason = str(exc)
        print(f"BLOCKED: NemoClaw smoke infra blocked: {reason}", file=sys.stderr, flush=True)
        _append_blocked_summary(
            reason=reason,
            scenario=f"{args.skills} / {platform_filter or 'declared-platforms'}",
            scenario_id="infra-blocked",
        )
        _append_eval_row_completion(
            run_id=run_id,
            planned=len(scenarios),
            executed=executed,
            skipped=skipped_scenarios,
            unexecuted_reason="row execution ended after an infrastructure blocker",
        )
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"BLOCKED: NemoClaw smoke setup failed: {exc}", file=sys.stderr, flush=True)
        body = (
            "## NemoClaw VSS Skill Eval\n\n"
            "- Status: `BLOCKED`\n"
            f"- Scenario: `{args.skills} / {platform_filter or 'declared-platforms'}`\n"
            f"- Reason: `{exc}`\n"
        )
        if os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as handle:
                handle.write(body)
        else:
            print(body, flush=True)
        _write_benchmark_input(run_id, "setup-blocked", body)
        _append_eval_row_completion(
            run_id=run_id,
            planned=len(scenarios),
            executed=executed,
            skipped=skipped_scenarios,
            unexecuted_reason="row execution ended after a controlled setup failure",
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
