"""Harbor environment provider that runs tasks on Brev GPU instances.

Usage with Harbor:
    harbor run --env "tools.eval.harbor.envs.brev_env:BrevEnvironment" \
        --dataset datasets/my-dataset --agent claude-code

Requires:
    - `brev` CLI installed and authenticated (`brev login`)
    - `harbor` package installed (provides BaseEnvironment)

The provider creates a bare Brev instance per task. All setup (Docker,
NVIDIA toolkit, repo clone, deployment) is the agent's responsibility.
GPU type is selected from task metadata (task.toml) or falls back to
the BREV_INSTANCE_TYPE env var.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import uuid
from enum import Enum
from pathlib import Path

from harbor.environments.base import BaseEnvironment, ExecResult

logger = logging.getLogger(__name__)

# Defaults — override via env vars or task metadata
DEFAULT_INSTANCE_TYPE = os.environ.get("BREV_INSTANCE_TYPE", "g5.xlarge")
DEFAULT_GPU_TYPE = os.environ.get("BREV_GPU_TYPE", "A10G")
BREV_STARTUP_TIMEOUT = int(os.environ.get("BREV_STARTUP_TIMEOUT", "600"))
BREV_POLL_INTERVAL = int(os.environ.get("BREV_POLL_INTERVAL", "15"))
# Default timeout for brev CLI commands (the CLI enters an interactive
# walkthrough after output, so we need to kill it after capturing output)
BREV_CMD_TIMEOUT = int(os.environ.get("BREV_CMD_TIMEOUT", "60"))


class BrevEnvironmentType(str, Enum):
    BREV = "brev"


class BrevEnvironment(BaseEnvironment):
    """Harbor environment that provisions a bare Brev GPU instance per task.

    Lifecycle:
        start()    → brev create, wait for RUNNING
        exec()     → brev exec <command>
        upload()   → scp via brev SSH config
        download() → scp via brev SSH config
        stop()     → brev delete

    The instance is bare — no Docker, no repo, no setup. The agent
    (or oracle solve.sh) handles all setup as part of the task.
    """

    def __init__(self, **kwargs):  # noqa: ANN003
        super().__init__(**kwargs)
        self._instance_name: str | None = None
        self._instance_type: str = DEFAULT_INSTANCE_TYPE
        self._started = False

    @staticmethod
    def type() -> BrevEnvironmentType:
        return BrevEnvironmentType.BREV

    @property
    def is_mounted(self) -> bool:
        # Files are uploaded/downloaded explicitly, not bind-mounted
        return False

    @property
    def supports_gpus(self) -> bool:
        return True

    @property
    def can_disable_internet(self) -> bool:
        return False

    def _validate_definition(self) -> None:
        # Check brev CLI is available
        if not _which("brev"):
            msg = "brev CLI not found. Install from https://docs.brev.dev/"
            raise RuntimeError(msg)

    def _resolve_instance_type(self) -> str:
        """Resolve instance type from task.toml metadata or env vars.

        Reads task.toml from the parent of environment_dir (the task root).
        """
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

        task_toml = self.environment_dir.parent / "task.toml"
        if task_toml.exists():
            data = tomllib.loads(task_toml.read_text())
            meta = data.get("metadata", {})
            if "brev_instance_type" in meta:
                return meta["brev_instance_type"]
            if "gpu" in meta:
                return _gpu_to_instance_type(meta["gpu"])
        return self._instance_type

    async def start(self, force_build: bool) -> None:
        """Create a Brev instance and wait for it to be ready."""
        if self._started:
            return

        self._instance_type = self._resolve_instance_type()
        self._instance_name = f"harbor-{uuid.uuid4().hex[:8]}"

        logger.info(
            "Creating Brev instance %s (type=%s)",
            self._instance_name,
            self._instance_type,
        )

        # Create instance. Pipe the instance type via stdin (not --type) to
        # force the exact type instead of Brev's search/fallback logic.
        # --detached avoids the interactive onboarding prompt.
        logger.warning(
            "Creating Brev instance %s with type %s (piped via stdin)",
            self._instance_name, self._instance_type,
        )
        result = await _run_brev(
            "create", self._instance_name,
            "--detached",
            stdin_data=self._instance_type,
        )
        logger.warning("brev create result: rc=%s stdout=%s stderr=%s",
                       result.return_code, result.stdout[:200] if result.stdout else None,
                       result.stderr[:200] if result.stderr else None)
        if result.return_code != 0:
            msg = f"brev create failed: {result.stderr}"
            raise RuntimeError(msg)

        # Wait for RUNNING status
        await self._wait_for_running()

        self._started = True
        logger.info("Brev instance %s is ready", self._instance_name)

    async def stop(self, delete: bool) -> None:
        """Stop and optionally delete the Brev instance."""
        if not self._instance_name:
            return

        if delete:
            logger.info("Deleting Brev instance %s", self._instance_name)
            await _run_brev("delete", self._instance_name)
        else:
            logger.info("Stopping Brev instance %s", self._instance_name)
            await _run_brev("stop", self._instance_name)

        self._started = False

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        """Upload a local file to the Brev instance via SCP."""
        assert self._instance_name
        result = await _run_brev(
            "cp", f"local:{source_path}", f"{self._instance_name}:{target_path}",
        )
        if result.return_code != 0:
            msg = f"Upload failed: {result.stderr}"
            raise RuntimeError(msg)

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        """Upload a local directory to the Brev instance."""
        assert self._instance_name
        result = await _run_brev(
            "cp", f"local:{source_dir}", f"{self._instance_name}:{target_dir}",
        )
        if result.return_code != 0:
            msg = f"Upload dir failed: {result.stderr}"
            raise RuntimeError(msg)

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        """Download a file from the Brev instance to local."""
        assert self._instance_name
        result = await _run_brev(
            "cp", f"{self._instance_name}:{source_path}", f"local:{target_path}",
        )
        if result.return_code != 0:
            msg = f"Download failed: {result.stderr}"
            raise RuntimeError(msg)

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        """Download a directory from the Brev instance to local."""
        assert self._instance_name
        result = await _run_brev(
            "cp", f"{self._instance_name}:{source_dir}", f"local:{target_dir}",
        )
        if result.return_code != 0:
            msg = f"Download dir failed: {result.stderr}"
            raise RuntimeError(msg)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_sec: int | None = None,
        user: str | int | None = None,
    ) -> ExecResult:
        """Execute a command on the Brev instance."""
        assert self._instance_name

        # Build the command with optional cwd and env
        parts = []
        if env:
            for k, v in env.items():
                parts.append(f"export {shlex.quote(k)}={shlex.quote(v)};")
        if cwd:
            parts.append(f"cd {shlex.quote(cwd)};")
        parts.append(command)

        full_cmd = " ".join(parts)

        return await _run_brev(
            "exec", self._instance_name, full_cmd,
            timeout=timeout_sec,
        )

    # -- Internal helpers --

    async def _wait_for_running(self) -> None:
        """Poll until the instance reaches RUNNING status."""
        elapsed = 0
        while elapsed < BREV_STARTUP_TIMEOUT:
            result = await _run_brev("ls", "--json")
            if result.return_code == 0 and result.stdout:
                try:
                    # brev ls --json appends walkthrough text after the JSON array.
                    # Strip everything after the closing bracket.
                    raw = result.stdout
                    bracket = raw.rfind("]")
                    if bracket >= 0:
                        raw = raw[: bracket + 1]
                    instances = json.loads(raw)
                    for inst in instances:
                        if inst.get("name") == self._instance_name:
                            if inst.get("status") == "RUNNING":
                                return
                except json.JSONDecodeError:
                    pass

            logger.info(
                "Waiting for %s to start (%ds / %ds)...",
                self._instance_name, elapsed, BREV_STARTUP_TIMEOUT,
            )
            await asyncio.sleep(BREV_POLL_INTERVAL)
            elapsed += BREV_POLL_INTERVAL

        msg = f"Brev instance {self._instance_name} did not start within {BREV_STARTUP_TIMEOUT}s"
        raise TimeoutError(msg)


# -- Module-level helpers --

def _which(cmd: str) -> bool:
    """Check if a command is on PATH."""
    import shutil
    return shutil.which(cmd) is not None


async def _run_brev(
    *args: str,
    timeout: int | None = None,
    stdin_data: str | None = None,
) -> ExecResult:
    """Run a brev CLI command and return structured result.

    Uses a default timeout because the Brev CLI enters an interactive
    walkthrough after printing output, which would hang forever.
    A timeout kill is treated as success if stdout was captured.
    """
    if timeout is None:
        timeout = BREV_CMD_TIMEOUT

    cmd = ["brev", *args]
    logger.debug("Running: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE if stdin_data else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdin_bytes = stdin_data.encode() if stdin_data else None
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=stdin_bytes),
            timeout=timeout,
        )
    except TimeoutError:
        # Brev CLI likely hanging on interactive walkthrough after output.
        # Kill it and return whatever stdout we captured.
        proc.kill()
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode() if stdout else None
        stderr_str = stderr.decode() if stderr else None
        # If we got stdout, treat the timeout as success (walkthrough killed)
        if stdout_str and stdout_str.strip():
            logger.debug("brev command timed out but produced output — treating as success")
            return ExecResult(stdout=stdout_str, stderr=stderr_str, return_code=0)
        return ExecResult(stdout=stdout_str, stderr="Command timed out", return_code=124)

    return ExecResult(
        stdout=stdout.decode() if stdout else None,
        stderr=stderr.decode() if stderr else None,
        return_code=proc.returncode or 0,
    )


# GPU name -> Brev instance type mapping
_GPU_INSTANCE_MAP = {
    "A10G": "g5.xlarge",
    "A100": "p4d.24xlarge",
    "A100-80GB": "p4de.24xlarge",
    "H100": "p5.48xlarge",
    "L40S": "g6e.xlarge",
    "L4": "g6.xlarge",
    "T4": "g4dn.xlarge",
}


def _gpu_to_instance_type(gpu_name: str) -> str:
    """Map a GPU name from task metadata to a Brev instance type."""
    gpu_upper = gpu_name.upper()
    for key, instance_type in _GPU_INSTANCE_MAP.items():
        if key.upper() in gpu_upper:
            return instance_type
    logger.warning("Unknown GPU %s, falling back to %s", gpu_name, DEFAULT_INSTANCE_TYPE)
    return DEFAULT_INSTANCE_TYPE
