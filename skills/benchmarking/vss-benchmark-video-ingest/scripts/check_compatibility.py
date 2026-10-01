#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Gate benchmarking on the deployed VSS version and this skill's metadata.

Like VSS's check_vss_version.py, compare MAJOR.MINOR.PATCH: deployment build
and prerelease suffixes do not change the release being checked. No uploads,
ES credentials, local CLI version fallback, or automatic retries are used.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import operator
import os
from pathlib import Path
import re
import sys
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from config import DEFAULT_CONFIG_PATH, ConfigError, load_config, resolve_defaults
from httpio import parse_endpoint
from vss_cli import VssCli

METADATA_PATH = Path(__file__).resolve().parents[1] / "metadata.yml"
# Same strict SemVer grammar as vss_core.version, without importing the server.
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
BOUND_PATTERN = re.compile(r"(>=|<=|==|>|<)\s*((?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*))")
OPERATORS = {">=": operator.ge, "<=": operator.le, "==": operator.eq, ">": operator.gt, "<": operator.lt}


class CompatibilityError(ValueError):
    """A failed lookup or compatibility rule must stop the run before upload."""


def release(version: str) -> tuple[int, int, int]:
    match = SEMVER_PATTERN.fullmatch(version) if isinstance(version, str) else None
    if match is None:
        raise CompatibilityError("Version is not valid Semantic Versioning 2.0.0")
    return tuple(int(part) for part in match.groups())


def read_metadata(path: Path = METADATA_PATH) -> tuple[str, str, list]:
    try:
        metadata = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise CompatibilityError("Cannot read the skill's metadata.yml") from exc
    if not isinstance(metadata, dict) or metadata.get("skill-name") != "vss-benchmark-video-ingest":
        raise CompatibilityError("metadata.yml must identify vss-benchmark-video-ingest")
    version = metadata.get("skill-version")
    requirement = metadata.get("requires-vss")
    if not isinstance(version, str) or not isinstance(requirement, str):
        raise CompatibilityError("metadata.yml needs skill-version and requires-vss strings")
    release(version.removeprefix("v"))
    clauses = []
    for clause in requirement.split(","):
        match = BOUND_PATTERN.fullmatch(clause.strip())
        if match is None:
            raise CompatibilityError("requires-vss must contain comma-separated comparisons such as ==3.3.0")
        comparison, bound = match.groups()
        clauses.append((comparison, release(bound)))
    return version, requirement, clauses


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
    metadata_path: Path = METADATA_PATH,
) -> dict:
    skill_version, requirement, clauses = read_metadata(metadata_path)
    if (
        not isinstance(timeout_sec, (int, float))
        or isinstance(timeout_sec, bool)
        or not math.isfinite(timeout_sec)
        or timeout_sec <= 0
    ):
        raise CompatibilityError("Version request timeout must be finite and positive")
    url = endpoint_url(deployment, version_url)
    # Deliberately do not use httpio.request_json: VSS_AUTH_TOKEN is ES-only.
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urlopen(request, timeout=timeout_sec) as response:
            if response.status != 200:
                raise CompatibilityError(f"Version endpoint returned HTTP {response.status}; compatibility is unknown")
            payload = json.load(response)
    except HTTPError as exc:
        hint = " (endpoint absent or not routed)" if exc.code == 404 else ""
        raise CompatibilityError(f"Version endpoint returned HTTP {exc.code}{hint}; stopping before upload") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise CompatibilityError("Version endpoint is unreachable; stopping before upload") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise CompatibilityError("Version endpoint returned invalid JSON; stopping before upload") from exc
    if not isinstance(payload, dict) or payload.get("service") != "vss" or not isinstance(payload.get("version"), str):
        raise CompatibilityError('Version endpoint must return {"service":"vss","version":"<semver>"}')
    version = payload["version"]
    actual = release(version)
    if not all(OPERATORS[comparison](actual, bound) for comparison, bound in clauses):
        raise CompatibilityError(
            f"Deployed VSS {version} is incompatible with skill {skill_version} (requires-vss: {requirement}); "
            "use a compatible skill/deployment before benchmarking"
        )
    return {
        "status": "compatible",
        "skill_version": skill_version,
        "deployed_vss_version": version,
        "requires_vss": requirement,
        "comparison": "major.minor.patch (VSS release convention)",
        "version_url": url,
        "metadata_file": str(metadata_path.resolve()),
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
        default=Path(os.environ.get("VSS_REPO_ROOT", str(Path.home() / "video-search-and-summarization"))),
    )
    parser.add_argument("--cli-config-home", type=Path, default=None)
    parser.add_argument("--cli-executable", default=None)
    parser.add_argument("--uv-executable", default="uv")
    add_arguments(parser)
    try:
        config_path = known.config or DEFAULT_CONFIG_PATH
        flat = {} if known.no_config else load_config(config_path, explicit=known.config is not None)
        scalars, _ = resolve_defaults(flat, config_path.parent)
        keys = {"vss_repo", "cli_config_home", "cli_executable", "uv_executable", "version_url", "version_timeout"}
        parser.set_defaults(**{k: v for k, v in scalars.items() if k in keys})
        args = parser.parse_args(argv)
        if args.base_url or args.version_url:
            deployment = {"base_url": args.base_url}
        else:
            deployment = VssCli(
                args.vss_repo, args.cli_config_home, args.uv_executable, executable=args.cli_executable
            ).deployment(check_health=False)
        result = check_compatibility(deployment, version_url=args.version_url, timeout_sec=args.version_timeout)
    except (ConfigError, ValueError, OSError) as exc:
        print(f"ERROR  {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
