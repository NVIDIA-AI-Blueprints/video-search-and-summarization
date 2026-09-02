#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Attach VSS capabilities to an existing NemoClaw-managed agent.

This installer is deliberately additive. It installs skills, network access, a
project-local CLI, and a capability receipt, but never uploads or edits the
agent's persona, memory, provider, model, or canonical workspace documents.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import NoReturn
from urllib.parse import urlparse

ARTIFACT_PROTOCOL_VERSION = "1.0"
MAX_API_RESPONSE_BYTES = 1_000_000
DEFAULT_RUNTIME_REPOSITORY = (
    "https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git"
)
DEFAULT_RUNTIME_DIR = "/sandbox/video-search-and-summarization"
RECEIPT_PATH = "/sandbox/.vss/agent-capabilities.json"
SANDBOX_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class AttachError(RuntimeError):
    """The requested capability attachment could not be completed safely."""


@dataclass(frozen=True, slots=True)
class HarnessProfile:
    runtime: str
    cli: str
    api_port: int
    model: str
    identity_root: str
    enable_responses: bool
    session_header: str


PROFILES = {
    "openclaw": HarnessProfile(
        runtime="openclaw",
        cli="nemoclaw",
        api_port=18789,
        model="openclaw",
        identity_root="/sandbox/.openclaw/workspace",
        enable_responses=True,
        session_header="x-openclaw-session-key",
    ),
    "hermes": HarnessProfile(
        runtime="hermes",
        cli="nemohermes",
        api_port=8642,
        model="hermes-agent",
        identity_root="/sandbox/.hermes",
        enable_responses=False,
        session_header="X-Hermes-Session-Key",
    ),
}

IDENTITY_FILENAMES = (
    "AGENTS.md",
    "SOUL.md",
    "IDENTITY.md",
    "USER.md",
    "MEMORY.md",
    "TOOLS.md",
    "BOOTSTRAP.md",
)


@dataclass(frozen=True, slots=True)
class Origin:
    url: str
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class ApiReadiness:
    origin: str
    token: str
    model: str


@dataclass(frozen=True, slots=True)
class AttachmentResult:
    receipt: dict[str, object]
    api: ApiReadiness | None


