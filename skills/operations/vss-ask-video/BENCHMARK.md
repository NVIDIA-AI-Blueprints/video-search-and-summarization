# Skill Benchmark: vss-ask-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-ask-video`
- Evaluation date: 2026-10-15
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
| Overall | 90.1% — baseline ran, but no comparable score was available; uplift unavailable | 84.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 67.7% → 100.0% (+32.3 points) |
| Correctness | 20.0% → 95.3% (+75.3 points) | 38.8% → 83.5% (+44.7 points) |
| Discoverability | 84.7% — baseline ran, but no comparable score was available; uplift unavailable | 88.1% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 36.9% → 77.4% (+40.5 points) | 49.0% → 56.6% (+7.6 points) |
| Efficiency | 93.3% — baseline ran, but no comparable score was available; uplift unavailable | 94.4% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,909,348 | 2,650,531 | +258,817 | +9.76% | skill 17/17; base 17/17 |
| claude-code | archive-search-non-activation | 336,646 | 120,803 | +215,843 | +178.67% | skill 1/1; base 1/1 |
| claude-code | direct-file-vlm | 728,079 | 254,160 | +473,919 | +186.46% | skill 1/1; base 1/1 |
| claude-code | exact-stored-job | 218,618 | 277,225 | -58,607 | -21.14% | skill 1/1; base 1/1 |
| claude-code | explicit-fresh-window | 180,868 | 187,278 | -6,410 | -3.42% | skill 1/1; base 1/1 |
| claude-code | hot-context-sufficient | 29,426 | 28,983 | +443 | +1.53% | skill 1/1; base 1/1 |
| claude-code | introspection-disabled | 104,531 | 29,674 | +74,857 | +252.26% | skill 1/1; base 1/1 |
| claude-code | introspection-partial | 66,616 | 151,395 | -84,779 | -56.00% | skill 1/1; base 1/1 |
| claude-code | introspection-unconfigured | 143,189 | 311,174 | -167,985 | -53.98% | skill 1/1; base 1/1 |
| claude-code | invalid-child-identity | 67,215 | 248,690 | -181,475 | -72.97% | skill 1/1; base 1/1 |
| claude-code | markdown-pointer-introspection-enabled | 144,936 | 30,037 | +114,899 | +382.52% | skill 1/1; base 1/1 |
| claude-code | markdown-sufficient | 67,324 | 29,592 | +37,732 | +127.51% | skill 1/1; base 1/1 |
| claude-code | no-markdown-introspection-enabled | 256,073 | 29,978 | +226,095 | +754.20% | skill 1/1; base 1/1 |
| claude-code | no-memory-grounded-window | 107,925 | 123,720 | -15,795 | -12.77% | skill 1/1; base 1/1 |
| claude-code | no-memory-without-scope | 29,854 | 28,993 | +861 | +2.97% | skill 1/1; base 1/1 |
| claude-code | separate-shell-cli | 66,801 | 28,972 | +37,829 | +130.57% | skill 1/1; base 1/1 |
| claude-code | whole-recording-long | 103,508 | 619,791 | -516,283 | -83.30% | skill 1/1; base 1/1 |
| claude-code | whole-recording-short | 257,739 | 150,066 | +107,673 | +71.75% | skill 1/1; base 1/1 |
| codex | All cases | 1,170,902 | 4,206,095 | -3,035,193 | -72.16% | skill 17/17; base 17/17 |
| codex | archive-search-non-activation | 168,713 | 102,373 | +66,340 | +64.80% | skill 1/1; base 1/1 |
| codex | direct-file-vlm | 65,266 | 242,881 | -177,615 | -73.13% | skill 1/1; base 1/1 |
| codex | exact-stored-job | 46,634 | 623,759 | -577,125 | -92.52% | skill 1/1; base 1/1 |
| codex | explicit-fresh-window | 47,171 | 70,703 | -23,532 | -33.28% | skill 1/1; base 1/1 |
| codex | hot-context-sufficient | 30,084 | 13,261 | +16,823 | +126.86% | skill 1/1; base 1/1 |
| codex | introspection-disabled | 114,148 | 144,098 | -29,950 | -20.78% | skill 1/1; base 1/1 |
| codex | introspection-partial | 30,017 | 13,280 | +16,737 | +126.03% | skill 1/1; base 1/1 |
| codex | introspection-unconfigured | 118,002 | 266,049 | -148,047 | -55.65% | skill 1/1; base 1/1 |
| codex | invalid-child-identity | 94,072 | 680,321 | -586,249 | -86.17% | skill 1/1; base 1/1 |
| codex | markdown-pointer-introspection-enabled | 81,744 | 554,174 | -472,430 | -85.25% | skill 1/1; base 1/1 |
| codex | markdown-sufficient | 89,611 | 13,275 | +76,336 | +575.04% | skill 1/1; base 1/1 |
| codex | no-markdown-introspection-enabled | 63,843 | 1,175,103 | -1,111,260 | -94.57% | skill 1/1; base 1/1 |
| codex | no-memory-grounded-window | 30,717 | 13,413 | +17,304 | +129.01% | skill 1/1; base 1/1 |
| codex | no-memory-without-scope | 13,508 | 54,875 | -41,367 | -75.38% | skill 1/1; base 1/1 |
| codex | separate-shell-cli | 48,114 | 51,594 | -3,480 | -6.74% | skill 1/1; base 1/1 |
| codex | whole-recording-long | 81,951 | 88,774 | -6,823 | -7.69% | skill 1/1; base 1/1 |
| codex | whole-recording-short | 47,307 | 98,162 | -50,855 | -51.81% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,080,250 | 6,856,626 | -2,776,376 | -40.49% | skill 34/34; base 34/34 |

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
