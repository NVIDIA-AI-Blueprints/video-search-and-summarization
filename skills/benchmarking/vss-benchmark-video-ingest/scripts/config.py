#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Benchmark configuration file support.

``config.yml`` supplies optional CLI selection, corpus, concurrency and
Elasticsearch readiness settings. The VSS origin comes from CLI configuration.

Precedence, highest first:

1. An explicit command-line flag
2. ``config.yml`` (or whatever ``--config`` points at)
3. The built-in default baked into the argument parser

That ordering is what makes the file safe: it moves the defaults out of the
command line without ever silently overriding something the operator typed.

Secrets are NOT read from here. ``VSS_AUTH_TOKEN`` stays an environment
variable, so a config file can be committed to a repository without leaking a
credential.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:  # PyYAML is only needed when a config file is actually present.
    import yaml
except ImportError:  # pragma: no cover - exercised only on a bare interpreter
    yaml = None  # type: ignore[assignment]

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yml"
DEFAULT_RESULTS_DIR = Path("./benchmark-results/vss/ingest")
DEFAULT_WARMUP = 1


class ConfigError(Exception):
    """Raised when a config file exists but cannot be used as written."""


# ---------------------------------------------------------------------------
# Schema. Keys are dotted config paths; values are the argparse ``dest`` they
# feed. Anything not listed here is rejected, so a typo fails loudly at load
# time instead of silently doing nothing for a two-hour run.
# ---------------------------------------------------------------------------

SCALAR_KEYS: dict[str, str] = {
    "cli.repo": "vss_repo",
    "cli.config_home": "cli_config_home",
    "cli.executable": "cli_executable",
    "compatibility.version_url": "version_url",
    "compatibility.request_timeout_sec": "version_timeout",
    "corpus": "corpus",
    "results_dir": "results_dir",
    "sweep.profile": "profile",
    "sweep.video_class_name": "video_class_name",
    "sweep.limit": "limit",
    "sweep.warmup": "warmup",
    "es_readiness.elasticsearch_url": "elasticsearch_url",
    "es_readiness.poll_interval_sec": "readiness_poll_interval",
    "es_readiness.timeout_sec": "readiness_timeout",
    "es_readiness.embed_index": "es_embed_index",
    "es_readiness.raw_index": "es_raw_index",
    "es_readiness.embed_chunk_duration_sec": "embed_chunk_duration",
    "es_readiness.frame_processing_time_ms": "frame_processing_time_ms",
    "es_readiness.raw_drop_grace_sec": "raw_drop_grace_sec",
    "es_readiness.request_timeout_sec": "es_request_timeout",
    "upload.ramp.stagger_sec": "stagger_sec",
    "upload.ramp.max_ramp_sec": "max_ramp_sec",
    "cleanup.policy": "cleanup",
    "cleanup.timeout_sec": "cleanup_timeout",
    "cleanup.settle_sec": "cleanup_settle_sec",
    "limits.transfer_ceiling_gb": "transfer_ceiling_gb",
}

# ``action="append"`` options. argparse appends CLI values onto a non-None
# default, so these are never passed through ``set_defaults`` -- the caller
# applies them only when the flag was absent. See apply_to_args below.
LIST_KEYS: dict[str, str] = {
    "sweep.video_classes": "classes",
    "sweep.videos": "videos",
    "sweep.concurrencies": "concurrencies",
}

# Coerced to argparse's own types so a value from the file behaves exactly like
# the same value typed on the command line.
_PATH_DESTS = {"corpus", "results_dir", "vss_repo", "cli_config_home", "videos"}

