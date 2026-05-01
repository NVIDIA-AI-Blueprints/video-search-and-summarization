#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Helm-sync agent — single-shot CI-driven runner.

Called by .github/workflows/helm-sync.yml on push to `pull-request/<N>`
when files under `deploy/` (or this harness) change. Spawns one
`claude-agent-sdk` agent with `.github/helm-sync/AGENTS.md` as its
system prompt and lets it drive: full-PR diff → docker-vs-helm parity
check → optional bot PR + comment on source PR.

The agent gets Bash/Read/Edit/Write/Glob/Grep. AGENTS.md tells it that
it must NOT modify docker-side files (compose*.yml, Dockerfiles,
.env*) and must NOT run trials.

Env (set by the workflow step):
    PR_NUMBER        PR being checked (e.g. "123")
    PR_BASE          Base branch (e.g. "develop")
    PR_HEAD_SHA      Mirror head SHA (full)
    PR_REPO          "owner/repo"
    GITHUB_RUN_ID    CI run id (used for bot-branch namespacing)
    ANTHROPIC_*      Agent SDK credentials (sourced from coordinator .env)
    GH_TOKEN         PAT for bot PR creation + comment posting

Exit codes:
    0 - agent completed (drift may still have been reported via bot PR)
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

# .github/helm-sync/helm_sync_agent.py:
#   parents[0] = .github/helm-sync
#   parents[1] = .github
#   parents[2] = repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = Path(__file__).resolve().parent / "AGENTS.md"

# Hard cap on the agent's tool loop. The agent's job is bounded:
# walk the diff (~1 turn), compare each file pair (~1 turn each),
# optionally edit helm files + git push + gh pr create (~10 turns).
# 200 covers a wide PR with ~50 files; tune if a real PR shows churn.
MAX_TURNS = int(os.environ.get("HELM_SYNC_MAX_TURNS", "200"))

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
    """The NVIDIA Anthropic proxy rejects requests carrying the
    `context_management` field claude-agent-sdk emits by default
    ("context_management: Extra inputs are not permitted", HTTP 400).
    Setting `CLAUDE_CODE_DISABLE_THINKING=1` strips the field. The CI
    workflow already exports this; set it defensively for local
    smoke-tests too. Mirrors `skills_eval_agent.py`."""
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

    # User prompt = per-invocation runtime data only. Procedure,
    # tool guidance, hard rules, output format all live in AGENTS.md
    # (system prompt) — duplicating them here just spends tokens.
    user_prompt = (
        f"PR_NUMBER={pr_number}\n"
        f"PR_BASE={pr_base}\n"
        f"PR_HEAD_SHA={pr_head}\n"
        f"PR_REPO={pr_repo}\n"
        f"GITHUB_RUN_ID={run_id}\n"
        f"REPO_ROOT={REPO_ROOT}\n"
        f"\n"
        f"Process this PR per AGENTS.md."
    )

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
                        # Stream text to stdout so the GH Actions log
                        # has a live trace.
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
    return 0


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
    return rc


if __name__ == "__main__":
    sys.exit(main())
