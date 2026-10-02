# Skill Benchmark: vss-generate-video-report-rag

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-generate-video-report-rag`
- Evaluation date: 2026-10-02
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 3 evaluation tasks (2 positive, 1 negative)
- Dataset digest: `sha256:f91e7a51aeca96528dda454c2c82f368bbefbfaaea8558971605461d1415fa00` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 74.0% — baseline ran, but no comparable score was available; uplift unavailable | 68.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 83.3% (-16.7 points) |
| Correctness | 13.3% → 80.0% (+66.7 points) | 53.3% → 73.3% (+20.0 points) |
| Discoverability | 40.0% — baseline ran, but no comparable score was available; uplift unavailable | 47.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 16.4% → 51.7% (+35.3 points) | 35.2% → 41.9% (+6.7 points) |
| Efficiency | 98.3% — baseline ran, but no comparable score was available; uplift unavailable | 98.6% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 327,834 | 334,926 | -7,092 | -2.12% | skill 3/3; base 3/3 |
| claude-code | rag-config-deploy | 127,783 | 120,864 | +6,919 | +5.72% | skill 1/1; base 1/1 |
| claude-code | rag-report-routing | 29,212 | 29,540 | -328 | -1.11% | skill 1/1; base 1/1 |
| claude-code | standard-report-non-activation | 170,839 | 184,522 | -13,683 | -7.42% | skill 1/1; base 1/1 |
| codex | All cases | 125,337 | 148,270 | -22,933 | -15.47% | skill 3/3; base 3/3 |
| codex | rag-config-deploy | 29,845 | 79,256 | -49,411 | -62.34% | skill 1/1; base 1/1 |
| codex | rag-report-routing | 13,333 | 13,592 | -259 | -1.91% | skill 1/1; base 1/1 |
| codex | standard-report-non-activation | 82,159 | 55,422 | +26,737 | +48.24% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 453,171 | 483,196 | -30,025 | -6.21% | skill 6/6; base 6/6 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 14 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 2 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 3 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_correctness: SKILL_SPEC recommended field missing: 'metadata.author' (`skills/operations/vss-generate-video-report-rag/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-generate-video-report-rag`)
- **MEDIUM** SCHEMA/body_recommended_section: Missing recommended section: '## Instructions' (`skills/operations/vss-generate-video-report-rag/SKILL.md`)
- **MEDIUM** SCHEMA/body_recommended_section: Missing recommended section: '## Examples' (`skills/operations/vss-generate-video-report-rag/SKILL.md`)
- **MEDIUM** SCHEMA/author_missing: Author not specified in metadata (`skills/operations/vss-generate-video-report-rag/SKILL.md`)
- 9 additional finding(s) are available in the full evaluation artifacts.

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
