#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Render a leg's result comment from its Harbor output, deterministically.

`skills_eval_agent.py` currently asks an LLM to do this: read the results
tree, extract per-trial metrics, and render the table in AGENTS.md
§ "Result comment format". Measured across real job logs, that costs p50
114 s over a median of 11 read-only tool calls, and it reruns from scratch
every leg because the agent has no memory of the schema it parsed last
time. One sampled coordinator rediscovered the trajectory layout three
times before it could fill in a single column.

Nothing in that step is a judgement, so none of it needs a model.

THIS MODULE DOES NOT DECIDE WHETHER THE LEG PASSED. It renders what Harbor
recorded and nothing more. An earlier draft also emitted a verdict and a
pass/fail exit code, and that one responsibility produced every serious
defect found in review: it turned two real `AgentTimeoutError` trials green,
it counted steps while calling them specs, and its step-accounting flipped
316 real single-step legs falsely red. The caller holds `run_leg.py`'s exit
status and the spec, so the caller owns the verdict. Removing that
responsibility here removes the whole class of bug and keeps 94% of the
saving, because the cost is in the reading, not in the deciding.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import json
import math
import os
import re
import sys
from pathlib import Path

TRACE_URLS_NAME = "trace-urls.tsv"
PHASE_TIMINGS_NAME = "phase-timings.json"
MACHINE_NAME = "machine.txt"
MISSING = "—"

# Rendered, never returned as a verdict.
MARK_PASS = "✅"
MARK_FAIL = "❌"
MARK_SKIP = "⏭️"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _ts(value: str | None) -> _dt.datetime | None:
    """Parse a Harbor timestamp, always returning an aware UTC datetime.

    Harbor writes most timestamps with a trailing `Z`, but not all of them:
    mixing the two makes `min()`/`max()` raise "can't compare offset-naive
    and offset-aware datetimes", which took out 1,174 of 1,177 real legs
    when this module first ran over the corpus.
    """
    if not value:
        return None
    with contextlib.suppress(Exception):
        parsed = _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return parsed
    return None


def _load_json(path: Path) -> dict | list | None:
    with contextlib.suppress(Exception):
        return json.loads(path.read_text())
    return None


def _step_index(trial_name: str) -> int | None:
    """`step-3__aBcD` -> 3. Single-step trials are named after the platform."""
    match = re.match(r"step-(\d+)__", trial_name or "")
    return int(match.group(1)) if match else None


def _trial_key(trial_name: str) -> str:
    """Identity of a step across retries.

    `rsplit` rather than `split`: a task base that itself contains `__`
    would otherwise collapse into a different step's group.
    """
    return (trial_name or "").rsplit("__", 1)[0] or trial_name


def _failing_checks_section(trials: list[dict], multi_step: bool) -> list[str]:
    """The named checks that failed, for the first step that failed.

    31 of the 33 failing comments in the posted history carry this section,
    and it is the only place a reader learns WHICH assertion failed rather
    than just that the reward was 0.5. Sequential dispatch stops at the first
    failure, so there is exactly one section, which matches the history.
    """
    for trial in trials:
        checks = trial.get("checks")
        if not isinstance(checks, list):
            continue
        failing = [(i, c) for i, c in enumerate(checks, 1)
                   if isinstance(c, dict) and c.get("pass") is False]
        if not failing:
            continue
        head = "### Failing checks"
        if multi_step and trial.get("step"):
            head += f" (step-{trial['step']})"
        out = ["", head, ""]
        for index, check in failing:
            text = str(check.get("check") or "").strip()
            why = str(check.get("rationale") or "").strip()
            item = f'- **Check {index}**: "{text}"'
            if why:
                item += f" \u2014 {why}"
            out.append(item)
        return out
    return []


def _fmt_reward(value: float) -> str:
    """Match the reward formatting the harness has always posted.

    Real comments and the documented example use `1.0`, not `1`; `.3g` alone
    drops the trailing zero and silently changes the format readers know.
    """
    text = f"{value:.3g}"
    return f"{text}.0" if "." not in text and "e" not in text else text


