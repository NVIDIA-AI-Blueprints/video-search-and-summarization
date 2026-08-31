# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for the deterministic leg-result renderer.

Most of these guard defects found by replaying the renderer over 1,177 real
harvested legs and by adversarial review. All of them were silent: none
raised, and several produced a confidently wrong comment.

The module renders and does not judge, so the tests assert what a reviewer
would SEE, not a pass/fail the module has no authority to decide.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "leg_report", Path(__file__).resolve().parents[1] / "leg_report.py")
leg_report = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(leg_report)


def _trial(tmp: Path, run: str, name: str, *, reward, start, finish,
           passed=None, total=None, query="", tokens=True, exception=None,
           checks=None) -> Path:
    d = tmp / run / name
    (d / "verifier").mkdir(parents=True, exist_ok=True)
    (d / "config.json").write_text("{}")
    payload = {
        "trial_name": name,
        "task_name": f"nvidia-vss/{name}",
        "started_at": start,
        "finished_at": finish,
        "verifier_result": {"rewards": {"reward": reward}} if reward is not None else {},
        "agent_result": ({"n_input_tokens": 1200, "n_cache_tokens": 1000} if tokens else {}),
    }
    if exception:
        payload["exception_info"] = {"exception_type": exception}
    (d / "result.json").write_text(json.dumps(payload))
    if passed is not None:
        judge = {"passed": passed, "total": total, "query": query}
        if checks is not None:
            judge["checks"] = checks
        (d / "verifier" / "judge.json").write_text(json.dumps(judge))
    return d


def _rows(body: str, platform: str = "L40S") -> list[str]:
    return [ln for ln in body.splitlines() if ln.startswith(f"| {platform} |")]


def _render(tmp: Path, declared=None, platform="L40S") -> str:
    leg = leg_report.collect_leg(tmp)
    return leg_report.render_comment(leg, spec_path="s.json", platform=platform,
                                     head_sha="0862faf3", declared=declared)


# --- structure --------------------------------------------------------------

def test_run_level_result_json_is_not_mistaken_for_a_trial(tmp_path: Path) -> None:
    # REGRESSION: the run-level result.json sits beside a config.json exactly
    # like a trial does, so filtering on config.json duplicated every row.
    (tmp_path / "r").mkdir(parents=True)
    (tmp_path / "r" / "config.json").write_text("{}")
    (tmp_path / "r" / "result.json").write_text(json.dumps(
        {"id": "abc", "n_total_trials": 2, "started_at": "2026-08-20T08:00:00Z"}))
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=5, total=5,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:10:00Z")

    leg = leg_report.collect_leg(tmp_path)
    assert [t["trial_name"] for t in leg["trials"]] == ["step-1__aaa"]


def test_naive_and_aware_timestamps_can_coexist(tmp_path: Path) -> None:
    # REGRESSION: mixing them made min()/max() raise, killing 1,174 of 1,177.
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:10:00Z")
    _trial(tmp_path, "r", "step-2__bbb", reward=1.0,
           start="2026-08-20T08:10:00", finish="2026-08-20T08:20:00")
    body = _render(tmp_path)
    assert len(_rows(body)) == 2


def test_steps_render_once_and_in_order(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-2__bbb", reward=0.75, passed=3, total=4,
           start="2026-08-20T08:20:00Z", finish="2026-08-20T08:24:00Z")
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=5, total=5,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:16:00Z")
    rows = _rows(_render(tmp_path))
    assert len(rows) == 2
    assert "step-1" in rows[0] and "step-2" in rows[1]
    assert leg_report.MARK_PASS in rows[0]
    assert leg_report.MARK_FAIL in rows[1] and "(3/4)" in rows[1]


# --- the false-red the earlier draft introduced ------------------------------

