# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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


def _record_started_instance(name: str) -> None:
    """Append an auto-provisioned instance name to the wrapper's
    cleanup marker (`/tmp/brev/started-by-<run_id>.txt`) so
    skills_eval_agent.cleanup_instances() tears it down even if the
    agent never observes the name. No-op outside CI (no GITHUB_RUN_ID)."""
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not run_id:
        return
    try:
        marker = Path(f"/tmp/brev/started-by-{run_id}.txt")
        marker.parent.mkdir(parents=True, exist_ok=True)
        with marker.open("a") as fh:
            fh.write(f"{name}\n")
    except OSError as exc:
        logger.warning("failed to record %s in started-by marker: %s", name, exc)


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
            "min_root_disk_gb": int(meta.get("min_root_disk_gb", 0)),
            "min_gpu_driver_version": meta.get("min_gpu_driver_version"),
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
            # Record the harbor-* instance in the wrapper's cleanup marker
            # so skills_eval_agent.cleanup_instances() tears it down even if
            # the trial fails before the agent tracks it. Append before
            # _wait_for_running so a timeout there doesn't leak an orphan.
            _record_started_instance(self._instance_name)
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

        # Post-provision resource checks: root disk + GPU driver.
        # These catch provider quirks that brev search doesn't surface
        # (e.g. hyperstack_H100x2 lists disk_min_gb=1600 but mounts the
        # big volume on /ephemeral — / is only ~100 GB, which OOMs on
        # local NIM pulls).
        await _check_live_resources(self._instance_name, requirements)

        # Pre-create harbor's expected directories with correct ownership
        # so that agent and verifier processes can write to them.
        await _run_brev_exec(
            self._instance_name,
            "sudo mkdir -p /logs/agent /logs/verifier /logs/artifacts /tests /solution /skills && "
            "sudo chown -R $(whoami):$(id -gn) /logs /tests /solution /skills",
            timeout=30,
        )

        # Archive any session JSONLs left by prior trials on this warm-pool
        # box. Without this, harbor's claude-code mapper merges every
        # `*.jsonl` file under `/logs/agent/sessions/projects/<project>/`
        # into one trajectory.json — producing thousand-step trajectories
        # that conflate this trial with every preceding one (observed:
        # trial 25083019759/.../step-1__XZNnjCX showed 7549 steps spanning
        # 50h of prior runs).
        #
        # We *move* (not delete) the JSONLs into `$HOME/.claude-archive/<ts>/`
        # so they remain visitable via SSH for forensic debugging. Each
        # trial's own snapshot is preserved per-trial under
        # `/tmp/skill-eval/results/<run>/<date>/<trial>/agent/sessions/`
        # already (harbor's per-trial copy-back), so this archive is just
        # box-side history.
        #
        # Why archive only, not also per-trial cwd: harbor's claude-code
        # agent (vendor cache) invokes `claude --print` with no cwd
        # override, so all trials share `cwd=/home/shadeform` and the
        # project key is `-home-shadeform`. Forcing a per-trial cwd would
        # require forking harbor — out of scope. Empty-on-start is
        # sufficient for the harbor mapper's "exactly one session dir"
        # heuristic to produce a clean per-trial trajectory.
        archive_cmd = (
            "ts=$(date +%Y%m%d-%H%M%S); "
            "PROJ=/logs/agent/sessions/projects; "
            'if [ -d "$PROJ" ] && [ -n "$(ls -A "$PROJ" 2>/dev/null)" ]; then '
            '  ARCHIVE=$HOME/.claude-archive/$ts; '
            '  mkdir -p "$ARCHIVE" && mv "$PROJ"/* "$ARCHIVE/" 2>/dev/null || true; '
            '  echo "[trajectory-isolation] archived prior project dirs to $ARCHIVE"; '
            "fi"
        )
        await _run_brev_exec(self._instance_name, archive_cmd, timeout=30)

        # Forward task-critical env vars from the local shell into the
        # instance's ~/.eval_env (sourced by ~/.profile, which every
        # brev exec then sources).  Harbor's claude-code agent only
        # propagates ANTHROPIC_* env vars, so anything else needed
        # during deploy (NGC_CLI_API_KEY, NVIDIA_API_KEY) must land on
        # the instance out-of-band.
        forwarded: list[tuple[str, str]] = [
            # claude-code 2.1.x emits a `context_management` field in every
            # /v1/messages body to drive server-side thinking-block cleanup
            # (`clear_thinking_20251015`). NVIDIA's Anthropic-compatible
            # proxy (our subagent trials route through it via
            # `--ak api_base=${ANTHROPIC_BASE_URL}/v1`) rejects the field
            # with HTTP 400. Disabling thinking client-side is the only
            # CLI toggle that stops the field from being sent; trials
            # don't rely on extended thinking, so the cost is negligible.
            # Revisit if/when the proxy accepts the field.
            ("CLAUDE_CODE_DISABLE_THINKING", "1"),
        ]
        for key in ("NGC_CLI_API_KEY", "NVIDIA_API_KEY", "HF_TOKEN"):
            val = os.environ.get(key)
            if val:
                forwarded.append((key, val))
        if forwarded:
            env_block = "\n".join(
                f"export {k}={shlex.quote(v)}" for k, v in forwarded
            )
            bootstrap = (
                f"cat > ~/.eval_env <<'__HARBOR_EOF__'\n"
                f"{env_block}\n"
                f"__HARBOR_EOF__\n"
                f"grep -q 'source ~/.eval_env' ~/.profile 2>/dev/null || "
                f"echo 'source ~/.eval_env 2>/dev/null' >> ~/.profile"
            )
            logger.info("Writing %d forwarded env vars to ~/.eval_env on instance",
                        len(forwarded))
            await _run_brev_exec(self._instance_name, bootstrap, timeout=30)

        # Upload the task's skills/ directory to /skills on the instance
        # so Claude Code can register them via task.toml:
        # [environment] skills_dir = "/skills"
        task_dir = self.environment_dir.parent
        task_skills_dir = task_dir / "skills"
        if task_skills_dir.is_dir():
            logger.info("Uploading skills from %s to /skills on instance", task_skills_dir)
            await self.upload_dir(str(task_skills_dir), "/skills")

        # Pre-deploy any prerequisite profile declared in task.toml [metadata].
        # Idempotent via marker file on the box, so dependent trials reuse the
        # deployment without re-running it.
        await self._ensure_prerequisite_deployed(meta)

        self._started = True
        logger.info("Brev instance %s is reachable", self._instance_name)

    async def _ensure_prerequisite_deployed(self, meta: dict) -> None:
        """If task.toml [metadata] declares both `profile` and
        `prerequisite_deploy_mode`, ensure /deploy has run on the Brev
        box for that profile-mode pair. Reads a single canonical
        marker that records what is currently RUNNING on the box —
        not a deploy log. See specs/stale-marker.spec.

        Algorithm:
          1. cat /tmp/skill-eval/active-deploy.txt on the box.
          2. If contents == f"{profile}-{deploy_mode}" → no-op (hot).
          3. Else → run /deploy via claude --print; the deploy skill's
             own step-0 teardown handles any prior stack. On success
             OVERWRITE the marker. On failure leave it alone — next
             trial re-evaluates.

        Trials with NO `profile` (skills that don't need a deployed
        VSS) skip this entirely. Deploy/* trials set `profile` but NOT
        `prerequisite_deploy_mode` (they ARE the deploy), so they also
        early-return; their test.sh writes the marker on reward=1.0.

        claude-code is expected on the box from a prior deploy/* trial's
        harbor agent setup; persists across trials on the reused
        vss-eval-* instance. Override the wall clock via
        PRE_DEPLOY_TIMEOUT_SEC (default 1800s)."""
        profile = meta.get("profile")
        deploy_mode = meta.get("prerequisite_deploy_mode")
        if not profile or not deploy_mode:
            return

        desired = f"{profile}-{deploy_mode}"
        marker_path = "/tmp/skill-eval/active-deploy.txt"
        probe = await _run_brev_exec(
            self._instance_name,
            f"cat {shlex.quote(marker_path)} 2>/dev/null || true",
            timeout=30,
        )
        current = (probe.stdout or "").strip()
        if current == desired:
            logger.info(
                "prerequisite %s already running on %s; skipping pre-deploy",
                desired, self._instance_name,
            )
            return
        logger.info(
            "prerequisite mismatch on %s (active=%r, desired=%r); pre-deploying",
            self._instance_name, current or "<empty>", desired,
        )

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
        model = (
            os.environ.get("ANTHROPIC_MODEL")
            or "claude-sonnet-4-6"
        )
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY must be set on the coordinator to "
                "pre-deploy a prerequisite profile via claude --print."
            )

        env_prefix_parts = [
            f"ANTHROPIC_API_KEY={shlex.quote(api_key)}",
            f"ANTHROPIC_MODEL={shlex.quote(model)}",
            "CLAUDE_CODE_DISABLE_THINKING=1",
        ]
        if base_url:
            env_prefix_parts.append(f"ANTHROPIC_BASE_URL={shlex.quote(base_url)}")
        env_prefix = " ".join(env_prefix_parts)

        prompt = f"/deploy -p {profile} -m {deploy_mode}"
        # Overwrite (>) the canonical marker on /deploy success — the
        # marker reflects what is currently running, not a deploy log.
        # PATH prepend: brev exec runs a non-interactive shell that does
        # not source ~/.bashrc, where harbor writes
        # `export PATH="$HOME/.local/bin:$PATH"`. claude-code installs
        # to ~/.local/bin via its curl installer, so a bare `claude`
        # invocation here resolves "command not found" without this.
        cmd = (
            f'export PATH="$HOME/.local/bin:$PATH" && '
            f"mkdir -p /tmp/skill-eval && "
            f"{env_prefix} claude --print --dangerously-skip-permissions "
            f"{shlex.quote(prompt)} "
            f"&& printf '%s\\n' {shlex.quote(desired)} > {shlex.quote(marker_path)}"
        )

        timeout_sec = int(os.environ.get("PRE_DEPLOY_TIMEOUT_SEC", "1800"))
        logger.info(
            "Pre-deploying %s on %s (timeout=%ds)",
            desired, self._instance_name, timeout_sec,
        )
        result = await _run_brev_exec(
            self._instance_name, cmd, timeout=timeout_sec,
        )
        if result.return_code != 0:
            tail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(
                f"pre-deploy /deploy -p {profile} -m {deploy_mode} failed "
                f"on {self._instance_name}: exit {result.return_code}; "
                f"output tail: {tail!r}"
            )
        logger.info(
            "Pre-deploy %s succeeded on %s; active marker overwritten",
            desired, self._instance_name,
        )

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
        # brev exec: tar on remote, base64-encode with markers, capture
        # via exec, decode+untar locally.  Use sentinel markers to isolate
        # base64 from brev CLI spinner/connection noise.
        import base64 as _b64, re as _re, subprocess as _sp
        marker = "__HARBOR_B64_" + uuid.uuid4().hex[:8] + "__"
        result = await _run_brev_exec(
            self._instance_name,
            f"echo '{marker}START'; "
            f"tar -czf - -C {shlex.quote(source_dir)} . 2>/dev/null | base64 -w 0; "
            f"echo; echo '{marker}END'",
            timeout=120,
        )
        if result.return_code != 0:
            raise RuntimeError(f"Download dir failed: {result.stderr}")
        stdout = result.stdout or ""
        # Extract only the bytes between START and END markers
        m = _re.search(rf"{marker}START\s*\n(.*?)\n{marker}END", stdout, _re.DOTALL)
        if not m:
            raise RuntimeError(
                f"Download dir failed: markers not found in output "
                f"(len={len(stdout)})"
            )
        # Strip any remaining non-base64 chars (e.g. CR, stray spinner bytes)
        raw_b64 = "".join(c for c in m.group(1) if c in
                          "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=")
        if not raw_b64:
            raise RuntimeError("Download dir failed: no base64 data between markers")
        tar_bytes = _b64.b64decode(raw_b64)
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
        # phase runs package-manager commands that need root.  Detect
        # real install commands (not substrings like `command -v apk`)
        # and wrap them with sudo; everything else runs as the normal
        # user so that file ownership stays consistent with brev copy.
        import re
        needs_root = (
            user == "root" or user == 0
            # Match package-manager INSTALL actions at word boundaries,
            # not bare mentions like `command -v apt-get`.
            or bool(re.search(
                r"\b(apt-get|apt|apk|yum|dnf)\s+(install|add|update|upgrade)\b",
                command,
            ))
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


# Registered external nodes (BYOH / DGX-Spark / IGX-Thor) can't use
# `brev exec` — they require a direct SSH session via the alias that
# `brev shell` writes into ~/.brev/ssh_config.  We cache the list on
# first query to avoid repeated `brev ls nodes` round-trips.
_registered_nodes_cache: dict[str, dict] | None = None


async def _load_registered_nodes() -> dict[str, dict]:
    """Return {lower_name: node_dict} from `brev ls nodes --json`.
    Cached per-process.  Safe to call on any host that has the brev CLI."""
    global _registered_nodes_cache
    if _registered_nodes_cache is not None:
        return _registered_nodes_cache
    _registered_nodes_cache = {}
    try:
        result = await _run_brev("ls", "nodes", "--json", timeout=15)
        nodes = _parse_brev_json(result.stdout) if result.stdout else []
        for n in nodes:
            name = (n.get("name") or "").strip()
            if name:
                _registered_nodes_cache[name.lower()] = n
    except Exception as e:
        logger.warning("brev ls nodes failed (registered nodes unavailable): %s", e)
    return _registered_nodes_cache


async def _is_registered_node(name: str) -> bool:
    """True if *name* matches a registered external node (case-insensitive)."""
    if not name:
        return False
    cache = await _load_registered_nodes()
    return name.lower() in cache


def _ssh_alias_for(name: str) -> str:
    """`brev shell <name>` writes a lowercased `Host <name.lower()>` entry
    into ~/.brev/ssh_config (which ~/.ssh/config includes).  Use that alias."""
    return name.lower()


async def _run_ssh_exec(
    alias: str,
    command: str,
    timeout: int = BREV_EXEC_TIMEOUT,
) -> ExecResult:
    """Run `ssh <alias> <command>` — for registered nodes."""
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=30",
        "-o", "StrictHostKeyChecking=no",
        alias, command,
    ]
    logger.debug("ssh %s: %s", alias, command[:200])
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b""),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return ExecResult(
            stdout=stdout.decode() if stdout else None,
            stderr="SSH command timed out",
            return_code=124,
        )
    return ExecResult(
        stdout=stdout.decode() if stdout else None,
        stderr=stderr.decode() if stderr else None,
        return_code=proc.returncode or 0,
    )


