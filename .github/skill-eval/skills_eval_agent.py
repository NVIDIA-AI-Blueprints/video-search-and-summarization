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
    0 - all reported specs passed, or the agent reported a valid blocker
    1 - setup error (missing env, AGENTS.md not found, sdk install failed)
    2 - agent crashed
    3 - agent hit max_turns without finishing
    4 - missing or malformed terminal protocol marker
    5 - agent completed but one or more reported specs failed
    6 - the agent's reserved work window expired before a verdict
"""
from __future__ import annotations

import asyncio
import datetime
import glob
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# .github/skill-eval/skills_eval_agent.py:
#   parents[0] = .github/skill-eval
#   parents[1] = .github
#   parents[2] = repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
AGENTS_MD = Path(__file__).resolve().parent / "AGENTS.md"
SKILL_EVAL_PYTHON_VERSION = (3, 12)
CLAUDE_AGENT_SDK_REQUIREMENT = "claude-agent-sdk==0.2.128"

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
# real ceiling (skills-eval.yml timeout-minutes: 840 is the wall-
# clock gate; this knob is just a safety valve against runaway
# loops).
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "2000"))

# Match the 840-minute workflow job window. A Bash call starts after the job,
# so the Actions watchdog always fires first; Claude must never background a
# still-running multi-step run_leg.py call before the job owns cancellation.
BASH_FOREGROUND_TIMEOUT_MS = 840 * 60 * 1000
# Reserve the last two hours of the 14-hour Actions job for benchmark assembly
# and job-level artifact upload. Within the 12-hour SDK session, reserve a final
# 30 minutes for the agent to inspect Harbor output, post the PR comment, and
# emit its terminal marker. run_leg.py consumes the earlier Harbor deadline and
# refuses to start a child unless its full timeout plus teardown grace fits.
SKILL_EVAL_WORK_BUDGET_SEC = 12 * 60 * 60
SKILL_EVAL_AGENT_VERDICT_RESERVE_SEC = 30 * 60
SKILL_EVAL_WORK_DEADLINE_ENV = "SKILL_EVAL_WORK_DEADLINE_MONOTONIC"
SKILL_EVAL_HARBOR_DEADLINE_ENV = "SKILL_EVAL_HARBOR_DEADLINE_MONOTONIC"

_PROTOCOL_FAILURE_EXIT_CODE = 4
_EVAL_FAILURE_EXIT_CODE = 5
_WORK_DEADLINE_EXIT_CODE = 6
_DONE_RESULT_RE = re.compile(
    r"^DONE:\s*(?P<passed>\d+)\s*/\s*(?P<total>\d+)\s+"
    r"spec(?:s)?\s+passed\b"
)
# Claude often wraps the mandatory DONE:/BLOCKED: line in markdown.
# Run 32225077286 put it in a fenced block (closing ``` was the last
# line). Run 32229635259 put the whole marker in inline backticks
# (`DONE: ...`). Neither form starts with DONE:/BLOCKED: until unwrapped.
_MARKDOWN_FENCE_RE = re.compile(r"^```[\w+-]*\s*$")
_INLINE_CODE_RE = re.compile(r"^`+(?P<body>.*)`+$")

# ---------------------------------------------------------------------------
# Pre-flight
# ---------------------------------------------------------------------------

def _require_supported_python() -> None:
    """Fail clearly if the workflow lost its pinned coordinator runtime."""
    actual = sys.version_info[:2]
    if actual != SKILL_EVAL_PYTHON_VERSION:
        expected = ".".join(map(str, SKILL_EVAL_PYTHON_VERSION))
        found = ".".join(map(str, actual))
        raise RuntimeError(
            f"skills eval requires Python {expected}.x; found {found}"
        )


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
             CLAUDE_AGENT_SDK_REQUIREMENT],
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
    non-CI caller get the same guarantee. The timeout matches the workflow
    job window; because the Bash call starts later, the job watchdog owns the
    final cancellation of a genuinely hung call."""
    for name in ("BASH_DEFAULT_TIMEOUT_MS", "BASH_MAX_TIMEOUT_MS"):
        try:
            configured_ms = int(os.environ.get(name, "0"))
        except ValueError:
            configured_ms = 0
        os.environ[name] = str(max(configured_ms, BASH_FOREGROUND_TIMEOUT_MS))


def _set_work_deadline() -> None:
    """Publish SDK and earlier Harbor deadlines to every Bash command."""
    sdk_deadline = time.monotonic() + SKILL_EVAL_WORK_BUDGET_SEC
    os.environ[SKILL_EVAL_WORK_DEADLINE_ENV] = str(sdk_deadline)
    os.environ[SKILL_EVAL_HARBOR_DEADLINE_ENV] = str(
        sdk_deadline - SKILL_EVAL_AGENT_VERDICT_RESERVE_SEC
    )


