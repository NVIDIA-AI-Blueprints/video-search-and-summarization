# Skill Benchmark: vss-search-archive

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-search-archive`
- Evaluation date: 2026-10-05
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 11 evaluation tasks (9 positive, 2 negative)
- Dataset digest: `sha256:d3d57e60e47f9a1f556d52df6af4589543e1834407e038b4dd114809b7176346` (skill-evaluator-dataset-snapshot/1)
- Attempts per task: 1
- Environment: `k8s-sandbox`
- Tier 2 evidence: required for publication
- Tier 3 evidence: required for publication

Each task attempt ran in its own isolated sandbox pod.

## What This Report Answers

The three-tier evaluation checks whether the skill:

- is safe to use;
- produces correct answers;
- is discovered and activated when needed;
- helps the agent complete the user's goal and expected workflow; and
- avoids wasted skill and tool usage.

## Results at a Glance

| Measure | Claude Code (Baseline → Skill Uplift) | Codex (Baseline → Skill Uplift) |
|---|---:|---:|
| Overall | 72.6% — baseline ran, but no comparable score was available; uplift unavailable | 64.1% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 86.4% → 100.0% (+13.6 points) | 68.2% → 100.0% (+31.8 points) |
| Correctness | 21.8% → 60.0% (+38.2 points) | 32.7% → 29.1% (-3.6 points) |
| Discoverability | 84.2% — baseline ran, but no comparable score was available; uplift unavailable | 78.3% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 27.1% → 35.3% (+8.2 points) | 29.1% → 30.5% (+1.4 points) |
| Efficiency | 83.6% — baseline ran, but no comparable score was available; uplift unavailable | 82.5% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,318,758 | 2,600,921 | +717,837 | +27.60% | skill 11/11; base 11/11 |
| claude-code | search-archive | 395,537 | 634,669 | -239,132 | -37.68% | skill 1/1; base 1/1 |
| claude-code | search-archive-confirm-verification | 317,321 | 119,629 | +197,692 | +165.25% | skill 1/1; base 1/1 |
| claude-code | search-archive-fusion-decompose | 493,453 | 244,273 | +249,180 | +102.01% | skill 1/1; base 1/1 |
| claude-code | search-archive-implicit | 356,230 | 422,746 | -66,516 | -15.73% | skill 1/1; base 1/1 |
| claude-code | search-archive-missing-source | 391,215 | 150,768 | +240,447 | +159.48% | skill 1/1; base 1/1 |
| claude-code | search-archive-mixed-upload-and-stream | 434,977 | 451,069 | -16,092 | -3.57% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-direct-video-qa | 120,329 | 119,057 | +1,272 | +1.07% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-summary | 29,832 | 89,314 | -59,482 | -66.60% | skill 1/1; base 1/1 |
| claude-code | search-archive-partially-verified | 29,730 | 29,589 | +141 | +0.48% | skill 1/1; base 1/1 |
| claude-code | search-archive-results-first | 318,332 | 250,048 | +68,284 | +27.31% | skill 1/1; base 1/1 |
| claude-code | search-archive-rtsp-live-stream | 431,802 | 89,759 | +342,043 | +381.07% | skill 1/1; base 1/1 |
| codex | All cases | 2,035,859 | 2,809,518 | -773,659 | -27.54% | skill 11/11; base 11/11 |
| codex | search-archive | 80,968 | 107,069 | -26,101 | -24.38% | skill 1/1; base 1/1 |
| codex | search-archive-confirm-verification | 200,970 | 71,775 | +129,195 | +180.00% | skill 1/1; base 1/1 |
| codex | search-archive-fusion-decompose | 212,978 | 435,068 | -222,090 | -51.05% | skill 1/1; base 1/1 |
| codex | search-archive-implicit | 786,925 | 99,409 | +687,516 | +691.60% | skill 1/1; base 1/1 |
| codex | search-archive-missing-source | 80,111 | 55,371 | +24,740 | +44.68% | skill 1/1; base 1/1 |
| codex | search-archive-mixed-upload-and-stream | 80,569 | 1,768,722 | -1,688,153 | -95.44% | skill 1/1; base 1/1 |
| codex | search-archive-negative-direct-video-qa | 54,883 | 54,531 | +352 | +0.65% | skill 1/1; base 1/1 |
| codex | search-archive-negative-summary | 258,028 | 56,149 | +201,879 | +359.54% | skill 1/1; base 1/1 |
| codex | search-archive-partially-verified | 13,383 | 13,270 | +113 | +0.85% | skill 1/1; base 1/1 |
| codex | search-archive-results-first | 152,996 | 77,024 | +75,972 | +98.63% | skill 1/1; base 1/1 |
| codex | search-archive-rtsp-live-stream | 114,048 | 71,130 | +42,918 | +60.34% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 5,354,617 | 5,410,439 | -55,822 | -1.03% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 23 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 2 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_correctness: No documented scripts in table format (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** QUALITY/quality_correctness: Instructions don't mention 'run_script' (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Deeply nested references in source_lifecycle.md (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-search-archive`)
- **MEDIUM** SCHEMA/body_recommended_section: Missing recommended section: '## Instructions' (`skills/operations/vss-search-archive/SKILL.md`)
- 18 additional finding(s) are available in the full evaluation artifacts.

</details>

## Scoring Methodology

<details>
<summary>Show dimension definitions, source signals, and thresholds</summary>

| Dimension | Question | Scored signals |
|---|---|---|
| Security | Is it safe to use? | `security` (100%) |
| Correctness | Is the answer correct? | `accuracy` (100%) |
| Discoverability | Was the right skill loaded when needed? | `skill_execution` (100%) |
| Effectiveness | Did the skill help complete the task? | `goal_accuracy` (50%) + `behavior_check` (50%) |
| Efficiency | Did it avoid wasted tool calls and token usage? | `skill_efficiency` (50%) + `token_efficiency` (50%) |

- Dimension bands: PASS at 50% or above; NEUTRAL from 40% to below 50%; FAIL below 40%.
- Overall Tier 3 lift: PASS at +5 points or more; FAIL at -10 points or less; values between those bands are NEUTRAL.
- Overall verdict: PASS only when every configured dimension passes for at least one supported agent. Lift is reported as diagnostic evidence and does not override this gate.
- The 50% attempt pass threshold is a separate per-task gate; it is not the dimension pass threshold.
- Effectiveness is the equal-weight mean of goal completion (`goal_accuracy`) and expected workflow adherence (`behavior_check`).
- Efficiency is 50% tool-call productivity (the backward-compatible `skill_efficiency` wire id) and 50% `token_efficiency`. Positive-case skill routing is scored under Discoverability, not Efficiency; a negative case without a routing target is N/A. N/A sources are omitted, remaining weights are renormalized, and the dimension is marked partial.

Signals present in this run:

- `security` (Security): unsafe operations, secret leakage, and unauthorized access.
- `skill_execution` (Skill Execution): whether the expected skill was selected, decoys were avoided, and the workflow executed.
- `skill_efficiency` (Tool Productivity): tool-call productivity (legacy wire id; routing is scored under Discoverability).
- `accuracy` (Accuracy): final-answer correctness against the reference answer.
- `goal_accuracy` (Goal Accuracy): whether the user's goal was achieved.
- `behavior_check` (Behavior Check): whether the expected workflow behavior was followed.
- `token_efficiency` (Token Efficiency): actual uncached prompt plus completion usage (50% of Efficiency).

</details>

## Freshness

Regenerate this benchmark when the skill, evaluation dataset, target agent/model, evaluator version, environment, or scoring policy changes.
