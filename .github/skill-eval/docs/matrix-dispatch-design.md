<!--
SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
-->

# Skills-eval matrix dispatch — design

Status: **proposed** (pre-implementation). Supersedes the single-job
`skills-eval.yml` that runs one long agent session looping over every
changed spec.

## Why

Today one agent session, on one runner, sweeps **all** changed specs
serially inside a single `uvx harbor run` loop. Two problems:

1. **No real parallelism.** A full-PR sweep (~25 specs) runs back-to-back
   on one box even though the `vss-eval-*` pool has several boxes. The
   agent's only route to parallelism was to background `harbor` and poll
   — the exact anti-pattern that produced the exit-code-4 "protocol
   failure" (one agent burned its turn budget on `sleep && tail` before a
   cold-box trial finished, then exited with no `DONE:`/`BLOCKED:`).
2. **One slow/failed spec blocks the rest.** Serial dispatch means a spec
   that hits the 1 h agent ceiling delays every spec behind it; a crash
   mid-loop can drop later specs entirely.

## Core idea

Move fan-out from *inside the agent* to *GitHub Actions `strategy.matrix`*.
Each matrix leg is **one runner driving one foreground agent that
evaluates exactly one spec**, synchronously. The agent is unchanged in
substance — same adapter autogeneration, harbor invocation, result
gathering, comment posting, and analysis — it is simply **scoped to a
single `(skill, spec)`** instead of looping over all of them.

Parallelism now comes from the matrix + the brev pool: N legs run
concurrently and contend for hardware-matching boxes via a `run_leg.py`
wrapper-held per-box `flock`. No leg ever backgrounds work; the SDK-side hardening
(`disallowed_tools` for `Task*`/`BashOutput`/`KillShell` + a PreToolUse
hook rejecting `run_in_background`/`&`/`nohup`) guarantees it.

```
push to pull-request/<N>
        │
        ▼
┌──────────────────────────────────────────────────────────────┐
│ plan  (lightweight runner, no GPU, no brev)                    │
│  • diff vs PR base                                             │
│  • map each changed path → target (skill, spec) set (rules ↓)  │
│  • cheap missing-adapter check → collapse those skills         │
│  • emit matrix JSON + has_targets flag                         │
└───────────────┬──────────────────────────────────────────────┘
                │ needs.plan.outputs.matrix
                ▼
┌──────────────────────────────────────────────────────────────┐
│ eval  (strategy.matrix, one leg per (spec, platform))          │
│  runs-on: [self-hosted, vss-skill-eval-runner]                 │
│  fail-fast: false   max-parallel: <≈ box count>                │
│                                                                │
│  each leg:  EVAL_SKILL / EVAL_SPEC set                         │
│    → foreground agent, single-spec mode (AGENTS.md override)   │
│    → ensure adapter+dataset → pick a matching box              │
│    → run_leg.py (flock + synchronous Harbor, this spec only)   │
│    → gather results → POST ITS OWN per-spec comment            │
│    → DONE:/BLOCKED:                                            │
│    → upload this leg's results artifact                        │
└──────────────────────────────────────────────────────────────┘
```

There is **no separate report/aggregation job** — each leg posts its own
comment per `§ Result comment format`, exactly as the single agent does
today. (Trade-off accepted: N comments per push, in exchange for the
per-leg agent path staying identical and fully independent.)

## Dispatch rules (the `plan` job)

Given the cumulative diff `base...pull-request/<N>`:

| Changed path | Matrix effect |
|---|---|
| `skills/<skill>/evals/<spec>.json` (or legacy `eval/`) | dispatch **just** `(skill, spec)` |
| any other file under `skills/<skill>/` (SKILL.md, references, skill-card…) | dispatch **all** specs under `<skill>` |
| `.github/skill-eval/adapters/<skill>/**` | dispatch **all** specs under `<skill>` |
| `.github/skill-eval/{envs,verifiers}/**`, `skills_eval_agent.py`, `AGENTS.md`, `skills-eval.yml`, `plan_matrix.py` | **skip eval entirely** (harness-only — validate via manual dispatch) |

After collecting the target `(skill, spec)` set:

- **De-dupe.** A spec reached by both "spec changed" and "skill changed"
  appears once.
- **Missing-adapter collapse.** For each unique skill in the target set,
  check `.github/skill-eval/adapters/<skill>/generate.py` exists (cheap
  file stat, no execution). If it's **missing**, drop that skill's spec
  legs and emit a single `kind: missing_adapter` leg for the skill. That
  one leg's agent generates + commits the adapter directly to the
  contributor's PR branch (§ 3c) — so N specs of an adapterless skill
  don't race to commit it N times.
- **Stale-but-present adapters** are still detected **per-leg** by the
  agent (staleness can only be known by running the adapter), which then
  regenerates + commits the adapter for its own skill. Two legs of the
  same skill both finding it stale is possible but rare; the § 3c
  diff-guard (commit only when the staged adapter differs) + a
  re-fetch-and-retry on non-fast-forward push make the losing leg a no-op.

If the target set is empty (`has_targets=false`), the `eval` job is
skipped and the workflow is a green no-op (same as today's
"nothing under skills/ changed" path).

## Matrix granularity