async def _run_scp(
    src: str, dst: str,
    timeout: int = BREV_COPY_TIMEOUT,
) -> ExecResult:
    """Run `scp -r <src> <dst>` — for registered nodes.

    Expects either src or dst to be of form `<alias>:<path>`.  Uses the
    same SSH options as _run_ssh_exec."""
    cmd = [
        "scp", "-r",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=no",
        src, dst,
    ]
    logger.debug("scp: %s -> %s", src, dst)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b""),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        return ExecResult(
            stdout=stdout.decode() if stdout else None,
            stderr="scp timed out",
            return_code=124,
        )
    return ExecResult(
        stdout=stdout.decode() if stdout else None,
        stderr=stderr.decode() if stderr else None,
        return_code=proc.returncode or 0,
    )


async def _run_brev_exec(
    instance: str,
    command: str,
    timeout: int = BREV_EXEC_TIMEOUT,
) -> ExecResult:
    """Run ``brev exec <instance> <command>`` and return result.

    For registered external nodes (e.g. DGX-Spark / IGX-Thor), transparently
    falls back to direct ``ssh <alias>`` since brev exec can't reach them.

    Uses ``bash -c`` wrapping via a shell so that ``brev exec`` receives
    a single command string.  Stdin is piped with empty input so the
    brev CLI doesn't enter interactive mode.
    """
    if await _is_registered_node(instance):
        return await _run_ssh_exec(_ssh_alias_for(instance), command, timeout)
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
    """Run ``brev copy <src> <dst>`` and return result.

    For registered external nodes, transparently falls back to ``scp``
    using the ssh alias (same host:path convention, just with lowercase
    name)."""
    # Detect registered-node endpoint on either side: "<name>:<path>"
    for endpoint in (src, dst):
        if ":" not in endpoint:
            continue
        instance_name = endpoint.split(":", 1)[0]
        if await _is_registered_node(instance_name):
            alias = _ssh_alias_for(instance_name)
            scp_src = src.replace(f"{instance_name}:", f"{alias}:", 1) if src.startswith(f"{instance_name}:") else src
            scp_dst = dst.replace(f"{instance_name}:", f"{alias}:", 1) if dst.startswith(f"{instance_name}:") else dst
            return await _run_scp(scp_src, scp_dst, timeout)

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
    """Return the brev ls entry for `name`, or None if missing.

    If the name isn't a Brev-managed instance, falls back to registered
    external nodes (brev ls nodes) — those are reachable over SSH but not
    via `brev exec`.  Returns a synthesized dict with `type="registered"`
    and whatever fields the node exposes.

    Retries a few times — `brev ls` sometimes hits transient RPC
    deadline-exceeded errors and returns empty stdout.
    """
    for attempt in range(4):
        result = await _run_brev("ls", "--json", timeout=30)
        raw = result.stdout or ""
        # A well-formed JSON array response (even if empty) is authoritative —
        # treat an empty-list response as "not a Brev-managed instance" and
        # fall through to the registered-node check.  Only truly empty stdout
        # or missing closing `]` is transient.
        if raw.strip() == "" or raw.rfind("]") < 0:
            logger.info("brev ls returned empty stdout (attempt %s) — retrying", attempt + 1)
            await asyncio.sleep(5)
            continue

        parsed = _parse_brev_json(raw)
        for inst in parsed:
            if inst.get("name") == name:
                return inst

        # JSON parsed, just no match for this name — check registered nodes
        nodes = await _load_registered_nodes()
        node = nodes.get(name.lower())
        if node:
            return {
                "name": node.get("name") or name,
                "type": "registered",
                "gpu": node.get("gpu") or "",
                "instance_type": "registered-external-node",
                "status": node.get("status") or "?",
                "_registered": True,
            }
        return None
    return None


