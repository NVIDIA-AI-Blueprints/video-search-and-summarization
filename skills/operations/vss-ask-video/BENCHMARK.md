# Skill Benchmark: vss-ask-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-ask-video`
- Evaluation date: 2026-10-07
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 17 evaluation tasks (16 positive, 1 negative)
- Dataset digest: `sha256:830efbe7c931304d5f442008bdbca6a2d7b5552ad59745dfd4190886fe681b13` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 87.4% — baseline ran, but no comparable score was available; uplift unavailable | 86.1% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 94.1% (-5.9 points) | 73.5% → 100.0% (+26.5 points) |
| Correctness | 24.7% → 94.1% (+69.4 points) | 31.8% → 78.8% (+47.0 points) |
| Discoverability | 80.1% — baseline ran, but no comparable score was available; uplift unavailable | 94.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 36.9% → 78.5% (+41.6 points) | 43.3% → 62.6% (+19.3 points) |
| Efficiency | 90.4% — baseline ran, but no comparable score was available; uplift unavailable | 94.6% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,796,170 | 3,160,856 | -364,686 | -11.54% | skill 17/17; base 17/17 |
| claude-code | archive-search-non-activation | 198,147 | 285,110 | -86,963 | -30.50% | skill 1/1; base 1/1 |
| claude-code | direct-file-vlm | 627,767 | 222,547 | +405,220 | +182.08% | skill 1/1; base 1/1 |
| claude-code | exact-stored-job | 218,428 | 212,787 | +5,641 | +2.65% | skill 1/1; base 1/1 |
| claude-code | explicit-fresh-window | 297,260 | 151,220 | +146,040 | +96.57% | skill 1/1; base 1/1 |
| claude-code | hot-context-sufficient | 29,149 | 28,983 | +166 | +0.57% | skill 1/1; base 1/1 |
| claude-code | introspection-disabled | 143,360 | 29,707 | +113,653 | +382.58% | skill 1/1; base 1/1 |
| claude-code | introspection-partial | 66,744 | 151,967 | -85,223 | -56.08% | skill 1/1; base 1/1 |
| claude-code | introspection-unconfigured | 142,681 | 155,935 | -13,254 | -8.50% | skill 1/1; base 1/1 |
| claude-code | invalid-child-identity | 66,986 | 648,226 | -581,240 | -89.67% | skill 1/1; base 1/1 |
| claude-code | markdown-pointer-introspection-enabled | 143,744 | 29,612 | +114,132 | +385.42% | skill 1/1; base 1/1 |
| claude-code | markdown-sufficient | 29,910 | 29,667 | +243 | +0.82% | skill 1/1; base 1/1 |
| claude-code | no-markdown-introspection-enabled | 180,212 | 90,623 | +89,589 | +98.86% | skill 1/1; base 1/1 |
| claude-code | no-memory-grounded-window | 106,580 | 214,288 | -107,708 | -50.26% | skill 1/1; base 1/1 |
| claude-code | no-memory-without-scope | 29,667 | 29,384 | +283 | +0.96% | skill 1/1; base 1/1 |
| claude-code | separate-shell-cli | 66,593 | 59,323 | +7,270 | +12.25% | skill 1/1; base 1/1 |
| claude-code | whole-recording-long | 66,980 | 578,617 | -511,637 | -88.42% | skill 1/1; base 1/1 |
| claude-code | whole-recording-short | 381,962 | 242,860 | +139,102 | +57.28% | skill 1/1; base 1/1 |
| codex | All cases | 1,190,220 | 5,178,168 | -3,987,948 | -77.01% | skill 17/17; base 17/17 |
| codex | archive-search-non-activation | 109,736 | 55,170 | +54,566 | +98.91% | skill 1/1; base 1/1 |
| codex | direct-file-vlm | 64,586 | 617,375 | -552,789 | -89.54% | skill 1/1; base 1/1 |
| codex | exact-stored-job | 46,731 | 157,979 | -111,248 | -70.42% | skill 1/1; base 1/1 |
| codex | explicit-fresh-window | 47,185 | 55,499 | -8,314 | -14.98% | skill 1/1; base 1/1 |
| codex | hot-context-sufficient | 30,261 | 13,311 | +16,950 | +127.34% | skill 1/1; base 1/1 |
| codex | introspection-disabled | 86,628 | 305,409 | -218,781 | -71.64% | skill 1/1; base 1/1 |
| codex | introspection-partial | 30,082 | 13,290 | +16,792 | +126.35% | skill 1/1; base 1/1 |
| codex | introspection-unconfigured | 106,093 | 1,980,574 | -1,874,481 | -94.64% | skill 1/1; base 1/1 |
| codex | invalid-child-identity | 129,476 | 781,308 | -651,832 | -83.43% | skill 1/1; base 1/1 |
| codex | markdown-pointer-introspection-enabled | 47,236 | 806,064 | -758,828 | -94.14% | skill 1/1; base 1/1 |
| codex | markdown-sufficient | 30,341 | 13,310 | +17,031 | +127.96% | skill 1/1; base 1/1 |
| codex | no-markdown-introspection-enabled | 117,447 | 103,274 | +14,173 | +13.72% | skill 1/1; base 1/1 |
| codex | no-memory-grounded-window | 47,969 | 13,446 | +34,523 | +256.75% | skill 1/1; base 1/1 |
| codex | no-memory-without-scope | 30,110 | 13,331 | +16,779 | +125.86% | skill 1/1; base 1/1 |
| codex | separate-shell-cli | 82,141 | 65,284 | +16,857 | +25.82% | skill 1/1; base 1/1 |
| codex | whole-recording-long | 120,289 | 92,891 | +27,398 | +29.49% | skill 1/1; base 1/1 |
| codex | whole-recording-short | 63,909 | 90,653 | -26,744 | -29.50% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 3,986,390 | 8,339,024 | -4,352,634 | -52.20% | skill 34/34; base 34/34 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 13 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 2 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 17 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_correctness: SKILL_SPEC recommended field missing: 'metadata.author' (`skills/operations/vss-ask-video/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (5211 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-ask-video/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-ask-video`)
- **MEDIUM** SCHEMA/body_recommended_section: Missing recommended section: '## Instructions' (`skills/operations/vss-ask-video/SKILL.md`)
- **MEDIUM** SCHEMA/author_missing: Author not specified in metadata (`skills/operations/vss-ask-video/SKILL.md`)
- 8 additional finding(s) are available in the full evaluation artifacts.

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
