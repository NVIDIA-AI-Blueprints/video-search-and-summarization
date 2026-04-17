"""Harbor environment provider for Brev GPU instances.

Two modes:

1. **Reuse an existing instance** (BREV_INSTANCE env var):
   Validate the instance's GPU meets the task's requirements
   (gpu_type, gpu_count, min_vram_gb_per_gpu from task.toml [metadata])
   and fail early if not.

2. **Auto-provision** (no BREV_INSTANCE):
   Query `brev search --json` for a matching instance type, create
   one, wait for ready.  The instance is stopped (not deleted) on
   trial completion so subsequent trials can reuse it.

Task.toml [metadata] fields consumed:
    gpu_type              — e.g. "L40S", "H100", "RTX PRO 6000"
    gpu_count             — 1 or 2
    min_vram_gb_per_gpu   — e.g. 48, 80
    brev_search           — (optional) substring override for brev search
    brev_instance         — (optional) explicit instance name override
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

    def _read_task_metadata(self) -> dict:
        """Read [metadata] from this task's task.toml."""
        try:
            import tomllib
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[no-redef]

        task_toml = self.environment_dir.parent / "task.toml"
        if not task_toml.exists():
            return {}
        return tomllib.loads(task_toml.read_text()).get("metadata", {}) or {}

    def _resolve_instance_name(self) -> str | None:
        """Resolve instance name: env var > task.toml > None (auto-provision)."""
        if DEFAULT_INSTANCE:
            return DEFAULT_INSTANCE
        meta = self._read_task_metadata()
        if "brev_instance" in meta:
            return meta["brev_instance"]
        return None

    async def start(self, force_build: bool) -> None:
        """Validate or provision a Brev instance matching task GPU requirements."""
        if self._started:
            return

        meta = self._read_task_metadata()
        requirements = {
            "gpu_type": meta.get("gpu_type"),
            "gpu_count": int(meta.get("gpu_count", 1)),
            "min_vram_gb_per_gpu": int(meta.get("min_vram_gb_per_gpu", 0)),
            "brev_search": meta.get("brev_search") or meta.get("gpu_type"),
        }

        self._instance_name = self._resolve_instance_name()

        if self._instance_name:
            # Mode 1: validate existing instance's GPU fits task requirements
            logger.info("Validating Brev instance '%s' against task requirements %s",
                        self._instance_name, requirements)
            instance = await _find_brev_instance(self._instance_name)
            if instance is None:
                raise RuntimeError(
                    f"Brev instance '{self._instance_name}' not found "
                    f"(is it deleted? wrong org?)"
                )
            _check_instance_matches(instance, requirements)
        else:
            # Mode 2: auto-provision via brev search + create.
            # Some platforms (DGX-SPARK, IGX-THOR) aren't provisionable as
            # cloud instance types — they're physical devices registered via
            # `brev register`.  Check there first and give a helpful error.
            if not requirements["brev_search"]:
                raise RuntimeError(
                    "No BREV_INSTANCE set and no GPU requirements in task.toml "
                    "[metadata] — cannot auto-provision."
                )
            logger.info("Auto-provisioning Brev instance for %s", requirements)
            instance_type = await _find_cheapest_matching_type(requirements)
            if not instance_type:
                # Before failing, list any registered nodes that might fit.
                suggestions = await _suggest_registered_devices(requirements)
                msg = [
                    f"Cannot auto-provision: no Brev cloud instance type matches",
                    f"  requirements: {requirements}",
                ]
                if suggestions:
                    msg.append("")
                    msg.append("Registered device(s) matching (or partially matching) these requirements:")
                    for s in suggestions:
                        msg.append(f"  - {s}")
                    msg.append("")
                    msg.append(
                        "Set `BREV_INSTANCE=<name>` or add `brev_instance = \"<name>\"` "
                        "to task.toml [metadata] to use one of these."
                    )
                else:
                    msg.append("")
                    msg.append(
                        "No registered devices match either. Options:\n"
                        "  1. Register a physical device via `brev register` "
                        "(DGX Spark / IGX Thor are typically registered, not provisioned).\n"
                        "  2. Adjust gpu_type / brev_search in the task to a provisionable "
                        "platform (e.g. H100, L40S, RTX PRO 6000)."
                    )
                full_msg = "\n".join(msg)
                logger.error(full_msg)
                raise RuntimeError(full_msg)
            self._instance_name = f"harbor-{uuid.uuid4().hex[:8]}"
            logger.info("Creating %s as %s", self._instance_name, instance_type)
            create_result = await _run_brev(
                "create", self._instance_name, "--detached",
                stdin_data=instance_type,
                timeout=120,
            )
            if create_result.return_code != 0:
                raise RuntimeError(f"brev create failed: {create_result.stderr}")
            await _wait_for_running(self._instance_name)

        # Quick smoke test — ensure exec works
        result = await _run_brev_exec(
            self._instance_name, "echo harbor-ready",
            timeout=60,
        )
        if result.return_code != 0:
            raise RuntimeError(
                f"Cannot reach Brev instance '{self._instance_name}': "
                f"{result.stderr}"
            )
        if "harbor-ready" not in (result.stdout or ""):
            raise RuntimeError(
                f"Unexpected response from instance '{self._instance_name}': "
                f"{(result.stdout or '')[:200]!r}"
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

        parts = [
            # Make sure user-installed binaries (claude, uv, etc.) are on PATH
            # even though `brev exec` spawns a non-interactive non-login shell.
            'export PATH="$HOME/.local/bin:$HOME/.claude/bin:$PATH";',
            "source ~/.profile 2>/dev/null;",
        ]
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


# ---------------------------------------------------------------------------
# Brev CLI wrappers (for create / ls / search)
# ---------------------------------------------------------------------------

async def _run_brev(*args: str, timeout: int = 30, stdin_data: str | None = None) -> ExecResult:
    """Generic brev CLI wrapper.  Stdin is closed via empty pipe if no data
    provided — prevents the CLI from hanging on its interactive walkthrough."""
    cmd = ["brev", *args]
    logger.debug("brev: %s", " ".join(args))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=(stdin_data or "").encode() + b"\n"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        if stdout and stdout.strip():
            return ExecResult(
                stdout=stdout.decode(),
                stderr=stderr.decode() if stderr else None,
                return_code=0,
            )
        return ExecResult(
            stdout=stdout.decode() if stdout else None,
            stderr="brev command timed out",
            return_code=124,
        )
    return ExecResult(
        stdout=stdout.decode() if stdout else None,
        stderr=stderr.decode() if stderr else None,
        return_code=proc.returncode or 0,
    )


def _parse_brev_json(raw: str | None) -> list[dict]:
    """Strip trailing walkthrough text and parse JSON array from brev CLI."""
    if not raw:
        return []
    bracket = raw.rfind("]")
    if bracket < 0:
        return []
    try:
        return json.loads(raw[: bracket + 1])
    except json.JSONDecodeError:
        return []


async def _find_brev_instance(name: str) -> dict | None:
    """Return the brev ls entry for `name`, or None if missing."""
    result = await _run_brev("ls", "--json", timeout=15)
    for inst in _parse_brev_json(result.stdout):
        if inst.get("name") == name:
            return inst
    return None


def _check_instance_matches(instance: dict, req: dict) -> None:
    """Raise RuntimeError if the instance's GPU doesn't meet task requirements."""
    gpu = (instance.get("gpu") or "").upper()
    gpu_count = int(instance.get("gpu_count", 0) or 0)
    total_vram = float(instance.get("total_vram_gb", 0) or 0)
    vram_per_gpu = (total_vram / gpu_count) if gpu_count > 0 else 0

    required_type = (req.get("gpu_type") or "").upper()
    required_count = req.get("gpu_count", 1)
    required_vram = req.get("min_vram_gb_per_gpu", 0)

    errors = []
    if required_type and required_type not in gpu:
        errors.append(f"gpu_type: want {required_type!r}, have {gpu!r}")
    if gpu_count < required_count:
        errors.append(f"gpu_count: want {required_count}, have {gpu_count}")
    if required_vram and vram_per_gpu < required_vram:
        errors.append(
            f"vram_per_gpu: want {required_vram} GB, have {vram_per_gpu:.0f} GB"
        )

    if errors:
        raise RuntimeError(
            f"Brev instance '{instance.get('name')}' does not meet task "
            f"requirements:\n  - " + "\n  - ".join(errors) +
            f"\n  (instance: type={instance.get('instance_type')}, "
            f"gpu={gpu}, count={gpu_count}, vram={total_vram} GB)"
        )

    logger.info(
        "Instance '%s' meets requirements: gpu=%s count=%s vram=%s GB",
        instance.get("name"), gpu, gpu_count, total_vram,
    )


async def _find_cheapest_matching_type(req: dict) -> str | None:
    """Find the cheapest `brev search` instance type matching GPU requirements."""
    result = await _run_brev("search", "--json", timeout=30)
    search = (req.get("brev_search") or "").lower()
    required_count = req.get("gpu_count", 1)
    required_vram = req.get("min_vram_gb_per_gpu", 0)

    candidates = []
    for inst in _parse_brev_json(result.stdout):
        gpu_name = (inst.get("gpu_name") or "").lower()
        gpu_count = int(inst.get("gpu_count", 0) or 0)
        total_vram = float(inst.get("total_vram_gb", 0) or 0)
        if search and search not in gpu_name:
            continue
        if gpu_count < required_count:
            continue
        if required_vram and (total_vram / max(gpu_count, 1)) < required_vram:
            continue
        candidates.append(inst)

    if not candidates:
        return None
    candidates.sort(key=lambda x: float(x.get("price_per_hour", 0) or 0))
    return candidates[0].get("type")


async def _suggest_registered_devices(req: dict) -> list[str]:
    """Query `brev ls nodes --json` for registered physical devices that
    match the task's requirements (best-effort, by name substring).
    Returns human-readable strings for error messages."""
    result = await _run_brev("ls", "nodes", "--json", timeout=15)
    nodes = _parse_brev_json(result.stdout)
    if not nodes:
        return []
    search = (req.get("brev_search") or req.get("gpu_type") or "").lower()
    suggestions = []
    for n in nodes:
        name = n.get("name") or ""
        status = n.get("status") or "?"
        # Node entries don't include GPU specs; fall back to name matching.
        # If search term appears in node name, it's a likely fit.
        if search and search in name.lower():
            suggestions.append(f"{name}  (status={status})  [name matches '{search}']")
    # Also include all connected nodes as fallback suggestions.
    if not suggestions:
        for n in nodes:
            if n.get("status") == "Connected":
                suggestions.append(
                    f"{n.get('name')}  (status=Connected)  "
                    f"[GPU unknown — verify manually]"
                )
    return suggestions


async def _wait_for_running(
    name: str,
    timeout_sec: int = 2400,
    poll_interval: int = 15,
) -> None:
    """Poll `brev ls` until the named instance reaches RUNNING + shell READY."""
    elapsed = 0
    while elapsed < timeout_sec:
        inst = await _find_brev_instance(name)
        if inst:
            status = inst.get("status")
            shell = inst.get("shell_status")
            if status == "FAILURE":
                raise RuntimeError(f"Brev instance {name} creation FAILED")
            if status == "RUNNING" and shell == "READY":
                return
            logger.info(
                "Waiting for %s (status=%s shell=%s, %ds/%ds)",
                name, status, shell, elapsed, timeout_sec,
            )
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
    raise TimeoutError(
        f"Brev instance {name} did not become ready within {timeout_sec}s"
    )
