#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skills eval agent — single-shot CI-driven runner.

Spawns one `claude-agent-sdk` agent with `.github/skill-eval/AGENTS.md`
as its system prompt and lets it drive an eval end-to-end:
adapter/dataset → Brev box selection → run_leg.py → results comment. Two modes:

  - Single-spec (push): the `plan` job in skills-eval.yml resolves the PR
    diff into a matrix of one leg per (spec, platform); each leg invokes
    this script with EVAL_* set and evaluates exactly that one trial.
  - Manual full-sweep (workflow_dispatch): no diff; enumerate every spec
    on the picked skill(s) and write tables to $GITHUB_STEP_SUMMARY.

The agent gets Bash/Read/Edit/Write/Glob/Grep, and is explicitly told (in
AGENTS.md) it must NOT modify anything under `skills/`. Background/task
tools are disabled (see ClaudeAgentOptions below) so it drives harbor
synchronously.

Env (set by the workflow step):
    PR_NUMBER             PR being evaluated, e.g. "100" (blank on workflow_dispatch)
    PR_BASE               Base branch, e.g. "develop" (blank on workflow_dispatch)
    PR_HEAD_SHA           Mirror or main-branch head SHA (full)
    PR_REPO               "owner/repo"
    GITHUB_RUN_ID         CI run id (lock + results dir scoping)
    GITHUB_STEP_SUMMARY   Markdown file appended to the Actions run summary;
                          manual-sweep writes per-spec tables here.
    EVAL_KIND             Single-spec mode: "eval" or "missing_adapter".
    EVAL_SKILL            Single-spec mode: the skill dir name.
    EVAL_SPEC_PATH        Single-spec mode: skills/<skill>/evals/<spec>.json.
    EVAL_SPEC_STEM        Single-spec mode: the spec filename without .json.
    EVAL_PLATFORM         Single-spec mode: the one platform this leg runs.
    MANUAL_SKILLS_FILTER  Skill name from the dispatch input, or "*" for all —
                          consumed by plan_matrix.py to build the manual-sweep
                          matrix; manual legs run as single-spec with an empty
                          PR_NUMBER (results go to the job summary).
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
import datetime
import glob
import os
import re
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
# turns (startup + brev wait + `run_leg.py` exec + reading results +
# migrating to _viewer), so a full-PR fan-out of 10-15 trials plus
# recon/retry overhead exceeds the previous 300 ceiling. The 600 cap
# that replaced it was still tight when the agent hit a novel
# situation it had to discover (e.g. gpu_count selection rejecting
# the default candidate, or harbor flag semantics from a fresh runner
# without prior context) — each "discovery" burst is 5-10 turns of
# Read/Grep/Bash spelunking on top of the steady-state per-trial
# cost. Bumping to 2000 absorbs that overhead without lifting the
# real ceiling (skills-eval.yml timeout-minutes: 360 is the wall-
# clock gate; this knob is just a safety valve against runaway
# loops).
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "2000"))

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


def _set_bash_timeouts() -> None:
    """Raise the Bash tool's timeout cap above the worst-case `run_leg.py`
    foreground call.

    Claude Code moves a foreground Bash command to a background task once it
    crosses the Bash *max* timeout (default 600000 ms = 10 min), then
    surfaces it as pollable task output. That silently defeats AGENTS.md's
    "block on run_leg.py / Harbor -- no polling" contract. A full leg can
    include lock contention plus multiple ordered Harbor subprocesses, so the
    foreground cap must cover the workflow job window, not just one Harbor
    attempt. Past the cap the foreground call is backgrounded and the agent
    falls into polling its task .output files. The
    `_block_bash_background` hook can't prevent it: the runtime sets
    run_in_background *after* the timeout, not in the call input the hook
    inspects. Raising the cap is the only structural fix. The CI workflow
    exports these too; set them here defensively so local smoke-tests and any
    non-CI caller get the same guarantee. Both stay under the workflow's
    timeout-minutes so a genuinely hung call is still reaped by the job."""
    os.environ.setdefault("BASH_DEFAULT_TIMEOUT_MS", "21600000")  # 6h
    os.environ.setdefault("BASH_MAX_TIMEOUT_MS", "21600000")      # 6h


# ---------------------------------------------------------------------------
# Benchmark report
# ---------------------------------------------------------------------------