def test_an_unnumbered_single_step_trial_is_logical_step_one(tmp_path: Path) -> None:
    # REGRESSION: single-step trials are named after the platform, not
    # `step-N`. Treating logical step 1 as absent flipped 316 real passing
    # legs to a "not run" row.
    _trial(tmp_path, "r", "rtxpro6000bw__aaa", reward=1.0, passed=4, total=4,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    body = _render(tmp_path, declared=["Deploy the base profile"])
    rows = _rows(body)
    assert len(rows) == 1
    assert "not run" not in body
    assert leg_report.MARK_PASS in rows[0]


def test_a_step_the_spec_declares_but_never_ran_is_shown_as_skipped(tmp_path: Path) -> None:
    """Sequential dispatch stops at the first failure, so name the cause.

    step-1 scored 0.2, so steps 2 and 3 did not run because of it. Reporting
    a bare "not run" would hide why, and this is the wording these comments
    have always used.
    """
    _trial(tmp_path, "r", "step-1__aaa", reward=0.2, passed=1, total=5,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    body = _render(tmp_path, declared=["deploy", "ingest", "query"])
    rows = _rows(body)
    assert len(rows) == 3
    assert body.count("skipped (prior-step fail, step-1 reward=0.2)") == 2
    assert "ingest" in rows[1]          # the declared query still shows


def test_the_declared_step_list_comes_from_the_spec(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"expects": [{"query": "deploy it"},
                                            {"query": "ask a question"}]}))
    assert leg_report.spec_steps(spec) == ["deploy it", "ask a question"]
    assert leg_report.spec_steps(None) == []          # none named: fine
    try:                                              # named but absent: error
        leg_report.spec_steps(tmp_path / "missing.json")
    except leg_report.SpecError:
        pass
    else:
        raise AssertionError("a named-but-missing spec must raise")


# --- fail-closed rendering ---------------------------------------------------

def test_a_timed_out_trial_is_never_green_even_at_reward_one(tmp_path: Path) -> None:
    # REGRESSION, real data: two corpus trials carry AgentTimeoutError with
    # reward 1.0. AGENTS.md says a timeout is a failure regardless of reward.
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=16, total=16,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z",
           exception="AgentTimeoutError")
    body = _render(tmp_path)
    assert leg_report.MARK_PASS not in body
    assert "AgentTimeoutError" in body          # the detail must survive
    assert "(16/16)" in body
    assert "reward 1" in body


