# Skill Benchmark: vss-search-archive

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-search-archive`
- Evaluation date: 2026-10-02
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
| Overall | 67.8% — baseline ran, but no comparable score was available; uplift unavailable | 63.1% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 95.5% (-4.5 points) | 90.9% → 95.5% (+4.6 points) |
| Correctness | 21.8% → 58.2% (+36.4 points) | 23.6% → 30.9% (+7.3 points) |
| Discoverability | 65.6% — baseline ran, but no comparable score was available; uplift unavailable | 78.3% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 25.3% → 40.2% (+14.9 points) | 28.6% → 27.0% (-1.6 points) |
| Efficiency | 79.8% — baseline ran, but no comparable score was available; uplift unavailable | 83.8% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,703,336 | 2,475,246 | +228,090 | +9.21% | skill 11/11; base 11/11 |
| claude-code | search-archive | 281,988 | 460,920 | -178,932 | -38.82% | skill 1/1; base 1/1 |
| claude-code | search-archive-confirm-verification | 429,787 | 119,554 | +310,233 | +259.49% | skill 1/1; base 1/1 |
| claude-code | search-archive-fusion-decompose | 315,610 | 375,558 | -59,948 | -15.96% | skill 1/1; base 1/1 |
| claude-code | search-archive-implicit | 431,063 | 330,461 | +100,602 | +30.44% | skill 1/1; base 1/1 |
| claude-code | search-archive-missing-source | 390,811 | 151,846 | +238,965 | +157.37% | skill 1/1; base 1/1 |
| claude-code | search-archive-mixed-upload-and-stream | 30,393 | 518,778 | -488,385 | -94.14% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-direct-video-qa | 241,230 | 119,218 | +122,012 | +102.34% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-summary | 59,139 | 91,125 | -31,986 | -35.10% | skill 1/1; base 1/1 |
| claude-code | search-archive-partially-verified | 29,756 | 29,598 | +158 | +0.53% | skill 1/1; base 1/1 |
| claude-code | search-archive-results-first | 432,954 | 218,593 | +214,361 | +98.06% | skill 1/1; base 1/1 |
| claude-code | search-archive-rtsp-live-stream | 60,605 | 59,595 | +1,010 | +1.69% | skill 1/1; base 1/1 |
| codex | All cases | 1,183,666 | 1,688,717 | -505,051 | -29.91% | skill 11/11; base 11/11 |
| codex | search-archive | 186,012 | 307,837 | -121,825 | -39.57% | skill 1/1; base 1/1 |
| codex | search-archive-confirm-verification | 85,707 | 56,130 | +29,577 | +52.69% | skill 1/1; base 1/1 |
| codex | search-archive-fusion-decompose | 80,136 | 86,617 | -6,481 | -7.48% | skill 1/1; base 1/1 |
| codex | search-archive-implicit | 114,661 | 99,225 | +15,436 | +15.56% | skill 1/1; base 1/1 |
| codex | search-archive-missing-source | 80,716 | 55,069 | +25,647 | +46.57% | skill 1/1; base 1/1 |
| codex | search-archive-mixed-upload-and-stream | 133,791 | 55,033 | +78,758 | +143.11% | skill 1/1; base 1/1 |
| codex | search-archive-negative-direct-video-qa | 55,057 | 54,850 | +207 | +0.38% | skill 1/1; base 1/1 |
| codex | search-archive-negative-summary | 256,201 | 59,434 | +196,767 | +331.07% | skill 1/1; base 1/1 |
| codex | search-archive-partially-verified | 13,432 | 13,349 | +83 | +0.62% | skill 1/1; base 1/1 |
| codex | search-archive-results-first | 80,526 | 118,136 | -37,610 | -31.84% | skill 1/1; base 1/1 |
| codex | search-archive-rtsp-live-stream | 97,427 | 783,037 | -685,610 | -87.56% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 3,887,002 | 4,163,963 | -276,961 | -6.65% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 24 finding(s) |
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
- 19 additional finding(s) are available in the full evaluation artifacts.

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