# Per-run scratch root. AGENTS.md § "Startup hygiene" mandates that
# every piece of state this run owns lives under $SCRATCH so that
# parallel workflow_dispatch sweeps don't trample each other's
# in-flight files. The agent writes per-spec result comments to
# `$SCRATCH/pr-<spec>.md` before posting via `gh pr comment` (per
# § "Result comment format"); we read them back from the same place
# rather than re-fetching from the PR — that path also works in
# manual-sweep mode, where there's no PR to read.
_RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
_SCRATCH = Path(f"/tmp/skill-eval/{_RUN_ID}")
BENCHMARK_INPUT_GLOB = str(_SCRATCH / "pr-*.md")
BENCHMARK_OUT_PATH = _SCRATCH / "benchmark.md"

_MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\([^)\n]*\)")
_BARE_URL_RE = re.compile(r"https?://\S+")


def _sanitize_public(text: str) -> str:
    """Scrub a per-spec result body for public consumption.

    The benchmark.md is published as a workflow artifact downloadable
    by anyone with read access to the Actions run, so we strip:
      - internal tool names ("Harbor" → "Skill") — Harbor is an
        internal-only product name and shouldn't appear in published
        artifacts.
      - markdown links `[text](url)` → keep `text`, drop `url`. Trace
        URLs point at internal viewer endpoints; PR/run links leak
        org-internal routing that's already evident from the artifact's
        provenance.
      - bare http(s) URLs anywhere in prose.
    """
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _BARE_URL_RE.sub("", text)
    text = re.sub(r"\bHarbor\b", "Skill", text)
    return text


def build_benchmark_md(out_path: Path = BENCHMARK_OUT_PATH) -> Path | None:
    """Concatenate per-spec result comments into one benchmark report.

    Reads every `$SCRATCH/pr-*.md` the agent produced (one per (PR,
    spec) batch per AGENTS.md § "Result comment format") and writes a
    single `benchmark.md` with a run-level header followed by each spec
    body in deterministic order. Output is sanitized for public
    consumption via `_sanitize_public` — see that docstring for what's
    stripped. The glob is run-scoped so a parallel workflow_dispatch
    peer's per-spec comments never leak into this run's benchmark.

    Returns the output path on success, or `None` if no per-spec
    comments were found — that's a valid outcome (blocker before any
    trial ran) and shouldn't fail the workflow.
    """
    sources = sorted(glob.glob(BENCHMARK_INPUT_GLOB))
    if not sources:
        print(f"[benchmark] no per-spec comments at {BENCHMARK_INPUT_GLOB} — "
              "skipping benchmark.md (agent likely blocked before running trials)",
              flush=True)
        return None

    generated = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")

    title = "Skills Eval Benchmark"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as fp:
        fp.write(f"# {title}\n\n")
        fp.write(f"Generated: {generated}  \n")
        fp.write(f"Specs: {len(sources)}\n\n")
        fp.write("---\n\n")
        for src in sources:
            try:
                body = Path(src).read_text()
            except OSError as exc:
                print(f"[benchmark] skip {src}: {exc!r}", flush=True)
                continue
            # Demote any top-level `# heading` inside the per-spec body
            # to `##` so the benchmark TOC stays single-rooted at the
            # `# ` title above. AGENTS.md § Result comment format starts
            # spec bodies with `## ...` so this is usually a no-op, but
            # be defensive against future format drift.
            body = "\n".join(
                ("#" + line) if line.startswith("# ") else line
                for line in body.splitlines()
            )
            fp.write(_sanitize_public(body).rstrip() + "\n\n---\n\n")

    print(f"[benchmark] wrote {out_path} ({len(sources)} spec comments)",
          flush=True)
    return out_path


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

