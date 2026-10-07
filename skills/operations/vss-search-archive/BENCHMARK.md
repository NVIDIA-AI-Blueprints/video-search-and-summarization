# Skill Benchmark: vss-search-archive

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-search-archive`
- Evaluation date: 2026-10-07
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
| Overall | 68.7% — baseline ran, but no comparable score was available; uplift unavailable | 61.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 95.5% → 100.0% (+4.5 points) | 81.8% → 90.9% (+9.1 points) |
| Correctness | 12.7% → 52.7% (+40.0 points) | 20.0% → 30.9% (+10.9 points) |
| Discoverability | 74.4% — baseline ran, but no comparable score was available; uplift unavailable | 70.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 25.8% → 29.7% (+3.9 points) | 22.9% → 26.5% (+3.6 points) |
| Efficiency | 86.4% — baseline ran, but no comparable score was available; uplift unavailable | 89.2% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,902,123 | 3,260,418 | -358,295 | -10.99% | skill 11/11; base 11/11 |
| claude-code | search-archive | 401,691 | 976,487 | -574,796 | -58.86% | skill 1/1; base 1/1 |
| claude-code | search-archive-confirm-verification | 270,826 | 119,192 | +151,634 | +127.22% | skill 1/1; base 1/1 |
| claude-code | search-archive-fusion-decompose | 509,844 | 451,966 | +57,878 | +12.81% | skill 1/1; base 1/1 |
| claude-code | search-archive-implicit | 356,441 | 312,276 | +44,165 | +14.14% | skill 1/1; base 1/1 |
| claude-code | search-archive-missing-source | 309,133 | 119,878 | +189,255 | +157.87% | skill 1/1; base 1/1 |
| claude-code | search-archive-mixed-upload-and-stream | 358,740 | 343,733 | +15,007 | +4.37% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-direct-video-qa | 212,253 | 119,039 | +93,214 | +78.31% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-summary | 29,941 | 120,273 | -90,332 | -75.11% | skill 1/1; base 1/1 |
| claude-code | search-archive-partially-verified | 29,748 | 29,489 | +259 | +0.88% | skill 1/1; base 1/1 |
| claude-code | search-archive-results-first | 393,476 | 578,248 | -184,772 | -31.95% | skill 1/1; base 1/1 |
| claude-code | search-archive-rtsp-live-stream | 30,030 | 89,837 | -59,807 | -66.57% | skill 1/1; base 1/1 |
| codex | All cases | 1,240,919 | 1,437,020 | -196,101 | -13.65% | skill 11/11; base 11/11 |
| codex | search-archive | 286,279 | 311,243 | -24,964 | -8.02% | skill 1/1; base 1/1 |
| codex | search-archive-confirm-verification | 123,752 | 83,929 | +39,823 | +47.45% | skill 1/1; base 1/1 |
| codex | search-archive-fusion-decompose | 80,208 | 85,121 | -4,913 | -5.77% | skill 1/1; base 1/1 |
| codex | search-archive-implicit | 188,670 | 99,568 | +89,102 | +89.49% | skill 1/1; base 1/1 |
| codex | search-archive-missing-source | 134,641 | 55,304 | +79,337 | +143.46% | skill 1/1; base 1/1 |
| codex | search-archive-mixed-upload-and-stream | 80,018 | 521,284 | -441,266 | -84.65% | skill 1/1; base 1/1 |
| codex | search-archive-negative-direct-video-qa | 69,636 | 69,273 | +363 | +0.52% | skill 1/1; base 1/1 |
| codex | search-archive-negative-summary | 151,898 | 99,303 | +52,595 | +52.96% | skill 1/1; base 1/1 |
| codex | search-archive-partially-verified | 13,429 | 13,404 | +25 | +0.19% | skill 1/1; base 1/1 |
| codex | search-archive-results-first | 98,870 | 57,325 | +41,545 | +72.47% | skill 1/1; base 1/1 |
| codex | search-archive-rtsp-live-stream | 13,518 | 41,266 | -27,748 | -67.24% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,143,042 | 4,697,438 | -554,396 | -11.80% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 22 finding(s) |
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
- 17 additional finding(s) are available in the full evaluation artifacts.

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
