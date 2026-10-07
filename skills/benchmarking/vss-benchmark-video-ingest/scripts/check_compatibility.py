#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Wrap the repository's shared version comparison with CLI discovery and JSON evidence.

Exit codes follow check_vss_version.py: 0 compatible, 1 indeterminate,
3 incompatible, and 2 for command-line usage errors. No uploads or retries.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from config import DEFAULT_CONFIG_PATH, ConfigError, load_config, resolve_defaults
from httpio import parse_endpoint
from vss_cli import VssCli

SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"
SHARED_CHECKER = Path("services/agent/scripts/check_vss_version.py")


class CompatibilityError(ValueError):
    """A failed gate, preserving the shared checker's indeterminate/incompatible distinction."""

    def __init__(self, message: str, *, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


def default_repo() -> Path:
    if os.environ.get("VSS_REPO_ROOT"):
        return Path(os.environ["VSS_REPO_ROOT"])
    for parent in Path(__file__).resolve().parents:
        if (parent / SHARED_CHECKER).is_file():
            return parent
    return Path.home() / "video-search-and-summarization"


def load_shared_checker(vss_repo: Path | None = None):
    """Load the prepared checkout's comparison; never vendor a second version parser."""
    path = (vss_repo or default_repo()).expanduser().resolve() / SHARED_CHECKER
    if not path.is_file():
        raise CompatibilityError(f"Shared version checker is missing at {path}; set --vss-repo to the prepared VSS checkout")
    spec = importlib.util.spec_from_file_location("vss_shared_version_checker", path)
    if spec is None or spec.loader is None:
        raise CompatibilityError(f"Cannot load the shared version checker at {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, SyntaxError) as exc:
        raise CompatibilityError(f"Cannot load the shared version checker at {path}: {exc}") from exc
    return module, path


def endpoint_url(deployment: dict, explicit_url: str = "") -> str:
    base = deployment.get("base_url")
    if not explicit_url and (not isinstance(base, str) or not base.strip()):
        raise CompatibilityError("CLI configuration has no deployment base_url; configure it or set --version-url")
    url = explicit_url or f"{base.rstrip('/')}/api/v1/version"
    if not isinstance(url, str):
        raise CompatibilityError("Version endpoint must be an HTTP(S) URL")
    try:
        parsed = parse_endpoint(url, label="VSS version endpoint")
    except ValueError as exc:
        raise CompatibilityError("Version endpoint must be a valid HTTP(S) URL") from exc
    # Credentials are environment/configuration concerns, never URL/artifact data.
    if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise CompatibilityError("Version endpoint URL must not contain credentials, query parameters or a fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise CompatibilityError("Version endpoint has an invalid port") from exc
    return url


def check_compatibility(
    deployment: dict,
    *,
    version_url: str = "",
    timeout_sec: float = 10.0,
    vss_repo: Path | None = None,
    skill_path: Path = SKILL_PATH,
) -> dict:
    shared, checker_path = load_shared_checker(vss_repo)
    try:
        requirement, requirement_text, skill_version = shared.requirement_from_skill(skill_path)
        # The stamped frontmatter supplies provenance; absence must not silently
        # substitute "unknown" into an apparently validated benchmark report.
        shared.precedence(skill_version.removeprefix("v"))
    except (shared.IndeterminateError, ValueError, OSError, UnicodeError) as exc:
        raise CompatibilityError(f"Cannot determine the skill's version requirements: {exc}") from exc
    if (
        not isinstance(timeout_sec, (int, float))
        or isinstance(timeout_sec, bool)
        or not math.isfinite(timeout_sec)
        or timeout_sec <= 0
    ):
        raise CompatibilityError("Version request timeout must be finite and positive")
    url = endpoint_url(deployment, version_url)
    # Keep the explicit public-route override and ES-only authentication boundary.
    # Version/range parsing and comparison belong exclusively to the shared checker.
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            if response.status != 200:
                raise CompatibilityError(f"Version endpoint returned HTTP {response.status}; compatibility is unknown")
            payload = json.load(response)
    except HTTPError as exc:
        raise CompatibilityError(f"Version endpoint returned HTTP {exc.code}; stopping before upload") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CompatibilityError("Version endpoint is unreachable; stopping before upload") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CompatibilityError("Version endpoint returned invalid JSON; stopping before upload") from exc
    if not isinstance(payload, dict) or payload.get("service") != "vss" or not isinstance(payload.get("version"), str):
        raise CompatibilityError('Version endpoint must return {"service":"vss","version":"<semver>"}')
    version = payload["version"]
    try:
        compatible = shared.satisfies(version, requirement)
    except ValueError as exc:
        raise CompatibilityError(f"Version endpoint reported an invalid version: {exc}") from exc
    if not compatible:
        raise CompatibilityError(
            f"Deployed VSS {version} is incompatible with skill {skill_version} (requires-vss: {requirement_text})",
            exit_code=shared.EXIT_INCOMPATIBLE,
        )
    return {
        "status": "compatible",
        "skill_version": skill_version,
        "deployed_vss_version": version,
        "requires_vss": requirement_text,
        "comparison": "major.minor.patch (shared VSS checker)",
        "version_url": url,
        "skill_file": str(skill_path.resolve()),
        "checker_file": str(checker_path),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version-url",
        default="",
        help="Public deployed VSS version API URL; defaults to CLI base_url/api/v1/version.",
    )
    parser.add_argument("--version-timeout", type=float, default=10.0, help="Version API request timeout, seconds.")


def main(argv: list[str] | None = None) -> int:
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre.add_argument("--no-config", action="store_true")
    known, _ = pre.parse_known_args(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--no-config", action="store_true")
    parser.add_argument("--base-url", help="Public deployment origin; otherwise read configure show.")
    parser.add_argument(
        "--vss-repo",
        type=Path,
        default=default_repo(),
    )
    parser.add_argument("--cli-config-home", type=Path, default=None)
    parser.add_argument("--cli-executable", default=None)
    add_arguments(parser)
    try:
        config_path = known.config or DEFAULT_CONFIG_PATH
        flat = {} if known.no_config else load_config(config_path, explicit=known.config is not None)
        scalars, _ = resolve_defaults(flat, config_path.parent)
        keys = {"vss_repo", "cli_config_home", "cli_executable", "version_url", "version_timeout"}
        parser.set_defaults(**{k: v for k, v in scalars.items() if k in keys})
        args = parser.parse_args(argv)
        if args.base_url or args.version_url:
            deployment = {"base_url": args.base_url}
        else:
            deployment = VssCli(
                args.vss_repo, args.cli_config_home, executable=args.cli_executable
            ).deployment(check_health=False)
        result = check_compatibility(
            deployment, version_url=args.version_url, timeout_sec=args.version_timeout, vss_repo=args.vss_repo
        )
    except CompatibilityError as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return exc.exit_code
    except (ConfigError, ValueError, OSError) as exc:
        print(f"ERROR  Cannot determine compatibility: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