def test_a_failing_row_always_carries_its_evidence(tmp_path: Path) -> None:
    # REGRESSION: 483 replayed failures rendered as a bare red row with the
    # failing-check count or exception type dropped.
    _trial(tmp_path, "r", "step-1__aaa", reward=0.25, passed=1, total=4,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    row = _rows(_render(tmp_path))[0]
    assert leg_report.MARK_FAIL in row and "(1/4)" in row and "0.25" in row


def test_a_reward_that_contradicts_the_judge_is_not_a_pass(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=0, total=5,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    row = _rows(_render(tmp_path))[0]
    assert leg_report.MARK_PASS not in row
    assert "disagree" in row


def test_malformed_rewards_are_not_passes(tmp_path: Path) -> None:
    # True is an int in Python; inf and 1.000001 both survive `>= 1.0`.
    for bad in (True, float("inf"), float("nan"), 1.000001, "1.0"):
        d = _trial(tmp_path / str(id(bad)), "r", "step-1__aaa", reward=0.0,
                   start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
        payload = json.loads((d / "result.json").read_text())
        payload["verifier_result"] = {"rewards": {"reward": bad}}
        (d / "result.json").write_text(json.dumps(payload))
        body = _render(tmp_path / str(id(bad)))
        assert leg_report.MARK_PASS not in body, f"{bad!r} rendered as a pass"


# --- retries -----------------------------------------------------------------

def test_a_retry_supersedes_the_earlier_attempt(tmp_path: Path) -> None:
    # REGRESSION, real data: 15 of 1,177 legs have several attempts per step.
    _trial(tmp_path, "2026-08-20__08-00-00", "step-1__first", reward=0.5,
           passed=3, total=6, start="2026-08-20T08:00:00Z",
           finish="2026-08-20T08:05:00Z")
    _trial(tmp_path, "2026-08-20__09-00-00", "step-1__retry", reward=1.0,
           passed=6, total=6, start="2026-08-20T09:00:00Z",
           finish="2026-08-20T09:05:00Z")
    leg = leg_report.collect_leg(tmp_path)
    assert [t["trial_name"] for t in leg["trials"]] == ["step-1__retry"]
    assert leg["trials"][0]["attempts"] == 2
    body = leg_report.render_comment(leg, spec_path="s.json", platform="L40S",
                                     head_sha="0862faf3")
    assert "Retried" in body and "×2" in body


def test_an_undated_retry_is_not_hidden_by_an_older_pass(tmp_path: Path) -> None:
    # A later failed retry with no start timestamp must not be overwritten by
    # an older timestamped pass, so undated attempts sort LAST.
    _trial(tmp_path, "r1", "step-1__old", reward=1.0, passed=4, total=4,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    d = _trial(tmp_path, "r2", "step-1__undated", reward=0.0, passed=0, total=4,
               start=None, finish=None)
    payload = json.loads((d / "result.json").read_text())
    payload.pop("started_at", None)
    payload.pop("finished_at", None)
    (d / "result.json").write_text(json.dumps(payload))

    leg = leg_report.collect_leg(tmp_path)
    assert [t["trial_name"] for t in leg["trials"]] == ["step-1__undated"]
    body = leg_report.render_comment(leg, spec_path="s.json", platform="L40S",
                                     head_sha="0862faf3")
    assert leg_report.MARK_PASS not in body
    assert "No start timestamp recorded" in body


def test_a_task_base_containing_a_double_underscore_does_not_collapse(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "some__task__aaa", reward=1.0,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    _trial(tmp_path, "r", "other__task__bbb", reward=1.0,
           start="2026-08-20T08:06:00Z", finish="2026-08-20T08:10:00Z")
    leg = leg_report.collect_leg(tmp_path)
    assert len(leg["trials"]) == 2, [t["trial_name"] for t in leg["trials"]]


def test_an_unreadable_result_is_surfaced_not_dropped(tmp_path: Path) -> None:
    # A corrupt NEWER retry beside a valid older pass would otherwise leave
    # the leg looking clean.
    _trial(tmp_path, "r1", "step-1__ok", reward=1.0, passed=4, total=4,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    bad = tmp_path / "r2" / "step-1__corrupt"
    bad.mkdir(parents=True)
    (bad / "result.json").write_text("{not json")

    leg = leg_report.collect_leg(tmp_path)
    assert leg["unreadable"], "a corrupt result.json must be reported"
    body = leg_report.render_comment(leg, spec_path="s.json", platform="L40S",
                                     head_sha="0862faf3")
    assert "Unreadable result files" in body


# --- misc --------------------------------------------------------------------

def test_prompt_tokens_exclude_the_cached_portion(tmp_path: Path) -> None:
    d = _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
               start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    payload = json.loads((d / "result.json").read_text())
    payload["agent_result"] = {"n_input_tokens": 1_216_083,
                               "n_cache_tokens": 1_145_395}
    (d / "result.json").write_text(json.dumps(payload))
    leg = leg_report.collect_leg(tmp_path)
    assert leg["trials"][0]["prompt_tok"] == 1_216_083 - 1_145_395
    assert leg["trials"][0]["cached_tok"] == 1_145_395


def test_missing_metrics_degrade_and_never_raise(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z",
           tokens=False)
    assert leg_report.MISSING in _render(tmp_path)


def test_trace_urls_are_matched_by_step_and_by_trial_name(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    (tmp_path / leg_report.TRACE_URLS_NAME).write_text(
        "step-1\tstep-1__aaa\thttps://harbor.example/jobs/x\n")
    assert "[trace](https://harbor.example/jobs/x)" in _render(tmp_path)


def test_a_pipe_in_a_query_cannot_break_the_table(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=1, total=1,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    body = _render(tmp_path, declared=["run `docker ps | grep vss-agent`"])
    row = next(ln for ln in body.splitlines() if ln.startswith("| L40S |"))
    assert "\\|" in row
    assert row.replace("\\|", "").count("|") == 11      # 10 columns + trailing


def test_no_trials_renders_nothing_and_exits_two(tmp_path: Path) -> None:
    leg = leg_report.collect_leg(tmp_path)
    assert leg["trials"] == []
    assert leg_report.main(["--results-root", str(tmp_path)]) == 2


def test_the_module_emits_no_verdict(tmp_path: Path, capsys) -> None:
    # The caller owns pass/fail. A failed leg still renders and still exits 0,
    # because the exit code describes rendering, and the module says so
    # rather than implying a verdict it has no authority to give.
    _trial(tmp_path, "r", "step-1__aaa", reward=0.0, passed=0, total=5,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    assert leg_report.main(["--results-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "DONE:" not in out
    assert "specs passed" not in out
    assert not hasattr(leg_report, "terminal_line")
    assert not hasattr(leg_report, "leg_passed")


# --- round-4 fixes ----------------------------------------------------------

def test_the_query_column_shows_what_harbor_evaluated(tmp_path: Path) -> None:
    # REGRESSION: 46 of 50 specs carry placeholders like {{platform}}. The
    # spec holds the template; judge.json holds the substituted text Harbor
    # actually judged. Showing the template told the operator the wrong thing.
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=3, total=3,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z",
           query="Deploy the VSS alerts profile on L40S in verification mode")
    body = _render(tmp_path, declared=["Deploy the VSS alerts profile on {{platform}}"])
    row = next(ln for ln in body.splitlines() if ln.startswith("| L40S |"))
    assert "{{platform}}" not in row
    assert "on L40S" in row


def test_a_not_run_step_shows_the_declaration_and_says_so(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=3, total=3,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z", query="ran")
    body = _render(tmp_path, declared=["ran", "Watch {{sensor}} for PPE"])
    rows = _rows(body)
    assert "*(declared)*" in rows[1]     # never mistaken for executed text
    assert "not run" in rows[1]


def test_an_unreadable_judge_is_not_a_pass(tmp_path: Path) -> None:
    # A truncated judge.json beside reward 1.0 used to render green because
    # unreadable was treated exactly like absent.
    d = _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
               start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    (d / "verifier").mkdir(exist_ok=True)
    (d / "verifier" / "judge.json").write_text('{"passed": 3, "tot')
    leg = leg_report.collect_leg(tmp_path)
    assert leg["trials"][0]["judge_state"] == "unreadable"
    assert leg_report.row_state(leg["trials"][0]) == "ambiguous"
    body = leg_report.render_comment(leg, spec_path="s.json", platform="L40S",
                                     head_sha="0862faf3")
    assert leg_report.MARK_PASS not in body
    assert "unreadable" in body


def test_absent_judge_with_a_perfect_reward_is_still_a_pass(tmp_path: Path) -> None:
    # Not every trial writes a judge.json; reward is the authority there.
    # Failing these closed would invent false reds.
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    leg = leg_report.collect_leg(tmp_path)
    assert leg["trials"][0]["judge_state"] == "absent"
    assert leg_report.row_state(leg["trials"][0]) == "recorded-pass"


def test_unusable_check_counts_are_ambiguous_not_green(tmp_path: Path) -> None:
    for judge in ({"passed": 1, "total": 0},          # total==0 short-circuited
                  {"passed": "0", "total": "5"},      # string counts ignored
                  {"passed": True, "total": True}):   # bools are ints
        root = tmp_path / str(abs(hash(str(judge))))
        d = _trial(root, "r", "step-1__aaa", reward=1.0,
                   start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
        (d / "verifier").mkdir(exist_ok=True)
        (d / "verifier" / "judge.json").write_text(json.dumps(judge))
        leg = leg_report.collect_leg(root)
        assert leg_report.row_state(leg["trials"][0]) == "ambiguous", judge
        body = leg_report.render_comment(leg, spec_path="s.json", platform="L40S",
                                         head_sha="0862faf3")
        assert leg_report.MARK_PASS not in body, judge


def test_a_named_spec_that_cannot_be_used_is_an_error(tmp_path: Path) -> None:
    # Returning [] here made a partial chain render complete and exit 0.
    missing = tmp_path / "nope.json"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    noexp = tmp_path / "noexp.json"
    noexp.write_text('{"skills": []}')
    for p in (missing, bad, noexp):
        try:
            leg_report.spec_steps(p)
        except leg_report.SpecError:
            continue
        raise AssertionError(f"{p.name} should have raised SpecError")
    assert leg_report.spec_steps(None) == []      # no spec named at all

    _trial(tmp_path, "r", "step-1__aaa", reward=1.0,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z")
    assert leg_report.main(["--results-root", str(tmp_path),
                            "--spec-file", str(bad)]) == 3


def test_multiple_attempts_with_a_missing_timestamp_are_ambiguous(tmp_path: Path) -> None:
    # Undated-sorts-last is a convention, not a chronology: an older undated
    # pass would otherwise beat a newer dated failure.
    _trial(tmp_path, "r1", "step-1__dated", reward=0.0, passed=0, total=4,
           start="2026-08-20T09:00:00Z", finish="2026-08-20T09:05:00Z")
    d = _trial(tmp_path, "r2", "step-1__undated", reward=1.0, passed=4, total=4,
               start=None, finish=None)
    payload = json.loads((d / "result.json").read_text())
    payload.pop("started_at", None)
    payload.pop("finished_at", None)
    (d / "result.json").write_text(json.dumps(payload))

    leg = leg_report.collect_leg(tmp_path)
    assert leg["trials"][0]["attempts"] == 2
    assert leg_report.row_state(leg["trials"][0]) == "ambiguous"
    body = leg_report.render_comment(leg, spec_path="s.json", platform="L40S",
                                     head_sha="0862faf3")
    assert leg_report.MARK_PASS not in body
    assert "no timestamp" in body


def test_the_summary_gives_a_caller_everything_without_markdown(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=3, total=3,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z", query="ran it")
    leg = leg_report.collect_leg(tmp_path)
    summary = leg_report.leg_summary(leg, declared=["ran it", "never ran"],
                                     spec_path="s.json", platform="L40S")
    assert summary["schema"] == leg_report.SUMMARY_SCHEMA
    assert summary["declared_steps"] == 2
    assert [s["state"] for s in summary["steps"]] == ["recorded-pass", "not-run"]
    first = summary["steps"][0]
    assert first["reward"] == 1.0 and first["judge"] == "valid"
    assert first["passed"] == 3 and first["total"] == 3
    assert first["attempts"] == 1 and first["executed_query"] == "ran it"
    assert summary["collection_errors"] == []
    # a caller can decide the verdict from this alone
    assert all(s["state"] in {"recorded-pass", "recorded-fail", "no-verdict",
                              "not-run", "ambiguous"} for s in summary["steps"])


# --- the posted format must not drift from what readers already know -------

def test_whole_rewards_keep_one_decimal() -> None:
    """Real comments and the documented example post `1.0`, never `1`.

    `.3g` alone drops the trailing zero, which silently changes a format
    every reviewer of this repo already reads.
    """
    assert leg_report._fmt_reward(1.0) == "1.0"
    assert leg_report._fmt_reward(0.0) == "0.0"
    # fractions keep their real precision
    assert leg_report._fmt_reward(0.2) == "0.2"
    assert leg_report._fmt_reward(0.75) == "0.75"
    assert leg_report._fmt_reward(0.833) == "0.833"


def test_a_skipped_step_names_the_step_that_blamed_it() -> None:
    """Sequential dispatch stops at the first failure; say which one."""
    by_step = {1: {"reward": 1.0}, 2: {"reward": 0.2}, 3: None}
    assert leg_report._skip_cell(3, by_step) == (
        "⏭️ skipped (prior-step fail, step-2 reward=0.2)"
    )


def test_a_skipped_step_after_an_error_says_so() -> None:
    by_step = {1: {"reward": None, "exception": "AgentTimeoutError"}}
    assert "prior-step error, step-1" in leg_report._skip_cell(2, by_step)


def test_a_gap_with_no_earlier_failure_is_not_blamed_on_anyone() -> None:
    """Never invent a cause: with nothing failing earlier, say `not run`."""
    assert leg_report._skip_cell(2, {1: {"reward": 1.0}}).endswith("not run")


def test_a_failing_step_lists_which_checks_failed(tmp_path: Path) -> None:
    """31 of 33 failing comments in the posted history carry this section.

    Without it a reader learns the reward was 0.5 but not which assertion
    broke, which is the only actionable part of a failure report.
    """
    _trial(tmp_path, "r", "step-1__aaa", reward=0.5, passed=1, total=3,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z",
           checks=[{"pass": True, "check": "it deploys"},
                   {"pass": False, "check": "it answers", "rationale": "got 500"},
                   {"pass": False, "check": "it persists"}])
    body = _render(tmp_path, declared=["deploy"])
    assert "### Failing checks (step-1)" in body
    assert '- **Check 2**: "it answers" — got 500' in body
    assert '- **Check 3**: "it persists"' in body
    assert "it deploys" not in body.split("### Failing checks")[1]  # passing omitted


def test_no_failing_checks_section_when_everything_passed(tmp_path: Path) -> None:
    _trial(tmp_path, "r", "step-1__aaa", reward=1.0, passed=2, total=2,
           start="2026-08-20T08:00:00Z", finish="2026-08-20T08:05:00Z",
           checks=[{"pass": True, "check": "a"}, {"pass": True, "check": "b"}])
    assert "### Failing checks" not in _render(tmp_path, declared=["deploy"])