class WorkDeadlineExceeded(RuntimeError):
    pass


async def _run_agent_with_work_deadline() -> int:
    """Cancel the SDK session before it can consume reporting headroom."""
    deadline = float(os.environ[SKILL_EVAL_WORK_DEADLINE_ENV])
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WorkDeadlineExceeded("skill-eval work window already expired")
    try:
        async with asyncio.timeout(remaining):
            return await run_agent()
    except TimeoutError as exc:
        if time.monotonic() >= deadline:
            raise WorkDeadlineExceeded(
                "skill-eval work window expired before a terminal verdict"
            ) from exc
        raise


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


def build_user_prompt(*, eval_kind: str, eval_skill: str, eval_spec_path: str,
                      eval_platform: str, eval_slug: str, eval_spec_stem: str,
                      pr_repo: str, pr_number: str, pr_base: str, pr_head: str,
                      run_id: str, manual: bool, daily_run: bool) -> str:
    """Build the turn prompt for one leg.

    Module level and free of any SDK dependency so the prompt each branch
    actually sends can be asserted directly in a test. Nothing constructed a
    prompt in a test before, which is how a change that wired only the PR
    branch and left the nightly branch rebuilding the table by hand looked
    complete.
    """
    results_root, summary_path, report_path = leg_paths(
        eval_slug, eval_spec_stem)

    # Single-sourced so the PR leg and the nightly leg cannot drift apart:
    # both render through leg_report.py and both must land the body where
    # build_benchmark_md() globs for it.
    render_block = f"""→ render the comment with ONE command (§ Result comment format):

    python3 {REPO_ROOT}/.github/skill-eval/leg_report.py \\
      --results-root "{results_root}" --spec-path "{eval_spec_path}" \\
      --platform "{eval_platform}" --head-sha "{pr_head}" \\
      --summary-json "{summary_path}" \\
      --out "{report_path}"

  Every path above is absolute on purpose. Do NOT substitute $RES or
  $SCRATCH: shell state does not persist between Bash calls, so they are
  unset and would silently render an empty results tree.
  DO NOT read the results tree, trajectories or judge.json yourself and DO
  NOT rebuild the table by hand — the renderer owns the format, and doing it
  by hand is what used to cost ~114s of rediscovery per leg.
  If the renderer exits NON-ZERO the leg FAILED. Do not inspect raw results
  and do not use `BLOCKED`; end with
  `DONE: 0/1 specs passed; leg_report.py failed (exit N)`."""

    verdict_block = f"""After a zero renderer exit, read `{summary_path}` for the verdict. The leg
passed only if every entry in `steps[]` has `state == "recorded-pass"`;
`recorded-fail`, `no-verdict`, `not-run` and `ambiguous` all mean it did not.
A non-empty `collection_errors` also means it did not."""

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
background it)
{render_block}
→ append `{report_path}` to `$GITHUB_STEP_SUMMARY` (no PR to comment on).
Do NOT touch any other spec or skill.

{verdict_block}

End with `DONE: 1/1 specs passed` when it passed, `DONE: 0/1 specs passed;
<the failing steps and their states>` when it did not, or
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
{render_block}
→ {post_step}, posting `{report_path}` verbatim
  (`gh pr comment --body-file`).
Do NOT touch any other spec or skill.

{verdict_block}