def _check_instance_matches(instance: dict, req: dict) -> None:
    """Raise RuntimeError if the instance's GPU doesn't meet task requirements.

    `brev ls --json` only returns {name, gpu (string), instance_type, status}
    — no gpu_count / total_vram_gb.  So we do a loose name match here and
    defer stricter checks to the search catalog when available.

    For registered external nodes, `gpu` may be empty (not reported by
    `brev ls nodes`).  Skip the string match in that case and defer to the
    live nvidia-smi check in _check_live_resources.
    """
    if instance.get("_registered"):
        logger.info(
            "Instance '%s' is a registered external node — "
            "skipping catalog GPU-name match (rely on live nvidia-smi check)",
            instance.get("name"),
        )
        return

    if int(req.get("gpu_count", 1) or 0) == 0:
        logger.info(
            "Instance '%s' gpu_count=0 (remote-all or GPU-independent task) — "
            "skipping GPU-type match; any live instance is acceptable",
            instance.get("name"),
        )
        return

    gpu = (instance.get("gpu") or "").upper()
    instance_type = (instance.get("instance_type") or "").upper()
    required_type = (req.get("gpu_type") or "").upper()

    # Loose GPU name match: `RTX PRO 6000` ⊆ `RTX PRO SERVER 6000`
    # Require ALL tokens of `want` to appear in `have` (and `want ⊆ have` as
    # a substring fallback for dashed variants like `H100-SXM-80GB`).
    def _loose_match(want: str, have: str) -> bool:
        want_tokens = set(want.replace("-", " ").split())
        have_tokens = set(have.replace("-", " ").split())
        return want_tokens.issubset(have_tokens) or want in have

    # Brev API transient-flake soft-fail: `brev ls --json` occasionally
    # returns gpu="-" (or "") for a healthy instance for a few seconds while
    # the catalog refreshes. If the catalog instance_type carries the GPU
    # token (e.g. "massedcompute_L40Sx2" carries "L40S"), accept the
    # instance and defer the strict check to live nvidia-smi in
    # _check_live_resources. Without this we raise spuriously and the next
    # trial wastes ~20 min running pre-deploy from scratch.
    gpu_blank = gpu in ("", "-", "N/A", "NONE")
    type_carries_token = (
        required_type and instance_type
        and _loose_match(required_type, instance_type)
    )

    errors = []
    if required_type and not _loose_match(required_type, gpu):
        if gpu_blank and type_carries_token:
            logger.warning(
                "Instance '%s' brev ls returned gpu=%r (likely transient "
                "API flake); instance_type=%r carries %r — accepting and "
                "deferring to live nvidia-smi check",
                instance.get("name"), instance.get("gpu"),
                instance.get("instance_type"), required_type,
            )
        else:
            errors.append(
                f"gpu_type: want tokens of {required_type!r} in {gpu!r}"
            )

    if errors:
        raise RuntimeError(
            f"Brev instance '{instance.get('name')}' does not meet task "
            f"requirements:\n  - " + "\n  - ".join(errors) +
            f"\n  (instance: type={instance.get('instance_type')}, gpu={gpu})"
        )

    logger.info(
        "Instance '%s' GPU name matches (%s ~= %s); vram/count not "
        "verified (not returned by `brev ls --json`)",
        instance.get("name"), gpu, required_type,
    )


