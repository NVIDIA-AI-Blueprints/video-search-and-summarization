"""Harbor environment provider that runs tasks on Brev GPU instances.

Usage with Harbor:
    harbor run --env "tools.eval.harbor.brev_env:BrevEnvironment" \
        --dataset datasets/my-dataset --agent claude-code

Requires:
    - `brev` CLI installed and authenticated (`brev login`)
    - `harbor` package installed (provides BaseEnvironment)

The provider creates a Brev instance per task, uploads the task files,
runs the agent, downloads results, and tears down the instance.

GPU type is selected from task metadata (task.toml) or falls back to
the BREV_GPU_TYPE / BREV_INSTANCE_TYPE env vars.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from enum import Enum
from pathlib import Path

from harbor.environments.base import BaseEnvironment, ExecResult

logger = logging.getLogger(__name__)

# Defaults — override via env vars or task metadata
DEFAULT_INSTANCE_TYPE = os.environ.get("BREV_INSTANCE_TYPE", "g5.xlarge")
DEFAULT_GPU_TYPE = os.environ.get("BREV_GPU_TYPE", "A10G")
BREV_STARTUP_TIMEOUT = int(os.environ.get("BREV_STARTUP_TIMEOUT", "600"))
BREV_POLL_INTERVAL = int(os.environ.get("BREV_POLL_INTERVAL", "15"))


class BrevEnvironmentType(str, Enum):
    BREV = "brev"


class BrevEnvironment(BaseEnvironment):
    """Harbor environment that provisions a Brev GPU instance per task.

    Lifecycle:
        start()  → brev create, wait for RUNNING, install Docker
        exec()   → brev exec <command>
        upload() → scp via brev SSH config
        download() → scp via brev SSH config
        stop()   → brev delete
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
        """Resolve instance type from task metadata or env vars."""
        # Check task metadata for GPU requirements
        if hasattr(self, "task") and self.task:
            meta = getattr(self.task, "metadata", {}) or {}
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
        self._instance_name = f"harbor-{os.getpid()}-{id(self)}"

        logger.info(
            "Creating Brev instance %s (type=%s)",
            self._instance_name,
            self._instance_type,
        )

        # Create instance
        result = await _run_brev(
            "create", self._instance_name,
            "--type", self._instance_type,
            "--no-open",
        )
        if result.return_code != 0:
            msg = f"brev create failed: {result.stderr}"
            raise RuntimeError(msg)

        # Wait for RUNNING status
        await self._wait_for_running()

        # Install Docker if needed (Brev images may not have it)
        await self._ensure_docker()

        self._started = True
        logger.info("Brev instance %s is ready", self._instance_name)

    async def stop(self, delete: bool) -> None:
        """Stop and optionally delete the Brev instance."""
        if not self._instance_name:
            return

        if delete:
            logger.info("Deleting Brev instance %s", self._instance_name)
            await _run_brev("delete", self._instance_name, "--yes")
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
                    instances = json.loads(result.stdout)
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

    async def _ensure_docker(self) -> None:
        """Install Docker on the instance if not present."""
        check = await self.exec("docker --version")
        if check.return_code == 0:
            return

        logger.info("Installing Docker on %s...", self._instance_name)
        install_cmd = (
            "curl -fsSL https://get.docker.com | sh && "
            "sudo usermod -aG docker $USER"
        )
        result = await self.exec(install_cmd)
        if result.return_code != 0:
            msg = f"Docker install failed: {result.stderr}"
            raise RuntimeError(msg)


# -- Module-level helpers --

def _which(cmd: str) -> bool:
    """Check if a command is on PATH."""
    import shutil
    return shutil.which(cmd) is not None


async def _run_brev(*args: str, timeout: int | None = None) -> ExecResult:
    """Run a brev CLI command and return structured result."""
    cmd = ["brev", *args]
    logger.debug("Running: %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        return ExecResult(stdout=None, stderr="Command timed out", return_code=124)

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
