#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Skills eval agent — single-shot CI-driven runner.

Spawns one `claude-agent-sdk` agent with `.github/skill-eval/AGENTS.md`
as its system prompt and lets it drive an eval end-to-end:
adapter/dataset → Brev lock → harbor run → results comment. Two modes:

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
    MANUAL_FULL_SWEEP     "1" on workflow_dispatch: full-sweep mode (see above).
    MANUAL_SKILLS_FILTER  Skill name from the dispatch input, or "*" for all.
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
# turns (startup + brev wait + `uvx harbor run` exec + reading results +
# migrating to _viewer), so a full-PR fan-out of 10-15 trials plus
# recon/retry overhead exceeds the previous 300 ceiling. The 600 cap
# that replaced it was still tight when the agent hit a novel
# situation it had to discover (e.g. gpu_count selection rejecting
# the default candidate, or harbor flag semantics from a fresh runner
# without prior context) — each "discovery" burst is 5-10 turns of
# Read/Grep/Bash spelunking on top of the steady-state per-trial
# cost. Bumping to 2000 absorbs that overhead without lifting the
# real ceiling (skills-eval.yml timeout-minutes: 480 is the wall-
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
    """Raise the Bash tool's timeout cap above the worst-case single
    `uvx harbor run` so the runtime never auto-backgrounds the foreground
    trial call.

    Claude Code moves a foreground Bash command to a background task once it
    crosses the Bash *max* timeout (default 600000 ms = 10 min), then
    surfaces it as pollable task output. That silently defeats AGENTS.md's
    "block on harbor — no polling" contract for any trial longer than 10 min
    (most real deploys: env-build 1800s + agent 3600s + verify 1800s ≈ 2h).
    Past the cap the foreground call is backgrounded and the agent falls into
    polling its task .output files. The
    `_block_bash_background` hook can't prevent it: the runtime sets
    run_in_background *after* the timeout, not in the call input the hook
    inspects. Raising the cap is the only structural fix. The CI workflow
    exports these too; set them here defensively so local smoke-tests and any
    non-CI caller get the same guarantee. Both stay under the workflow's
    timeout-minutes so a genuinely hung call is still reaped by the job."""
    os.environ.setdefault("BASH_DEFAULT_TIMEOUT_MS", "7200000")   # 2h
    os.environ.setdefault("BASH_MAX_TIMEOUT_MS", "10800000")      # 3h


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

    manual_sweep = os.environ.get("MANUAL_FULL_SWEEP") == "1"
    generated = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC")

    title = ("Skills Eval Benchmark — Manual full-sweep" if manual_sweep
             else "Skills Eval Benchmark")

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

    AGENTS.md § "No polling — block on harbor" requires `uvx harbor run`
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
                    "Backgrounding forbidden — run harbor synchronously "
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
        # Single-spec mode (push path): the `plan` job already resolved the
        # diff into one matrix leg, so this run evaluates exactly one
        # (skill, spec) — no diff, no looping. EVAL_KIND distinguishes a
        # normal eval leg from a missing-adapter leg (which only commits the
        # adapter, no trial). The legacy whole-PR-diff loop is gone; the matrix owns
        # fan-out now.
        pr_number = _require("PR_NUMBER")
        pr_base = _require("PR_BASE")
        eval_kind = os.environ.get("EVAL_KIND", "eval")
        eval_skill = _require("EVAL_SKILL")
        eval_spec_path = os.environ.get("EVAL_SPEC_PATH", "")
        eval_platform = os.environ.get("EVAL_PLATFORM", "")

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

  Step 1 (override): skip the diff entirely. Enumerate `skills/*/evals/*.json`
    on the checked-out workspace. Keep only the skill named in `skills filter`
    (the dispatch dropdown is single-select; `*` matches all). All specs on the
    chosen skill(s) run — there is no spec-level filter. Skills with no eval/
    dir are runtime libraries and are skipped as in the normal path.

  Step 3 (override): the adapter auto-commit flow is OFF in manual mode
    (there's no contributor branch to target). If an adapter is missing or
    stale for a spec, record that spec as BLOCKED with the trigger that fired
    (missing / stale / spec drift) and a one-line reason in the results
    table — DO NOT push anything. Keep processing the
    remaining (skill, spec) pairs.

  Step 6 (override): there is no PR to comment on. For each completed
    `(skill, spec)` batch, append the same markdown table you would have
    posted via `gh pr comment` to the path in `$GITHUB_STEP_SUMMARY`. Use:

      cat >> "$GITHUB_STEP_SUMMARY" <<'MD'
      ## Harbor Eval — `skills/<skill>/evals/<spec>.json`
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
    elif eval_kind == "missing_adapter":
        user_prompt = f"""
PR #{pr_number}: skill `{eval_skill}` ships eval specs but has NO adapter at
`.github/skill-eval/adapters/{eval_skill}/generate.py`. The `plan` job
collapsed every spec on this skill into this one leg so the adapter is
committed exactly once.

Context:
  repo         = {pr_repo}
  PR number    = {pr_number}
  base branch  = {pr_base}
  mirror head  = {pr_head}
  workflow run = {run_id}
  working dir  = {REPO_ROOT}

Per AGENTS.md § "Single-spec mode" (missing-adapter case) + § 3c: generate
the adapter and COMMIT it directly to the source PR's `headRefName` (NOT the
mirror) so the eval re-runs against it on the next sync. Do NOT run any trial
in this leg (the re-run evaluates the committed adapter), and do NOT post a
results comment. For an external-fork PR (the bot can't push to a fork),
comment that the contributor must add the adapter and BLOCK instead.

End with `BLOCKED: missing adapter for {eval_skill} auto-committed (<sha>)`
once pushed, `BLOCKED: fork PR — adapter must be added by the contributor`
for a fork, or `BLOCKED: <reason>` if you could not commit.
"""
    else:
        user_prompt = f"""
PR #{pr_number}: evaluate exactly ONE spec on ONE platform —
`{eval_spec_path}` (skill `{eval_skill}`, platform `{eval_platform or "see spec"}`).

Context:
  repo         = {pr_repo}
  PR number    = {pr_number}
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
(missing/stale → commit it to the PR branch per § 3c, then exit BLOCKED;
the eval re-runs on sync — never run a locally-patched adapter in this leg)
→ generate the dataset → acquire a per-box flock
on a `vss-eval-*` member matching `{eval_platform or "the spec's platform"}` →
run harbor synchronously for this platform (§ Harbor invocation; never
background it) → gather results →
post ONE PR comment for this spec (§ Result comment format). Do NOT touch
any other spec or skill.

End with `DONE: <reward summary>` after posting the comment, or
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