End with `DONE: 1/1 specs passed` when it passed, `DONE: 0/1 specs passed;
<the failing steps and their states>` when it did not, or
`BLOCKED: <reason>` (e.g. stale adapter auto-committed, pool exhausted).
"""
    return user_prompt


def leg_paths(eval_slug: str, eval_spec_stem: str = "spec") -> tuple[Path, Path, Path]:
    """Return (results_root, summary_path, report_path) for one leg.

    Absolute and precomputed: the agent's Bash tool starts a fresh shell per
    call, so a command written against $RES / $SCRATCH would expand to empty
    strings and render an empty tree.

    The body is keyed by EVAL_SLUG, never the spec stem. Stems are not unique
    across skills (`search` and `standalone_deploy` are each shared by more
    than one skill, covering 5 legs of the current matrix), and every leg of a
    run shares the scratch dir on a self-hosted runner host, so a stem-keyed
    name lets one leg overwrite another leg's comment. plan_matrix.py already uniqueness-checks the slug, and the
    resulting name still matches BENCHMARK_INPUT_GLOB.
    """
    results_root = Path(f"/tmp/skill-eval/results/{eval_slug}/{_RUN_ID}")
    return (results_root,
            results_root / "leg-summary.json",
            _SCRATCH / f"pr-{eval_slug or eval_spec_stem}.md")


def missing_renderer_outputs(marker: str, results_root: Path,
                             summary_path: Path,
                             report_path: Path) -> list[Path]:
    """Renderer outputs that must exist but do not, given the agent's marker.

    leg_report.py exits 2 (no trials) or 3 (unusable spec) and can crash, and
    none of that reaches this process, which gates only on the final marker.
    Without this, `BLOCKED: leg_report.py crashed` is a GREEN check on a leg
    that really ran. Both prompts forbid rebuilding the table by hand, so
    there is no correct-but-slow fallback to fall back on either.

    A genuine pre-trial BLOCKED stays green: it has no trials and claims no
    DONE, so nothing is required of it. Returns [] when nothing is owed.
    """
    owes_output = marker.startswith("DONE:") or any(
        results_root.rglob("result.json"))
    if not owes_output:
        return []
    return [p for p in (summary_path, report_path) if not p.is_file()]


def _unwrap_protocol_line(line: str) -> str | None:
    """Return a protocol-line candidate, or None to skip blank/fence lines.

    Surrounding inline-code ticks are stripped so a last line of
    `` `DONE: 1/1 specs passed; ...` `` is still a valid marker. Leading
    space on a bare marker still fails closed.
    """
    if not line.strip():
        return None
    if _MARKDOWN_FENCE_RE.fullmatch(line.strip()):
        return None
    candidate = line.rstrip()
    match = _INLINE_CODE_RE.fullmatch(candidate.strip())
    if match is not None:
        return match.group("body").rstrip()
    return candidate


def _last_nonempty_line(text_blocks: list[str]) -> str | None:
    """Return the final printed assistant line without accepting leading space.

    Trailing markdown fences are ignored, and a last line that is only
    an inline-code span is unwrapped, so a well-formed ``DONE:`` /
    ``BLOCKED:`` marker wrapped in markdown still counts. Other trailing
    prose still fails closed.
    """
    for block in reversed(text_blocks):
        for line in reversed(block.splitlines()):
            candidate = _unwrap_protocol_line(line)
            if candidate is None:
                continue
            return candidate
    return None


def _evaluate_terminal_marker(final_text: list[str]) -> tuple[int, str]:
    """Validate the final protocol marker and return its exit code and reason.

    AGENTS.md defines ``BLOCKED:`` as a valid outcome for conditions such as
    unavailable capacity or an adapter update that needs a rerun, so it remains
    exit 0. A completed eval is successful only when its final ``DONE:`` marker
    reports a positive, complete ``N/N specs passed`` result. Syntactically valid
    partial results fail with exit 5; malformed or misplaced markers fail closed
    with the existing protocol-error exit 4.
    """
    marker = _last_nonempty_line(final_text)
    if marker is None:
        return (
            _PROTOCOL_FAILURE_EXIT_CODE,
            "no final DONE: or BLOCKED: marker",
        )

    if marker.startswith("BLOCKED:"):
        blocker_reason = marker.removeprefix("BLOCKED:").strip()
        if not blocker_reason:
            return (
                _PROTOCOL_FAILURE_EXIT_CODE,
                "malformed BLOCKED marker; a non-empty reason is required",
            )
        return 0, f"reported blocker: {blocker_reason}"

    if not marker.startswith("DONE:"):
        return (
            _PROTOCOL_FAILURE_EXIT_CODE,
            "final non-empty line does not start with DONE: or BLOCKED:",
        )

    match = _DONE_RESULT_RE.match(marker)
    if match is None:
        return (
            _PROTOCOL_FAILURE_EXIT_CODE,
            "malformed DONE marker; expected 'DONE: N/M specs passed'",
        )

    passed = int(match.group("passed"))
    total = int(match.group("total"))
    if total == 0 or passed > total:
        return (
            _PROTOCOL_FAILURE_EXIT_CODE,
            f"invalid DONE result count: {passed}/{total}",
        )
    if passed != total:
        return (
            _EVAL_FAILURE_EXIT_CODE,
            f"eval failed: only {passed}/{total} specs passed",
        )
    return 0, f"eval passed: {passed}/{total} specs passed"


def _result_message_state(message: object) -> tuple[bool, bool]:
    """Return ``(hit_max_turns, is_error)`` across SDK result schemas."""
    reasons = (
        getattr(message, "stop_reason", None),
        getattr(message, "terminal_reason", None),
        getattr(message, "subtype", None),
    )

    def normalized(value: object) -> str:
        value = getattr(value, "value", value)
        return str(value or "").strip().lower().replace("-", "_")

    hit_max_turns = any(
        normalized(reason) in {"max_turns", "error_max_turns"}
        for reason in reasons
    )
    return hit_max_turns, bool(getattr(message, "is_error", False))


async def run_agent() -> int:
    from claude_agent_sdk import AssistantMessage  # type: ignore
    from claude_agent_sdk import ClaudeAgentOptions  # type: ignore
    from claude_agent_sdk import ClaudeSDKClient  # type: ignore
    from claude_agent_sdk import HookMatcher  # type: ignore
    from claude_agent_sdk import ResultMessage  # type: ignore
    from claude_agent_sdk import TextBlock  # type: ignore
    from claude_agent_sdk import ToolUseBlock  # type: ignore

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
    # The workflow exports this alongside EVAL_SPEC_PATH; the renderer writes
    # `$SCRATCH/pr-<spec>.md` and the benchmark step globs for exactly that.
    eval_spec_stem = os.environ.get("EVAL_SPEC_STEM", "") or "spec"
    eval_slug = os.environ.get("EVAL_SLUG", "")
    manual = not pr_number

    if not AGENTS_MD.exists():
        print(f"FATAL: {AGENTS_MD} not found", file=sys.stderr)
        return 1

    system_prompt = AGENTS_MD.read_text()

    user_prompt = build_user_prompt(
        eval_kind=eval_kind, eval_skill=eval_skill,
        eval_spec_path=eval_spec_path, eval_platform=eval_platform,
        eval_slug=eval_slug, eval_spec_stem=eval_spec_stem,
        pr_repo=pr_repo, pr_number=pr_number, pr_base=pr_base,
        pr_head=pr_head, run_id=run_id, manual=manual, daily_run=daily_run)
    results_root, summary_path, report_path = leg_paths(
        eval_slug, eval_spec_stem)

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
    result_is_error = False
    saw_result_message = False

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
                saw_result_message = True
                total_cost = getattr(msg, "total_cost_usd", 0.0) or 0.0
                hit_max_turns, result_is_error = _result_message_state(msg)
                break

    print(f"[agent] finished · cost=${total_cost:.2f}", flush=True)
    if hit_max_turns:
        print("[agent] hit max_turns — agent may not have completed",
              file=sys.stderr)
        return 3
    if not saw_result_message or result_is_error:
        reason = (
            "SDK response ended without a ResultMessage"
            if not saw_result_message
            else "SDK ResultMessage reported an agent error"
        )
        print(f"[agent] {reason}", file=sys.stderr)
        return 2

    # Protocol enforcement: the agent's final non-empty line must be a
    # `DONE:` or `BLOCKED:` marker. Without this guard, an agent that
    # quits mid-flow (model decided the conversation was over without
    # reaching the comment-post step — observed on run 25256515296,
    # PR #221, where the agent burned ~25 turns polling and then
    # stopped without DONE/BLOCKED, leaving the workflow green ✓ but
    # the source PR with no result comment) would produce a silent green
    # check. A well-formed DONE marker also has to report a positive N/N
    # result; 0/N and partial N/M outcomes are completed eval failures.
    exit_code, reason = _evaluate_terminal_marker(final_text)
    print(f"[agent] {reason}", file=sys.stderr)

    # Fail closed on a renderer that never produced output. leg_report.py
    # exits 2 (no trials) or 3 (unusable spec) and can crash; none of that
    # reaches this process, which gates only on the agent's final marker. So
    # `BLOCKED: leg_report.py crashed` would otherwise be a GREEN check on a
    # leg that really ran. Both prompts now forbid rebuilding the table by
    # hand, so there is no correct-but-slow fallback to rely on either.
    # A genuine pre-trial BLOCKED stays green: no result.json, no DONE.
    if eval_kind == "eval" and exit_code == 0:
        missing = missing_renderer_outputs(
            _last_nonempty_line(final_text) or "",
            results_root, summary_path, report_path)
        if missing:
            names = ", ".join(str(p) for p in missing)
            print(f"[agent] mandatory renderer output missing: {names}",
                  file=sys.stderr)
            return _EVAL_FAILURE_EXIT_CODE
    return exit_code


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
    try:
        _require_supported_python()
    except RuntimeError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1
    # Publish the exact per-leg interpreter to the SDK's Bash children. The
    # workflow also pins PATH, but an explicit path makes the run_leg boundary
    # immune to shell hashing or profile changes inside the agent session.
    os.environ["SKILL_EVAL_PYTHON"] = sys.executable
    _disable_server_thinking()
    _set_bash_timeouts()
    _set_work_deadline()
    _ensure_sdk()
    try:
        rc = asyncio.run(_run_agent_with_work_deadline())
    except WorkDeadlineExceeded as exc:
        print(f"[agent] {exc}", file=sys.stderr)
        rc = _WORK_DEADLINE_EXIT_CODE
    except KeyboardInterrupt:
        print("[agent] interrupted", file=sys.stderr)
        rc = 2
    except Exception as exc:  # noqa: BLE001
        print(f"[agent] crashed: {exc!r}", file=sys.stderr)
        traceback.print_exc()
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