async def _find_cheapest_matching_type(req: dict) -> str | None:
    """Find the cheapest `brev search` instance type matching GPU requirements."""
    result = await _run_brev("search", "--json", timeout=30)
    search = (req.get("brev_search") or "").lower()
    required_count = req.get("gpu_count", 1)
    required_vram = req.get("min_vram_gb_per_gpu", 0)
    required_disk = req.get("min_root_disk_gb", 0)

    candidates = []
    for inst in _parse_brev_json(result.stdout):
        gpu_name = (inst.get("gpu_name") or "").lower()
        gpu_count = int(inst.get("gpu_count", 0) or 0)
        total_vram = float(inst.get("total_vram_gb", 0) or 0)
        disk_min_gb = int(inst.get("disk_min_gb", 0) or 0)
        if search and search not in gpu_name:
            continue
        if gpu_count < required_count:
            continue
        if required_vram and (total_vram / max(gpu_count, 1)) < required_vram:
            continue
        # Pre-filter by disk_min_gb.  Some providers misreport this (e.g.
        # hyperstack lists ephemeral-disk size not root), so the live check
        # in _check_live_resources is authoritative; this filter just prunes
        # candidates that are obviously undersized.
        if required_disk and disk_min_gb and disk_min_gb < required_disk:
            continue
        candidates.append(inst)

    if not candidates:
        return None
    candidates.sort(key=lambda x: float(x.get("price_per_hour", 0) or 0))
    return candidates[0].get("type")