One leg = one **`(spec, platform)`**. `plan_matrix.py` explodes each
spec into one leg per entry in its `resources.platforms`, so the leg
slug `<skill>__<spec_stem>__<platform>` is a unique trial identity and
maps 1:1 to a single harbor target. All in-tree specs are single-platform
today, so this is the same leg count as per-spec — but it generalizes
cleanly to multi-platform specs (each platform parallelizes onto its own
runner) and makes the per-`(spec,platform)` output root automatic.

`max-parallel` is capped near the `vss-eval-*` box count so legs don't
all grab runner slots only to wait inside `run_leg.py`. `fail-fast: false` so one
failing leg doesn't cancel the others.

## What changes in code

| Component | Change |
|---|---|
| `.github/skill-eval/plan_matrix.py` | **new.** Pure-Python, no LLM. Reads the diff (local `git diff`) — or, on a manual sweep, enumerates the picked skill's specs — applies the rules above, prints `matrix` JSON + `has_targets` to `$GITHUB_OUTPUT`. Unit-testable offline. |
| `.github/workflows/skills-eval.yml` | **rewrite.** `plan` job → `eval` matrix job, on push AND `workflow_dispatch` (a manual sweep feeds the picked skill's specs into the same matrix; legs write to `$GITHUB_STEP_SUMMARY` since there's no PR). |
| `.github/skill-eval/skills_eval_agent.py` | **small add.** New single-spec mode keyed on `EVAL_SKILL`/`EVAL_SPEC`: skip the diff/detection (plan already decided), build a user prompt scoped to one spec, otherwise reuse the existing SDK session, options, hardening, and DONE/BLOCKED enforcement verbatim. |
| `.github/skill-eval/AGENTS.md` | **add a "Single-spec mode" section** (parallel to "Manual full-sweep mode"): when `EVAL_SPEC` is set, skip step 1's diff — you are handed exactly one `(skill, spec)`; run steps 2–7 for it only, post the one comment, emit the marker. Everything else (hard rules, fleet selection § 5a, wrapper-held lock § 5b, harbor invocation, result format, failure modes) applies unchanged. |

The background-task removal is already in place (`disallowed_tools` +
PreToolUse hook); each leg's foreground agent therefore *must* drive
`run_leg.py` synchronously.

## Unchanged on purpose

- Per-leg agent logic: adapter autogeneration, adapter auto-commit flow, harbor flags
  (incl. `--agent-timeout-multiplier 6.0` = 1 h), result-comment format,
  per-trial metric extraction, failure-mode handling, no `skills/` writes,
  no instance lifecycle calls.
- The drop of harness-side profile pre-deploy / `active-deploy.txt`
  marker (this branch's parent change): each trial still deploys its own
  profile in its first agent turn.
- The artifact tarball step (now per-leg, one artifact per
  `(spec,platform)`: `skills-eval-results-pr-<N>-<slug>-<run_id>.tar.gz`,
  `slug = skill__spec_stem__platform`).

## Concurrency / shared-state isolation

All legs of one run share `GITHUB_RUN_ID` and — on a single runner host —
the same `/tmp/skill-eval/` tree. So every runner-local path is scoped by
`<leg-slug>/<run_id>` (`leg-slug = <skill>__<spec_stem>__<platform>`,
== `$EVAL_SLUG`): `datasets/<slug>/<run_id>/`, `results/<slug>/<run_id>/`,
the viewer entry `_viewer/<slug>__<run_id>__<date>/`, and
`brev-snapshot-<slug>.json`. Slug-first groups every run of one trial
under one `<slug>/` dir (history); `<run_id>` underneath isolates the run.
The old global `rm -rf datasets/*` startup wipe is gone (it would delete a
concurrent sibling's live dataset) — each leg cleans only its own scratch
and age-GCs *other* run_ids. `run_id` is in the key too, so two different
PRs' runs on the same host don't collide either.

**Box mutex.** `run_leg.py` opens `/tmp/brev/<box>.lock`, takes an
exclusive `flock`, and keeps that file descriptor open while it launches
every Harbor subprocess for the leg. This serializes trials on a box and
avoids the broken pattern where an agent acquires a lock in one shell
call and runs Harbor in a later shell call after the FD has closed. The
mutex is still valid **only while all eval legs run on one host** — flock
is host-local. If the `vss-skill-eval-runner` label ever spans multiple
hosts, two legs could lock their own local files and both drive the same
brev box (docker/port collision + trajectory corruption via `start()`'s
archive-on-start racing a live session). Keep the label pinned to one
host, or move the lock onto the box itself.

## Open questions / future

- **No pre-trial box reset.** Post-#781 there is no harness-side
  `docker compose down` / volume wipe before a trial — cleanup is
  delegated to the trial's own `/vss-deploy-profile` first query. With
  the matrix reusing warm boxes across *heterogeneous* specs (leg A =
  alerts, leg B = base), cross-profile container/volume leftovers can
  cause port/GPU conflicts or stale-data false results. Candidate fix: a
  harness-side reset in `BrevEnvironment.start()` (compose-down all VSS
  projects + optional volume prune) so every trial starts clean.

- **(spec × platform) legs** for very long multi-platform specs.
- **Consolidated comment** — if N per-spec comments prove noisy, add a
  thin `report` job later; the per-leg artifacts already carry everything
  a reporter would need.
- **`max-parallel` tuning** once we see real box contention.
