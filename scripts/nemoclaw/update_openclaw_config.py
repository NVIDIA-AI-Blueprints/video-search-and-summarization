#!/usr/bin/env python3
import argparse
import json
import re
import shlex
import socket
import subprocess
import sys


DEFAULT_CONTAINER = "openshell-cluster-nemoclaw"
DEFAULT_NAMESPACE = "openshell"
DEFAULT_CONFIG_PATH = "/sandbox/.openclaw/openclaw.json"
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


def get_brev_env_id() -> str:
    hostname = socket.gethostname().strip()
    prefix = "brev-"
    if not hostname.startswith(prefix) or len(hostname) <= len(prefix):
        raise ValueError(
            f"Unable to derive Brev environment ID from hostname: {hostname!r}"
        )
    return hostname[len(prefix) :]


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
    args = parser.parse_args()

    env_id = get_brev_env_id()
    origin = f"https://openclaw0-{env_id}.brevlab.com"

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

    updated_json = json.dumps(data, indent=2) + "\n"

    if args.dry_run:
        print("Dry run only. No changes written.")
        print(f"Derived env_id: {env_id}")
        print(f"Target file: {args.config_path}")
        print(f"Origin enabled: {origin}")
        print(f"Would change file: {'yes' if changed else 'no'}")
        print()
        print(updated_json)
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

    print(f"Brev instance ID: {env_id}")
    print(f"Origin allowed in OpenClaw: {origin}")
    if dashboard_token:
        print(f"Dashboard token: {dashboard_token}")
        ui_url = f"{origin}/#token={dashboard_token}"
        print()
        print(highlight_message("=" * 120))
        print(highlight_message(f"OpenClaw UI at {ui_url}"))
        print(highlight_message("=" * 120))
    else:
        print("No dashboard token found")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        raise SystemExit(1)
    except subprocess.CalledProcessError as e:
        print(f"Command failed with exit code {e.returncode}", file=sys.stderr)
        if e.stdout:
            print(e.stdout, file=sys.stderr)
        if e.stderr:
            print(e.stderr, file=sys.stderr)
        raise SystemExit(e.returncode)
