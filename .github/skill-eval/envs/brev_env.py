# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Harbor environment provider for Brev GPU instances.

Connects to a pre-existing operator-managed `vss-eval-*` pool member
resolved via the `BREV_INSTANCE` env var (or `brev_instance` in
task.toml [metadata]). Validates that the resolved instance is
reachable and that its GPU meets the task's requirements; raises if
no instance is resolved. The harness does NOT auto-provision — see
AGENTS.md § 5a for the fleet-selection algorithm the skill-eval
agent uses to pick a pool member.

Task.toml [metadata] fields consumed:
    gpu_type              — e.g. "L40S", "H100", "RTX PRO 6000"
    gpu_count             — 1 or 2
    min_vram_gb_per_gpu   — e.g. 48, 80
    min_root_disk_gb      — root-disk floor enforced post-resolve
    min_gpu_driver_version — driver floor enforced post-resolve
    brev_instance         — (optional) explicit instance name override
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import signal
import subprocess
import tempfile
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

# Artifact-collection (download_*) resilience. A stalled transfer that is
# killed can orphan its ssh child and wedge the box for the next step;
# retrying a transient stall on a fresh connection recovers it. Tunable.
BREV_DOWNLOAD_RETRIES = int(os.environ.get("BREV_DOWNLOAD_RETRIES", "3"))
BREV_DOWNLOAD_BACKOFF_SEC = float(os.environ.get("BREV_DOWNLOAD_BACKOFF_SEC", "5"))


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
        """Resolve instance name: env var > task.toml > None (error)."""
        if DEFAULT_INSTANCE:
            return DEFAULT_INSTANCE
        meta = self._read_task_metadata()
        if "brev_instance" in meta:
            return meta["brev_instance"]
        return None

    async def start(self, force_build: bool) -> None:
        """Validate that the resolved Brev instance is reachable and matches
        the task's GPU requirements. Errors if no instance is resolved —
        the harness does not auto-provision."""
        if self._started:
            return

        meta = self._read_task_metadata()
        requirements = {
            "gpu_type": meta.get("gpu_type"),
            "gpu_count": int(meta.get("gpu_count", 1)),
            "min_vram_gb_per_gpu": int(meta.get("min_vram_gb_per_gpu", 0)),
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
            await _check_instance_matches(instance, requirements)
        else:
            raise RuntimeError(
                "No BREV_INSTANCE set and no `brev_instance` in task.toml "
                "[metadata]. The harness no longer auto-provisions — every "
                "trial must run on an operator-managed `vss-eval-*` pool "
                "member. The skill-eval agent picks one per AGENTS.md § 5a "
                "and exports BREV_INSTANCE before invoking `uvx harbor run`. "
                "If you're running harbor manually, export "
                "BREV_INSTANCE=<vss-eval-*-name> first."
            )

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

        # Live resource checks: root disk + GPU driver. The pool box was
        # provisioned by the operator and is expected to meet these, but
        # the checks catch silent regressions (e.g. a driver downgrade or
        # a box where the big volume mounts on /ephemeral and / is only
        # ~100 GB — which OOMs on local NIM pulls).
        await _check_live_resources(self._instance_name, requirements)

        # Pre-create harbor's expected directories with correct ownership
        # so that agent and verifier processes can write to them.
        #
        # Wipe /logs/artifacts and /logs/verifier FIRST: harbor's
        # Trial._download_artifacts() does a blanket download_dir(/logs/artifacts)
        # and nothing on a warm-pool box ever clears that dir, so a prior
        # trial's arbitrarily-named files get collected as THIS trial's
        # artifacts (observed: 3-day-old `nemoclaw/` base-deploy logs surfacing
        # in an unrelated profile_in_1 trial's artifact tarball). /logs/agent is
        # left intact here — its prior-trial session JSONLs are handled by the
        # archive step just below (move-not-delete, for forensic SSH access).
        setup_dirs_result = await _run_brev_exec(
            self._instance_name,
            "sudo rm -rf /logs/artifacts /logs/verifier && "
            "sudo mkdir -p /logs/agent /logs/verifier /logs/artifacts /tests /solution /skills && "
            "sudo chown -R $(whoami):$(id -gn) /logs /tests /solution /skills",
            timeout=30,
        )
        # Fail loud: this is the load-bearing artifacts wipe. A silent failure
        # would leave the prior trial's /logs/artifacts in place and re-collect
        # it as this trial's output — the exact contamination being fixed —
        # so it gets the same exit-code guard as the docker reset / repo sync.
        if setup_dirs_result.return_code != 0:
            tail = (setup_dirs_result.stderr or setup_dirs_result.stdout or "")[-500:]
            raise RuntimeError(
                f"log-dir reset/setup failed on {self._instance_name}: "
                f"exit {setup_dirs_result.return_code}; tail:\n{tail}"
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
        for key in (
            "NGC_CLI_API_KEY", "NVIDIA_API_KEY", "HF_TOKEN",
            "LLM_REMOTE_URL", "LLM_REMOTE_MODEL",
            "VLM_REMOTE_URL", "VLM_REMOTE_MODEL",
            # Pin the eval's deploy step to the PR's actual head SHA on
            # the actual source repo — the pre-deploy script reads these
            # and resets $REPO to that SHA. Without them, the adapter's
            # baked-in branch wins and warm-pool boxes drift from PR
            # reality (NVBug 6154461 / PR #377 finding: spec asserted
            # the renamed release/3.2.0 container names while the eval
            # deployed feat/skills's old names).
            "PR_HEAD_SHA", "PR_REPO",
            # Identifies this CI run inside the trial environment for
            # logs and any future per-run scratch dirs the agent may
            # create. No longer load-bearing now that the harness
            # doesn't pre-deploy profiles or maintain an active-deploy
            # marker.
            "GITHUB_RUN_ID",
        ):
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

        # Sync ~/video-search-and-summarization on the box to the PR's
        # actual head SHA before any deploy/agent step reads it.
        #
        # Without this, every trial runs against whatever happened to be
        # checked out on the box from a prior session — often a stale
        # tarball-style checkout (no `.git`) with an obsolete directory
        # layout (`deployments/` instead of `deploy/docker/`) and the
        # pre-rename container names. The pre-deploy script generated
        # by `adapters/vss-deploy-profile/generate.py::generate_solve_script`
        # only syncs on the *gold-solution* path; the trial's agent invokes
        # `/vss-deploy-profile` directly against `$REPO`, so without this step the
        # PR_HEAD_SHA forwarded above never actually lands on disk.
        await self._sync_repo_to_pr_head()

        # Wipe the warm-pool box's docker runtime to a clean slate so no
        # prior trial's deployment state can contaminate this one. Images are
        # preserved (re-pulling the image set is slow); all containers,
        # user-defined networks, and volumes are removed. See
        # _reset_docker_runtime for why this is blanket, not VSS-scoped.
        #
        # Gate: ONLY on a spec's first trial — a single-step spec (task dir is
        # the platform, e.g. `rtxpro6000bw`) or `step-1` of a multi-step spec.
        # Multi-step checks for step N assume the deployment state established
        # by step N-1 (AGENTS.md § "Multi-step specs"), and each step is a
        # separate `harbor run` → separate start(); resetting before step-2+
        # would destroy the very state under test. step-1 gets the clean box;
        # later steps build on it. (`environment_dir.parent` is the task dir —
        # named `step-N` for multi-step, the platform for single-step.)
        # Caveat: a manual `harbor run` targeting only `step-2+` in isolation
        # skips the reset and inherits whatever is on the box — run `step-1`
        # first, or reset by hand. Normal CI always runs `step-1` first on a
        # freshly reset box, so the gate is correct there.
        task_dir_name = self.environment_dir.parent.name
        if task_dir_name.startswith("step-") and task_dir_name != "step-1":
            logger.info(
                "Skipping docker runtime reset on %s — %s of a multi-step spec "
                "must preserve step-1's deployment state",
                self._instance_name, task_dir_name,
            )
        else:
            await self._reset_docker_runtime()

        # The harness intentionally does NOT pre-deploy any VSS profile
        # here. Each eval spec's first `expects[]` query is responsible
        # for invoking `/vss-deploy-profile` (or the appropriate
        # standalone-deploy runbook) — making the deploy step visible
        # in the trial's reward + trajectory rather than hidden in the
        # env provider. The previous `_ensure_prerequisite_deployed`
        # hook + `/tmp/skill-eval/active-deploy.txt` marker are gone.

        self._started = True
        logger.info("Brev instance %s is reachable", self._instance_name)

    async def _reset_docker_runtime(self) -> None:
        """Wipe the warm-pool box's docker runtime before the trial.

        Removes **all** containers (running + stopped), **all** volumes
        (named + anonymous), and **all** user-defined networks, while
        **preserving images** — re-pulling the multi-GB VSS/NIM image set on
        every trial would dominate wall-clock.

        Why blanket, not VSS-project-scoped: trials reach a deploy through
        heterogeneous paths — direct `docker compose --profile …`, the
        `/vss-deploy-profile` runbook, an MCP-orchestrator base deploy — under
        different compose project names. A project- or label-scoped
        `compose down` from the incoming trial therefore cannot reach a
        *predecessor's* stack, so a leftover container port-conflicts the new
        deploy (observed: a profile_in_1 trial where `phoenix` was stuck
        `Created` and several init containers were missing because a prior
        base-profile deploy's containers still held the ports). Removing
        everything is the only reset that doesn't depend on knowing what the
        last trial deployed. Safe because `vss-eval-*` boxes are a dedicated,
        flock-serialised eval pool — nothing else runs on them.

        NOTE: wiping all volumes also drops the model-weight caches
        (`rtvi-hf-cache`, `rtvi-ngc-model-cache`), so the next deploy pays the
        full cold model-weight download (~20 min vs ~55 s warm). The caller
        gates this to a spec's first trial only (single-step, or step-1 of a
        multi-step spec — later steps reuse step-1's deployment), so under the
        canonical `-n 1 --max-retries 0` invocation (one trial per spec) the
        cost is paid once per spec, not once per step. An `-n>1` rollout, a
        harbor retry, or a repeated manual run on the same warm box each
        re-wipes the caches and re-pays the cold start. The per-trial harbor
        timeout already budgets for a cold deploy.

        Runs as the normal (docker-group) user — the same identity the
        trial's deploy uses; no sudo. `network prune` leaves the built-in
        bridge/host/none networks, which is correct. Fails loud (`set -u`,
        explicit `exit 1`) if the daemon is unreachable or dies mid-reset, or
        if any container, volume, or user-defined network survives, so a
        half-reset box surfaces as a trial error rather than silent cross-trial
        contamination.
        """
        cmd = r"""set -uo pipefail
docker info >/dev/null 2>&1 || { echo "docker daemon unreachable" >&2; exit 1; }
cids=$(docker ps -aq); [ -n "$cids" ] && docker rm -f $cids >/dev/null 2>&1 || true
vols=$(docker volume ls -q); [ -n "$vols" ] && docker volume rm -f $vols >/dev/null 2>&1 || true
docker network prune -f >/dev/null 2>&1 || true
# Re-confirm the daemon survived the reset. Without `set -e`, a daemon that
# died mid-script would make the count commands below print nothing and the
# guard read 0/0/0 -- faking a clean reset. The counts run microseconds after
# this check, so the remaining TOCTOU window is negligible.
docker info >/dev/null 2>&1 || { echo "docker daemon died during reset" >&2; exit 1; }
rc=$(docker ps -aq | wc -l | tr -d ' ')
rv=$(docker volume ls -q | wc -l | tr -d ' ')
# Only user-defined networks should be gone; the built-in bridge/host/none
# are never removable, so filter to type=custom. A surviving user network
# would collide ("network already exists" / address-range clash) on the next
# `compose up`, so it must fail the reset like a surviving container/volume.
rn=$(docker network ls --filter type=custom -q | wc -l | tr -d ' ')
if [ "$rc" != "0" ] || [ "$rv" != "0" ] || [ "$rn" != "0" ]; then
  echo "docker runtime reset incomplete: ${rc} containers, ${rv} volumes, ${rn} user-defined networks remain" >&2
  exit 1
fi
echo "docker runtime reset OK; images preserved ($(docker images -q | wc -l | tr -d ' ') layers)"
"""
        logger.info(
            "Resetting docker runtime (all containers/networks/volumes; images kept) on %s",
            self._instance_name,
        )
        result = await _run_brev_exec(self._instance_name, cmd, timeout=300)
        if result.return_code != 0:
            tail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(
                f"docker runtime reset failed on {self._instance_name}: "
                f"exit {result.return_code}; tail:\n{tail}"
            )
        logger.info(
            "Docker reset on %s: %s",
            self._instance_name,
            (result.stdout or "").strip().splitlines()[-1] if result.stdout else "<no output>",
        )

    async def _sync_repo_to_pr_head(self) -> None:
        """Reset `~/video-search-and-summarization` on the Brev box to the
        PR's actual head SHA. Runs once per trial, before any deploy or
        agent step reads `$REPO`.

        Why this is in the env provider (not the deploy adapter): the
        vss-deploy-profile adapter's solve.sh syncs the repo on the *gold-solution*
        path, but the trial's claude-code agent invokes `/vss-deploy-profile`
        directly against whatever's on disk. Without this sync, the
        forwarded `PR_HEAD_SHA` env var has no effect on the actual
        compose/skill files the agent reads.

        Handles three pre-states:

        - **Empty / missing dir** — fresh clone.
        - **Stale non-git checkout** (tarball-style, no `.git` dir) —
          this is the load-bearing fix: prior versions of the dir
          shipped from before the repo was renamed and the layout
          changed (`deployments/` not `deploy/docker/`). Nuke and
          re-clone; never silently fall through to `git fetch` on
          a non-git dir.
        - **Existing git checkout** — `git remote set-url` (handles
          cross-fork PRs) + `git fetch <PR_HEAD_SHA>` + hard reset.

        Preserves `data/` (NGC sample bundle) and `.env` (active trial
        overrides) on `git clean`. Fails loud — `set -euo pipefail` so
        any sync error short-circuits start() before the agent runs.
        """
        # PR_HEAD_SHA + PR_REPO come from the workflow step's env and are
        # forwarded into ~/.eval_env on the instance by the loop above.
        # When unset (local dev / smoke test), fall back to develop.
        cmd = r"""set -euo pipefail
PR_REPO="${PR_REPO:-NVIDIA-AI-Blueprints/video-search-and-summarization}"
PR_HEAD_SHA="${PR_HEAD_SHA:-}"
REPO="$HOME/video-search-and-summarization"
VSS_REPO_URL="https://github.com/${PR_REPO}.git"

# Case 1: dir exists but isn't a git repo (stale tarball checkout) — nuke
#         and re-clone. Case 2: dir doesn't exist — clone fresh.
if [ ! -d "$REPO/.git" ]; then
  rm -rf "$REPO"
  git clone --no-checkout --depth=1 --branch develop "$VSS_REPO_URL" "$REPO"
fi
cd "$REPO"
git remote set-url origin "$VSS_REPO_URL"
if [ -n "$PR_HEAD_SHA" ]; then
  git fetch --depth=1 origin "$PR_HEAD_SHA"
  git -c advice.detachedHead=false checkout --force "$PR_HEAD_SHA"
  git reset --hard "$PR_HEAD_SHA"
else
  git fetch --depth=1 origin develop
  git -c advice.detachedHead=false checkout --force FETCH_HEAD
  git reset --hard FETCH_HEAD
fi
# Drop leftover working-tree state from a prior trial, but keep data/
# (sample-data extract — slow to re-pull from NGC) and any .env tweaks
# the active trial may have placed.
git clean -fdx -e data/ -e .env
echo "synced $REPO to $(git rev-parse --short HEAD)"
"""
        logger.info("Syncing $REPO on %s to PR_HEAD_SHA", self._instance_name)
        result = await _run_brev_exec(self._instance_name, cmd, timeout=300)
        if result.return_code != 0:
            tail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(
                f"repo sync failed on {self._instance_name}: "
                f"exit {result.return_code}; tail:\n{tail}"
            )
        logger.info(
            "Repo sync on %s: %s",
            self._instance_name, (result.stdout or "").strip().splitlines()[-1] if result.stdout else "<no output>",
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
        # brev copy has broken directory nesting behaviour. Package the
        # directory locally, copy one archive, then extract remotely. Do
        # not embed the archive bytes in a brev exec argv: larger skill
        # bundles can exceed the OS per-argument limit.
        src = str(source_dir).rstrip("/")
        fd, tar_path_str = tempfile.mkstemp(
            prefix="brev-upload-", suffix=".tar.gz",
        )
        os.close(fd)
        tar_path = Path(tar_path_str)
        remote_upload_dir = f"/tmp/skill-eval/uploads/{uuid.uuid4().hex}"
        remote_tar = f"{remote_upload_dir}/archive.tar.gz"

        try:
            subprocess.check_call(
                ["tar", "-czf", str(tar_path), "-C", src, "."],
                timeout=60,
            )

            result = await _run_brev_exec(
                self._instance_name,
                f"mkdir -p {shlex.quote(remote_upload_dir)}",
                timeout=30,
            )
            if result.return_code != 0:
                raise RuntimeError(f"Upload dir failed: {result.stderr}")

            result = await _run_brev_copy(
                str(tar_path), f"{self._instance_name}:{remote_tar}",
            )
            if result.return_code != 0:
                raise RuntimeError(f"Upload dir failed: {result.stderr}")

            target = shlex.quote(target_dir)
            remote_archive = shlex.quote(remote_tar)
            remote_dir = shlex.quote(remote_upload_dir)
            result = await _run_brev_exec(
                self._instance_name,
                f"sudo mkdir -p {target} && "
                f"sudo chown $(whoami):$(id -gn) {target}; "
                "status=$?; "
                "if [ $status -eq 0 ]; then "
                f"tar -xzf {remote_archive} -C {target}; "
                "status=$?; "
                "fi; "
                f"rm -f {remote_archive}; "
                f"rmdir {remote_dir} 2>/dev/null || true; "
                "exit $status",
                timeout=120,
            )
            if result.return_code != 0:
                raise RuntimeError(f"Upload dir failed: {result.stderr}")
        finally:
            tar_path.unlink(missing_ok=True)

    async def download_file(self, source_path: str, target_path: Path | str) -> None:
        assert self._instance_name
        last_err = ""
        for attempt in range(BREV_DOWNLOAD_RETRIES):
            result = await _run_brev_copy(
                f"{self._instance_name}:{source_path}", str(target_path),
            )
            if result.return_code == 0:
                return
            last_err = result.stderr or ""
            if attempt + 1 < BREV_DOWNLOAD_RETRIES:
                logger.warning(
                    "download_file attempt %d/%d failed (%s) — retrying",
                    attempt + 1, BREV_DOWNLOAD_RETRIES, last_err,
                )
                await asyncio.sleep(BREV_DOWNLOAD_BACKOFF_SEC * (attempt + 1))
        raise RuntimeError(
            f"Download failed after {BREV_DOWNLOAD_RETRIES} attempts: {last_err}"
        )

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        # Retry the pull: a transient stall — or a prior attempt whose ssh
        # child was killed (now reaped via _run_brev_exec's process-group
        # kill, so it can't wedge the box) — usually clears on a fresh
        # connection. Raise loud only after exhausting retries.
        last: Exception | None = None
        for attempt in range(BREV_DOWNLOAD_RETRIES):
            try:
                await self._download_dir_once(source_dir, target_dir)
                return
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                last = exc
                if attempt + 1 < BREV_DOWNLOAD_RETRIES:
                    logger.warning(
                        "download_dir attempt %d/%d failed (%s) — retrying",
                        attempt + 1, BREV_DOWNLOAD_RETRIES, exc,
                    )
                    await asyncio.sleep(BREV_DOWNLOAD_BACKOFF_SEC * (attempt + 1))
        assert last is not None
        raise last

    async def _download_dir_once(self, source_dir: str, target_dir: Path | str) -> None:
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


def _kill_proc_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's whole process group.

    `proc.kill()` signals only the immediate child (the `brev`/`ssh`/`scp`
    CLI). On a stalled transfer that leaves the underlying ssh data channel
    orphaned — holding the secure-link/session open and wedging the box for
    the next step (a killed large artifact pull can otherwise leave the
    following trial's ports unreachable). Killing
    the whole group reaps the orphan. Requires the child to have been
    started with `start_new_session=True` so it leads its own group; falls
    back to a plain kill if the group lookup fails."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


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
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b""),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_proc_group(proc)
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
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b""),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_proc_group(proc)
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
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b"\n"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_proc_group(proc)
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
        start_new_session=True,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=b"\n"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_proc_group(proc)
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
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=(stdin_data or "").encode() + b"\n"),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        _kill_proc_group(proc)
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


async def _get_instance_gpu_count_from_catalog(instance_type: str) -> int | None:
    """Look up an instance type's gpu_count via `brev search gpu --json`.

    Returns None when the SKU isn't in the current catalog (temporarily
    out of stock, retired, or never listed). Callers should warn and fall
    back to a live nvidia-smi check.
    """
    if not instance_type:
        return None
    try:
        result = await _run_brev("search", "gpu", "--json", timeout=30)
    except Exception as exc:
        logger.warning("brev search gpu --json failed: %s", exc)
        return None
    if result.return_code != 0:
        return None
    for row in _parse_brev_json(result.stdout):
        if row.get("type") == instance_type:
            try:
                return int(row.get("gpu_count", 0) or 0)
            except (TypeError, ValueError):
                return None
    return None


async def _check_live_gpu_count(instance_name: str, required_count: int) -> None:
    """SSH in and count GPUs via nvidia-smi. Raises on mismatch."""
    result = await _run_brev_exec(
        instance_name,
        "nvidia-smi --query-gpu=name --format=csv,noheader | wc -l",
        timeout=30,
    )
    if result.return_code != 0 or not result.stdout.strip():
        logger.warning(
            "nvidia-smi count failed on '%s'; cannot enforce gpu_count. "
            "stderr: %s",
            instance_name, (result.stderr or "")[:200],
        )
        return
    try:
        actual = int(result.stdout.strip().split("\n")[0])
    except ValueError:
        logger.warning(
            "Could not parse nvidia-smi count output for '%s': %r",
            instance_name, result.stdout,
        )
        return
    if actual != required_count:
        raise RuntimeError(
            f"Brev instance '{instance_name}' has {actual} GPU(s) (live "
            f"nvidia-smi); task requires exactly {required_count}. Pool "
            f"partition mismatch — pick a fleet member with the matching "
            f"GPU count (e.g. vss-eval-l40s-1g for 1-GPU, vss-eval-l40s* "
            f"for 2-GPU)."
        )
    logger.info(
        "Instance '%s' live gpu_count: %d (matches required %d)",
        instance_name, actual, required_count,
    )


async def _check_instance_matches(instance: dict, req: dict) -> None:
    """Raise RuntimeError if the instance's GPU doesn't meet task requirements.

    `brev ls --json` only returns {name, gpu (string), instance_type, status}
    — no gpu_count / total_vram_gb.  So we do a loose name match here and
    defer stricter checks to the search catalog when available, falling
    back to a live nvidia-smi count if the SKU isn't in the catalog.

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

    # gpu_count check — strict equality so pool partitioning works.
    # A 1-GPU task on a 2-GPU box wastes capacity (the other GPU could
    # serve a sibling 1-GPU trial in parallel); a 2-GPU task on a 1-GPU
    # box can't even launch the second LLM/VLM. Strict match makes both
    # cases loud at validate time instead of mid-trial.
    required_count = int(req.get("gpu_count", 1) or 0)
    if required_count > 0:
        catalog_count = await _get_instance_gpu_count_from_catalog(
            instance.get("instance_type") or ""
        )
        if catalog_count is None:
            logger.warning(
                "Instance '%s' instance_type=%r not in `brev search gpu --json` "
                "catalog (SKU may be temporarily out of stock); falling back to "
                "live nvidia-smi for gpu_count check",
                instance.get("name"), instance.get("instance_type"),
            )
            try:
                await _check_live_gpu_count(instance.get("name"), required_count)
            except RuntimeError as exc:
                errors.append(str(exc))
        elif catalog_count != required_count:
            errors.append(
                f"gpu_count: want exactly {required_count}, instance has "
                f"{catalog_count} (instance_type={instance.get('instance_type')})"
            )

    if errors:
        # Actionable hint so the agent doesn't burn its turn budget
        # re-discovering how to find a matching pool member. Stay
        # generic — don't name specific pool boxes here, the pool
        # is operator-managed and naming couples this code to the
        # current fleet topology. `required_count` and `required_type`
        # are already bound above; reuse them. Build the "require …"
        # phrase conditionally so an empty `gpu_type` (count-only
        # specs) doesn't render as `gpu_type='' + gpu_count=N` and
        # mislead the agent into filtering for a literal empty string.
        require_clauses = []
        if required_type:
            require_clauses.append(f"gpu_type={required_type!r}")
        require_clauses.append(f"gpu_count={required_count}")
        require_phrase = " + ".join(require_clauses)
        hint = (
            f"\n\nTo find a matching pool member, scan vss-eval-* "
            f"candidates and require {require_phrase}:\n"
            f"  brev ls --json | jq -r '.[] | select(.name | "
            f"startswith(\"vss-eval-\")) | \"\\(.name)\\t\\(.instance_type)"
            f"\\t\\(.gpu)\"'\n"
            f"Cross-reference each candidate's instance_type against "
            f"`brev search gpu --json` to confirm gpu_count, then "
            f"re-export BREV_INSTANCE=<candidate> and retry. Do NOT "
            f"`brev create` a new instance — the pool is operator-"
            f"managed (see AGENTS.md § Platform topology)."
        )
        raise RuntimeError(
            f"Brev instance '{instance.get('name')}' does not meet task "
            f"requirements:\n  - " + "\n  - ".join(errors) +
            f"\n  (instance: type={instance.get('instance_type')}, gpu={gpu})"
            + hint
        )

    logger.info(
        "Instance '%s' GPU name matches (%s ~= %s); gpu_count verified "
        "against catalog or live nvidia-smi",
        instance.get("name"), gpu, required_type,
    )


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


