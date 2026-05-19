#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys


DEFAULT_CONTAINER = "openshell-cluster-nemoclaw"
DEFAULT_NAMESPACE = "openshell"
DEFAULT_CONFIG_PATH = "/sandbox/.openclaw/openclaw.json"
DEFAULT_WORKSPACE_DIR = "/sandbox/.openclaw/workspace"
RED_BOLD = "\033[1;31m"
RESET = "\033[0m"


def run_kubectl_exec(
    container: str,
    namespace: str,
    sandbox_name: str,
    remote_args: list[str],
    capture_output: bool = False,
) -> subprocess.CompletedProcess:
    cmd = [
        "sudo",
        "docker",
        "exec",
        container,
        "kubectl",
        "exec",
        "-n",
        namespace,
        sandbox_name,
        "--",
        *remote_args,
    ]
    return subprocess.run(
        cmd,
        check=True,
        text=True,
        capture_output=capture_output,
    )


def shell_quote_multiline(text: str) -> str:
    return text


def read_etc_environment() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open("/etc/environment", encoding="utf-8") as fp:
            for raw in fp:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return env
    return env


def get_brev_env_id() -> str:
    """Return the Brev environment ID, or '' on a non-Brev host."""
    env_id = os.environ.get("BREV_ENV_ID", "").strip()
    if env_id:
        return env_id

    env_id = read_etc_environment().get("BREV_ENV_ID", "").strip()
    if env_id:
        return env_id

    hostname_candidates = [
        os.environ.get("HOSTNAME", ""),
        socket.getfqdn(),
        socket.gethostname(),
    ]
    for hostname in hostname_candidates:
        host = hostname.strip().lower().rstrip(".")
        if not host.endswith(".brevlab.com"):
            continue
        host = host[: -len(".brevlab.com")]
        if "-" in host:
            return host.split("-", 1)[1]

    return ""


def read_remote_file(
    container: str,
    namespace: str,
    sandbox_name: str,
    config_path: str,
) -> str:
    result = run_kubectl_exec(
        container,
        namespace,
        sandbox_name,
        ["cat", config_path],
        capture_output=True,
    )
    return result.stdout


def write_remote_file(
    container: str,
    namespace: str,
    sandbox_name: str,
    config_path: str,
    content: str,
) -> None:
    shell_cmd = f"cat > {shlex.quote(config_path)} <<'EOF'\n{content}EOF"
    run_kubectl_exec(
        container,
        namespace,
        sandbox_name,
        ["sh", "-c", shell_cmd],
    )


def backup_remote_file(
    container: str,
    namespace: str,
    sandbox_name: str,
    config_path: str,
    backup_path: str,
) -> None:
    run_kubectl_exec(
        container,
        namespace,
        sandbox_name,
        ["cp", config_path, backup_path],
    )


def chmod_and_chown(
    container: str,
    namespace: str,
    sandbox_name: str,
    config_path: str,
) -> None:
    run_kubectl_exec(
        container,
        namespace,
        sandbox_name,
        ["chmod", "644", config_path],
    )
    run_kubectl_exec(
        container,
        namespace,
        sandbox_name,
        ["chown", "sandbox:sandbox", config_path],
    )


def get_dashboard_token(
    container: str,
    namespace: str,
    sandbox_name: str,
) -> str | None:
    try:
        result = subprocess.run(
            ["nemoclaw", sandbox_name, "gateway-token", "--quiet"],
            check=True,
            text=True,
            capture_output=True,
        )
        token = result.stdout.strip()
        if token:
            return token
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    try:
        result = run_kubectl_exec(
            container,
            namespace,
            sandbox_name,
            ["sh", "-lc", 'su - sandbox -c "openclaw dashboard"'],
            capture_output=True,
        )
    except subprocess.CalledProcessError:
        return None

    output = "\n".join(part for part in (result.stdout, result.stderr) if part)
    match = re.search(r"/#token=([0-9a-fA-F]+)", output)
    if not match:
        return None

    return match.group(1)


def highlight_message(message: str) -> str:
    return f"{RED_BOLD}{message}{RESET}"


def update_hooks_config(
    data: dict,
    *,
    enabled: bool,
    token: str,
    path: str,
) -> bool:
    if not enabled:
        return False

    if not token:
        raise ValueError("OpenClaw hooks token is required when hooks are enabled")

    hooks = data.setdefault("hooks", {})
    before = json.dumps(hooks, sort_keys=True)
    hooks["enabled"] = True
    hooks["token"] = token
    hooks["path"] = path or "/hooks"
    return json.dumps(hooks, sort_keys=True) != before


