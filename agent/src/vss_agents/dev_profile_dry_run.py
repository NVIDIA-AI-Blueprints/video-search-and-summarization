# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""CLI entry point for prerequisite checks + dev profile dry-run generation.

Run as a module:
    python -m vss_agents.orchestrator.dev_profile_dry_run compose_generate --profile base --output-dir /tmp/vss-artifacts --config-file deployments/developer-workflow/vss-orchestrator-mcp/vss_orchestrator_mcp_config.yml
    python -m vss_agents.orchestrator.dev_profile_dry_run up --profile base
    python -m vss_agents.orchestrator.dev_profile_dry_run down --profile base

Or via the installed console script:
    vss-orchestrator compose_generate --profile base --output-dir /tmp/vss-artifacts --config-file deployments/developer-workflow/vss-orchestrator-mcp/vss_orchestrator_mcp_config.yml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

import yaml

from .docker_compose_util import (
    ValidationError,
    create_dry_run_recipe,
    generate_dry_run_artifacts,
    parse_env_overrides,
    print_configuration_summary,
    run_compose_command,
)
from .prerequisite_check import run_prerequisite_checks
from .storage import ensure_data_directories

GENERATED_ENV_FILENAME = "generated.dry-run.env"
GENERATED_COMPOSE_FILENAME = "compose.resolved.dry-run.yml"


def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Generate dry-run env/compose artifacts, then run docker compose up/down "
            "using those generated files."
        )
    )
    parser.add_argument(
        "command",
        choices=("compose_generate", "up", "down"),
        help="First run 'compose_generate', then run 'up' or 'down' as a separate command.",
    )
    parser.add_argument(
        "--profile",
        required=True,
        help="Deployment profile. Supported values include: base, search, lvs, alerts",
    )
    parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Environment override. Pass multiple times for a list of overrides.",
    )
    parser.add_argument("--output-dir", required=True, help="Directory for generated dry-run env/compose artifacts.")
    parser.add_argument(
        "--config-file",
        required=True,
        help=(
            "Path to vss_orchestrator MCP YAML config. "
            "Used as source of mdx_data_directories and model_resolution rules."
        ),
    )
    parser.add_argument(
        "--deployments-dir",
        default="deployments",
        help="Path to deployments directory (absolute or relative to cwd).",
    )
    parser.add_argument(
        "--skip-prerequisite-checks",
        action="store_true",
        help="Skip GPU/Docker prerequisite checks and only generate dry-run outputs.",
    )
    parser.add_argument(
        "--ngc-cli-api-key",
        default="",
        help="Optional NGC key to inject when NGC_CLI_API_KEY is missing in env inputs.",
    )
    parser.add_argument(
        "--nvidia-api-key",
        default="",
        help="Optional NVIDIA API key to inject when NVIDIA_API_KEY is missing in env inputs.",
    )
    return parser.parse_args(list(argv))


def _ensure_generated_artifacts(env_path: Path, compose_path: Path) -> None:
    if not env_path.is_file():
        raise ValidationError(f"Generated env file not found: {env_path}. Run the 'compose_generate' command first.")
    if not compose_path.is_file():
        raise ValidationError(f"Resolved compose file not found: {compose_path}. Run the 'compose_generate' command first.")


def _load_vss_orchestrator_settings(config_file: str) -> tuple[tuple[str, ...], dict]:
    config_path = Path(config_file).expanduser().resolve()
    if not config_path.is_file():
        raise ValidationError(f"Config file not found: {config_path}")
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationError(f"Failed to parse YAML config file: {config_path}") from exc

    try:
        group_config = loaded["function_groups"]["vss_orchestrator"]
        mdx_data_directories = tuple(group_config["mdx_data_directories"])
        model_resolution = dict(group_config["model_resolution"])
    except (KeyError, TypeError) as exc:
        raise ValidationError(
            "Config file is missing required keys under function_groups.vss_orchestrator: "
            "mdx_data_directories and model_resolution.{hardware,llm,vlm}."
        ) from exc

    return mdx_data_directories, model_resolution


def main(argv: Iterable[str]) -> int:
    try:
        args = parse_args(argv)
        mdx_data_directories, model_resolution = _load_vss_orchestrator_settings(args.config_file)
        output_dir = Path(args.output_dir).expanduser().resolve()
        output_env_file = output_dir / GENERATED_ENV_FILENAME
        output_compose_file = output_dir / GENERATED_COMPOSE_FILENAME
        env_overrides = parse_env_overrides(args.env)
        config = create_dry_run_recipe(
            profile=args.profile,
            env_overrides=env_overrides,
            ngc_cli_api_key=args.ngc_cli_api_key,
            nvidia_api_key=args.nvidia_api_key,
            model_resolution=model_resolution,
            output_env_file=str(output_env_file),
            output_compose_file=str(output_compose_file),
            deployments_dir=args.deployments_dir,
        )
        env_path = config.output_env_file
        compose_path = config.output_compose_file

        if args.command == "compose_generate":
            resolved_env, env_path, compose_path = generate_dry_run_artifacts(config)
            ensure_data_directories(
                resolved_env["MDX_DATA_DIR"],
                required_subdirectories=mdx_data_directories,
            )
            if not args.skip_prerequisite_checks:
                run_prerequisite_checks(profile=args.profile, resolved_env=resolved_env)
            print_configuration_summary(config, resolved_env)
            print(f"Profile: {config.profile}")
            print(f"Generated env file: {env_path}")
            print(f"Resolved compose file: {compose_path}")
        elif args.command in ("up", "down"):
            _ensure_generated_artifacts(env_path, compose_path)
            compose_args = ("-d", "--force-recreate", "--build") if args.command == "up" else ("-v", "--remove-orphans")
            run_compose_command(config, env_path, compose_path, args.command, *compose_args)
            print(f"Executed: docker compose {args.command} {' '.join(compose_args)}")
            print(f"Using env file: {env_path}")
            print(f"Using compose file: {compose_path}")
        return 0
    except (ValidationError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