def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten nested mappings into dotted keys, leaving lists intact."""
    flat: dict[str, Any] = {}
    for key, value in node.items():
        path = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{path}."))
        else:
            flat[path] = value
    return flat


def load_config(path: Path | None, *, explicit: bool = False) -> dict[str, Any]:
    """Read and validate a config file.

    A missing file is only an error when the operator named it with
    ``--config``; the default path is allowed to be absent so the runner still
    works in a checkout without one.
    """
    if path is None:
        return {}
    if not path.exists():
        if explicit:
            raise ConfigError(f"config file not found: {path}")
        return {}
    if yaml is None:
        raise ConfigError(
            f"{path} exists but PyYAML is not installed. "
            "Install it with: pip install -r scripts/requirements.txt "
            "(or pass --no-config to run on built-in defaults)."
        )

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:  # type: ignore[union-attr]
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level, got {type(loaded).__name__}")

    flat = _flatten(loaded)
    if "es_readiness.raw_completion_ratio" in flat:
        raise ConfigError(
            f"{path}: remove obsolete key es_readiness.raw_completion_ratio. "
            "Readiness now requires the full expected raw-frame count."
        )
    known = set(SCALAR_KEYS) | set(LIST_KEYS)
    unknown = sorted(set(flat) - known)
    if unknown:
        raise ConfigError(
            f"{path} has unrecognized key(s): {', '.join(unknown)}.\n"
            f"  Valid keys: {', '.join(sorted(known))}"
        )
    return flat


def _coerce(dest: str, value: Any, base_dir: Path | None = None) -> Any:
    if value is None:
        return None
    if dest in _PATH_DESTS:
        # Relative paths resolve against the config file's directory, not the
        # working directory, so `config.yml` keeps meaning the same corpus no
        # matter where the runner is invoked from. A path typed on the command
        # line is still relative to the shell, as a shell argument should be.
        path = Path(str(value)).expanduser()
        if not path.is_absolute() and base_dir is not None:
            path = (base_dir / path).resolve()
        return path
    if dest == "cli_executable":
        text = str(value)
        if "/" in text or text.startswith("~"):
            path = Path(text).expanduser()
            if not path.is_absolute() and base_dir is not None:
                path = (base_dir / path).resolve()
            return str(path)
        return text
    if dest in {"version_timeout", "cleanup_timeout", "cleanup_settle_sec"}:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{dest} must be a number") from exc
    return value


def resolve_defaults(
    flat: dict[str, Any], base_dir: Path | None = None
) -> tuple[dict[str, Any], dict[str, list]]:
    """Split a flattened config into (scalar defaults, list values) by dest.

    Both are keyed by argparse ``dest``. Scalars are safe to hand to
    ``parser.set_defaults``; lists are not, and must be applied post-parse.
    ``base_dir`` is the config file's directory, used to resolve relative paths.
    """
    scalars: dict[str, Any] = {}
    lists: dict[str, list] = {}

    for key, dest in SCALAR_KEYS.items():
        if key not in flat:
            continue
        value = flat[key]
        if value is None and (dest == "vss_repo" or dest in scalars):
            # A null optional checkout means use the same discovery default as
            # an omitted key, not pass None into the CLI/shared-checker setup.
            continue
        scalars[dest] = _coerce(dest, value, base_dir)

    for key, dest in LIST_KEYS.items():
        if key not in flat or flat[key] is None:
            continue
        value = flat[key]
        if not isinstance(value, list):
            raise ConfigError(f"{key} must be a list, got {type(value).__name__}")
        lists[dest] = list(value)

    if "videos" in lists:
        if any(not isinstance(source, str) or not source.strip() for source in lists["videos"]):
            raise ConfigError("sweep.videos must be a list of nonempty file or directory paths")
        lists["videos"] = [str(_coerce("videos", source, base_dir)) for source in lists["videos"]]

    if "concurrencies" in lists:
        # int(1.9) and int(True) silently change a YAML workload. Accept only
        # integers or integer strings, just as --concurrency does.
        if any(type(value) is not int and not isinstance(value, str) for value in lists["concurrencies"]):
            raise ConfigError("sweep.concurrencies must be a list of integers, not booleans or decimal values")
        try:
            lists["concurrencies"] = [int(c) for c in lists["concurrencies"]]
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"sweep.concurrencies must be a list of integers: {exc}") from exc
        if any(c < 1 for c in lists["concurrencies"]):
            raise ConfigError("sweep.concurrencies entries must be >= 1")

    if "classes" in lists:
        lists["classes"] = [str(c) for c in lists["classes"]]

    return scalars, lists


def apply_lists(args, lists: dict[str, list]) -> None:
    """Fill append-action options that the command line did not set.

    argparse appends to a list default rather than replacing it, so these
    cannot go through ``set_defaults``. ``None`` means the flag was absent.
    An explicit ``--video`` without ``--video-class`` selects only those videos,
    so configured corpus classes must not be added to that selection.
    """
    # Capture CLI provenance before any config lists are applied. Config-only
    # selections may deliberately combine videos and corpus classes.
    cli_video_only = getattr(args, "videos", None) is not None and getattr(args, "classes", None) is None
    for dest, value in lists.items():
        if dest == "classes" and cli_video_only:
            continue
        if getattr(args, dest, None) is None:
            setattr(args, dest, list(value))


PROFILE_OWNED_LIST_DESTS: tuple[str, ...] = ("classes", "videos", "concurrencies")


def profile_given_on_cli(argv: list[str] | None) -> bool:
    """Return whether the operator explicitly typed ``--profile``."""
    argv = list(argv if argv is not None else __import__("sys").argv[1:])
    return any(arg == "--profile" or arg.startswith("--profile=") for arg in argv)


def drop_profile_owned_lists(lists: dict[str, list], profile: str) -> list[str]:
    """Let an explicit named profile replace config-owned matrix lists."""
    if profile == "custom":
        return []
    dropped = [dest for dest in PROFILE_OWNED_LIST_DESTS if dest in lists]
    for dest in dropped:
        lists.pop(dest, None)
    return dropped


def describe(path: Path, flat: dict[str, Any]) -> str:
    """One line for the run log, recording which file supplied the defaults."""
    if not flat:
        return "  config: <none> (built-in defaults)"
    return f"  config: {path} ({len(flat)} key(s))"
