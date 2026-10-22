# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary

"""Provision an explicitly approved random-hex deployment secret."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
def generate_random_hex(byte_count: int) -> str:
    """Generate exactly ``byte_count`` cryptographically secure random bytes."""
    return secrets.token_hex(byte_count)


def write_compose_env(path: Path, env_name: str, value: str) -> None:
    """Atomically set one env key while preserving unrelated file content."""
    if not ENV_NAME.fullmatch(env_name):
        raise ValueError(f"Invalid Compose environment variable name: {env_name!r}")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output: list[str] = []
    replaced = False
    prefix = f"{env_name}="
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                output.append(f"{env_name}={value}")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        if output and output[-1]:
            output.append("")
        output.append(f"{env_name}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def initialize_compose_env(template: Path, path: Path) -> None:
    """Create or validate a protected runtime env file without replacing values."""
    if template == path:
        raise ValueError("Compose secret template and runtime file must differ")
    if not template.is_file():
        raise FileNotFoundError(f"Compose secret template does not exist: {template}")
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Compose secret destination must be a regular file: {path}")
        ensure_local_git_exclusion(path)
        path.chmod(0o600)
        return

    ensure_local_git_exclusion(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(template.read_text(encoding="utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError as error:
            raise FileExistsError(
                f"Refusing to overwrite concurrently created secret file {path}"
            ) from error
    finally:
        temporary_path.unlink(missing_ok=True)


def ensure_local_git_exclusion(path: Path) -> None:
    """Keep a generated Compose secret file out of the enclosing Git index."""
    probe = path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        root_result = subprocess.run(
            ["git", "-C", str(probe), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return
    if root_result.returncode != 0:
        return
    root = Path(root_result.stdout.strip()).resolve()
    relative = path.relative_to(root).as_posix()
    tracked = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", relative],
        capture_output=True,
        check=False,
    )
    if tracked.returncode == 0:
        raise RuntimeError(f"Refusing to write generated secret data to tracked file {path}")
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "-q", "--", relative],
        capture_output=True,
        check=False,
    )
    if ignored.returncode == 0:
        return

    exclude_result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--git-path", "info/exclude"],
        check=True,
        capture_output=True,
        text=True,
    )
    exclude_path = Path(exclude_result.stdout.strip())
    if not exclude_path.is_absolute():
        exclude_path = root / exclude_path
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    pattern = "/" + re.sub(r"([ #!])", r"\\\1", relative)
    with exclude_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n# Blueprint Builder generated deployment secrets\n{pattern}\n")


def kubectl_command(value: str) -> list[str]:
    command = shlex.split(value)
    if not command:
        raise ValueError("kubectl command cannot be empty")
    return command


def provision_kubernetes_secret(
    command: list[str],
    namespace: str,
    secret_name: str,
    secret_key: str,
    value: str,
) -> None:
    """Create or replace one Secret key while preserving existing keys."""
    get_result = subprocess.run(
        [
            *command,
            "get",
            "secret",
            secret_name,
            "-n",
            namespace,
            "--ignore-not-found",
            "-o",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    exists = bool(get_result.stdout.strip())
    if exists:
        manifest: dict[str, Any] = json.loads(get_result.stdout)
        manifest.setdefault("data", {})[secret_key] = base64.b64encode(value.encode()).decode()
        manifest.get("metadata", {}).pop("managedFields", None)
        action = "replace"
    else:
        manifest = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": secret_name, "namespace": namespace},
            "type": "Opaque",
            "data": {secret_key: base64.b64encode(value.encode()).decode()},
        }
        action = "create"

    subprocess.run(
        [*command, action, "-f", "-"],
        input=json.dumps(manifest),
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="backend", required=True)

    compose = subparsers.add_parser("compose")
    compose.add_argument("--env-file", required=True)
    compose.add_argument("--env-var", action="append", required=True)
    compose.add_argument("--bytes", type=int, required=True)

    compose_init = subparsers.add_parser("compose-init")
    compose_init.add_argument("--template", required=True)
    compose_init.add_argument("--env-file", required=True)

    kubernetes = subparsers.add_parser("kubernetes")
    kubernetes.add_argument("--namespace", required=True)
    kubernetes.add_argument("--secret", required=True)
    kubernetes.add_argument("--key", required=True)
    kubernetes.add_argument("--bytes", type=int, required=True)
    kubernetes.add_argument("--kubectl-command", default="kubectl")

    args = parser.parse_args()
    if args.backend == "compose-init":
        template = Path(args.template).expanduser().resolve()
        path = Path(args.env_file).expanduser().absolute()
        initialize_compose_env(template, path)
        print(f"Initialized protected Compose secret file {path}.")
        return 0

    if args.bytes <= 0:
        parser.error("--bytes must be positive")
    value = generate_random_hex(args.bytes)

    if args.backend == "compose":
        path = Path(args.env_file).expanduser().resolve()
        ensure_local_git_exclusion(path)
        for env_name in args.env_var:
            write_compose_env(path, env_name, value)
        print(f"Configured {', '.join(args.env_var)} in {path} (value not displayed).")
        return 0

    provision_kubernetes_secret(
        kubectl_command(args.kubectl_command),
        args.namespace,
        args.secret,
        args.key,
        value,
    )
    print(
        f"Configured {args.secret}/{args.key} in namespace {args.namespace} (value not displayed)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