class CommandRunner:
    def __init__(self, *, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def run(
        self,
        command: list[str],
        *,
        capture: bool = False,
        sensitive_output: bool = False,
        timeout: int = 900,
    ) -> str:
        if self.dry_run:
            print("DRY-RUN:", " ".join(command))
            return ""
        try:
            result = subprocess.run(
                command,
                check=True,
                text=True,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
                timeout=timeout,
            )
        except FileNotFoundError as error:
            raise AttachError(
                f"required command is unavailable: {command[0]}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise AttachError(
                f"command timed out after {timeout}s: {command[0]}"
            ) from error
        except subprocess.CalledProcessError as error:
            detail = (
                "" if sensitive_output else (error.stderr or error.stdout or "").strip()
            )
            suffix = f": {detail}" if detail else ""
            raise AttachError(
                f"command failed ({error.returncode}): {command[0]}{suffix}"
            ) from error
        return result.stdout.strip() if capture else ""


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def fail(message: str) -> NoReturn:
    raise AttachError(message)


def validate_sandbox_name(value: str) -> str:
    if not SANDBOX_NAME_PATTERN.fullmatch(value):
        fail("sandbox name must contain only letters, digits, '.', '_', and '-'")
    return value


def validate_origin(value: str) -> Origin:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        fail("VSS origin must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail("VSS origin must not contain credentials, a query, or a fragment")
    if parsed.path not in {"", "/"} or parsed.params:
        fail("VSS origin must not contain a path")
    host = parsed.hostname
    if not HOST_PATTERN.fullmatch(host):
        fail("VSS origin host must be a DNS name or IPv4 address")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as error:
        raise AttachError("VSS origin contains an invalid port") from error
    if not 1 <= port <= 65535:
        fail("VSS origin port must be between 1 and 65535")
    normalized = f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return Origin(url=normalized, host=host, port=port)


def validate_runtime_dir(value: str) -> str:
    path = PurePosixPath(value.strip())
    if (
        not path.is_absolute()
        or path == PurePosixPath("/sandbox")
        or path.parts[:2] != ("/", "sandbox")
        or ".." in path.parts
    ):
        fail("runtime directory must be an absolute child of /sandbox")
    return str(path)


def is_private_host(host: str) -> bool:
    if host in {"localhost", "host.openshell.internal"}:
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def discover_skills(root: Path) -> tuple[Path, ...]:
    skills_root = root / "skills"
    if not skills_root.is_dir():
        fail(f"VSS skills directory not found: {skills_root}")
    skill_dirs = sorted(
        {
            path.parent
            for path in skills_root.rglob("SKILL.md")
            if not any(
                part.startswith(".") for part in path.relative_to(skills_root).parts
            )
        },
        key=lambda path: str(path.relative_to(skills_root)),
    )
    if not skill_dirs:
        fail(f"no SKILL.md files found below {skills_root}")
    return tuple(skill_dirs)


def resolve_runtime_ref(root: Path, requested: str | None) -> str:
    if requested:
        ref = requested.strip()
    else:
        try:
            ref = subprocess.run(
                ["git", "-C", str(root), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
                timeout=30,
            ).stdout.strip()
        except (FileNotFoundError, subprocess.SubprocessError) as error:
            raise AttachError("could not resolve the VSS repository HEAD") from error
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", ref):
        fail("runtime ref must be a full 40- or 64-character Git commit ID")
    return ref.lower()


def verify_source_snapshot(root: Path, runtime_ref: str) -> None:
    """Ensure installed skill bytes and the cloned CLI name one commit."""

    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.strip()
        head = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.strip()
        dirty_source = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            text=True,
            capture_output=True,
            timeout=30,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError) as error:
        raise AttachError("VSS source must be a readable Git checkout") from error
    if Path(top_level).resolve() != root:
        fail("--repo-root must be the root of the VSS Git checkout")
    if head.lower() != runtime_ref:
        fail("runtime ref must match the VSS source checkout HEAD")
    if dirty_source:
        fail("VSS source has uncommitted changes; commit them before attachment")


def build_policy(origin: Origin) -> dict[str, object]:
    runtime_binary_paths = [
        "/usr/bin/curl",
        "/usr/bin/python3",
        "/usr/bin/python3.13",
        "/usr/bin/node",
        "/usr/local/bin/node",
        "/usr/local/bin/openclaw",
        "/tmp/.local/bin/uv",
        "/tmp/.local/bin/uvx",
        "/usr/local/bin/uv",
        "/root/.local/bin/uv",
        "/sandbox/.local/bin/uv",
        "/home/sandbox/.local/bin/uv",
    ]
    runtime_binaries = [{"path": path} for path in runtime_binary_paths]
    return {
        "preset": {
            "name": "vss-agent-capabilities",
            "description": "VSS ingress and project-local CLI access for a BYO agent",
        },
        "network_policies": {
            "vss-agent-origin": {
                "name": "vss-agent-origin",
                "endpoints": [
                    {"host": origin.host, "port": origin.port, "access": "full"}
                ],
                "binaries": runtime_binaries,
            },
            "vss-agent-source": {
                "name": "vss-agent-source",
                "endpoints": [{"host": "github.com", "port": 443, "access": "full"}],
                "binaries": [{"path": "/usr/bin/git"}],
            },
            "vss-agent-packages": {
                "name": "vss-agent-packages",
                "endpoints": [
                    {"host": "pypi.org", "port": 443, "access": "full"},
                    {
                        "host": "files.pythonhosted.org",
                        "port": 443,
                        "access": "full",
                    },
                    {"host": "pypi.nvidia.com", "port": 443, "access": "full"},
                    {
                        "host": "download.pytorch.org",
                        "port": 443,
                        "access": "full",
                    },
                ],
                "binaries": [
                    {"path": path}
                    for path in (
                        "/usr/bin/python3",
                        "/usr/bin/python3.13",
                        "/usr/bin/pip",
                        "/usr/bin/pip3",
                        "/tmp/.local/bin/uv",
                        "/tmp/.local/bin/uvx",
                        "/usr/local/bin/uv",
                        "/root/.local/bin/uv",
                        "/sandbox/.local/bin/uv",
                        "/home/sandbox/.local/bin/uv",
                        "/usr/bin/curl",
                        "/usr/local/bin/node",
                    )
                ],
            },
        },
    }


def render_policy(origin: Origin) -> str:
    """Render stdlib-only YAML compatible with NemoClaw's policy mutator.

    NemoClaw parses YAML for its preview, then deliberately extracts a literal
    ``network_policies:\n`` section for the live merge. A one-line JSON document
    passes preview (JSON is YAML) but cannot pass that second boundary.
    """

    policy = build_policy(origin)
    preset = policy["preset"]
    network_policies = policy["network_policies"]
    if not isinstance(network_policies, dict):  # pragma: no cover - invariant
        fail("generated policy has no network policies")
    lines = [
        f"preset: {json.dumps(preset, separators=(',', ':'))}",
        "network_policies:",
    ]
    for name, value in network_policies.items():
        lines.append(f"  {name}: {json.dumps(value, separators=(',', ':'))}")
    return "\n".join(lines) + "\n"


def validate_runtime_repository(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname != "github.com":
        fail("runtime repository must be an https://github.com URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail("runtime repository must not contain credentials, a query, or a fragment")
    if not re.fullmatch(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?", parsed.path):
        fail("runtime repository must name one GitHub owner and repository")
    return value.strip()


def sandbox_command(profile: HarnessProfile, sandbox: str, *args: str) -> list[str]:
    return [profile.cli, sandbox, *args]


def sandbox_exec(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    *command: str,
    capture: bool = False,
    timeout: int = 900,
) -> str:
    return runner.run(
        sandbox_command(profile, sandbox, "exec", "--", *command),
        capture=capture,
        timeout=timeout,
    )


def identity_digest(
    runner: CommandRunner, profile: HarnessProfile, sandbox: str
) -> str:
    script = """
set -eu
root="$1"
shift
for name in "$@"; do
  path="$root/$name"
  if [ -f "$path" ]; then sha256sum "$path"; else printf 'missing  %s\\n' "$path"; fi
done
""".strip()
    listing = sandbox_exec(
        runner,
        profile,
        sandbox,
        "bash",
        "-lc",
        script,
        "vss-identity-check",
        profile.identity_root,
        *IDENTITY_FILENAMES,
        capture=True,
        timeout=60,
    )
    if runner.dry_run:
        return hashlib.sha256(b"").hexdigest()
    expected_paths = {f"{profile.identity_root}/{name}" for name in IDENTITY_FILENAMES}
    records = []
    for line in listing.splitlines():
        match = re.fullmatch(r"(?:[0-9a-f]{64}|missing)\s+(.+)", line.strip())
        if match and match.group(1) in expected_paths:
            records.append(line.strip())
    if len(records) != len(IDENTITY_FILENAMES):
        fail("could not capture a complete agent identity digest")
    return hashlib.sha256("\n".join(sorted(records)).encode()).hexdigest()


def install_policy(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    origin: Origin,
) -> None:
    with tempfile.TemporaryDirectory(prefix="vss-agent-attach-") as temporary:
        # NemoClaw validates the filename before parsing; JSON is valid YAML, but
        # the custom-policy loader intentionally accepts only .yaml/.yml paths.
        path = Path(temporary) / "policy.yaml"
        path.write_text(render_policy(origin), encoding="utf-8")
        command = sandbox_command(
            profile,
            sandbox,
            "policy",
            "add",
            "--from-file",
            str(path),
            "--yes",
        )
        # host.openshell.internal is NemoClaw's reviewed sandbox-to-host bridge
        # and is accepted directly. Declaring it as an arbitrary trusted-private
        # DNS name instead invokes a host-side DNS preflight, where this
        # sandbox-only alias intentionally cannot resolve.
        if is_private_host(origin.host) and origin.host != "host.openshell.internal":
            command.extend(["--trusted-private-host", origin.host])
        runner.run(command, timeout=300)


def install_skills(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    skills: tuple[Path, ...],
) -> None:
    for index, skill in enumerate(skills, 1):
        print(f"Installing VSS skill {index}/{len(skills)}: {skill.name}")
        runner.run(
            sandbox_command(profile, sandbox, "skill", "install", str(skill)),
            timeout=300,
        )


def prepare_runtime(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    *,
    runtime_dir: str,
    repository: str,
    runtime_ref: str,
    origin: Origin,
) -> str:
    repository = validate_runtime_repository(repository)
    script = r"""
set -eu
runtime_dir="$1"
repository="$2"
runtime_ref="$3"
vss_origin="$4"

case "$runtime_dir" in
  /sandbox/*) ;;
  *) echo "runtime directory must be below /sandbox" >&2; exit 2 ;;
esac

if [ -e "$runtime_dir" ] && [ ! -d "$runtime_dir/.git" ]; then
  echo "existing runtime directory is not a Git checkout: $runtime_dir" >&2
  exit 2
fi

fresh_checkout=0
if [ ! -e "$runtime_dir" ]; then
    mkdir -p "$(dirname "$runtime_dir")"
    git clone --filter=blob:none --no-checkout "$repository" "$runtime_dir"
    git -C "$runtime_dir" sparse-checkout init --cone
    fresh_checkout=1
else
    current_repository=$(git -C "$runtime_dir" remote get-url origin)
    [ "$current_repository" = "$repository" ] || {
    echo "existing VSS checkout uses a different origin; refusing to modify it" >&2
    exit 2
    }
fi

if [ "$fresh_checkout" -eq 0 ]; then
  test -z "$(git -C "$runtime_dir" status --porcelain=v1 --untracked-files=all)" || {
    echo "existing VSS checkout has local changes; refusing to execute it" >&2
    exit 2
  }
fi

# A clean, same-origin managed checkout is safe to advance on a repeated
# deployment. Fetch the exact immutable commit; never follow a moving branch.
git -C "$runtime_dir" sparse-checkout set services/agent
git -C "$runtime_dir" fetch --filter=blob:none --depth 1 origin "$runtime_ref"
git -C "$runtime_dir" checkout --detach FETCH_HEAD
test "$(git -C "$runtime_dir" rev-parse HEAD)" = "$runtime_ref"
test -z "$(git -C "$runtime_dir" status --porcelain=v1 --untracked-files=all)"

test -f "$runtime_dir/services/agent/pyproject.toml"
export PATH="$HOME/.local/bin:/tmp/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --user --break-system-packages uv
fi
uv_bin=$(command -v uv)
python_bin=/usr/bin/python3.13
[ -x "$python_bin" ] || python_bin=/usr/bin/python3
"$uv_bin" run --python "$python_bin" \
  --project "$runtime_dir/services/agent" --no-dev --extra cli vss --version
"$uv_bin" run --python "$python_bin" \
  --project "$runtime_dir/services/agent" --no-dev --extra cli vss \
  configure --base-url "$vss_origin"
"$uv_bin" run --python "$python_bin" \
  --project "$runtime_dir/services/agent" --no-dev --extra cli vss configure show
""".strip()
    return sandbox_exec(
        runner,
        profile,
        sandbox,
        "bash",
        "-lc",
        script,
        "vss-runtime-setup",
        runtime_dir,
        repository,
        runtime_ref,
        origin.url,
        capture=True,
        timeout=1800,
    )


def build_receipt(
    profile: HarnessProfile,
    sandbox: str,
    *,
    origin: Origin,
    runtime_dir: str,
    runtime_ref: str,
    skills: tuple[Path, ...],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "attached_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "sandbox": sandbox,
        "harness": profile.runtime,
        "identity_mode": "preserve",
        "vss_origin": origin.url,
        "runtime": {"repo_root": runtime_dir, "commit": runtime_ref},
        "skills": [skill.name for skill in skills],
        "ui_artifacts": {
            "version": ARTIFACT_PROTOCOL_VERSION,
            "envelope": "vss-ui-artifact",
            "kinds": ["vss.search.results", "vss.alert.incidents"],
        },
    }


def write_private_text(path: Path, content: str) -> None:
    """Atomically write a deployment artifact without a world-readable window."""

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
        path.chmod(0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary_path.unlink(missing_ok=True)
        raise


def write_receipt(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    receipt: dict[str, object],
) -> None:
    with tempfile.TemporaryDirectory(prefix="vss-agent-receipt-") as temporary:
        path = Path(temporary) / "agent-capabilities.json"
        path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        path.chmod(0o600)
        sandbox_exec(
            runner,
            profile,
            sandbox,
            "mkdir",
            "-p",
            "/sandbox/.vss",
            timeout=60,
        )
        runner.run(
            sandbox_command(
                profile,
                sandbox,
                "upload",
                str(path),
                "/sandbox/.vss/",
            ),
            timeout=120,
        )
        sandbox_exec(
            runner,
            profile,
            sandbox,
            "chmod",
            "600",
            RECEIPT_PATH,
            timeout=60,
        )


def validate_gateway_bind_host(value: str) -> str:
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as error:
        raise AttachError("gateway bind host must be a private IPv4 address") from error
    if (
        address.version != 4
        or not address.is_private
        or address.is_loopback
        or address.is_unspecified
    ):
        fail("gateway bind host must be a private, non-loopback IPv4 address")
    return str(address)


def resolve_gateway_bind_host(runner: CommandRunner, explicit_host: str | None) -> str:
    if explicit_host:
        return validate_gateway_bind_host(explicit_host)
    output = runner.run(
        [
            "docker",
            "network",
            "inspect",
            "bridge",
            "--format",
            "{{(index .IPAM.Config 0).Gateway}}",
        ],
        capture=True,
        timeout=30,
    )
    if not output:
        fail(
            "could not discover Docker's private bridge gateway; pass "
            "--gateway-bind-host"
        )
    return validate_gateway_bind_host(output.splitlines()[-1])


def encode_receipt(receipt: dict[str, object]) -> tuple[str, str]:
    raw = json.dumps(
        receipt,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.b64encode(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def dotenv_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def write_gateway_env(
    path: Path,
    *,
    profile: HarnessProfile,
    api: ApiReadiness,
    receipt: dict[str, object],
    runtime_ref: str,
    bind_host: str,
    port: int,
) -> None:
    encoded_receipt, receipt_digest = encode_receipt(receipt)
    gateway_token = secrets.token_hex(32)
    values = {
        "VSS_AGENT_GATEWAY_ENABLED": "true",
        "VSS_AGENT_GATEWAY_BIND_HOST": bind_host,
        "VSS_AGENT_GATEWAY_PORT": str(port),
        "VSS_AGENT_GATEWAY_URL": f"http://host.docker.internal:{port}",
        "VSS_AGENT_GATEWAY_TOKEN": gateway_token,
        "VSS_AGENT_GATEWAY_REQUIRE_CAPABILITIES": "true",
        "VSS_AGENT_GATEWAY_CAPABILITIES_B64": encoded_receipt,
        "VSS_AGENT_GATEWAY_CAPABILITIES_SHA256": receipt_digest,
        "VSS_AGENT_GATEWAY_EXPECTED_RUNTIME_REF": runtime_ref,
        "VSS_AGENT_BACKEND_PROTOCOL": "responses",
        "VSS_AGENT_BACKEND_URL": api.origin,
        "VSS_AGENT_BACKEND_PATH": "/v1/responses",
        "VSS_AGENT_BACKEND_TOKEN": api.token,
        "VSS_AGENT_BACKEND_MODEL": api.model,
        "VSS_AGENT_BACKEND_SESSION_FIELD": "user",
        "VSS_AGENT_BACKEND_SESSION_HEADER": profile.session_header,
        "NEXT_PUBLIC_ENABLE_CHAT_TAB": "true",
        "NEXT_PUBLIC_FORCE_HTTP_CHAT_TRANSPORT": "true",
        "NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON": "false",
        "NEXT_PUBLIC_SIDEBAR_CHAT_WEB_SOCKET_DEFAULT_ON": "false",
    }
    content = "".join(f"{key}={dotenv_quote(value)}\n" for key, value in values.items())
    write_private_text(path, content)


def enable_api(
    runner: CommandRunner, profile: HarnessProfile, sandbox: str
) -> AttachError | None:
    if not profile.enable_responses:
        return None
    sandbox_exec(
        runner,
        profile,
        sandbox,
        "openclaw",
        "config",
        "set",
        "gateway.http.endpoints.responses.enabled",
        "true",
        timeout=120,
    )
    try:
        runner.run(
            sandbox_command(profile, sandbox, "gateway", "restart"),
            capture=True,
            timeout=300,
        )
    except AttachError as error:
        # The managed restart command also insists on owning the preferred host
        # forward. An operator-owned forward can make that auxiliary step fail
        # after the gateway itself restarted successfully. Defer the decision
        # to the authenticated live-route checks below.
        return error
    return None


def discover_api_origin(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    explicit_url: str | None,
) -> Origin:
    if explicit_url:
        return validate_origin(explicit_url)
    if runner.dry_run:
        return validate_origin(f"http://127.0.0.1:{profile.api_port}")

    listing = runner.run(["openshell", "forward", "list"], capture=True, timeout=60)
    candidate_ports: list[int] = []
    for raw_line in ANSI_ESCAPE_PATTERN.sub("", listing).splitlines():
        fields = raw_line.split()
        if len(fields) < 5 or fields[0] != sandbox or fields[-1] != "running":
            continue
        try:
            port = int(fields[2])
        except ValueError:
            continue
        if profile.runtime == "hermes" and not 8642 <= port <= 8652:
            continue
        candidate_ports.append(port)
    if not candidate_ports:
        fail(
            "could not discover the harness API forward; pass --agent-api-url "
            "with its loopback origin"
        )
    if profile.api_port in candidate_ports:
        candidate_ports = [profile.api_port]
    if len(set(candidate_ports)) > 1:
        fail("multiple harness API forwards matched; pass --agent-api-url explicitly")
    return validate_origin(f"http://127.0.0.1:{candidate_ports[0]}")


def verify_api(
    runner: CommandRunner,
    profile: HarnessProfile,
    sandbox: str,
    api_url: str,
) -> ApiReadiness | None:
    if runner.dry_run:
        print(f"DRY-RUN: verify authenticated GET {api_url}/v1/models")
        return None
    token = runner.run(
        sandbox_command(profile, sandbox, "gateway-token", "--quiet"),
        capture=True,
        sensitive_output=True,
        timeout=120,
    )
    token_lines = [
        ANSI_ESCAPE_PATTERN.sub("", line).strip()
        for line in token.splitlines()
        if ANSI_ESCAPE_PATTERN.sub("", line).strip()
    ]
    token = token_lines[-1] if token_lines else ""
    if (
        not token
        or len(token) > 8_192
        or any(character.isspace() for character in token)
    ):
        fail("agent gateway token command returned an invalid or empty value")
    request = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/models",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read(MAX_API_RESPONSE_BYTES + 1)
            if len(body) > MAX_API_RESPONSE_BYTES:
                fail("agent Responses API returned an oversized model list")
            payload = json.loads(body)
    except (
        urllib.error.URLError,
        TimeoutError,
        ValueError,
        UnicodeDecodeError,
    ) as error:
        raise AttachError("agent Responses API readiness probe failed") from error
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list) or not models:
        fail("agent Responses API returned no models")
    model_ids = [
        item.get("id")
        for item in models
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if not model_ids:
        fail("agent Responses API returned no usable model IDs")

    route_request = urllib.request.Request(
        f"{api_url.rstrip('/')}/v1/responses",
        method="GET",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(route_request, timeout=15) as response:
            route_status = response.status
    except urllib.error.HTTPError as error:
        route_status = error.code
    except (urllib.error.URLError, TimeoutError) as error:
        raise AttachError("agent Responses API route probe failed") from error
    # OpenAI-compatible servers normally return 405 for a GET on this POST-only
    # route. A 400 response is also an acceptable route-level rejection; 404
    # means the harness has not enabled the Responses endpoint.
    if route_status not in {200, 400, 405}:
        fail("agent Responses API endpoint is not enabled or ready")
    return ApiReadiness(
        origin=api_url.rstrip("/"),
        token=token,
        model=profile.model if profile.model in model_ids else model_ids[0],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add VSS capabilities to an existing agent without replacing its identity."
    )
    parser.add_argument("--runtime", choices=sorted(PROFILES), required=True)
    parser.add_argument(
        "--sandbox", required=True, help="Existing NemoClaw sandbox name"
    )
    parser.add_argument("--vss-origin", required=True, help="VSS ingress origin")
    parser.add_argument("--repo-root", type=Path, default=repository_root())
    parser.add_argument(
        "--runtime-ref", help="Published VSS Git commit (defaults to repo HEAD)"
    )
    parser.add_argument("--runtime-repository", default=DEFAULT_RUNTIME_REPOSITORY)
    parser.add_argument("--runtime-dir", default=DEFAULT_RUNTIME_DIR)
    parser.add_argument("--agent-api-url", help="Forwarded harness API origin")
    parser.add_argument("--skip-api-check", action="store_true")
    parser.add_argument(
        "--receipt-output",
        type=Path,
        help="Optional protected host copy of the installed capability receipt",
    )
    parser.add_argument(
        "--gateway-env-output",
        type=Path,
        help=(
            "Write a protected Compose env overlay containing gateway/backend "
            "settings and the verified capability receipt"
        ),
    )
    parser.add_argument(
        "--gateway-bind-host",
        help=(
            "Private Docker bridge address for the gateway listener; discovered "
            "from the default bridge when omitted"
        ),
    )
    parser.add_argument("--gateway-port", type=int, default=18090)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def attach(args: argparse.Namespace, runner: CommandRunner) -> AttachmentResult:
    profile = PROFILES[args.runtime]
    sandbox = validate_sandbox_name(args.sandbox)
    origin = validate_origin(args.vss_origin)
    root = args.repo_root.resolve()
    skills = discover_skills(root)
    runtime_ref = resolve_runtime_ref(root, args.runtime_ref)
    verify_source_snapshot(root, runtime_ref)
    runtime_dir = validate_runtime_dir(args.runtime_dir)
    if args.gateway_env_output and args.skip_api_check:
        fail("--gateway-env-output cannot be combined with --skip-api-check")
    if args.gateway_env_output and runner.dry_run:
        fail("--gateway-env-output is unavailable during --dry-run")
    if not 1024 <= args.gateway_port <= 65535:
        fail("gateway port must be between 1024 and 65535")
    gateway_bind_host = (
        resolve_gateway_bind_host(runner, args.gateway_bind_host)
        if args.gateway_env_output
        else None
    )
    if shutil.which(profile.cli) is None and not runner.dry_run:
        fail(f"{profile.cli} is not installed")

    # Probe the sandbox execution boundary directly. The harness CLI's status
    # command may try to recover its preferred host forward, which is both
    # unnecessary and incorrect when an operator supplied --agent-api-url or
    # owns that forward separately. The authenticated API probe below verifies
    # the actual Responses delivery path after attachment.
    sandbox_exec(runner, profile, sandbox, "true", timeout=60)
    identity_before = identity_digest(runner, profile, sandbox)
    install_policy(runner, profile, sandbox, origin)
    install_skills(runner, profile, sandbox, skills)
    runtime_output = prepare_runtime(
        runner,
        profile,
        sandbox,
        runtime_dir=runtime_dir,
        repository=args.runtime_repository,
        runtime_ref=runtime_ref,
        origin=origin,
    )
    if runtime_output:
        print(runtime_output)
    restart_error = enable_api(runner, profile, sandbox)
    identity_after = identity_digest(runner, profile, sandbox)
    if identity_before != identity_after:
        fail("agent identity files changed during VSS attachment")

    api_origin = discover_api_origin(runner, profile, sandbox, args.agent_api_url)
    api = (
        None
        if args.skip_api_check
        else verify_api(runner, profile, sandbox, api_origin.url)
    )
    if restart_error is not None:
        if api is None:
            raise restart_error
        print(
            "Managed gateway restart reported an auxiliary failure; "
            "the live authenticated Responses API is ready."
        )
    # The receipt is the success marker consumed by VSS-aware skills. Publish it
    # only after every mutation and readiness check has passed, so a failed
    # first-time attachment cannot advertise capabilities it did not finish.
    receipt = build_receipt(
        profile,
        sandbox,
        origin=origin,
        runtime_dir=runtime_dir,
        runtime_ref=runtime_ref,
        skills=skills,
    )
    write_receipt(runner, profile, sandbox, receipt)
    if args.receipt_output:
        write_private_text(
            args.receipt_output,
            json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        )
        print(f"Host capability receipt: {args.receipt_output.expanduser()}")
    if args.gateway_env_output:
        if api is None or gateway_bind_host is None:  # pragma: no cover - guarded above
            fail("verified agent API and gateway bind host are required")
        write_gateway_env(
            args.gateway_env_output,
            profile=profile,
            api=api,
            receipt=receipt,
            runtime_ref=runtime_ref,
            bind_host=gateway_bind_host,
            port=args.gateway_port,
        )
        print(
            "Protected agent-gateway Compose overlay: "
            f"{args.gateway_env_output.expanduser()}"
        )
    print(
        "VSS capabilities attached; existing agent identity and memory were preserved."
    )
    print(f"Harness API: {api_origin.url}")
    print(f"Gateway protocol: responses; model: {api.model if api else profile.model}")
    print(f"Capability receipt: {RECEIPT_PATH}")
    if profile.runtime == "hermes":
        print("Start a new Hermes chat session to load the newly installed skills.")
    return AttachmentResult(receipt=receipt, api=api)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        attach(args, CommandRunner(dry_run=args.dry_run))
    except AttachError as error:
        print(f"attach-vss-agent: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
