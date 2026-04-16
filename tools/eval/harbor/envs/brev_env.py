"""Harbor environment provider that uses a pre-existing Brev GPU instance.

Instead of creating/destroying instances per task, this provider connects
to an already-running Brev instance via ``brev exec`` and ``brev copy``.
This avoids the slow and unreliable instance provisioning loop.

Set the instance name via:
  - BREV_INSTANCE env var, or
  - ``brev_instance`` in task.toml [metadata]

Usage:
    # Pre-create your instance once:
    brev create my-eval-gpu --detached
    # Wait for it to be RUNNING+READY, then:

    BREV_INSTANCE=my-eval-gpu harbor run \
        --environment-import-path "tools.eval.harbor.envs.brev_env:BrevEnvironment" \
        -p datasets/deploy -a claude-code -n 1

Requires:
    - ``brev`` CLI installed and authenticated
    - A running Brev instance (status=RUNNING, shell=READY)
    - ``harbor`` package (provides BaseEnvironment)
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
from enum import Enum
from pathlib import Path

from harbor.environments.base import BaseEnvironment, ExecResult

logger = logging.getLogger(__name__)

# The pre-existing Brev instance to connect to.
# CLI env var > task.toml metadata > None (error).
DEFAULT_INSTANCE = os.environ.get("BREV_INSTANCE")

# Timeout for brev exec commands (seconds).  Set high for long deploys.
BREV_EXEC_TIMEOUT = int(os.environ.get("BREV_EXEC_TIMEOUT", "1800"))

# Timeout for brev copy commands.
BREV_COPY_TIMEOUT = int(os.environ.get("BREV_COPY_TIMEOUT", "300"))


class BrevEnvironmentType(str, Enum):
    BREV = "brev"


class BrevEnvironment(BaseEnvironment):
    """Harbor environment that connects to a pre-existing Brev instance.

    Lifecycle:
        start()    → validate instance is reachable (no provisioning)
        exec()     → brev exec <instance> <command>
        upload()   → brev copy local:<path> <instance>:<path>
        download() → brev copy <instance>:<path> local:<path>
        stop()     → no-op (instance stays running for reuse)
    """

    def __init__(self, **kwargs):  # noqa: ANN003
        super().__init__(**kwargs)
        self._instance_name: str | None = DEFAULT_INSTANCE
        self._started = False

    @staticmethod
    def type() -> BrevEnvironmentType:
        return BrevEnvironmentType.BREV

    @property
    def is_mounted(self) -> bool:
        return False

    @property
    def supports_gpus(self) -> bool:
        return True

    @property
    def can_disable_internet(self) -> bool:
        return False

    def _validate_definition(self) -> None:
        if not _which("brev"):
            raise RuntimeError(
                "brev CLI not found. Install from https://docs.brev.dev/"
            )

    def _resolve_instance_name(self) -> str:
        """Resolve instance name: env var > task.toml > error."""
        # Env var takes priority
        if DEFAULT_INSTANCE:
            return DEFAULT_INSTANCE

        # Check task.toml metadata
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

        task_toml = self.environment_dir.parent / "task.toml"
        if task_toml.exists():
            data = tomllib.loads(task_toml.read_text())
            meta = data.get("metadata", {})
            if "brev_instance" in meta:
                return meta["brev_instance"]

        raise RuntimeError(
            "No Brev instance specified. Set BREV_INSTANCE env var or "
            "add brev_instance to task.toml [metadata]."
        )

    async def start(self, force_build: bool) -> None:
        """Validate the pre-existing instance is reachable."""
        if self._started:
            return

        self._instance_name = self._resolve_instance_name()

        logger.info("Connecting to existing Brev instance: %s", self._instance_name)

        # Quick smoke test — run a trivial command
        result = await _run_brev_exec(
            self._instance_name, "echo harbor-ready",
            timeout=60,
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Cannot reach Brev instance '{self._instance_name}': "
                f"{result.stderr}"
            )

        stdout = (result.stdout or "").strip()
        if "harbor-ready" not in stdout:
            raise RuntimeError(
                f"Unexpected response from instance '{self._instance_name}': "
                f"{stdout!r}"
            )

        # Pre-create harbor's expected directories with correct ownership
        # so that agent and verifier processes can write to them.
        await _run_brev_exec(
            self._instance_name,
            "sudo mkdir -p /logs/agent /logs/verifier /logs/artifacts /tests /solution /skills && "
            "sudo chown -R $(whoami):$(id -gn) /logs /tests /solution /skills",
            timeout=30,
        )

        # Upload the task's skills/ directory to /skills on the instance
        # so Claude Code can register them via task.toml:
        # [environment] skills_dir = "/skills"
        task_dir = self.environment_dir.parent
        task_skills_dir = task_dir / "skills"
        if task_skills_dir.is_dir():
            logger.info("Uploading skills from %s to /skills on instance", task_skills_dir)
            await self.upload_dir(str(task_skills_dir), "/skills")

        self._started = True
        logger.info("Brev instance %s is reachable", self._instance_name)

    async def stop(self, delete: bool) -> None:
        """No-op — the instance stays running for reuse."""
        logger.info(
            "Leaving Brev instance %s running (delete=%s)",
            self._instance_name, delete,
        )
        self._started = False

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        assert self._instance_name
        # Ensure parent directory exists with correct ownership
        parent = str(Path(target_path).parent)
        if parent and parent != ".":
            await _run_brev_exec(
                self._instance_name,
                f"sudo mkdir -p {shlex.quote(parent)} && "
                f"sudo chown $(whoami):$(id -gn) {shlex.quote(parent)}",
                timeout=30,
            )
        result = await _run_brev_copy(
            str(source_path), f"{self._instance_name}:{target_path}",
        )
        if result.return_code != 0:
            raise RuntimeError(f"Upload failed: {result.stderr}")

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        assert self._instance_name
        # brev copy has broken directory nesting behaviour.  Use tar
        # piped over brev exec: tar locally, base64-encode, send via
        # exec, decode+untar on the remote side.
        src = str(source_dir).rstrip("/")
        import subprocess as _sp, base64 as _b64
        tar_bytes = _sp.check_output(
            ["tar", "-czf", "-", "-C", src, "."],
            timeout=60,
        )
        encoded = _b64.b64encode(tar_bytes).decode()
        result = await _run_brev_exec(
            self._instance_name,
            f"sudo mkdir -p {shlex.quote(target_dir)} && "
            f"sudo chown $(whoami):$(id -gn) {shlex.quote(target_dir)} && "
            f"echo '{encoded}' | base64 -d | tar -xzf - -C {shlex.quote(target_dir)}",
            timeout=120,
        )
        if result.return_code != 0:
            raise RuntimeError(f"Upload dir failed: {result.stderr}")

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        assert self._instance_name
        result = await _run_brev_copy(
            f"{self._instance_name}:{source_path}", str(target_path),
        )
        if result.return_code != 0:
            raise RuntimeError(f"Download failed: {result.stderr}")

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        assert self._instance_name
        # brev copy has broken directory nesting.  Use tar piped over
        # brev exec: tar on remote, base64-encode, capture via exec,
        # decode+untar locally.
        import base64 as _b64, subprocess as _sp
        result = await _run_brev_exec(
            self._instance_name,
            f"tar -czf - -C {shlex.quote(source_dir)} . 2>/dev/null | base64",
            timeout=120,
        )
        if result.return_code != 0:
            raise RuntimeError(f"Download dir failed: {result.stderr}")
        # Decode and untar locally
        stdout = result.stdout or ""
        # Strip any non-base64 noise (spinner chars, connection messages)
        clean = "".join(
            line for line in stdout.splitlines()
            if line and not line.startswith("[") and not line.startswith("Connection")
            and not line.startswith("ssh:") and line.strip()
        )
        tar_bytes = _b64.b64decode(clean)
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        _sp.run(
            ["tar", "-xzf", "-", "-C", str(target)],
            input=tar_bytes, check=True, timeout=60,
        )

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        assert self._instance_name

        parts = ["source ~/.profile 2>/dev/null;"]
        if env:
            for k, v in env.items():
                parts.append(f"export {shlex.quote(k)}={shlex.quote(v)};")
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)};")
        parts.append(command)

        inner_cmd = " ".join(parts)

        # Brev connects as non-root (ubuntu).  Harbor's agent-setup
        # phase runs package-manager commands that need root.  We detect
        # those and prepend sudo; everything else runs as the normal
        # user so that file ownership stays consistent with brev copy.
        needs_root = (
            user == "root" or user == 0
            or "apt-get " in command
            or "apk " in command
            or "yum " in command
        )
        if needs_root:
            full_cmd = f"sudo bash -c {shlex.quote(inner_cmd)}"
        else:
            full_cmd = inner_cmd

        return await _run_brev_exec(
            self._instance_name, full_cmd,
            timeout=timeout_sec or BREV_EXEC_TIMEOUT,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _which(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None


async def _run_brev_exec(
    instance: str,
    command: str,
    timeout: int = BREV_EXEC_TIMEOUT,
) -> ExecResult:
    """Run ``brev exec <instance> <command>`` and return result.

    Uses ``bash -c`` wrapping via a shell so that ``brev exec`` receives
    a single command string.  Stdin is piped with empty input so the
    brev CLI doesn't enter interactive mode.
    """
    # brev exec <instance> <command> — brev handles SSH transparently
    cmd = ["brev", "exec", instance, command]
    logger.debug("brev exec: %s", command[:200])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b"\n"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return ExecResult(
            stdout=stdout.decode() if stdout else None,
            stderr="Command timed out",
            return_code=124,
        )

    return ExecResult(
        stdout=stdout.decode() if stdout else None,
        stderr=stderr.decode() if stderr else None,
        return_code=proc.returncode or 0,
    )


async def _run_brev_copy(
    src: str,
    dst: str,
    timeout: int = BREV_COPY_TIMEOUT,
) -> ExecResult:
    """Run ``brev copy <src> <dst>`` and return result."""
    cmd = ["brev", "copy", src, dst]
    logger.debug("brev copy: %s -> %s", src, dst)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b"\n"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return ExecResult(
            stdout=stdout.decode() if stdout else None,
            stderr="Copy timed out",
            return_code=124,
        )

    return ExecResult(
        stdout=stdout.decode() if stdout else None,
        stderr=stderr.decode() if stderr else None,
        return_code=proc.returncode or 0,
    )