def update_mcp_server(data: dict, *, name: str, url: str) -> bool:
    """Register an HTTP MCP server under data['mcp']['servers'][name].

    Returns True if the config changed. No-ops when name or url is empty.
    """
    if not name or not url:
        return False
    server_config = {"type": "http", "url": url}
    mcp = data.setdefault("mcp", {})
    servers = mcp.setdefault("servers", {})
    if servers.get(name) == server_config:
        return False
    servers[name] = server_config
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely update openclaw.json inside a sandbox pod."
    )
    parser.add_argument(
        "sandbox_name",
        nargs="?",
        default="demo",
        help="Sandbox pod name (default: demo)",
    )
    parser.add_argument(
        "--container",
        default=DEFAULT_CONTAINER,
        help=f"Docker container name (default: {DEFAULT_CONTAINER})",
    )
    parser.add_argument(
        "--namespace",
        default=DEFAULT_NAMESPACE,
        help=f"Kubernetes namespace (default: {DEFAULT_NAMESPACE})",
    )
    parser.add_argument(
        "--config-path",
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to openclaw.json in the pod (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--backup-path",
        help="Optional backup path inside the pod, e.g. /sandbox/.openclaw/openclaw.json.bak",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the resulting JSON without writing it",
    )
    parser.add_argument(
        "--enable-hooks",
        action="store_true",
        default=os.environ.get("OPENCLAW_HOOKS_ENABLED", "").strip() == "1",
        help="Enable OpenClaw webhook hooks in openclaw.json",
    )
    parser.add_argument(
        "--hooks-token",
        default=os.environ.get("OPENCLAW_HOOKS_TOKEN", "").strip(),
        help="Shared secret for OpenClaw hooks. Required when hooks are enabled.",
    )
    parser.add_argument(
        "--hooks-path",
        default=os.environ.get("OPENCLAW_HOOKS_PATH", "/hooks").strip() or "/hooks",
        help="OpenClaw hooks path (default: /hooks)",
    )
    parser.add_argument(
        "--mcp-name",
        default=os.environ.get("VSS_ORCHESTRATOR_MCP_NAME", "vss_orchestrator").strip()
        or "vss_orchestrator",
        help="MCP server name to register under mcp.servers (default: vss_orchestrator)",
    )
    parser.add_argument(
        "--mcp-url",
        default=os.environ.get(
            "VSS_ORCHESTRATOR_MCP_URL", "http://host.openshell.internal:9902/mcp"
        ).strip(),
        help=(
            "HTTP MCP server URL to register; pass empty string to skip "
            "(default: http://host.openshell.internal:9902/mcp)"
        ),
    )
    args = parser.parse_args()

    env_id = get_brev_env_id()
    if env_id:
        origin = f"https://18789-{env_id}.brevlab.com"
    else:
        port = os.environ.get("NEMOCLAW_DASHBOARD_PORT", "18789").strip()
        origin = f"http://127.0.0.1:{port}"

    raw = read_remote_file(
        args.container,
        args.namespace,
        args.sandbox_name,
        args.config_path,
    )

    data = json.loads(raw)
    gateway = data.setdefault("gateway", {})
    control_ui = gateway.setdefault("controlUi", {})
    origins = control_ui.setdefault("allowedOrigins", [])

    changed = False
    if origin not in origins:
        origins.insert(0, origin)
        changed = True

    # Set agents.defaults.workspace so the VSS plugin's register hook can locate the
    # workspace dir and copy AGENTS.md / BOOTSTRAP.md / IDENTITY.md / SOUL.md / TOOLS.md.
    agents_defaults = data.setdefault("agents", {}).setdefault("defaults", {})
    if agents_defaults.get("workspace") != DEFAULT_WORKSPACE_DIR:
        agents_defaults["workspace"] = DEFAULT_WORKSPACE_DIR
    if update_hooks_config(
        data,
        enabled=args.enable_hooks,
        token=args.hooks_token,
        path=args.hooks_path,
    ):
        changed = True

    if update_mcp_server(data, name=args.mcp_name, url=args.mcp_url):
        changed = True

    updated_json = json.dumps(data, indent=2) + "\n"

    if args.dry_run:
        print("Dry run only. No changes written.")
        print(f"Derived env_id: {env_id or '(local / non-Brev)'}")
        print(f"Target file: {args.config_path}")
        print(f"Origin enabled: {origin}")
        if args.enable_hooks:
            print(f"OpenClaw hooks enabled at: {args.hooks_path}")
        print(f"Would change file: {'yes' if changed else 'no'}")
        print()
        print(json.dumps(updated_json, indent=2) + "\n")
        return 0

    if args.backup_path:
        backup_remote_file(
            args.container,
            args.namespace,
            args.sandbox_name,
            args.config_path,
            args.backup_path,
        )
        print(f"Backup created at {args.backup_path}")

    if changed:
        write_remote_file(
            args.container,
            args.namespace,
            args.sandbox_name,
            args.config_path,
            updated_json,
        )
        print(f"Updated {args.config_path}")
    else:
        print(f"No JSON change needed in {args.config_path}")

    chmod_and_chown(
        args.container,
        args.namespace,
        args.sandbox_name,
        args.config_path,
    )
    dashboard_token = get_dashboard_token(
        args.container,
        args.namespace,
        args.sandbox_name,
    )

    if env_id:
        print(f"Brev instance ID: {env_id}")
    print(f"Origin allowed in OpenClaw: {origin}")
    print(f"agents.defaults.workspace: {DEFAULT_WORKSPACE_DIR}")
    if args.enable_hooks:
        print(f"OpenClaw hooks enabled at: {args.hooks_path}")
    if args.mcp_url:
        print(f"MCP server registered: {args.mcp_name} -> {args.mcp_url}")
    if not dashboard_token:
        print("No dashboard token found")
        return 0

    print(f"Dashboard token: {dashboard_token}")
    ui_url = f"{origin}/#token={dashboard_token}"
    print()
    print(highlight_message("=" * 120))
    print(highlight_message(f"OpenClaw UI at {ui_url}"))
    print(highlight_message("=" * 120))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        raise SystemExit(1)
    except ValueError as e:
        print(f"Invalid configuration: {e}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}", file=sys.stderr)
        if e.stdout:
            print(e.stdout, file=sys.stderr)
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        raise SystemExit(e.returncode)
