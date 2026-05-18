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
    PR_NUMBER             PR being evaluated, e.g. "100" (push mode; blank on workflow_dispatch)
    PR_BASE               Base branch, e.g. "develop" (push mode; blank on workflow_dispatch)
    PR_HEAD_SHA           Mirror or main-branch head SHA (full)
    PR_REPO               "owner/repo"
    GITHUB_RUN_ID         CI run id (for lock + instance-started tracking)
    GITHUB_STEP_SUMMARY   Path to a markdown file appended to the Actions run summary
                          page. The agent writes per-spec results tables here in
                          manual-sweep mode (no PR to comment on).
    MANUAL_FULL_SWEEP     "1" when workflow_dispatch fired. Swaps user prompt:
                          enumerate every skills/<skill>/eval/*.json for the
                          skill named in MANUAL_SKILLS_FILTER (or all skills when
                          `*`), write results to $GITHUB_STEP_SUMMARY, never
                          post `gh pr comment`, never raise bot PRs (missing
                          adapter is a BLOCKED outcome). Every spec on the
                          chosen skill(s) runs — no spec-level filter knob.
    MANUAL_SKILLS_FILTER  Single skill name from the dispatch dropdown, or "*"
                          for all (default "*"). Validated server-side by GH
                          Actions against the type:choice enum.
    ANTHROPIC_*           Agent SDK credentials (sourced from coordinator .env)
    GH_TOKEN              PR comment posting (push mode only)
    NGC_CLI_API_KEY       Local NIM pulls in trials
    LLM_REMOTE_URL        Optional; enables remote-* deploy modes
    VLM_REMOTE_URL        Optional; enables remote-* deploy modes
    BREV_ENV_ID           Set by Brev on the coordinator host; part of secure-link URLs

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

# Hard cap on the agent's tool loop — one trial burns ~20-30 harness
# turns (startup + brev wait + `uvx harbor run` exec + reading results +
# migrating to _viewer), so a full-PR fan-out of 10-15 trials plus
# recon/retry overhead exceeds the previous 300 ceiling. Run
# 24879743425 burned ~270 turns on only 3 trials before hitting it
# mid-lvs with 10+ trials unstarted. 600 is a safety valve against
# runaway loops, not a budget knob — the workflow's 8h wall-clock
# (skills-eval.yml timeout-minutes: 480) is the real ceiling.
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "600"))

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

    manual_sweep = os.environ.get("MANUAL_FULL_SWEEP") == "1"
    pr_head = _require("PR_HEAD_SHA")
    pr_repo = _require("PR_REPO")
    run_id = os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")

    if manual_sweep:
        # workflow_dispatch path: no PR, no diff. PR_NUMBER/PR_BASE may be
        # blank — keep them as empty strings so any downstream prompt
        # interpolation still works.
        pr_number = os.environ.get("PR_NUMBER", "") or f"manual-{run_id}"
        pr_base = os.environ.get("PR_BASE", "") or "(manual)"
        # `type: choice` already constrains the value server-side, but
        # strip whitespace + newlines defensively before splicing into
        # the agent's user prompt. The agent runs with bypassPermissions
        # and full filesystem tools, so any prompt-templated user data is
        # worth scrubbing regardless of the upstream guard.
        skills_filter = os.environ.get("MANUAL_SKILLS_FILTER", "*").strip().splitlines()[0] if os.environ.get("MANUAL_SKILLS_FILTER", "").strip() else "*"
        step_summary = os.environ.get("GITHUB_STEP_SUMMARY", "")
    else:
        pr_number = _require("PR_NUMBER")
        pr_base = _require("PR_BASE")
        skills_filter = "*"
        step_summary = ""

    if not AGENTS_MD.exists():
        print(f"FATAL: {AGENTS_MD} not found", file=sys.stderr)
        return 1

    system_prompt = AGENTS_MD.read_text()

    if manual_sweep:
        user_prompt = f"""
**Manual full-sweep run** — `workflow_dispatch` fired (no PR, no diff).

Context:
  repo                = {pr_repo}
  head SHA            = {pr_head}
  workflow run        = {run_id}
  working dir         = {REPO_ROOT}
  skills filter       = {skills_filter}   (single skill name from the dispatch dropdown, or `*` = all)
  GITHUB_STEP_SUMMARY = {step_summary or '(unset — fall back to stdout)'}

Per AGENTS.md § "Manual full-sweep mode" — overrides apply to steps 1, 3, 6:

  Step 1 (override): skip the diff entirely. Enumerate `skills/*/eval/*.json`
    on the checked-out workspace. Keep only the skill named in `skills filter`
    (the dispatch dropdown is single-select; `*` matches all). All specs on the
    chosen skill(s) run — there is no spec-level filter. Skills with no eval/
    dir are runtime libraries and are skipped as in the normal path.

  Step 3 (override): the bot-PR flow is OFF in manual mode (there's no
    contributor branch to target). If an adapter is missing or stale for a
    spec, record that spec as BLOCKED with the trigger that fired
    (missing / stale / spec drift) and a one-line reason in the results
    table — DO NOT push a branch, DO NOT create a PR. Keep processing the
    remaining (skill, spec) pairs.

  Step 6 (override): there is no PR to comment on. For each completed
    `(skill, spec)` batch, append the same markdown table you would have
    posted via `gh pr comment` to the path in `$GITHUB_STEP_SUMMARY`. Use:

      cat >> "$GITHUB_STEP_SUMMARY" <<'MD'
      ## Harbor Eval — `skills/<skill>/eval/<spec>.json`
      ... (table + failing checks + suggestions, identical to § Result comment format) ...
      MD

    Append per-spec — don't buffer everything for the end. If
    `$GITHUB_STEP_SUMMARY` is empty/unset (smoke-test locally), print the
    same markdown to stdout instead and note the fallback.

Everything else in AGENTS.md applies unchanged: startup hygiene, fleet
selection (§ 5a), per-box flock (§ 5b), canonical harbor invocation, no
trial-supervision polling, no writes under `skills/`, no instance lifecycle
calls.

When done, emit `DONE: <n>/<total> specs passed; <m> blockers` on the final
line. If the sweep couldn't proceed at all (e.g. pool exhausted before the
first trial), emit `BLOCKED: <reason>` instead.
"""
    else:
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
