#!/usr/bin/env python3
"""Apply blueprint profile configuration for local eval latency runs.

This is a temporary runner-local adapter around the blueprint-configurator
ProfileConfigManager. It lets run_eval.sh apply eval/configs/bp_config_latency.yaml
before either Docker Compose or Helm deployment, without requiring the old
init-config-manager Docker service.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_DIR = REPO_ROOT / "eval"
DEFAULT_CONFIG_FILE = EVAL_DIR / "configs" / "bp_config_latency.yaml"
TEMP_CONFIGURATOR_APP_DIR = EVAL_DIR / "third_party" / "blueprint_configurator_app"
DEFAULT_READY_FILE = Path("/tmp/profile_config_ready")

_CACHE_MODULES = (
    "profile_config_manager",
    "profile_configurator_utils",
    "profile_configurator_utils.utils",
    "utils",
    "utils.logger",
)


def _manager_file(app_dir: Path) -> Path:
    return app_dir / "profile_configurator" / "profile_config_manager.py"


def _has_profile_config_manager(app_dir: Path) -> bool:
    return _manager_file(app_dir).is_file()


def resolve_configurator_app_dir(
    explicit: str | Path | None = None,
    *,
    repo_root: Path = REPO_ROOT,
    eval_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the blueprint-configurator docker/app directory."""
    env = os.environ if env is None else env
    eval_dir = eval_dir or repo_root / "eval"

    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())

    env_app_dir = env.get("BP_CONFIGURATOR_APP_DIR", "").strip()
    if env_app_dir:
        candidates.append(Path(env_app_dir).expanduser())

    candidates.extend(
        [
            repo_root
            / "video-search-and-summarization"
            / "blueprint-configurator"
            / "docker"
            / "app",
            repo_root / "blueprint-configurator" / "docker" / "app",
            eval_dir / "third_party" / "blueprint_configurator_app",
        ]
    )

    for candidate in candidates:
        if _has_profile_config_manager(candidate):
            return candidate.resolve()

    checked = "\n  - ".join(str(path) for path in candidates)
    raise FileNotFoundError(
        "Could not find blueprint-configurator ProfileConfigManager. "
        "Set BP_CONFIGURATOR_APP_DIR or ensure the VSS submodule/fallback exists. "
        f"Checked:\n  - {checked}"
    )


def resolve_blueprints_dir(
    explicit: str | Path | None = None,
    *,
    repo_root: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the deployment tree root used by profile config files."""
    env = os.environ if env is None else env
    candidates: list[Path] = []

    if explicit:
        candidates.append(Path(explicit).expanduser())

    env_blueprints_dir = env.get("BLUEPRINTS_DIR", "").strip()
    if env_blueprints_dir:
        candidates.append(Path(env_blueprints_dir).expanduser())

    candidates.extend(
        [
            repo_root,
            repo_root / "video-search-and-summarization",
            repo_root.parent / "met-blueprints",
        ]
    )

    required_subdirs = (
        "deploy/docker",
        "deployments",
        "deploy/helm",
        "video-search-and-summarization/deploy/helm",
    )

    for candidate in candidates:
        if any((candidate / subdir).is_dir() for subdir in required_subdirs):
            return candidate.resolve()

    checked = "\n  - ".join(str(path) for path in candidates)
    expected = ", ".join(f"{subdir}/" for subdir in required_subdirs)
    raise FileNotFoundError(
        "BLUEPRINTS_DIR must point to a checkout containing one of: "
        f"{expected}. Pass --blueprints-dir DIR or set BLUEPRINTS_DIR. "
        f"Checked:\n  - {checked}"
    )


def build_profile_config_env(
    *,
    base_env: Mapping[str, str],
    config_file: Path,
    blueprints_dir: Path,
    hardware_profile: str,
    ready_file: Path,
) -> dict[str, str]:
    """Build the environment expected by ProfileConfigManager."""
    env = dict(base_env)
    env["PROFILE_CONFIG_FILE"] = str(config_file)
    env["BLUEPRINTS_DIR"] = str(blueprints_dir)
    env["HARDWARE_PROFILE"] = hardware_profile
    env["DEPLOYMENT_MODES_ENABLED"] = "false"
    env["PROFILE_CONFIG_READY_FILE"] = str(ready_file)
    env.setdefault("LOG_LEVEL", "INFO")
    return env


def _load_manager_module(app_dir: Path):
    manager_path = _manager_file(app_dir)
    spec = importlib.util.spec_from_file_location(
        "_eval_profile_config_manager_runtime",
        manager_path,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load ProfileConfigManager from {manager_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_profile_config(
    *,
    app_dir: Path,
    config_file: Path,
    blueprints_dir: Path,
    hardware_profile: str,
    env_base: Mapping[str, str],
    ready_file: Path,
) -> int:
    """Run ProfileConfigManager.main() and return its exit code."""
    app_dir = Path(app_dir).resolve()
    config_file = Path(config_file).resolve()
    blueprints_dir = Path(blueprints_dir).resolve()
    ready_file = Path(ready_file).resolve()

    env = build_profile_config_env(
        base_env=env_base,
        config_file=config_file,
        blueprints_dir=blueprints_dir,
        hardware_profile=hardware_profile,
        ready_file=ready_file,
    )

    old_env = dict(os.environ)
    old_path = list(sys.path)
    old_modules = {name: sys.modules.get(name) for name in _CACHE_MODULES}
    runtime_name = "_eval_profile_config_manager_runtime"
    old_runtime_module = sys.modules.get(runtime_name)

    try:
        for name in _CACHE_MODULES:
            sys.modules.pop(name, None)
        sys.modules.pop(runtime_name, None)

        os.environ.clear()
        os.environ.update(env)
        sys.path.insert(0, str(app_dir / "profile_configurator"))
        sys.path.insert(0, str(app_dir))

        module = _load_manager_module(app_dir)
        result = module.main()
        return int(result or 0)
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        sys.path[:] = old_path
        for name in _CACHE_MODULES:
            sys.modules.pop(name, None)
            previous = old_modules[name]
            if previous is not None:
                sys.modules[name] = previous
        sys.modules.pop(runtime_name, None)
        if old_runtime_module is not None:
            sys.modules[runtime_name] = old_runtime_module


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply latency profile config before local eval deployment."
    )
    parser.add_argument("--config-file", default=str(DEFAULT_CONFIG_FILE))
    parser.add_argument("--blueprints-dir")
    parser.add_argument("--hardware-profile", default=os.environ.get("HARDWARE_PROFILE", "default"))
    parser.add_argument("--app-dir")
    parser.add_argument("--ready-file", default=str(DEFAULT_READY_FILE))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        config_file = Path(args.config_file).expanduser().resolve()
        if not config_file.is_file():
            raise FileNotFoundError(f"Profile config file not found: {config_file}")

        blueprints_dir = resolve_blueprints_dir(args.blueprints_dir)
        app_dir = resolve_configurator_app_dir(args.app_dir)
        ready_file = Path(args.ready_file).expanduser()

        return run_profile_config(
            app_dir=app_dir,
            config_file=config_file,
            blueprints_dir=blueprints_dir,
            hardware_profile=args.hardware_profile,
            env_base=os.environ,
            ready_file=ready_file,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary needs a clear message.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
