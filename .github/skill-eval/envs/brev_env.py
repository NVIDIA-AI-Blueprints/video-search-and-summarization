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
import contextlib
from enum import Enum
import json
import logging
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import uuid

from harbor.environments.base import BaseEnvironment
from harbor.environments.base import ExecResult

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


def _local_gpu_instance() -> str:
    """Runner-pinned local instance name, empty on coordinator hosts."""
    return os.environ.get("SKILL_EVAL_LOCAL_GPU_INSTANCE", "").strip()


def _is_local_gpu_instance(instance: str | None) -> bool:
    local = _local_gpu_instance()
    return bool(local and instance and local.lower() == instance.lower())


# Corp remote LLM/VLM endpoints are optional on coordinator Brev boxes.
# OpenShell guests cannot reach 10.86.6.50, and forwarding these keys
# makes /vss-deploy-profile write LLM_MODE=remote / VLM_MODE=remote so
# local NIMs never start and VRAM stays empty.
_REMOTE_PLACEMENT_KEYS = (
    "LLM_REMOTE_URL",
    "LLM_REMOTE_MODEL",
    "VLM_REMOTE_URL",
    "VLM_REMOTE_MODEL",
)


def _eval_env_forward_keys() -> tuple[str, ...]:
    keys = (
        "NGC_CLI_API_KEY",
        "NVIDIA_API_KEY",
        "HF_TOKEN",
        *_REMOTE_PLACEMENT_KEYS,
        "PR_HEAD_SHA",
        "PR_REPO",
        "GITHUB_RUN_ID",
    )
    if _local_gpu_instance():
        return tuple(key for key in keys if key not in _REMOTE_PLACEMENT_KEYS)
    return keys

# Keep every file-transfer API bounded below run_leg.py's recovery headroom.
# Remote work gets 600 seconds. The public 630-second wall-clock bound also
# accounts for two worst-case 11-second process-group reap paths (a timed-out
# primary agent-log pull followed by a timed-out raw-log fallback) plus eight
# seconds of scheduling/exception-unwind margin.
BREV_TRANSFER_TOTAL_TIMEOUT_SEC = int(
    os.environ.get("BREV_TRANSFER_TOTAL_TIMEOUT_SEC", "630")
)
BREV_TRANSFER_CANCELLATION_GRACE_SEC = 30
BREV_TRANSFER_ACTIVE_TIMEOUT_SEC = (
    BREV_TRANSFER_TOTAL_TIMEOUT_SEC - BREV_TRANSFER_CANCELLATION_GRACE_SEC
)
BREV_LOG_FALLBACK_TIMEOUT_SEC = 120
BREV_DOWNLOAD_PRIMARY_TIMEOUT_SEC = (
    BREV_TRANSFER_ACTIVE_TIMEOUT_SEC - BREV_LOG_FALLBACK_TIMEOUT_SEC
)
if BREV_DOWNLOAD_PRIMARY_TIMEOUT_SEC <= 0:
    raise ValueError(
        "BREV_TRANSFER_TOTAL_TIMEOUT_SEC must exceed fallback and "
        "cancellation-reap budgets"
    )

# If Claude exits before its session JSONL is materialized, Harbor's normal
# claude-code mapper has nothing to copy.  Keep a bounded tail of the tee'd
# stream as a forensic fallback without making every successful trial carry
# the (potentially very large) raw stream.
CLAUDE_LOG_FALLBACK_BYTES = int(
    os.environ.get("CLAUDE_LOG_FALLBACK_BYTES", str(1024 * 1024))
)
REMOTE_AGENT_RUN_ENV = "HARBOR_SKILL_EVAL_AGENT_RUN"
REMOTE_AGENT_RUN_PREFIX = "skill-eval-"

# Public relay used by the RT-VLM test suite. Operators can override it for
# isolated environments, but the eval remains runnable without extra CI
# configuration.
DEFAULT_RTSP_SAMPLE_URL = (
    "rtsp://global.stg.ga.launchpad.nvidia.com:11333/camera03"
)