def _version_lt(a: str, b: str) -> bool:
    """Return True if NVIDIA driver version `a` is older than `b`.

    Drivers are dotted ints (e.g. "570.195.03" vs "580.95")."""
    def tup(s: str) -> tuple[int, ...]:
        parts = s.strip().split(".")
        return tuple(int("".join(ch for ch in p if ch.isdigit()) or 0) for p in parts)
    return tup(a) < tup(b)


async def _check_live_resources(instance_name: str, req: dict) -> None:
    """SSH into the instance and verify root disk + driver meet requirements."""
    min_disk = req.get("min_root_disk_gb", 0)
    min_driver = req.get("min_gpu_driver_version")

    if min_disk:
        # df -BG reports total in GB; strip trailing 'G'.
        result = await _run_brev_exec(
            instance_name,
            "df -BG / | tail -1 | awk '{print $2}'",
            timeout=30,
        )
        if result.return_code == 0 and result.stdout.strip():
            total = result.stdout.strip().rstrip("G").strip()
            try:
                total_gb = int(total)
            except ValueError:
                logger.warning("Could not parse df output: %r", result.stdout)
                total_gb = None
            if total_gb is not None and total_gb < min_disk:
                raise RuntimeError(
                    f"Brev instance '{instance_name}' root disk is {total_gb} GB; "
                    f"task requires at least {min_disk} GB (for NIM images + VSS "
                    f"containers). Delete and reprovision with a larger-root "
                    f"instance type."
                )
            logger.info(
                "Instance '%s' root disk: %s GB (>= required %s GB)",
                instance_name, total_gb, min_disk,
            )

    if min_driver:
        result = await _run_brev_exec(
            instance_name,
            "nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1",
            timeout=30,
        )
        if result.return_code != 0 or not result.stdout.strip():
            logger.warning(
                "nvidia-smi failed on '%s'; skipping driver check. "
                "stderr: %s", instance_name, (result.stderr or "")[:200],
            )
            return
        actual = result.stdout.strip().split("\n")[0].strip()
        if _version_lt(actual, min_driver):
            raise RuntimeError(
                f"Brev instance '{instance_name}' has NVIDIA driver {actual}; "
                f"task requires {min_driver}+ (needed by the NIM images in this "
                f"profile). Delete and reprovision with a newer-driver instance "
                f"type, or upgrade the driver on the host."
            )
        logger.info(
            "Instance '%s' driver: %s (>= required %s)",
            instance_name, actual, min_driver,
        )


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
