# Skill Benchmark: vss-ask-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-ask-video`
- Evaluation date: 2026-10-02
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 17 evaluation tasks (16 positive, 1 negative)
- Dataset digest: `sha256:11d7820e9142590c1e831d6fd500ee91b88d7ffed531717d99d64a802013cc26` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 88.7% — baseline ran, but no comparable score was available; uplift unavailable | 85.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 73.5% → 100.0% (+26.5 points) |
| Correctness | 25.9% → 95.3% (+69.4 points) | 40.0% → 82.4% (+42.4 points) |
| Discoverability | 84.1% — baseline ran, but no comparable score was available; uplift unavailable | 92.8% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 37.5% → 76.6% (+39.1 points) | 45.3% → 59.6% (+14.3 points) |
| Efficiency | 87.4% — baseline ran, but no comparable score was available; uplift unavailable | 94.6% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,384,398 | 2,630,881 | +753,517 | +28.64% | skill 17/17; base 17/17 |
| claude-code | archive-search-non-activation | 252,199 | 120,374 | +131,825 | +109.51% | skill 1/1; base 1/1 |
| claude-code | direct-file-vlm | 663,393 | 312,995 | +350,398 | +111.95% | skill 1/1; base 1/1 |
| claude-code | exact-stored-job | 375,660 | 284,107 | +91,553 | +32.22% | skill 1/1; base 1/1 |
| claude-code | explicit-fresh-window | 381,743 | 213,785 | +167,958 | +78.56% | skill 1/1; base 1/1 |
| claude-code | hot-context-sufficient | 67,132 | 28,983 | +38,149 | +131.63% | skill 1/1; base 1/1 |
| claude-code | introspection-disabled | 143,272 | 29,412 | +113,860 | +387.12% | skill 1/1; base 1/1 |
| claude-code | introspection-partial | 66,386 | 120,862 | -54,476 | -45.07% | skill 1/1; base 1/1 |
| claude-code | introspection-unconfigured | 143,036 | 451,950 | -308,914 | -68.35% | skill 1/1; base 1/1 |
| claude-code | invalid-child-identity | 104,660 | 376,113 | -271,453 | -72.17% | skill 1/1; base 1/1 |
| claude-code | markdown-pointer-introspection-enabled | 181,807 | 29,545 | +152,262 | +515.36% | skill 1/1; base 1/1 |
| claude-code | markdown-sufficient | 29,691 | 29,491 | +200 | +0.68% | skill 1/1; base 1/1 |
| claude-code | no-markdown-introspection-enabled | 298,167 | 29,832 | +268,335 | +899.49% | skill 1/1; base 1/1 |
| claude-code | no-memory-grounded-window | 145,529 | 153,583 | -8,054 | -5.24% | skill 1/1; base 1/1 |
| claude-code | no-memory-without-scope | 29,570 | 119,124 | -89,554 | -75.18% | skill 1/1; base 1/1 |
| claude-code | separate-shell-cli | 66,660 | 29,911 | +36,749 | +122.86% | skill 1/1; base 1/1 |
| claude-code | whole-recording-long | 180,384 | 150,931 | +29,453 | +19.51% | skill 1/1; base 1/1 |
| claude-code | whole-recording-short | 255,109 | 149,883 | +105,226 | +70.21% | skill 1/1; base 1/1 |
| codex | All cases | 1,337,130 | 4,586,312 | -3,249,182 | -70.85% | skill 17/17; base 17/17 |
| codex | archive-search-non-activation | 147,209 | 100,588 | +46,621 | +46.35% | skill 1/1; base 1/1 |
| codex | direct-file-vlm | 80,433 | 342,578 | -262,145 | -76.52% | skill 1/1; base 1/1 |
| codex | exact-stored-job | 45,747 | 240,561 | -194,814 | -80.98% | skill 1/1; base 1/1 |
| codex | explicit-fresh-window | 47,046 | 56,130 | -9,084 | -16.18% | skill 1/1; base 1/1 |
| codex | hot-context-sufficient | 30,071 | 13,293 | +16,778 | +126.22% | skill 1/1; base 1/1 |
| codex | introspection-disabled | 92,198 | 147,947 | -55,749 | -37.68% | skill 1/1; base 1/1 |
| codex | introspection-partial | 29,646 | 13,328 | +16,318 | +122.43% | skill 1/1; base 1/1 |
| codex | introspection-unconfigured | 105,950 | 1,257,993 | -1,152,043 | -91.58% | skill 1/1; base 1/1 |
| codex | invalid-child-identity | 67,356 | 773,632 | -706,276 | -91.29% | skill 1/1; base 1/1 |
| codex | markdown-pointer-introspection-enabled | 182,170 | 536,085 | -353,915 | -66.02% | skill 1/1; base 1/1 |
| codex | markdown-sufficient | 29,838 | 13,279 | +16,559 | +124.70% | skill 1/1; base 1/1 |
| codex | no-markdown-introspection-enabled | 113,678 | 844,988 | -731,310 | -86.55% | skill 1/1; base 1/1 |
| codex | no-memory-grounded-window | 47,745 | 13,380 | +34,365 | +256.84% | skill 1/1; base 1/1 |
| codex | no-memory-without-scope | 48,677 | 13,365 | +35,312 | +264.21% | skill 1/1; base 1/1 |
| codex | separate-shell-cli | 64,348 | 66,131 | -1,783 | -2.70% | skill 1/1; base 1/1 |
| codex | whole-recording-long | 123,206 | 83,554 | +39,652 | +47.46% | skill 1/1; base 1/1 |
| codex | whole-recording-short | 81,812 | 69,480 | +12,332 | +17.75% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,721,528 | 7,217,193 | -2,495,665 | -34.58% | skill 34/34; base 34/34 |

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
- **MEDIUM** QUALITY/quality_efficiency: Large skill (5197 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-ask-video/SKILL.md`)
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