def _resolve_rtsp_sample_url() -> str:
    """Return the operator-provided RTSP sample URL or the public default."""
    return os.environ.get("RTSP_SAMPLE_URL") or DEFAULT_RTSP_SAMPLE_URL


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
        if not _local_gpu_instance() and not _which("brev"):
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
        local_instance = _local_gpu_instance()
        if local_instance:
            if (
                self._instance_name
                and not _is_local_gpu_instance(self._instance_name)
            ):
                raise RuntimeError(
                    "BREV_INSTANCE does not match "
                    "SKILL_EVAL_LOCAL_GPU_INSTANCE on this runner"
                )
            self._instance_name = local_instance

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
            if _is_local_gpu_instance(self._instance_name):
                await _check_local_gpu_requirements(
                    self._instance_name, requirements
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

        # Reap stray on-box agent processes left by a previous trial whose
        # runner-side job was cancelled or SIGKILLed. Cancellation kills the
        # runner-side harbor tree (releasing the box's flock, which dies with
        # run_leg.py), but the agent launched *on the box* over SSH — and the
        # Bash-tool children in its process groups — can survive and keep
        # driving `docker compose up` / model pulls. The docker reset below
        # removes containers, not host processes, so an unreaped orphan
        # contends with this trial and can resurrect containers after the
        # wipe (suspected in PR #1281's base/search legs losing SSH
        # mid-deploy on vss-eval-rtx-2g-2, minutes after a cancelled run's
        # legs died there). Must run before the /logs wipe and docker reset.
        reap_result = await _run_brev_exec(
            self._instance_name,
            _stray_agent_reap_command(),
            timeout=30,
        )
        if reap_result.return_code != 0:
            tail = (reap_result.stderr or reap_result.stdout or "")[-500:]
            raise RuntimeError(
                f"stray-agent reap failed on {self._instance_name}: "
                f"exit {reap_result.return_code}; tail:\n{tail}"
            )
        logger.info(
            "Stray-agent reap on %s: %s",
            self._instance_name, (reap_result.stdout or "").strip(),
        )

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
            "sudo rm -rf /tmp/skill-eval/uploads && "
            "sudo rm -f /tmp/.harbor_dl_*.b64 && "
            "sudo mkdir -p /logs/agent /logs/verifier /logs/artifacts /tests /solution /skills && "
            "sudo chown -RL $(whoami):$(id -gn) /logs /tests /solution /skills",
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

        # Archive session JSONLs and root-level agent outputs left by
        # prior trials on this warm-pool box. Without this, harbor's claude-code
        # mapper merges every
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
        archive_result = await _run_brev_exec(
            self._instance_name,
            _prior_agent_output_archive_command(),
            timeout=30,
        )
        if archive_result.return_code != 0:
            tail = (archive_result.stderr or archive_result.stdout or "")[-500:]
            raise RuntimeError(
                f"prior agent-output archive failed on {self._instance_name}: "
                f"exit {archive_result.return_code}; tail:\n{tail}"
            )

        # Clear Claude Code's background-task scratch before the new
        # `claude --print` starts. Session JSONL archival above handles stale
        # `/logs/agent/sessions/projects/*`, but completed background tasks
        # can also leave markers under `/tmp/claude-<uid>/.../tasks`. Claude
        # Code may surface those as fresh `<task-notification>` messages in
        # the next session, polluting the trajectory before the eval prompt.
        claude_task_cleanup_result = await _run_brev_exec(
            self._instance_name,
            _claude_task_scratch_cleanup_command(),
            timeout=30,
        )
        if claude_task_cleanup_result.return_code != 0:
            tail = (
                claude_task_cleanup_result.stderr
                or claude_task_cleanup_result.stdout
                or ""
            )[-500:]
            raise RuntimeError(
                f"claude task scratch cleanup failed on {self._instance_name}: "
                f"exit {claude_task_cleanup_result.return_code}; tail:\n{tail}"
            )

        # Forward task-critical env vars from the local shell into the
        # instance's ~/.eval_env (sourced by ~/.profile, which every
        # brev exec then sources).  Harbor's claude-code agent only
        # propagates ANTHROPIC_* env vars, so anything else needed
        # during deploy (NGC_CLI_API_KEY, NVIDIA_API_KEY) must land on
        # the instance out-of-band.
        forwarded: list[tuple[str, str]] = [
            # The verifier's LLM judge (claude-agent-sdk) runs on the instance
            # as whatever user the SSH grant lands as. On root-runner fleets
            # claude refuses --dangerously-skip-permissions for root unless
            # IS_SANDBOX=1 — without it every judge check dies with
            # ProcessError(exit 1) and the trial scores 0.0.
            ("IS_SANDBOX", "1"),
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
            # Dense-captioning evals require one URL that both the Brev host
            # and its bridge-networked RT-VLM container can reach.
            ("RTSP_SAMPLE_URL", _resolve_rtsp_sample_url()),
        ]
        for key in _eval_env_forward_keys():
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
        #
        # NOTE: docker reset runs BEFORE repo sync because running containers
        # may have bind-mounted host paths inside $REPO (e.g.
        # deploy/docker/data-dir/) and written root-owned files there. Without
        # stopping them first, `git clean` in the repo sync step fails with
        # "Permission denied" on those root-owned files/dirs.
        # A spec's first trial (a single-step spec, or `step-1` of a
        # multi-step chain) gets a clean slate; `step-2+` must PRESERVE the
        # environment step-1 established. This one predicate gates every
        # destructive box-prep action below — docker reset, host-data purge,
        # AND the repo sync — because each of them tears down state the later
        # step under test depends on. (`environment_dir.parent` is the task
        # dir — named `step-N` for multi-step, the platform for single-step.)
        task_dir_name = self.environment_dir.parent.name
        is_first_trial = not (
            task_dir_name.startswith("step-") and task_dir_name != "step-1"
        )
        if is_first_trial:
            await self._reset_docker_runtime()
            # Host bind-mount purge runs AFTER the docker reset so every
            # container that writes into these dirs is already gone —
            # purging first would race the writers and the dirs would be
            # dirty again by the time the trial starts.
            await self._purge_host_data_dirs()
        else:
            logger.info(
                "Skipping docker reset, host purge, and repo sync on %s — %s "
                "of a multi-step spec must preserve step-1's deployment state "
                "and its live bind-mount host dirs (e.g. deploy/docker/data-dir/, "
                "whose clip_storage/vst_data are bind-mounted into the still-"
                "running VIOS containers)",
                self._instance_name, task_dir_name,
            )

        # Sync ~/video-search-and-summarization on the box to the PR's
        # actual head SHA before any deploy/agent step reads it.
        #
        # Gated to the FIRST trial only. `_sync_repo_to_pr_head`'s
        # `git clean -fdx -e data/ -e .env` removes every untracked path not
        # matched by its excludes. NOTE `-e data/` is a gitignore-style pattern
        # with no leading slash, so it spares a directory named `data` at ANY
        # depth (including `deploy/docker/data/`) — but NOT
        # `deploy/docker/data-dir/`, whose name does not match. `data-dir/` is
        # the host source of the LVS/VIOS bind mounts (clip_storage, vst_data,
        # vst/temp_files, ...) whenever a deploy roots VSS_DATA_DIR there
        # (dev-profile.sh's default). On `step-2+` the step-1 containers are
        # still running and bind-mounted onto those dirs; cleaning them
        # mid-chain unlinks the host inode out from under the containers —
        # confirmed by the mount probe below: host inode gone, container still
        # pinned to it, link count 0 — and uploads then fail with "Failed to
        # open output file: No such file or directory" (PR #1227 /
        # lvs_profile_summarize step-2, the reported symptom).
        #
        # That name dependency IS the non-determinism this bug showed: a deploy
        # rooted at `deploy/docker/data/` survives the clean (spared by
        # `-e data/`) so summarize works, while one rooted at `.../data-dir/`
        # is deleted and breaks — same clean, opposite outcome, purely by
        # folder name. Gating removes the lottery: no clean runs on step-2+,
        # so neither root is ever touched.
        #
        # The re-sync is also redundant on later steps: the box is held by one
        # `run_leg.py` flock across the whole chain and nothing mutates $REPO
        # between steps, so it is already at PR_HEAD_SHA from step-1.
        #
        # Without this on the first trial, every trial runs against whatever
        # happened to be checked out on the box from a prior session — often a
        # stale tarball-style checkout (no `.git`) with an obsolete directory
        # layout (`deployments/` instead of `deploy/docker/`) and the
        # pre-rename container names. The pre-deploy script generated by
        # `adapters/vss-deploy-profile/generate.py::generate_solve_script`
        # only syncs on the *gold-solution* path; the trial's agent invokes
        # `/vss-deploy-profile` directly against `$REPO`, so without this step
        # the PR_HEAD_SHA forwarded above never actually lands on disk.
        # Permanent guardrail (non-fatal): probe the VIOS bind mounts right
        # before and after the sync decision. The probes run on EVERY step,
        # regardless of gating, so the log records the truth on both paths:
        #   - step-2+ WITH this fix   -> before=healthy, after=healthy (sync
        #     skipped, mounts preserved) — the fix, proven per run.
        #   - step-2+ WITHOUT the fix -> before=healthy, after=stale (the
        #     `git clean` deleted the data-dir root out from under the
        #     containers) — the regression, caught loudly.
        # Output lands in <trial>/artifacts/logs/artifacts/mount-probe.log.
        await self._probe_bind_mount(f"{task_dir_name}:before-sync")
        if is_first_trial:
            await self._sync_repo_to_pr_head()
        await self._probe_bind_mount(f"{task_dir_name}:after-sync")

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

    async def _purge_host_data_dirs(self) -> None:
        """Purge per-trial VSS state that lives in host bind-mounts.

        `_reset_docker_runtime` removes containers/volumes/networks, but
        several services persist state in **host directories bind-mounted
        into the containers** — invisible to `docker volume rm`:

        - `<root>/nvstreamer/videos{,-upload}/` — uploaded media. NvStreamer
          auto-suffixes a new upload whose filename already exists, so a
          leftover `warehouse_safety_0001.mp4` turns the next trial's upload
          into `warehouse_safety_0001_5` and fails identifier-semantics
          checks (observed: PR #1241 `nvstreamer_ops` step-1, six leftover
          copies on the box).
        - `<root>/nvstreamer/vst_data/` and `<root>/data_log/` — the
          NvStreamer/VST sensor registry and runtime DB, so sensors from
          prior trials survive the docker reset.
        - `<root>/videos/nvstreamer/` — alternate layout used by some
          profiles for the same uploaded-media state.

        The GitLab `ci-vss-oss` eval jobs have always done the equivalent
        ("Cleaning VSS_DATA_DIR data_log (kafka, elastic, redis, vst,
        nvstreamer, ...)"); this brings the skill-eval harness to parity.

        Roots are **globbed, not read from `$VSS_DATA_DIR`**: the env var is
        chosen per-deploy inside the trial, and pool boxes accumulate more
        than one root over time (observed: `/opt/vss-data` AND
        `~/vss-data` on the same box). `$REPO/deploy/docker/data-dir` needs
        no handling here — `_sync_repo_to_pr_head`'s `git clean` covers it.

        Contents are deleted but the directories themselves are kept, so
        operator-provisioned ownership/permissions on the mount points
        survive. `sudo` is required — containers write these files as root.
        Same first-trial-only gate as the docker reset: step-2+ of a
        multi-step spec depends on the state step-1 uploaded.
        """
        cmd = r"""set -uo pipefail
purged=""
for root in /opt/vss-data "$HOME"/vss-data; do
  [ -d "$root" ] || continue
  for sub in data_log nvstreamer/videos nvstreamer/videos-upload nvstreamer/vst_data videos/nvstreamer; do
    d="$root/$sub"
    [ -d "$d" ] || continue
    sudo find "$d" -mindepth 1 -delete || { echo "failed to purge $d" >&2; exit 1; }
    purged="$purged $d"
  done
done
# Fail loud if anything survived — a half-purged dir is the same silent
# cross-trial contamination the docker-reset guard protects against.
for d in $purged; do
  n=$(sudo find "$d" -mindepth 1 2>/dev/null | wc -l | tr -d ' ')
  [ "$n" = "0" ] || { echo "host data purge incomplete: $n entries remain in $d" >&2; exit 1; }
done
echo "host data purge OK:${purged:- nothing to purge}"
"""
        logger.info(
            "Purging host bind-mount data dirs (data_log, nvstreamer state) on %s",
            self._instance_name,
        )
        result = await _run_brev_exec(self._instance_name, cmd, timeout=300)
        if result.return_code != 0:
            tail = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(
                f"host data purge failed on {self._instance_name}: "
                f"exit {result.return_code}; tail:\n{tail}"
            )
        logger.info(
            "Host data purge on %s: %s",
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
# A prior STEP's deploy may have chattr +i'd generated files (e.g.
# deploy/docker/resolved.yml, developer-profiles/*/generated.env) — strip
# the immutable bit or git clean dies with "Operation not permitted" and
# kills the whole step chain.
chattr -R -i . 2>/dev/null || sudo chattr -R -i . 2>/dev/null || true
# Use sudo git clean as a fallback: prior docker containers may have created
# root-owned files in bind-mounted dirs (e.g. deploy/docker/data-dir/) that
# a non-root git clean cannot remove ("Permission denied").
git clean -fdx -e data/ -e .env 2>/dev/null || sudo git clean -fdx -e data/ -e .env
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

    async def _probe_bind_mount(self, label: str) -> None:
        """Non-fatal guardrail: record the liveness of every discovered VIOS
        bind mount on the box. Proves/guards against the step-2 data-dir
        deletion (see the call sites around _sync_repo_to_pr_head). The
        box-side probe tees its `MOUNTPROBE` lines to
        /logs/artifacts/mount-probe.log (collected into the trial artifact) —
        brev_env's Python logging is swallowed by harbor, so that file is the
        reliable channel. NEVER raises: a probe fault must not perturb the
        trial it only observes."""
        try:
            try:
                from envs import mount_probe  # normal harbor import path
            except ImportError:
                import mount_probe  # PYTHONPATH=.github/skill-eval fallback
            result = await _run_brev_exec(
                self._instance_name, mount_probe.build_probe_command(label),
                timeout=90,
            )
            lines = mount_probe.parse_probe_lines(result.stdout or "")
            if not lines:
                logger.warning(
                    "[mount-probe] %s: no MOUNTPROBE output (rc=%s) stderr=%s",
                    label, result.return_code, (result.stderr or "")[-200:],
                )
                return
            for d in lines:
                logger.info(
                    "[mount-probe] %s",
                    " ".join(f"{k}={v}" for k, v in d.items()),
                )
        except Exception as exc:  # noqa: BLE001 — diagnostic must never fail the trial
            logger.warning("[mount-probe] %s failed (non-fatal): %r", label, exc)

    async def stop(self, delete: bool) -> None:
        """Leave the instance running after bounded transfer-staging cleanup."""
        if self._instance_name:
            try:
                result = await _run_brev_exec(
                    self._instance_name,
                    "sudo rm -rf /tmp/skill-eval/uploads && "
                    "sudo rm -f /tmp/.harbor_dl_*.b64",
                    timeout=30,
                )
                if result.return_code != 0:
                    logger.warning(
                        "Remote transfer-staging cleanup failed on %s: %s",
                        self._instance_name,
                        result.stderr or result.stdout or "unknown error",
                    )
            except Exception as exc:  # noqa: BLE001 — stop remains best effort
                logger.warning(
                    "Remote transfer-staging cleanup failed on %s: %r",
                    self._instance_name,
                    exc,
                )
        logger.info(
            "Leaving Brev instance %s running (delete=%s)",
            self._instance_name, delete,
        )
        self._started = False

    async def upload_file(self, source_path: Path | str, target_path: str) -> None:
        assert self._instance_name
        try:
            async with asyncio.timeout(BREV_TRANSFER_ACTIVE_TIMEOUT_SEC):
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
        except TimeoutError as exc:
            raise RuntimeError(
                "Upload file exceeded the "
                f"{BREV_TRANSFER_TOTAL_TIMEOUT_SEC}s transfer budget"
            ) from exc

    async def upload_dir(self, source_dir: Path | str, target_dir: str) -> None:
        assert self._instance_name
        try:
            async with asyncio.timeout(BREV_TRANSFER_ACTIVE_TIMEOUT_SEC):
                await self._upload_dir_once(source_dir, target_dir)
        except TimeoutError as exc:
            raise RuntimeError(
                "Upload dir exceeded the "
                f"{BREV_TRANSFER_TOTAL_TIMEOUT_SEC}s transfer budget"
            ) from exc

    async def _upload_dir_once(
        self,
        source_dir: Path | str,
        target_dir: str,
    ) -> None:
        """Archive, copy, and extract one directory within the caller budget."""
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
            await _run_local_transfer_command(
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
        try:
            async with asyncio.timeout(BREV_TRANSFER_ACTIVE_TIMEOUT_SEC):
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
                        await asyncio.sleep(
                            BREV_DOWNLOAD_BACKOFF_SEC * (attempt + 1)
                        )
                raise RuntimeError(
                    f"Download failed after {BREV_DOWNLOAD_RETRIES} attempts: "
                    f"{last_err}"
                )
        except TimeoutError as exc:
            raise RuntimeError(
                "Download file exceeded the "
                f"{BREV_TRANSFER_TOTAL_TIMEOUT_SEC}s transfer budget"
            ) from exc

    async def download_dir(self, source_dir: str, target_dir: Path | str) -> None:
        # Retry the pull: a transient stall — or a prior attempt whose ssh
        # child was killed (now reaped via _run_brev_exec's process-group
        # kill, so it can't wedge the box) — usually clears on a fresh
        # connection. Raise loud only after exhausting retries.
        last: Exception | None = None
        try:
            async with asyncio.timeout(BREV_DOWNLOAD_PRIMARY_TIMEOUT_SEC):
                for attempt in range(BREV_DOWNLOAD_RETRIES):
                    try:
                        await self._download_dir_once(source_dir, target_dir)
                        last = None
                        break
                    except (
                        RuntimeError,
                        OSError,
                        subprocess.SubprocessError,
                    ) as exc:
                        last = exc
                        if attempt + 1 < BREV_DOWNLOAD_RETRIES:
                            logger.warning(
                                "download_dir attempt %d/%d failed (%s) — retrying",
                                attempt + 1, BREV_DOWNLOAD_RETRIES, exc,
                            )
                            await asyncio.sleep(
                                BREV_DOWNLOAD_BACKOFF_SEC * (attempt + 1)
                            )
        except TimeoutError as exc:
            last = RuntimeError(
                "Download dir exceeded the "
                f"{BREV_DOWNLOAD_PRIMARY_TIMEOUT_SEC}s primary transfer budget"
            )
            logger.warning("Primary directory transfer timed out: %r", exc)

        needs_fallback = (
            _is_agent_log_dir(source_dir)
            and (last is not None or not _has_mappable_agent_log(Path(target_dir)))
        )
        if needs_fallback:
            try:
                async with asyncio.timeout(BREV_LOG_FALLBACK_TIMEOUT_SEC):
                    recovered = await self._download_claude_log_fallback(
                        source_dir, target_dir
                    )
            except (
                RuntimeError,
                OSError,
                subprocess.SubprocessError,
                TimeoutError,
            ) as exc:
                logger.warning(
                    "Claude raw-log fallback failed (primary result retained): %s",
                    exc,
                )
            else:
                if recovered:
                    logger.warning(
                        "No usable Claude session trajectory was available; "
                        "recovered a bounded "
                        "claude-code.txt tail instead"
                    )
                    if last is not None:
                        return

        if last is None:
            return
        raise last

    async def _download_claude_log_fallback(
        self,
        source_dir: str,
        target_dir: Path | str,
    ) -> bool:
        """Recover the tail of Claude's raw stream when no session exists.

        The Brev gateway becomes unreliable for multi-megabyte command output,
        so this deliberately transfers a bounded, base64-encoded tail.  The
        standard filename lets Harbor artifacts and the trace viewer expose the
        only agent evidence that exists for an early exit or timeout.
        """
        assert self._instance_name
        import base64 as _b64
        import binascii as _binascii
        import re as _re

        marker = "__HARBOR_CLAUDE_FALLBACK_" + uuid.uuid4().hex[:8] + "__"
        remote_log = shlex.quote(
            f"{source_dir.rstrip('/')}/claude-code.txt"
        )
        result = await _run_brev_exec(
            self._instance_name,
            f"test -f {remote_log} || exit 44; "
            f"echo '{marker}START'; "
            f"tail -c {CLAUDE_LOG_FALLBACK_BYTES} {remote_log} | base64 -w 0; "
            f"echo; echo '{marker}END'",
            timeout=120,
        )
        if result.return_code == 44:
            logger.warning("Claude raw log does not exist at %s", remote_log)
            return False
        if result.return_code != 0:
            raise RuntimeError(
                "Claude raw-log fallback failed: "
                f"{result.stderr or 'unknown error'}"
            )

        match = _re.search(
            rf"{marker}START\s*\n(.*?)\n?{marker}END",
            result.stdout or "",
            _re.DOTALL,
        )
        if not match:
            raise RuntimeError("Claude raw-log fallback markers were not found")
        encoded = _re.sub(r"[^A-Za-z0-9+/=]", "", match.group(1))
        if not encoded:
            return False
        try:
            payload = _b64.b64decode(encoded, validate=True)
        except (_binascii.Error, ValueError) as exc:
            raise RuntimeError("Claude raw-log fallback was corrupt") from exc

        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "claude-code.txt").write_bytes(payload)
        return True

    async def _download_dir_once(self, source_dir: str, target_dir: Path | str) -> None:
        assert self._instance_name
        # brev copy has broken directory nesting.  Use tar piped over
        # brev exec: tar on remote, base64-encode with markers, capture
        # via exec, decode+untar locally.  Use sentinel markers to isolate
        # base64 from brev CLI spinner/connection noise.
        import re as _re
        marker = "__HARBOR_B64_" + uuid.uuid4().hex[:8] + "__"
        # Stage the archive as a base64 FILE on the node, then fetch it in
        # small slices with independent per-slice retries. A single-stream
        # fetch dies on the flaky gateway (MAC / bad-packet corruption is
        # near-certain above ~8MB), and a fresh session JSONL alone can be
        # >10MB, so streaming in one exec is structurally unreliable.
        remote_b64 = f"/tmp/.harbor_dl_{uuid.uuid4().hex[:8]}.b64"
        result = await _run_brev_exec(
            self._instance_name,
            # Exclude bulky non-essential payloads (archived prior sessions,
            # the tee'd raw stream) — harbor only needs the fresh session
            # JSONLs + trajectory.
            f"tar -czf - -C {shlex.quote(source_dir)} "
            f"--exclude='./sessions-archive*' --exclude='./claude-code.txt' "
            f"--exclude='*.tar.gz' --exclude='./sessions/debug' "
            f". 2>/dev/null | base64 -w 0 > {remote_b64}; "
            f"stat -c %s {remote_b64}",
            timeout=120,
        )
        if result.return_code != 0:
            raise RuntimeError(f"Download dir failed (stage): {result.stderr}")
        try:
            # brev exec may append the instance name as a trailing line to
            # stdout, so find the first line that is a valid integer (the
            # stat -c %s output) rather than blindly taking [-1].
            _lines = (result.stdout or "0").strip().splitlines()
            total = None
            for _l in reversed(_lines):
                _l = _l.strip()
                if _l.isdigit():
                    total = int(_l)
                    break
            if total is None:
                raise ValueError(f"no numeric line in stat output: {_lines!r}")
        except ValueError:
            raise RuntimeError(
                f"Download dir failed: could not stat staged archive "
                f"({(result.stdout or '')[-120:]})"
            )
        chunk = 2 * 1024 * 1024  # 2MB of base64 per slice — survives the gateway
        parts: list[str] = []
        offset = 0
        while offset < total:
            piece = None
            for attempt in range(4):
                res = await _run_brev_exec(
                    self._instance_name,
                    f"echo '{marker}START'; "
                    f"dd if={remote_b64} bs=64K skip={offset // 65536} "
                    f"count={chunk // 65536} 2>/dev/null; "
                    f"echo; echo '{marker}END'",
                    timeout=180,
                )
                mm = _re.search(rf"{marker}START\s*\n(.*?)\n?{marker}END",
                                res.stdout or "", _re.DOTALL)
                if res.return_code == 0 and mm:
                    got = _re.sub(r"[^A-Za-z0-9+/=]", "", mm.group(1))
                    expected = min(chunk, total - offset)
                    if len(got) == expected:
                        piece = got
                        break
                logger.warning(
                    "download slice @%d attempt %d/4 failed (rc=%s, got=%s/%s)",
                    offset, attempt + 1, res.return_code,
                    len(mm.group(1)) if mm else "no-markers",
                    min(chunk, total - offset),
                )
                await asyncio.sleep(5)
            if piece is None:
                await _run_brev_exec(self._instance_name,
                                     f"rm -f {remote_b64}", timeout=30)
                raise RuntimeError(
                    f"Download dir failed: slice at offset {offset} "
                    f"unrecoverable after retries"
                )
            parts.append(piece)
            offset += chunk
        await _run_brev_exec(self._instance_name, f"rm -f {remote_b64}",
                             timeout=30)

        if not parts:
            raise RuntimeError("Download dir failed: no base64 data between markers")
        # Joining and decoding a multi-hundred-MB trajectory on the event-loop
        # thread would prevent asyncio.timeout from firing. Every slice was
        # already marker-isolated and base64-cleaned above, so offload the only
        # remaining CPU-heavy assembly step.
        tar_bytes = await asyncio.to_thread(_decode_base64_parts, parts)
        target = Path(target_dir)
        target.mkdir(parents=True, exist_ok=True)
        await _run_local_transfer_command(
            ["tar", "-xzf", "-", "-C", str(target)],
            input_data=tar_bytes,
            timeout=60,
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

        is_trial_agent = (
            "claude --verbose --output-format=stream-json" in command
            or "codex exec " in command
        )
        agent_run_marker = (
            f"{REMOTE_AGENT_RUN_PREFIX}{uuid.uuid4().hex}"
            if is_trial_agent
            else None
        )

        parts = [
            # Make sure user-installed binaries (claude, uv, etc.) are on PATH
            # even though `brev exec` spawns a non-interactive non-login shell.
            'export PATH="$HOME/.local/bin:$HOME/.claude/bin:$PATH";',
        ]
        if not _is_local_gpu_instance(self._instance_name):
            parts.append("source ~/.profile 2>/dev/null;")
        # Ensure /logs/verifier is writable before the verifier exec —
        # Harbor's verifier phase redirects test-stdout.txt there, and the
        # directory can become root-owned between start() and verifier exec
        # (observed on warm-pool boxes where artifact collection or a
        # concurrent process recreates it as root). Only trigger when the
        # command redirects to /logs/verifier/ (the verifier stdout pattern).
        if "/logs/verifier/" in command:
            parts.append(
                "sudo chown -R $(whoami):$(id -gn) /logs/verifier 2>/dev/null || true;"
            )
        if env:
            for k, v in env.items():
                parts.append(f"export {shlex.quote(k)}={shlex.quote(v)};")
        if agent_run_marker is not None:
            # Claude/Bun background workers inherit this marker even after
            # setsid(). It gives cancellation and the next warm-box trial a
            # scope that process-group ancestry alone cannot provide.
            parts.append(
                f"export {REMOTE_AGENT_RUN_ENV}="
                f"{shlex.quote(agent_run_marker)};"
            )
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

        try:
            result = await _run_brev_exec(
                self._instance_name,
                full_cmd,
                timeout=timeout_sec or BREV_EXEC_TIMEOUT,
            )
            if agent_run_marker is not None:
                await self._reap_remote_agent_after_interrupt(
                    agent_run_marker,
                    best_effort=result.return_code != 0,
                )
            return result
        except asyncio.CancelledError:
            if agent_run_marker is not None:
                await self._reap_remote_agent_after_interrupt(
                    agent_run_marker,
                    best_effort=True,
                )
            raise

    async def _reap_remote_agent_after_interrupt(
        self,
        agent_run_marker: str,
        *,
        best_effort: bool,
    ) -> None:
        """Kill every process carrying one agent run's inherited marker."""
        assert self._instance_name
        try:
            result = await _run_brev_exec(
                self._instance_name,
                _stray_agent_reap_command(agent_run_marker),
                timeout=30,
            )
            if result.return_code != 0:
                raise RuntimeError(
                    "remote agent reap failed: "
                    + (result.stderr or result.stdout or "unknown error")
                )
        except Exception as exc:
            if not best_effort:
                raise
            logger.warning("Remote agent reap failed after interruption: %r", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decode_base64_parts(parts: list[str]) -> bytes:
    """Assemble pre-cleaned base64 slices off the asyncio event-loop thread."""
    import base64
    import binascii

    try:
        return base64.b64decode("".join(parts), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError("Download dir failed: staged base64 was corrupt") from exc


def _is_agent_log_dir(source_dir: str) -> bool:
    """Return whether Harbor is downloading its canonical agent-log dir."""
    return source_dir.rstrip("/") == "/logs/agent"


def _has_mappable_agent_log(target_dir: Path) -> bool:
    """Return whether a normal Claude trajectory/session was recovered."""
    if not target_dir.exists():
        return False
    if any(target_dir.rglob("*.jsonl")):
        return True
    return any(
        (target_dir / name).is_file()
        for name in ("trajectory.json", "trajectory.jsonl", "agent.log")
    )


def _which(cmd: str) -> bool:
    import shutil
    return shutil.which(cmd) is not None


def _stray_agent_reap_command(agent_run_marker: str | None = None) -> str:
    """SIGKILL on-box agent trees, including detached background workers.

    New agent commands export a unique marker inherited by every descendant.
    An exact marker scopes immediate post-agent cleanup; ``None`` matches all
    skill-eval markers during warm-box startup recovery. That startup mode also
    retains legacy Claude and Codex argv matches for agents started before the
    marker existed; exact immediate cleanup never broadens beyond its marker.
    Every matching PID's process group is snapshotted
    before any signal is sent, so killing the main agent group cannot hide a
    child that called ``setsid()``. The reaper's own group is always excluded.
    """
    if agent_run_marker is not None and not agent_run_marker.startswith(
        REMOTE_AGENT_RUN_PREFIX
    ):
        raise ValueError("invalid remote agent run marker")

    if agent_run_marker is None:
        marker_probe = (
            f'MARKER_RE="^{REMOTE_AGENT_RUN_ENV}='
            f'{REMOTE_AGENT_RUN_PREFIX}[0-9a-f]+$"; '
            "MATCH_EXACT=0; INCLUDE_LEGACY=1; "
        )
    else:
        marker_probe = (
            f"MARKER={shlex.quote(f'{REMOTE_AGENT_RUN_ENV}={agent_run_marker}')}; "
            "MATCH_EXACT=1; INCLUDE_LEGACY=0; "
        )

    return (
        marker_probe
        + "CLAUDE_PAT='claude --verbose --output-format=stream-jso[n]'; "
        "CODEX_PAT='codex exe[c]'; "
        'SELF_PGID=$(ps -o pgid= -p $$ | tr -d " "); '
        "PIDS=''; PGIDS=''; "
        "for env_file in /proc/[0-9]*/environ; do "
        '  [ -r "$env_file" ] || continue; '
        '  pid=${env_file#/proc/}; pid=${pid%/environ}; '
        '  [ "$pid" = "$$" ] && continue; '
        '  if [ "$MATCH_EXACT" -eq 1 ]; then '
        '    tr "\\0" "\\n" < "$env_file" 2>/dev/null '
        '      | grep -Fqx -- "$MARKER" || continue; '
        "  else "
        '    tr "\\0" "\\n" < "$env_file" 2>/dev/null '
        '      | grep -Eq -- "$MARKER_RE" || continue; '
        "  fi; "
        '  PIDS="$PIDS $pid"; '
        "done; "
        'if [ "$INCLUDE_LEGACY" -eq 1 ]; then '
        '  PIDS="$PIDS $(pgrep -f "$CLAUDE_PAT" || true) '
        '$(pgrep -f "$CODEX_PAT" || true)"; '
        "fi; "
        "for pid in $PIDS; do "
        '    PGID=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d " " || true); '
        '    if [ -n "$PGID" ] && [ "$PGID" = "$SELF_PGID" ]; then continue; fi; '
        '    if [ -n "$PGID" ] && [ "$PGID" != "0" ]; then '
        '      PGIDS="$PGIDS $PGID"; '
        "    fi; "
        "done; "
        "UNIQUE_PGIDS=''; "
        "for pgid in $(printf '%s\\n' $PGIDS | sort -un); do "
        '  [ -n "$pgid" ] || continue; '
        '  kill -9 -- "-$pgid" 2>/dev/null || true; '
        '  UNIQUE_PGIDS="$UNIQUE_PGIDS $pgid"; '
        "done; "
        "for pid in $PIDS; do kill -9 \"$pid\" 2>/dev/null || true; done; "
        'if [ -n "$UNIQUE_PGIDS" ]; then '
        '  echo "[stray-agent-reap] killed pgids:$UNIQUE_PGIDS"; '
        "else "
        '  echo "[stray-agent-reap] none"; '
        "fi"
    )


def _prior_agent_output_archive_command() -> str:
    """Archive every prior output that Harbor could mistake for this trial.

    Session projects are consumed by Claude's trajectory mapper. Root-level
    outputs are uploaded back by Harbor after a completed trial and are also
    recognized by our fallback/judge paths. Leaving either class in place can
    make a pre-agent failure inherit the previous trial's evidence. Move both
    classes aside: after an abrupt cancellation the root raw log may be the
    only surviving evidence because coordinator download never completed.
    Old archives are pruned to keep warm-box storage bounded.
    """
    return (
        "ts=$(date +%Y%m%d-%H%M%S)-$$; "
        "PROJ=/logs/agent/sessions/projects; "
        "ROOT=/logs/agent; "
        "OUTPUTS='claude-code.txt trajectory.json trajectory.jsonl agent.log'; "
        "HAS_SESSIONS=0; "
        "HAS_OUTPUT=0; "
        'if [ -d "$PROJ" ] && [ -n "$(ls -A "$PROJ" 2>/dev/null)" ]; then '
        "  HAS_SESSIONS=1; "
        "fi; "
        'for name in $OUTPUTS; do [ -e "$ROOT/$name" ] && HAS_OUTPUT=1; done; '
        'if [ "$HAS_SESSIONS" -eq 1 ] || [ "$HAS_OUTPUT" -eq 1 ]; then '
        '  ARCHIVE="$HOME/.claude-archive/$ts"; '
        '  mkdir -p "$ARCHIVE" || exit 1; '
        "fi; "
        'if [ "$HAS_SESSIONS" -eq 1 ]; then '
        '  mkdir -p "$ARCHIVE/sessions" || exit 1; '
        '  mv "$PROJ"/* "$ARCHIVE/sessions/" || exit 1; '
        '  echo "[trajectory-isolation] archived prior sessions to $ARCHIVE/sessions"; '
        "fi; "
        'if [ "$HAS_OUTPUT" -eq 1 ]; then '
        '  mkdir -p "$ARCHIVE/root-output" || exit 1; '
        "  for name in $OUTPUTS; do "
        '    [ ! -e "$ROOT/$name" ] || mv "$ROOT/$name" "$ARCHIVE/root-output/" || exit 1; '
        "  done; "
        '  echo "[trajectory-isolation] archived root agent outputs to $ARCHIVE/root-output"; '
        "fi; "
        'if [ -d "$HOME/.claude-archive" ]; then '
        '  find "$HOME/.claude-archive" -mindepth 1 -maxdepth 1 '
        '    -type d -mtime +7 -exec rm -rf {} + || exit 1; '
        "fi"
    )


def _claude_task_scratch_cleanup_command() -> str:
    """Remove stale Claude Code background-task markers for this user.

    Claude Code keys temp scratch by effective UID, e.g.
    `/tmp/claude-1002/-home-shadeform/<session>/tasks/<id>.output`. Removing
    the old `tasks/` dirs prevents completed background-command notifications
    from being replayed into the next Harbor trial.
    """
    return (
        "UID_NUM=$(id -u); "
        'BASE="/tmp/claude-${UID_NUM}"; '
        'if [ -d "$BASE" ]; then '
        '  BEFORE=$(find "$BASE" -type d -name tasks -prune 2>/dev/null | wc -l); '
        # No `2>/dev/null` on the rm step: a real cleanup failure's stderr must
        # reach claude_task_cleanup_result.stderr so the RuntimeError tail isn't
        # empty (the BEFORE/AFTER count-finds keep theirs — that noise is benign).
        '  find "$BASE" -type d -name tasks -prune -exec rm -rf {} + || exit 1; '
        '  AFTER=$(find "$BASE" -type d -name tasks -prune 2>/dev/null | wc -l); '
        '  echo "[claude-task-scratch] removed task dirs before=$BEFORE after=$AFTER base=$BASE"; '
        'else '
        '  echo "[claude-task-scratch] no scratch base $BASE"; '
        "fi"
    )


def _process_start_ticks(pid: int) -> str | None:
    """Return Linux /proc start ticks so run_leg can reject PID reuse."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError, PermissionError, OSError):
        return None
    closing_paren = stat.rfind(")")
    if closing_paren < 0:
        return None
    fields_from_state = stat[closing_paren + 2 :].split()
    return fields_from_state[19] if len(fields_from_state) > 19 else None


def _register_transport_process(proc: asyncio.subprocess.Process) -> None:
    """Publish a detached child identity for run_leg's teardown registry."""
    registry = os.environ.get("BREV_TRANSPORT_PGID_FILE")
    start_ticks = _process_start_ticks(proc.pid)
    if not registry or start_ticks is None:
        return
    line = f"{proc.pid} {start_ticks}\n".encode()
    try:
        fd = os.open(registry, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except OSError as exc:
        # Registration is a second teardown fence. Local cancellation remains
        # active even if an operator-provided registry path is unusable.
        logger.warning("Could not register transport PGID %s: %r", proc.pid, exc)


def _kill_proc_group(proc: asyncio.subprocess.Process) -> None:
    """SIGKILL the child's whole process group.

    `proc.kill()` signals only the immediate child (the `brev`/`ssh`/`scp`
    CLI). On a stalled transfer that leaves the underlying ssh data channel
    orphaned — holding the secure-link/session open and wedging the box for
    the next step (a killed large artifact pull can otherwise leave the
    following trial's ports unreachable). Killing
    the whole group reaps the orphan. Requires the child to have been
    started with `start_new_session=True` so it leads its own group; falls
    back to a plain kill if group signaling fails."""
    # Every caller starts the child with start_new_session=True, so its PID is
    # also the stable process-group id. Signal that known PGID even when the
    # immediate brev/ssh leader has already exited: descendants may still be
    # alive and holding stdout/stderr pipes open.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass


async def _communicate_with_cancellation_cleanup(
    proc: asyncio.subprocess.Process,
    *,
    input_data: bytes,
    timeout: int,
) -> tuple[bytes | None, bytes | None]:
    """Communicate with a subprocess and reap it if the caller is cancelled.

    Harbor enforces phase timeouts by cancelling the environment coroutine.
    Without this guard, the runner-side ``brev``/``ssh`` process and its SSH
    child outlive the coroutine, delaying Harbor's timeout handling and
    poisoning artifact recovery.  Re-raise the original cancellation only
    after the whole process group is dead and collected.
    """
    try:
        return await asyncio.wait_for(
            proc.communicate(input=input_data),
            timeout=timeout,
        )
    except asyncio.CancelledError:
        _kill_proc_group(proc)
        try:
            await asyncio.wait_for(proc.communicate(), timeout=10)
        except (
            asyncio.CancelledError,
            TimeoutError,
            BrokenPipeError,
            ConnectionResetError,
            ProcessLookupError,
        ) as exc:
            # SIGKILL was already sent to the stable PGID. Do not replace the
            # Harbor cancellation with a secondary pipe/reap error or wait
            # forever on an uninterruptible local transport.
            logger.warning("Subprocess reap after cancellation was incomplete: %r", exc)
            with contextlib.suppress(
                asyncio.CancelledError,
                TimeoutError,
                ProcessLookupError,
            ):
                await asyncio.wait_for(proc.wait(), timeout=1)
        raise


async def _run_local_transfer_command(
    args: list[str],
    *,
    input_data: bytes = b"",
    timeout: int,
) -> None:
    """Run local tar work asynchronously so transfer deadlines can cancel it."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    _register_transport_process(proc)
    try:
        stdout, stderr = await _communicate_with_cancellation_cleanup(
            proc,
            input_data=input_data,
            timeout=timeout,
        )
    except TimeoutError as exc:
        _kill_proc_group(proc)
        with contextlib.suppress(TimeoutError, ProcessLookupError):
            await asyncio.wait_for(proc.wait(), timeout=1)
        raise subprocess.TimeoutExpired(args, timeout) from exc
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            args,
            output=stdout,
            stderr=stderr,
        )


# Registered external nodes (BYOH / DGX-Spark / IGX-Thor) can't use
# `brev exec` — they require a direct SSH session via the alias that
# `brev shell` writes into ~/.brev/ssh_config.  We cache the list on
# first query to avoid repeated `brev ls nodes` round-trips.
_registered_nodes_cache: dict[str, dict] | None = None


async def _load_registered_nodes() -> dict[str, dict]:
    """Return {lower_name: node_dict} from `brev ls nodes --json`.
    Cached per-process.  Safe to call on any host that has the brev CLI."""
    global _registered_nodes_cache
    if _registered_nodes_cache:
        return _registered_nodes_cache
    # Retry transient CLI failures and NEVER cache an empty result from a
    # failed call — one hiccup here used to poison the whole trial with
    # "instance not found" (empty dict cached per-process, no second chance).
    for attempt in range(4):
        cache: dict[str, dict] = {}
        try:
            result = await _run_brev("ls", "nodes", "--json", timeout=15)
            nodes = _parse_brev_json(result.stdout) if result.stdout else []
            for n in nodes:
                name = (n.get("name") or "").strip()
                if name:
                    cache[name.lower()] = n
        except Exception as e:
            logger.warning("brev ls nodes failed (attempt %s): %s", attempt + 1, e)
        if cache:
            _registered_nodes_cache = cache
            return cache
        await asyncio.sleep(5)
    logger.warning("brev ls nodes returned no nodes after retries")
    return {}


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
    _register_transport_process(proc)
    try:
        stdout, stderr = await _communicate_with_cancellation_cleanup(
            proc,
            input_data=b"",
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
    _register_transport_process(proc)
    try:
        stdout, stderr = await _communicate_with_cancellation_cleanup(
            proc,
            input_data=b"",
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


async def _run_local_exec(
    command: str,
    timeout: int = BREV_EXEC_TIMEOUT,
) -> ExecResult:
    """Execute a Harbor environment command on this GPU runner."""
    proc = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        command,
        cwd=str(Path.home()),
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
            stderr="Command timed out",
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
    if _is_local_gpu_instance(instance):
        return await _run_local_exec(command, timeout)
    if await _is_registered_node(instance):
        # ssh command-execs run NON-LOGIN shells: ~/.profile (and thus the
        # forwarded ~/.eval_env) is never sourced, silently dropping
        # PR_HEAD_SHA/NGC keys/etc from every exec. Source it inline.
        command = f". ~/.eval_env 2>/dev/null || true; {command}"
        return await _run_ssh_exec(_ssh_alias_for(instance), command, timeout)
    # brev exec also spawns a NON-LOGIN shell — ~/.profile is never sourced,
    # so the forwarded env vars in ~/.eval_env (PR_HEAD_SHA, NGC keys, etc.)
    # are invisible to every command. Source it inline, same as SSH nodes.
    command = f". ~/.eval_env 2>/dev/null || true; {command}"
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
    _register_transport_process(proc)

    try:
        stdout, stderr = await _communicate_with_cancellation_cleanup(
            proc,
            input_data=b"\n",
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
    """Run ``brev copy <src> <dst>`` with transient-failure retries.

    The brev gateway occasionally corrupts a connection mid-transfer
    ("Bad packet length ... Connection corrupted"), which used to fail the
    whole trial (AddTestsDirError). Copies are idempotent — retry."""
    result = None
    for attempt in range(3):
        result = await _run_brev_copy_once(src, dst, timeout)
        if result.return_code == 0:
            return result
        logger.warning("brev copy failed (attempt %s): %s",
                       attempt + 1, (result.stderr or "")[-200:])
        await asyncio.sleep(10)
    return result


async def _run_brev_copy_once(
    src: str,
    dst: str,
    timeout: int = BREV_COPY_TIMEOUT,
) -> ExecResult:
    """Run ``brev copy <src> <dst>`` and return result.

    For registered external nodes, transparently falls back to ``scp``
    using the ssh alias (same host:path convention, just with lowercase
    name)."""
    local_instance = _local_gpu_instance()
    if local_instance:
        local_prefix = f"{local_instance}:"
        src_is_runner = src.lower().startswith(local_prefix.lower())
        dst_is_runner = dst.lower().startswith(local_prefix.lower())
        if src_is_runner or dst_is_runner:
            source = Path(src[len(local_prefix):] if src_is_runner else src)
            target = Path(dst[len(local_prefix):] if dst_is_runner else dst)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                await asyncio.to_thread(shutil.copy2, source, target)
            except OSError as exc:
                return ExecResult(
                    stdout=None,
                    stderr=str(exc),
                    return_code=1,
                )
            return ExecResult(stdout="", stderr=None, return_code=0)

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
    _register_transport_process(proc)

    try:
        stdout, stderr = await _communicate_with_cancellation_cleanup(
            proc,
            input_data=b"\n",
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
    _register_transport_process(proc)
    try:
        stdout, stderr = await _communicate_with_cancellation_cleanup(
            proc,
            input_data=(stdin_data or "").encode() + b"\n",
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
    """Strip trailing walkthrough text and parse JSON from brev CLI.

    Handles legacy flat arrays (`[{...}, ...]`) and object envelopes such as
    `{"workspaces": [{...}, ...]}`.
    """
    if not raw:
        return []

    def _extract_list(parsed: object) -> list[dict]:
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in ("workspaces", "instances", "nodes"):
                if isinstance(parsed.get(key), list):
                    return parsed[key]
            for value in parsed.values():
                if isinstance(value, list):
                    return value
        return []

    # Try full parse first (handles both formats without bracket heuristics).
    stripped = raw.strip()
    try:
        return _extract_list(json.loads(stripped))
    except json.JSONDecodeError:
        pass

    # Fallback: strip trailing walkthrough text after last `]`.
    bracket = raw.rfind("]")
    if bracket < 0:
        return []

    # Try a full object envelope first; this handles `{"workspaces": [...]}` plus
    # trailing CLI text.
    brace_end = raw.rfind("}")
    if brace_end > bracket:
        try:
            parsed = json.loads(raw[: brace_end + 1])
            extracted = _extract_list(parsed)
            if extracted:
                return extracted
        except json.JSONDecodeError:
            pass

    # Fallback: extract the final flat JSON array prefix.
    try:
        return _extract_list(json.loads(raw[: bracket + 1]))
    except json.JSONDecodeError:
        pass

    # Last resort: extract the inner-most array from an otherwise partial
    # wrapper.
    start = raw.find("[")
    if start >= 0 and bracket > start:
        try:
            return _extract_list(json.loads(raw[start: bracket + 1]))
        except json.JSONDecodeError:
            pass
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
    if _is_local_gpu_instance(name):
        return {
            "name": _local_gpu_instance(),
            "type": "local-gpu-runner",
            "gpu": "",
            "instance_type": "local-gpu-runner",
            "status": "RUNNING",
            "_registered": True,
            "_local_gpu_runner": True,
        }

    for attempt in range(4):
        result = await _run_brev("ls", "--json", timeout=30)
        raw = result.stdout or ""
        # A well-formed JSON array response (even if empty) is authoritative —
        # treat an empty-list response as "not a Brev-managed instance" and
        # fall through to the registered-node check.  Only truly empty stdout
        # or missing closing `]` is transient. An org with zero managed
        # instances prints `null` (not `[]`) — also authoritative-empty, or
        # every registered-external-node lookup would burn all retries here
        # and report "instance not found" without ever checking nodes.
        # (`null` may be followed by a "Please create a running instance…"
        # banner on the same stream.)
        if raw.strip().startswith("null"):
            parsed = []
        elif raw.strip() == "" or raw.rfind("]") < 0:
            logger.info("brev ls returned empty stdout (attempt %s) — retrying", attempt + 1)
            await asyncio.sleep(5)
            continue
        else:
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
    """SSH in and count GPUs via nvidia-smi. Raises only if the box has
    FEWER GPUs than required — over-provisioned boxes are accepted (>=)."""
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
    # Over-provisioning is fine — a 1-GPU spec runs on a 2-GPU box with the
    # 2nd GPU idle. Only UNDER-provisioning is fatal (a 2-GPU spec can't
    # launch its second model on a 1-GPU box). The orchestrator still PREFERS
    # an exact match for pool partitioning (AGENTS.md § 5a); this is the
    # fallback gate. Returning (not raising) lets start() proceed to
    # _reset_docker_runtime, so a fallback over-provisioned box still gets
    # wiped before the trial deploys onto it.
    if actual < required_count:
        raise RuntimeError(
            f"Brev instance '{instance_name}' has {actual} GPU(s) (live "
            f"nvidia-smi); task requires at least {required_count}. Pick a "
            f"fleet member with >= the required GPU count."
        )
    logger.info(
        "Instance '%s' live gpu_count: %d (satisfies required >= %d)",
        instance_name, actual, required_count,
    )


async def _check_local_gpu_requirements(instance_name: str, req: dict) -> None:
    """Fail closed on the physical hardware of a direct GPU runner."""
    required_count = int(req.get("gpu_count", 1) or 0)
    if required_count == 0:
        return

    result = await _run_local_exec(
        "nvidia-smi --query-gpu=name,memory.total "
        "--format=csv,noheader,nounits",
        timeout=30,
    )
    if result.return_code != 0 or not (result.stdout or "").strip():
        raise RuntimeError(
            f"Local GPU runner '{instance_name}' failed nvidia-smi: "
            f"{(result.stderr or result.stdout or '')[-300:]}"
        )

    gpus: list[tuple[str, int]] = []
    for line in result.stdout.splitlines():
        name, separator, memory = line.rpartition(",")
        if not separator:
            continue
        try:
            memory_mib = int(memory.strip())
        except ValueError:
            continue
        gpus.append((name.strip(), memory_mib))

    required_type = (req.get("gpu_type") or "").upper()

    def matches_type(name: str) -> bool:
        if not required_type:
            return True
        want_tokens = set(required_type.replace("-", " ").split())
        have_tokens = set(name.upper().replace("-", " ").split())
        return want_tokens.issubset(have_tokens) or required_type in name.upper()

    matching = [gpu for gpu in gpus if matches_type(gpu[0])]
    if len(matching) < required_count:
        names = ", ".join(name for name, _ in gpus) or "none"
        raise RuntimeError(
            f"Local GPU runner '{instance_name}' has {len(matching)} matching "
            f"GPU(s), task requires {required_count} of {required_type or 'any'}; "
            f"detected: {names}"
        )

    min_vram_gb = int(req.get("min_vram_gb_per_gpu", 0) or 0)
    if min_vram_gb:
        too_small = [
            f"{name} ({memory_mib} MiB)"
            for name, memory_mib in matching[:required_count]
            if memory_mib < min_vram_gb * 1000
        ]
        if too_small:
            raise RuntimeError(
                f"Local GPU runner '{instance_name}' does not provide "
                f"{min_vram_gb} GB per GPU: {', '.join(too_small)}"
            )

    logger.info(
        "Local GPU runner '%s' satisfies gpu_type=%r, gpu_count>=%d",
        instance_name,
        required_type,
        required_count,
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

    # gpu_count check — require >= (over-provisioned OK), NOT strict equality.
    # A 1-GPU spec runs fine on a 2-GPU box (2nd GPU idles); only an
    # UNDER-provisioned box (fewer GPUs than required) can't launch the spec's
    # second model and must be rejected. Pool partitioning is preserved by the
    # orchestrator PREFERRING an exact match (AGENTS.md § 5a) — this is the
    # validate-time gate for the fallback case. Crucially, because we no longer
    # raise here for an over-provisioned box, start() proceeds to
    # _reset_docker_runtime, so a fallback 2-GPU box is wiped clean before the
    # trial deploys onto it.
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
        elif catalog_count < required_count:
            errors.append(
                f"gpu_count: want at least {required_count}, instance has "
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
        require_clauses.append(f"gpu_count>={required_count}")
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