def _skip_cell(step: int, by_step: dict) -> str:
    """Why a declared step has no trial.

    § 5 dispatches steps sequentially and stops at the first failure, so a
    missing step is usually a skip with a cause, not an unexplained gap. Name
    the blaming step and its reward, which is the form these comments have
    always used. Falls back to a bare "not run" when nothing earlier failed.
    """
    for prior in range(step - 1, 0, -1):
        t = by_step.get(prior)
        if t is None:
            continue
        reward = t.get("reward")
        if reward is not None and float(reward) < 1.0:
            return (f"{MARK_SKIP} skipped (prior-step fail, "
                    f"step-{prior} reward={_fmt_reward(float(reward))})")
        if t.get("exception"):
            return f"{MARK_SKIP} skipped (prior-step error, step-{prior})"
    return f"{MARK_SKIP} not run"


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return MISSING
    seconds = round(seconds)
    if seconds >= 3600:
        return f"{seconds // 3600}hr {seconds % 3600 // 60}min"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _fmt_tokens(count: int | None) -> str:
    if not count:
        return MISSING
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}k"
    return str(count)


def _clean_reward(value: object) -> float | None:
    """A reward is a finite real number, or it is not a reward.

    `True` is an int in Python and would otherwise compare equal to 1.0, and
    `inf`/`nan` would slip through a naive `>= 1.0`. Anything else becomes
    None and renders as unknown rather than as a pass.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _trial_metrics(trial_dir: Path) -> dict:
    """Agent turn count for one trial, from the trajectory when it is there.

    The trajectory is excluded from the uploaded artifact but present on the
    box. Token counts come from `agent_result` in the caller: Harbor's
    FinalMetrics forbids extra fields, so the trajectory carries no per-model
    usage to prefer over it.
    """
    out: dict = {"turns": None}
    traj = _load_json(trial_dir / "agent" / "trajectory.json")
    if isinstance(traj, dict):
        steps = traj.get("steps")
        if isinstance(steps, list):
            turns = sum(1 for s in steps
                        if isinstance(s, dict) and s.get("source") == "agent")
            out["turns"] = turns or None
    return out


class SpecError(Exception):
    """The spec was named but could not be used."""


def spec_steps(spec_path: str | Path | None) -> list[str]:
    """Queries the spec declares, in order — the authoritative step list.

    Raises SpecError when a spec is NAMED but unusable. Returning [] there
    silently collapsed a partial chain into a complete-looking comment: six
    harvested legs would have rendered every observed row green with the
    later declared steps simply absent, and the CLI still exited 0. A
    caller path or CWD mistake must fail loudly.

    Returns [] only when no spec was named at all.
    """
    if not spec_path:
        return []
    path = Path(spec_path)
    if not path.is_file():
        raise SpecError(f"spec not found: {path}")
    data = _load_json(path)
    if not isinstance(data, dict):
        raise SpecError(f"spec is not a JSON object: {path}")
    expects = data.get("expects")
    if not isinstance(expects, list):
        raise SpecError(f"spec has no `expects` list: {path}")
    out = []
    for entry in expects:
        query = (entry or {}).get("query") if isinstance(entry, dict) else None
        out.append(str(query) if isinstance(query, str) else "")
    return out


def collect_leg(results_root: Path) -> dict:
    """Everything the comment needs, read once from the results tree."""
    results_root = Path(results_root)
    trials: list[dict] = []
    unreadable: list[str] = []
    for result_path in sorted(results_root.rglob("result.json")):
        trial_dir = result_path.parent
        data = _load_json(result_path)
        if data is None:
            # Surfaced, never dropped: a corrupt NEWER retry beside a valid
            # older pass would otherwise leave the leg looking clean.
            unreadable.append(str(result_path.relative_to(results_root)))
            continue
        if not isinstance(data, dict):
            continue
        # Harbor writes a run-level result.json beside the per-trial ones. It
        # carries `n_total_trials` and no `trial_name`, and sits next to a
        # config.json exactly like a trial does, so config.json cannot tell
        # them apart. Keying on `trial_name` can. Getting this wrong
        # duplicated every step in the rendered table.
        trial_name = data.get("trial_name")
        if not trial_name:
            continue
        started, finished = _ts(data.get("started_at")), _ts(data.get("finished_at"))
        rewards = ((data.get("verifier_result") or {}).get("rewards")) or {}
        judge_path = trial_dir / "verifier" / "judge.json"
        if not judge_path.exists():
            judge, judge_state = {}, "absent"
        else:
            loaded = _load_json(judge_path)
            if isinstance(loaded, dict):
                judge, judge_state = loaded, "valid"
            else:
                # Present but unreadable. Treated as "absent" this would let a
                # truncated judge sitting beside reward 1.0 render green.
                judge, judge_state = {}, "unreadable"
        agent_result = data.get("agent_result") or {}
        metrics = _trial_metrics(trial_dir)
        metrics["cached_tok"] = agent_result.get("n_cache_tokens")
        # `n_input_tokens` is TOTAL input and already contains the cached
        # tokens — fleet-wide 97.4% of it is cache reads. Reporting it in a
        # "Prompt tok" column beside "Cached tok" overstated the uncached
        # prompt by orders of magnitude.
        total_in = agent_result.get("n_input_tokens")
        cached = agent_result.get("n_cache_tokens") or 0
        metrics["prompt_tok"] = (max(total_in - cached, 0)
                                 if isinstance(total_in, int) else None)
        exc = data.get("exception_info")
        trials.append({
            "trial_name": trial_name,
            "key": _trial_key(trial_name),
            "step": _step_index(trial_name),
            "task_name": data.get("task_name") or "",
            "started": started,
            "finished": finished,
            "seconds": (finished - started).total_seconds() if started and finished else None,
            "reward": _clean_reward(rewards.get("reward")),
            "passed": (judge or {}).get("passed"),
            "checks": (judge or {}).get("checks"),
            "total": (judge or {}).get("total"),
            "judge_state": judge_state,
            "query": (judge or {}).get("query") or "",
            "exception": bool(exc),
            "exception_type": (exc.get("exception_type") if isinstance(exc, dict) else None),
            "cost_usd": agent_result.get("cost_usd"),
            "path": str(result_path.relative_to(results_root)),
            **metrics,
        })

    # A retry writes a NEW <timestamp>/step-N__xxx under the same results
    # root, so one step can have several attempts: 15 of 1,177 real legs do.
    # Report the latest attempt per step. Keeping them all both duplicated
    # rows and let a failed first attempt outvote a successful retry.
    # A trial with no timestamp sorts LAST, not first, so a later untimed
    # failure is never silently overwritten by an older timestamped pass.
    far_future = _dt.datetime.max.replace(tzinfo=_dt.timezone.utc)
    ordered = sorted(
        trials,
        key=lambda t: (t["step"] if t["step"] is not None else 0,
                       t["started"] or far_future, t["path"]),
    )
    latest: dict[str, dict] = {}
    for trial in ordered:
        previous = latest.get(trial["key"])
        trial["attempts"] = (previous or {}).get("attempts", 0) + 1
        trial["undated"] = trial["started"] is None
        # Sorting undated attempts last is a convention, not a chronology:
        # an older undated pass would still beat a newer dated failure. When
        # a step has several attempts and any of them lacks a timestamp we
        # cannot know which ran last, so say so instead of guessing.
        prior_undated = bool((previous or {}).get("any_undated"))
        trial["any_undated"] = prior_undated or trial["undated"]
        if trial["attempts"] > 1 and trial["any_undated"]:
            trial["ambiguous"] = (
                f"{trial['attempts']} attempts, at least one with no timestamp"
            )
        latest[trial["key"]] = trial
    trials = sorted(
        latest.values(),
        key=lambda t: (t["step"] if t["step"] is not None else 0,
                       t["started"] or far_future),
    )

    traces: dict[str, str] = {}
    trace_file = results_root / TRACE_URLS_NAME
    if trace_file.exists():
        with contextlib.suppress(Exception):
            for line in trace_file.read_text().splitlines():
                parts = line.split("\t")
                if len(parts) >= 3:
                    traces[parts[0].strip()] = parts[2].strip()
                    traces[parts[1].strip()] = parts[2].strip()

    machine = ""
    machine_file = results_root / MACHINE_NAME
    if machine_file.exists():
        with contextlib.suppress(Exception):
            machine = machine_file.read_text().split("\t")[0].strip()

    phases = _load_json(results_root / PHASE_TIMINGS_NAME) or {}
    starts = [t["started"] for t in trials if t["started"]]
    ends = [t["finished"] for t in trials if t["finished"]]
    return {
        "trials": trials,
        "unreadable": unreadable,
        "traces": traces,
        "machine": machine,
        "phases": phases if isinstance(phases, dict) else {},
        "first_started": min(starts) if starts else None,
        "last_finished": max(ends) if ends else None,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def row_state(trial: dict) -> str:
    """Machine-readable state for one executed step, fail-closed.

    `recorded-pass` requires ALL of: no exception, a finite non-boolean
    reward exactly 1.0, and judge evidence that either is absent entirely or
    is well-formed and unanimous. Anything else is a failure or an explicit
    ambiguity — never a pass by omission.
    """
    if trial.get("ambiguous"):
        return "ambiguous"
    if trial.get("exception"):
        return "recorded-fail"
    reward = trial.get("reward")
    if reward is None:
        return "no-verdict"
    if reward != 1.0:
        return "recorded-fail"

    state = trial.get("judge_state")
    if state == "unreadable":
        # Something is there and we cannot read it. Reward alone is not
        # enough to call this a pass.
        return "ambiguous"
    if state == "valid":
        passed, total = trial.get("passed"), trial.get("total")
        ints = isinstance(passed, int) and not isinstance(passed, bool) \
            and isinstance(total, int) and not isinstance(total, bool)
        if not ints:
            # e.g. string counts "0"/"5". An earlier version ignored these
            # and rendered green.
            return "ambiguous"
        if total <= 0:
            # `total == 0` short-circuited the old `and total` guard, so
            # {'passed': 1, 'total': 0} rendered green.
            return "ambiguous"
        if passed != total:
            return "recorded-fail"
    return "recorded-pass"


def _verdict_cell(trial: dict) -> str:
    """The Result cell. Fail-closed, and never bare.

    A failed row must carry its evidence: 483 replayed failures rendered as
    a bare red row with the failing-check count or the exception type
    dropped, which tells a reviewer nothing about what went wrong.
    """
    reward, passed, total = trial["reward"], trial.get("passed"), trial.get("total")
    detail = ""
    if isinstance(passed, (int, str)) and isinstance(total, (int, str)) \
            and not isinstance(passed, bool) and str(total) not in ("", "0"):
        detail = f" ({passed}/{total})"
    got = f" reward {_fmt_reward(reward)}" if isinstance(reward, float) else ""
    state = row_state(trial)

    if state == "recorded-pass":
        return f"{MARK_PASS} {_fmt_reward(reward)}{detail}"
    if state == "no-verdict":
        return f"{MARK_SKIP} no verdict recorded"
    if state == "ambiguous":
        why = {
            "unreadable": "judge.json unreadable",
        }.get(trial.get("judge_state"), "")
        if trial.get("ambiguous"):
            why = trial["ambiguous"]
        elif not why:
            why = "check counts unusable"
        return f"{MARK_FAIL} ambiguous ({why}){got}"
    if trial.get("exception"):
        kind = trial.get("exception_type") or "error"
        return f"{MARK_FAIL} {kind}{detail}{',' if got else ''}{got}"
    if reward == 1.0:
        # A perfect reward with a non-unanimous judge is confusing on its own
        # ("1 (0/5)"), so name the contradiction rather than leave the reader
        # to spot it.
        return f"{MARK_FAIL} {_fmt_reward(reward)}{detail} (reward/checks disagree)"
    return f"{MARK_FAIL} {_fmt_reward(reward)}{detail}"


def _trace_link(trial: dict, traces: dict) -> str:
    url = traces.get(trial["trial_name"])
    if not url and trial.get("step"):
        url = traces.get(f"step-{trial['step']}")
    return f"[trace]({url})" if url else MISSING


def _fill_declared(text: str, platform: str) -> str:
    """Substitute what we authoritatively know in a declared query.

    Adapters substitute placeholders before execution, so a spec template is
    not what Harbor judged. `judge.json.query` is preferred everywhere, but
    8% of trials record no judge query and every not-run row has none, so the
    template is the only text available there. `{{platform}}` is 51 of the
    105 placeholders in the spec corpus and its value is known here, so fill
    it rather than showing a reader raw mustache.
    """
    return text.replace("{{platform}}", platform) if platform else text


def _short(text: str, limit: int = 60) -> str:
    text = (text or "").replace("|", "\\|").replace("\n", " ").strip()
    return (text[: limit - 3] + "...") if len(text) > limit else (text or MISSING)


def render_comment(leg: dict, *, spec_path: str, platform: str, head_sha: str,
                   spec_sha: str = "", declared: list[str] | None = None) -> str:
    """Render the leg comment. `declared` is the spec's ordered query list."""
    trials, traces = leg["trials"], leg["traces"]
    declared = declared or []
    numbered = [t for t in trials if t["step"]]
    multi = bool(numbered) or len(declared) > 1

    lines = [f"## Harbor Eval — `{spec_path}`", ""]
    meta = [f"Head: `{head_sha[:8]}`", f"platform `{platform}`"]
    if spec_sha:
        meta.append(f"spec `{spec_sha[:8]}`")
    if leg["machine"]:
        meta.append(f"box `{leg['machine']}`")
    lines.append(" · ".join(meta))

    first, last = leg["first_started"], leg["last_finished"]
    total_s = leg.get("phases", {}).get("total_s")
    if total_s is None and first and last:
        total_s = (last - first).total_seconds()
    lines.append(
        f"First started: `{first.strftime('%Y-%m-%d %H:%M:%SZ') if first else MISSING}` · "
        f"Last finished: `{last.strftime('%Y-%m-%d %H:%M:%SZ') if last else MISSING}` · "
        f"Total: `{_fmt_duration(total_s)}`"
    )
    lines.append("")

    def row(cells: list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    if multi:
        lines.append(row(["Platform", "Step", "Query", "Result", "Reward",
                          "Duration", "Turns", "Prompt tok", "Cached tok", "Trace"]))
        lines.append("|" + "---|" * 10)
        # An unnumbered single trial IS logical step 1 when the spec declares
        # steps. Treating it as absent flipped 316 real legs falsely red.
        by_step = {}
        for t in trials:
            by_step[t["step"] or 1] = t
        span = range(1, max(len(declared), max(by_step, default=1)) + 1)
        for n in span:
            t = by_step.get(n)
            # `judge.json.query` is what Harbor actually evaluated; the spec
            # holds an unsubstituted template (46 of 50 specs contain
            # placeholders like {{platform}}). Show the executed text for a
            # step that ran, and fall back to the declaration only for a step
            # that never ran, labelled so it cannot be mistaken for executed.
            executed = (t or {}).get("query") or ""
            template = declared[n - 1] if n - 1 < len(declared) else ""
            if executed:
                query = _short(executed)
            elif template:
                query = _short(_fill_declared(template, platform)) + " *(declared)*"
            else:
                query = MISSING
            if t is None:
                lines.append(row([platform, f"step-{n}", query,
                                  _skip_cell(n, by_step)] + [MISSING] * 6))
                continue
            lines.append(row([
                platform, f"step-{n}", query, _verdict_cell(t),
                _fmt_reward(t["reward"]) if t["reward"] is not None else MISSING,
                _fmt_duration(t["seconds"]), str(t["turns"] or MISSING),
                _fmt_tokens(t["prompt_tok"]), _fmt_tokens(t["cached_tok"]),
                _trace_link(t, traces)]))
    else:
        lines.append(row(["Platform", "Result", "Reward", "Duration", "Turns",
                          "Prompt tok", "Cached tok", "Trace"]))
        lines.append("|" + "---|" * 8)
        for t in trials:
            lines.append(row([
                platform, _verdict_cell(t),
                _fmt_reward(t["reward"]) if t["reward"] is not None else MISSING,
                _fmt_duration(t["seconds"]), str(t["turns"] or MISSING),
                _fmt_tokens(t["prompt_tok"]), _fmt_tokens(t["cached_tok"]),
                _trace_link(t, traces)]))

    retried = [t for t in trials if t.get("attempts", 1) > 1]
    if retried:
        lines.append("")
        lines.append("Retried (latest attempt shown): "
                     + ", ".join(f"`{t['trial_name']}` ×{t['attempts']}" for t in retried))
    lines.extend(_failing_checks_section(
        trials, multi_step=any(t.get("step") for t in trials)))
    undated = [t for t in trials if t.get("undated")]
    if undated:
        lines.append("")
        lines.append("No start timestamp recorded for: "
                     + ", ".join(f"`{t['trial_name']}`" for t in undated))
    if leg["unreadable"]:
        lines.append("")
        lines.append("Unreadable result files (NOT reflected above): "
                     + ", ".join(f"`{p}`" for p in leg["unreadable"]))
    lines.append("")
    return "\n".join(lines)


SUMMARY_SCHEMA = 1


def leg_summary(leg: dict, *, declared: list[str], spec_path: str = "",
                platform: str = "") -> dict:
    """Versioned facts for a deterministic caller.

    The module still emits no overall verdict, but a caller must not have to
    parse Markdown to build one. Everything needed is here: per-step state,
    reward validity, judge presence and counts, the attempt actually shown,
    and any collection-integrity error.
    """
    steps = []
    by_step = {}
    for t in leg["trials"]:
        by_step[t["step"] or 1] = t
    span = range(1, max(len(declared), max(by_step, default=1)) + 1)
    for n in span:
        t = by_step.get(n)
        if t is None:
            steps.append({"step": n, "state": "not-run",
                          "declared_query": declared[n - 1] if n - 1 < len(declared) else None})
            continue
        steps.append({
            "step": n,
            "state": row_state(t),
            "trial_name": t["trial_name"],
            "reward": t["reward"],
            "reward_valid": t["reward"] is not None,
            "judge": t.get("judge_state"),
            "passed": t.get("passed"),
            "total": t.get("total"),
            "exception": t.get("exception_type") if t.get("exception") else None,
            "attempts": t.get("attempts", 1),
            "attempt_path": t.get("path"),
            "any_attempt_undated": bool(t.get("any_undated")),
            "seconds": t.get("seconds"),
            "executed_query": t.get("query") or None,
        })
    return {
        "schema": SUMMARY_SCHEMA,
        "spec_path": spec_path,
        "platform": platform,
        "machine": leg.get("machine") or None,
        "declared_steps": len(declared),
        "steps": steps,
        "collection_errors": list(leg.get("unreadable") or []),
    }


def main(argv: list[str] | None = None) -> int:
    """Render the comment to stdout or a file.

    NO VERDICT IS EMITTED and the exit code is about rendering only:
      0  a comment was produced
      2  nothing to render (no trials under the results root)
      3  a spec was named but could not be used

    The caller decides whether the leg passed. It holds `run_leg.py`'s exit
    status and the spec; this module holds neither, and an earlier draft that
    tried to decide anyway got it wrong in both directions.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-root", required=True, type=Path)
    ap.add_argument("--spec-path", default=os.environ.get("EVAL_SPEC_PATH", ""))
    ap.add_argument("--spec-file", type=Path, default=None,
                    help="Spec JSON to read the declared step list from "
                         "(defaults to --spec-path if it exists on disk)")
    ap.add_argument("--platform", default=os.environ.get("EVAL_PLATFORM", ""))
    ap.add_argument("--head-sha", default=os.environ.get("PR_HEAD_SHA", ""))
    ap.add_argument("--spec-sha", default="")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--summary-json", type=Path, default=None,
                    help="Write the machine-readable summary a caller should "
                         "use instead of parsing the rendered Markdown")
    args = ap.parse_args(argv)

    leg = collect_leg(args.results_root)
    if not leg["trials"]:
        print("no trials under results root", file=sys.stderr)
        return 2
    spec_file = args.spec_file
    if spec_file is None and args.spec_path and Path(args.spec_path).is_file():
        spec_file = Path(args.spec_path)
    try:
        declared = spec_steps(spec_file)
    except SpecError as exc:
        # Rendering without the spec would drop its declared steps and make a
        # partial chain look complete, so this is a hard error.
        print(f"spec error: {exc}", file=sys.stderr)
        return 3
    body = render_comment(leg, spec_path=args.spec_path, platform=args.platform,
                          head_sha=args.head_sha, spec_sha=args.spec_sha,
                          declared=declared)
    if args.summary_json:
        args.summary_json.parent.mkdir(parents=True, exist_ok=True)
        args.summary_json.write_text(json.dumps(
            leg_summary(leg, declared=declared, spec_path=args.spec_path,
                        platform=args.platform), indent=2, default=str))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body)
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