async def _block_bash_background(input_data, tool_use_id, context):
    """PreToolUse hook: deny any Bash call that backgrounds work.

    AGENTS.md § "No polling — block on harbor" requires `run_leg.py`
    to be invoked synchronously so the orchestrating agent blocks on
    stdout instead of polling an output file. Enforcing that in prose
    alone is fragile — a drifting agent can still set
    `run_in_background=True` or append `&`/`nohup`/`disown` to the
    command. This hook makes the rule structural at the SDK boundary.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {}) or {}
    if tool_name != "Bash":
        return {}
    if tool_input.get("run_in_background"):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Backgrounding forbidden — run run_leg.py synchronously "
                    "(AGENTS.md § No polling — block on harbor)."
                ),
            }
        }
    cmd = (tool_input.get("command") or "").strip()
    if cmd.endswith("&") or " nohup " in cmd or cmd.startswith("nohup ") or " disown" in cmd:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "No shell-level backgrounding (`&` / `nohup` / `disown`). "
                    "Run the command synchronously and block on it."
                ),
            }
        }
    return {}


async def run_agent() -> int:
    from claude_agent_sdk import (  # type: ignore
        AssistantMessage, ClaudeAgentOptions, ClaudeSDKClient,
        HookMatcher, ResultMessage, TextBlock, ToolUseBlock,
    )

    daily_run = os.environ.get("DAILY_RUN") == "true"
    pr_head = _require("PR_HEAD_SHA")
    pr_repo = _require("PR_REPO")
    run_id = os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")

    # Single-spec mode (push AND manual sweep): the `plan` job resolved a diff
    # (push) or the picked skill's specs (workflow_dispatch) into one matrix
    # leg, so this run evaluates exactly one (skill, spec, platform) — no diff,
    # no looping. EVAL_KIND distinguishes a normal eval leg from a
    # missing-adapter leg (which only commits the adapter). PR_NUMBER is empty
    # on a manual sweep — the leg then writes its result to its job summary
    # ($GITHUB_STEP_SUMMARY) instead of a PR comment, and cannot auto-commit an
    # adapter (no contributor branch). The legacy single-agent sweep is gone;
    # the matrix owns fan-out for both push and manual now.
    pr_number = os.environ.get("PR_NUMBER", "")   # empty ⇒ manual sweep
    pr_base = os.environ.get("PR_BASE", "")
    eval_kind = os.environ.get("EVAL_KIND", "eval")
    eval_skill = _require("EVAL_SKILL")
    eval_spec_path = os.environ.get("EVAL_SPEC_PATH", "")
    eval_platform = os.environ.get("EVAL_PLATFORM", "")
    manual = not pr_number

    if not AGENTS_MD.exists():
        print(f"FATAL: {AGENTS_MD} not found", file=sys.stderr)
        return 1

    system_prompt = AGENTS_MD.read_text()

    if eval_kind == "missing_adapter":
        target = f"PR #{pr_number}" if pr_number else "Manual sweep"
        user_prompt = f"""
{target}: skill `{eval_skill}` ships eval specs but has NO adapter at
`.github/skill-eval/adapters/{eval_skill}/generate.py`. The `plan` job
collapsed every spec on this skill into this one leg so the adapter is
committed exactly once.

Context:
  repo         = {pr_repo}
  PR number    = {pr_number or "(manual sweep — no PR)"}
  base branch  = {pr_base}
  mirror head  = {pr_head}
  workflow run = {run_id}
  working dir  = {REPO_ROOT}

