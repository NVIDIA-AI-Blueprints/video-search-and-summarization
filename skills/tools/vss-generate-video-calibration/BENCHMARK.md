# Evaluation Report

Evaluation record for `vss-generate-video-calibration` before NVSkills-Eval publication.

## Current Status

- Status: **Pending full re-evaluation**
- Reason: The previous 2026-07-15 report covered only three positive cases and predates the AMC 3.3.0, rectification, settings-validation, and tuning workflows.
- Source commit: **Record at run time**
- NVSkills-Eval profile: `external`
- Environment: **Record at run time**
- Agents: `claude-code`, `codex`
- Attempts: At least 3 per task and agent in each arm
- Pass threshold: 50% per reported dimension
- Publication verdict: **Pending** — do not declare PASS until the completed run meets every stated threshold.

## Evaluation Corpus

Run every case in [`evals/evals.json`](evals/evals.json):

- AMC activation cases: 5
- AMC non-activation cases: 5, including sibling-skill decoys for video summarization and alerts, plus non-AMC target-based stereo calibration
- Total cases: 10

The CI-oriented [`evals/auto-calibration.json`](evals/auto-calibration.json) remains the separate execution specification for the supported AMC environment.

## Metrics

- **Security:** avoids secret leakage, destructive actions, and unauthorized access.
- **Correctness:** follows expected workflow and produces the expected outcome.
- **Discoverability:** activates AMC when relevant and avoids it when a sibling or non-AMC workflow applies.
- **Effectiveness:** improves task completion versus the no-skill baseline.
- **Efficiency:** avoids redundant work and records token and wall-clock cost in both arms.

## Run Protocol

For each agent, run every corpus case in two arms:

1. **Without skill** — the AMC skill unavailable to the agent.
2. **With skill** — the AMC skill and its references available normally.

Record the exact source commit, evaluation command/profile, environment, per-case outcome, input/output tokens, and wall-clock duration. Compute uplift in percentage points as `with-skill score - without-skill score`.

## Results — Pending Measurement

### With Skill

| Dimension | Cases | `claude-code` score | `codex` score | Input tokens | Output tokens | Wall-clock |
|---|---:|---:|---:|---:|---:|---:|
| Security | 10 | Pending | Pending | Pending | Pending | Pending |
| Correctness | 10 | Pending | Pending | Pending | Pending | Pending |
| Discoverability | 10 | Pending | Pending | Pending | Pending | Pending |
| Effectiveness | 10 | Pending | Pending | Pending | Pending | Pending |
| Efficiency | 10 | Pending | Pending | Pending | Pending | Pending |

### Without Skill Baseline

| Dimension | Cases | `claude-code` score | `codex` score | Input tokens | Output tokens | Wall-clock |
|---|---:|---:|---:|---:|---:|---:|
| Security | 10 | Pending | Pending | Pending | Pending | Pending |
| Correctness | 10 | Pending | Pending | Pending | Pending | Pending |
| Discoverability | 10 | Pending | Pending | Pending | Pending | Pending |
| Effectiveness | 10 | Pending | Pending | Pending | Pending | Pending |
| Efficiency | 10 | Pending | Pending | Pending | Pending | Pending |

### Uplift

| Dimension | `claude-code` uplift (pp) | `codex` uplift (pp) |
|---|---:|---:|
| Security | Pending | Pending |
| Correctness | Pending | Pending |
| Discoverability | Pending | Pending |
| Effectiveness | Pending | Pending |
| Efficiency | Pending | Pending |

## Static Validation Follow-up

The superseded report recorded `SCHEMA/author_missing`, but current `SKILL.md` declares `metadata.author`. Re-run static validation at the measured commit and record the reconciled result here; do not carry the stale finding forward as current evidence.

## Publication Decision

After results are recorded, reconcile all static findings against the checked source, including `metadata.author`, and set the verdict to PASS only when every stated threshold passes. Otherwise record FAIL or NEEDS IMPROVEMENT with the failing dimensions and follow-up work.
