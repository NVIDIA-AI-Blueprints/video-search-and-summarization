#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skills eval agent — single-shot CI-driven runner.

Called by .github/workflows/skills-eval.yml on push to `pull-request/<N>`
when files under `skills/` (or the harness itself) change. Spawns one
`claude-agent-sdk` agent with `.github/skill-eval/AGENTS.md` as its
system prompt and lets it drive the eval end-to-end: diff →
adapter/dataset → Brev lock → harbor run → results comment → cleanup.

The agent gets Bash/Read/Edit/Write/Glob/Grep. It is explicitly told
(in AGENTS.md) that it must NOT modify anything under `skills/`.

Env (set by the workflow step):
    PR_NUMBER        PR being evaluated (e.g. "100")
    PR_BASE          Base branch (e.g. "feat/skills")
    PR_HEAD_SHA      Mirror head SHA (full)
    PR_REPO          "owner/repo"
    GITHUB_RUN_ID    CI run id (for lock + instance-started tracking)
    ANTHROPIC_*      Agent SDK credentials (sourced from coordinator .env)
    GH_TOKEN         PR comment posting
    NGC_CLI_API_KEY  Local NIM pulls in trials
    LLM_REMOTE_URL   Optional; enables remote-* deploy modes
    VLM_REMOTE_URL   Optional; enables remote-* deploy modes
    BREV_ENV_ID      Set by Brev on the coordinator host; part of secure-link URLs

Exit codes:
    0 - agent completed (eval may still report failures in PR comment)
    1 - setup error (missing env, AGENTS.md not found, sdk install failed)
    2 - agent crashed
    3 - agent hit max_turns without finishing
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# .github/skill-eval/skills_eval_agent.py:
#   parents[0] = .github/skill-eval
#   parents[1] = .github
#   parents[2] = repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = Path(__file__).resolve().parent / "AGENTS.md"

# Hard cap on the agent's tool loop — one `/deploy` trial is ~15 min of
# `Bash(uvx harbor run ...)`, plus its own tool calls. 300 turns covers
# a full fan-out with room for retries.
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "300"))

# How long to sleep after the agent exits before stopping/deleting Brev
# instances it spun up. Lets a human see last-minute logs / traces.
COOLDOWN_SEC = int(os.environ.get("POST_EVAL_COOLDOWN_SEC", "300"))


# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _require(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"FATAL: {name} not set in environment", file=sys.stderr)
        sys.exit(1)
    return v


def _ensure_sdk() -> None:
    """Install `claude-agent-sdk` if missing. Runner is stateful so this
    is usually a no-op after the first run."""
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "claude-agent-sdk>=0.0.5"],
            check=False, timeout=180,
        )


def _disable_server_thinking() -> None:
    """The NVIDIA Anthropic proxy rejects requests that carry the
    `context_management` field claude-code ≥ 2.1.x emits by default
    ("context_management: Extra inputs are not permitted", HTTP 400).
    Setting `CLAUDE_CODE_DISABLE_THINKING=1` strips the field before
    the request goes out. The CI workflow already exports this, but
    set it here defensively so local smoke-tests work against the
    NVIDIA proxy too."""
    if "CLAUDE_CODE_DISABLE_THINKING" not in os.environ:
        os.environ["CLAUDE_CODE_DISABLE_THINKING"] = "1"


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

async def run_agent() -> int:
    from claude_agent_sdk import (  # type: ignore
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
        ResultMessage, TextBlock, ToolUseBlock,
    )

    pr_number = _require("PR_NUMBER")
    pr_base = _require("PR_BASE")
    pr_head = _require("PR_HEAD_SHA")
    pr_repo = _require("PR_REPO")
    run_id = os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")

    if not AGENTS_MD.exists():
        print(f"FATAL: {AGENTS_MD} not found", file=sys.stderr)
        return 1

    system_prompt = AGENTS_MD.read_text()

    user_prompt = f"""
PR #{pr_number} just pushed new commits touching `skills/` (or eval harness code).

Context:
  repo          = {pr_repo}
  PR number     = {pr_number}
  base branch   = {pr_base}
  mirror head   = {pr_head}
  workflow run  = {run_id}
  working dir   = {REPO_ROOT}

Your workspace is the repo at `{REPO_ROOT}` (already checked out to the mirror head).
The coordinator host is vss-skill-validator; Brev CLI is authenticated, Docker is running.

Process this PR per AGENTS.md: diff → detect changed skills → update or create the
adapter under `.github/skill-eval/adapters/<skill>/` → generate the dataset → acquire
a Brev lock for the target platform(s) → run harbor trials → gather results →
post ONE comment per (PR, spec) batch → release the lock → stop/delete any Brev
instance you brought online.

Write the list of Brev instance IDs you provisioned to
`/tmp/brev/started-by-{run_id}.txt` (one per line). The CI step will use that file
to drive cleanup after a {COOLDOWN_SEC}s cooldown.

When done, emit a one-line final summary starting with `DONE:` so the workflow
can grep for it. On blocker (missing_probe, env issue, nothing to eval), emit a
line starting with `BLOCKED:` followed by the reason.
"""

    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
    print(f"[agent] starting · pr={pr_number} base={pr_base} head={pr_head[:8]} "
          f"model={model} max_turns={MAX_TURNS}", flush=True)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
        model=model,
        max_turns=MAX_TURNS,
        permission_mode="bypassPermissions",
        cwd=str(REPO_ROOT),
    )

    final_text: list[str] = []
    total_cost = 0.0
    hit_max_turns = False

    async with ClaudeSDKClient(options=options) as client:
        await client.query(user_prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock) and block.text:
                        # Stream text to stdout so the GH Actions log has a live trace.
                        print(block.text, flush=True)
                        final_text.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        # Single-line tool-call breadcrumb in the log.
                        name = getattr(block, "name", "?")
                        inp = getattr(block, "input", {}) or {}
                        hint = ""
                        if name == "Bash":
                            cmd = str(inp.get("command", ""))[:140]
                            hint = cmd.replace("\n", " ")
                        elif name in ("Read", "Edit", "Write"):
                            hint = str(inp.get("file_path", ""))[-140:]
                        elif name in ("Glob", "Grep"):
                            hint = str(inp.get("pattern", ""))[:140]
                        print(f"  [tool] {name} :: {hint}", flush=True)
            elif isinstance(msg, ResultMessage):
                total_cost = getattr(msg, "total_cost_usd", 0.0) or 0.0
                if getattr(msg, "stop_reason", None) == "max_turns":
                    hit_max_turns = True
                break

    print(f"[agent] finished · cost=${total_cost:.2f}", flush=True)
    if hit_max_turns:
        print("[agent] hit max_turns — agent may not have completed",
              file=sys.stderr)
        return 3

    # Protocol enforcement: the agent must end with `DONE:` or `BLOCKED:`
    # in its last few text blocks. Without this guard, an agent that
    # quits mid-flow (model decided the conversation was over without
    # reaching the comment-post step — observed on run 25256515296,
    # PR #221, where the agent burned ~25 turns polling and then
    # stopped without DONE/BLOCKED, leaving the workflow green ✓ but
    # the source PR with no result comment) would produce a silent
    # green check. Treat that as a real failure with exit code 4.
    summary = "\n".join(final_text[-10:])
    if "BLOCKED:" in summary:
        print("[agent] reported blocker", file=sys.stderr)
        return 0   # blocker is a valid outcome, not a crash
    if "DONE:" in summary:
        return 0
    print(
        "[agent] exited without a final DONE: or BLOCKED: marker — "
        "protocol failure (no verdict reached). This typically means "
        "the agent gave up mid-trial without posting a results comment. "
        "Look at the trial logs and the workflow artifact; per AGENTS.md "
        "§ Output requirements the final printed line must start with "
        "DONE: or BLOCKED:.",
        file=sys.stderr,
    )
    return 4


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