Per AGENTS.md § "Single-spec mode" (missing-adapter case) + § 3c: generate
the adapter and COMMIT it directly to the source PR's `headRefName` (NOT the
mirror) so the eval re-runs against it on the next sync. Do NOT run any trial
in this leg (the re-run evaluates the committed adapter), and do NOT post a
results comment. For an external-fork PR (the bot can't push to a fork),
comment that the contributor must add the adapter and BLOCK instead. If this
is a manual sweep (`PR number` above is blank) there is no branch to commit
to — record the missing adapter in `$GITHUB_STEP_SUMMARY` and BLOCK.

End with `BLOCKED: missing adapter for {eval_skill} auto-committed (<sha>)`
once pushed, `BLOCKED: fork PR — adapter must be added by the contributor`
for a fork, `BLOCKED: missing adapter for {eval_skill} (manual sweep)` for a
manual run, or `BLOCKED: <reason>` if you could not commit.
"""
    elif daily_run:
        user_prompt = f"""
Develop: evaluate exactly ONE spec on ONE platform —
`{eval_spec_path}` (skill `{eval_skill}`, platform `{eval_platform or "see spec"}`).

Context:
  repo         = {pr_repo}
  base branch  = develop
  mirror head  = {pr_head}
  workflow run = {run_id}
  working dir  = {REPO_ROOT}
  spec         = {eval_spec_path}
  platform     = {eval_platform or "(read from spec)"}
  leg slug     = {os.environ.get("EVAL_SLUG", "")}   (scratch scope; see § Per-leg scratch isolation)

Per AGENTS.md § "Single-spec mode": SKIP step 1's diff — the `plan` job
already selected this (spec, platform). Run steps 2–7 for it only:
ensure its adapter exists under `.github/skill-eval/adapters/{eval_skill}/`
(missing/stale → just skip this spec)
→ generate the dataset → acquire a per-box flock
on a `vss-eval-*` member matching `{eval_platform or "the spec's platform"}` →
run harbor synchronously for this platform (§ Harbor invocation; never
background it) → gather results →
append the result table to `$GITHUB_STEP_SUMMARY` (no PR to comment on). Do NOT touch
any other spec or skill.

End with `DONE: <reward summary>` after posting the comment, or
`BLOCKED: <reason>` (e.g. stale adapter auto-committed, pool exhausted).
"""
    else:
        target = f"PR #{pr_number}" if pr_number else "Manual sweep"
        post_step = (
            "append the result table to `$GITHUB_STEP_SUMMARY` (no PR to comment on)"
            if manual else "post ONE PR comment for this spec"
        )
        user_prompt = f"""
{target}: evaluate exactly ONE spec on ONE platform —
`{eval_spec_path}` (skill `{eval_skill}`, platform `{eval_platform or "see spec"}`).

Context:
  repo         = {pr_repo}
  PR number    = {pr_number or "(manual sweep — no PR)"}
  base branch  = {pr_base}
  mirror head  = {pr_head}
  workflow run = {run_id}
  working dir  = {REPO_ROOT}
  spec         = {eval_spec_path}
  platform     = {eval_platform or "(read from spec)"}
  leg slug     = {os.environ.get("EVAL_SLUG", "")}   (scratch scope; see § Per-leg scratch isolation)

Per AGENTS.md § "Single-spec mode": SKIP step 1's diff — the `plan` job
already selected this (spec, platform). Run steps 2–7 for it only:
ensure/refresh its adapter under `.github/skill-eval/adapters/{eval_skill}/`
(missing/stale → handle per § 3c, then exit BLOCKED — never run a
locally-patched adapter in this leg) → generate the dataset → select a
`vss-eval-*` member matching `{eval_platform or "the spec's platform"}` →
run `.github/skill-eval/run_leg.py` for this platform (§ Harbor invocation;
never background it; the wrapper holds the per-box lock while Harbor runs)
→ gather results →
{post_step} (§ Result comment format). Do NOT touch any other spec or skill.

End with `DONE: <reward summary>` after posting the result, or
`BLOCKED: <reason>` (e.g. stale adapter auto-committed, pool exhausted).
"""

    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-6"
    print(f"[agent] starting · pr={pr_number} base={pr_base} head={pr_head[:8]} "
          f"model={model} max_turns={MAX_TURNS}", flush=True)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Bash", "Read", "Edit", "Write", "Glob", "Grep"],
        # `allowed_tools` is an allowlist for primary tool calls, but the
        # SDK's background-shell and task-tracking affordances pass through
        # it because they're treated as runtime/harness features. List them
        # here explicitly so the agent can't create background tasks or
        # read backgrounded-shell output, which is how the polling
        # anti-pattern reaches into the trial wall-clock.
        disallowed_tools=[
            "BashOutput", "KillShell",
            "TaskCreate", "TaskUpdate", "TaskGet",
            "TaskList", "TaskOutput", "TaskStop",
        ],
        # Closes the `Bash(run_in_background=True)` / shell-`&` loophole that
        # `disallowed_tools` alone can't catch — see _block_bash_background.
        hooks={
            "PreToolUse": [
                HookMatcher(matcher="Bash", hooks=[_block_bash_background]),
            ],
        },
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
# Main
# ---------------------------------------------------------------------------
#
# No process-side cleanup here by design — each trial deploys whatever
# VSS profile it needs as part of its own first agent turn (the harness
# no longer pre-deploys or maintains an active-deploy marker). A
# previous-run leftover container on the box is the next trial's deploy-
# step problem, not the harness's, and tools like
# `docker compose down` invoked by the agent reconcile cleanly. That
# makes every exit path equivalent from the next run's perspective —
# happy path, max-turns, cancel-in-progress SIGTERM, agent crash,
# SIGKILL, host reboot — so we don't need atexit / signal handlers / a
# touched-boxes ledger to chase the cases where end-of-run cleanup
# might be skipped.

def main() -> int:
    _disable_server_thinking()
    _set_bash_timeouts()
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
    # Always try to assemble benchmark.md — even on crash/max-turns, any
    # specs the agent did finish have their per-spec markdown on disk and
    # are worth publishing. Errors here are non-fatal: the agent's verdict
    # (rc) is what gates the workflow, not the report builder.
    try:
        build_benchmark_md()
    except Exception as exc:  # noqa: BLE001
        print(f"[benchmark] failed to build benchmark.md: {exc!r}",
              file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
