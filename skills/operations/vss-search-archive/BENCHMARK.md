# Skill Benchmark: vss-search-archive

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-search-archive`
- Evaluation date: 2026-10-15
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 18 evaluation tasks (14 positive, 4 negative)
- Dataset digest: `sha256:012255238e0a0bfbd38be26e18659636ecf25d22d8d4fb51e274d34957eeb855` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 71.4% — baseline ran, but no comparable score was available; uplift unavailable | 72.8% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 97.2% → 94.4% (-2.8 points) | 80.6% → 97.2% (+16.6 points) |
| Correctness | 15.6% → 64.4% (+48.8 points) | 37.8% → 56.7% (+18.9 points) |
| Discoverability | 75.7% — baseline ran, but no comparable score was available; uplift unavailable | 83.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 24.3% → 40.1% (+15.8 points) | 33.2% → 32.0% (-1.2 points) |
| Efficiency | 82.5% — baseline ran, but no comparable score was available; uplift unavailable | 94.9% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 5,523,896 | 3,996,598 | +1,527,298 | +38.21% | skill 18/18; base 18/18 |
| claude-code | search-archive | 554,101 | 355,624 | +198,477 | +55.81% | skill 1/1; base 1/1 |
| claude-code | search-archive-attribute-flags | 557,348 | 971,168 | -413,820 | -42.61% | skill 1/1; base 1/1 |
| claude-code | search-archive-confirm-verification | 345,594 | 88,970 | +256,624 | +288.44% | skill 1/1; base 1/1 |
| claude-code | search-archive-delete-only | 211,903 | 214,145 | -2,242 | -1.05% | skill 1/1; base 1/1 |
| claude-code | search-archive-exit2-help | 29,069 | 29,698 | -629 | -2.12% | skill 1/1; base 1/1 |
| claude-code | search-archive-exit6-partial | 125,434 | 119,614 | +5,820 | +4.87% | skill 1/1; base 1/1 |
| claude-code | search-archive-exit6-without-data | 126,131 | 151,015 | -24,884 | -16.48% | skill 1/1; base 1/1 |
| claude-code | search-archive-fusion-decompose | 444,994 | 184,371 | +260,623 | +141.36% | skill 1/1; base 1/1 |
| claude-code | search-archive-implicit | 436,935 | 341,032 | +95,903 | +28.12% | skill 1/1; base 1/1 |
| claude-code | search-archive-ingest-only | 171,241 | 179,976 | -8,735 | -4.85% | skill 1/1; base 1/1 |
| claude-code | search-archive-missing-source | 515,579 | 181,291 | +334,288 | +184.39% | skill 1/1; base 1/1 |
| claude-code | search-archive-mixed-upload-and-stream | 840,828 | 297,141 | +543,687 | +182.97% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-direct-video-qa | 191,127 | 117,966 | +73,161 | +62.02% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-summary | 29,692 | 119,438 | -89,746 | -75.14% | skill 1/1; base 1/1 |
| claude-code | search-archive-object-flags | 397,652 | 277,484 | +120,168 | +43.31% | skill 1/1; base 1/1 |
| claude-code | search-archive-partially-verified | 29,734 | 28,968 | +766 | +2.64% | skill 1/1; base 1/1 |
| claude-code | search-archive-results-first | 456,367 | 279,905 | +176,462 | +63.04% | skill 1/1; base 1/1 |
| claude-code | search-archive-rtsp-live-stream | 60,167 | 58,792 | +1,375 | +2.34% | skill 1/1; base 1/1 |
| codex | All cases | 3,243,670 | 5,154,520 | -1,910,850 | -37.07% | skill 18/18; base 18/18 |
| codex | search-archive | 148,871 | 928,854 | -779,983 | -83.97% | skill 1/1; base 1/1 |
| codex | search-archive-attribute-flags | 79,909 | 496,513 | -416,604 | -83.91% | skill 1/1; base 1/1 |
| codex | search-archive-confirm-verification | 87,467 | 60,546 | +26,921 | +44.46% | skill 1/1; base 1/1 |
| codex | search-archive-delete-only | 548,431 | 112,522 | +435,909 | +387.40% | skill 1/1; base 1/1 |
| codex | search-archive-exit2-help | 119,922 | 56,688 | +63,234 | +111.55% | skill 1/1; base 1/1 |
| codex | search-archive-exit6-partial | 30,091 | 80,056 | -49,965 | -62.41% | skill 1/1; base 1/1 |
| codex | search-archive-exit6-without-data | 29,896 | 69,126 | -39,230 | -56.75% | skill 1/1; base 1/1 |
| codex | search-archive-fusion-decompose | 46,394 | 86,999 | -40,605 | -46.67% | skill 1/1; base 1/1 |
| codex | search-archive-implicit | 79,797 | 134,483 | -54,686 | -40.66% | skill 1/1; base 1/1 |
| codex | search-archive-ingest-only | 285,787 | 83,126 | +202,661 | +243.80% | skill 1/1; base 1/1 |
| codex | search-archive-missing-source | 63,003 | 69,565 | -6,562 | -9.43% | skill 1/1; base 1/1 |
| codex | search-archive-mixed-upload-and-stream | 80,432 | 944,698 | -864,266 | -91.49% | skill 1/1; base 1/1 |
| codex | search-archive-negative-direct-video-qa | 55,085 | 54,866 | +219 | +0.40% | skill 1/1; base 1/1 |
| codex | search-archive-negative-summary | 1,242,742 | 55,983 | +1,186,759 | +2119.86% | skill 1/1; base 1/1 |
| codex | search-archive-object-flags | 63,312 | 1,518,288 | -1,454,976 | -95.83% | skill 1/1; base 1/1 |
| codex | search-archive-partially-verified | 13,467 | 13,338 | +129 | +0.97% | skill 1/1; base 1/1 |
| codex | search-archive-results-first | 154,689 | 361,624 | -206,935 | -57.22% | skill 1/1; base 1/1 |
| codex | search-archive-rtsp-live-stream | 114,375 | 27,245 | +87,130 | +319.80% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 8,767,566 | 9,151,118 | -383,552 | -4.19% | skill 36/36; base 36/36 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 15 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 2 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 18 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_correctness: No documented scripts in table format (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** QUALITY/quality_correctness: Instructions don't mention 'run_script' (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Deeply nested references in cli_usage.md (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-search-archive`)
- **MEDIUM** SCHEMA/body_recommended_section: Missing recommended section: '## Instructions' (`skills/operations/vss-search-archive/SKILL.md`)
- 10 additional finding(s) are available in the full evaluation artifacts.

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