_STOPPABLE_TYPES = {"l40s", "rtx"}   # see AGENTS.md lifecycle table
_DELETE_TYPES = {"h100", "massedcompute"}
# SPARK / BYOH are no-op

def cleanup_instances() -> None:
    """After the agent exits, wait COOLDOWN_SEC then stop or delete any
    Brev instance the agent brought online. Identification comes from
    `/tmp/brev/started-by-<run_id>.txt`, which the agent is told to
    populate. Unknown entries are logged and skipped — never delete an
    instance we can't identify."""
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not run_id:
        print("[cleanup] no GITHUB_RUN_ID → skipping teardown", flush=True)
        return

    marker = Path(f"/tmp/brev/started-by-{run_id}.txt")
    if not marker.exists() or not marker.read_text().strip():
        print(f"[cleanup] {marker} missing/empty — nothing to tear down", flush=True)
        return

    names = [line.strip() for line in marker.read_text().splitlines() if line.strip()]
    if not names:
        return

    print(f"[cleanup] {COOLDOWN_SEC}s cooldown before tearing down: {names}", flush=True)
    time.sleep(COOLDOWN_SEC)

    # Re-check live state — name → (status, instance_type)
    try:
        import json as _json
        out = subprocess.check_output(
            ["brev", "ls", "--json"], timeout=30,
        ).decode()
        data = _json.loads(out)
        instances = data if isinstance(data, list) else [data]
        by_name = {i.get("name"): i for i in instances if isinstance(i, dict)}
    except Exception as exc:
        print(f"[cleanup] brev ls --json failed: {exc}; skipping", flush=True)
        return

    for name in names:
        inst = by_name.get(name)
        if inst is None:
            print(f"[cleanup] {name}: not found in brev ls — skip", flush=True)
            continue
        itype = (inst.get("instance_type") or "").lower()
        # Decide stop vs delete based on the AGENTS.md § lifecycle rules.
        if any(k in itype for k in ("h100", "dmz.h100", "massedcompute",
                                     "scaleway", "nebius", "hyperstack",
                                     "latitude", "oci")):
            action = ["brev", "delete", name]
            reason = "non-stoppable provider — delete"
        elif any(k in itype for k in ("l40s-48gb.2x", "l40s-48gb.1x",
                                       "g7e", "g6e", "crusoe")):
            action = ["brev", "stop", name]
            reason = "stoppable — stop"
        elif inst.get("_registered") or inst.get("kind") == "registered":
            print(f"[cleanup] {name}: BYOH registered node — no-op", flush=True)
            continue
        else:
            # Unknown provider — default to stop (safer than delete).
            action = ["brev", "stop", name]
            reason = f"unknown provider {itype!r} — defaulting to stop"

        print(f"[cleanup] {name}: {reason}  →  {' '.join(action)}", flush=True)
        try:
            subprocess.run(action, timeout=120, check=False)
        except subprocess.TimeoutExpired:
            print(f"[cleanup] {name}: {action[1]} timed out after 120s", flush=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    _disable_server_thinking()
    _ensure_sdk()
    try:
        rc = asyncio.run(run_agent())
    except KeyboardInterrupt:
        print("[agent] interrupted", file=sys.stderr)
        rc = 2
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] crashed: {exc!r}", file=sys.stderr)
        import traceback; traceback.print_exc()
        rc = 2
    finally:
        cleanup_instances()
    return rc


if __name__ == "__main__":
    sys.exit(main())
