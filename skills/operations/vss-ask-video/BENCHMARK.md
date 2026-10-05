# Skill Benchmark: vss-ask-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-ask-video`
- Evaluation date: 2026-10-05
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
| Overall | 88.6% — baseline ran, but no comparable score was available; uplift unavailable | 85.3% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 94.1% → 100.0% (+5.9 points) | 64.7% → 100.0% (+35.3 points) |
| Correctness | 22.4% → 94.1% (+71.7 points) | 40.0% → 81.2% (+41.2 points) |
| Discoverability | 92.5% — baseline ran, but no comparable score was available; uplift unavailable | 91.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 35.7% → 66.9% (+31.2 points) | 41.6% → 60.0% (+18.4 points) |
| Efficiency | 89.5% — baseline ran, but no comparable score was available; uplift unavailable | 93.5% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,681,783 | 2,204,592 | +477,191 | +21.65% | skill 17/17; base 17/17 |
| claude-code | archive-search-non-activation | 340,563 | 245,334 | +95,229 | +38.82% | skill 1/1; base 1/1 |
| claude-code | direct-file-vlm | 384,548 | 261,175 | +123,373 | +47.24% | skill 1/1; base 1/1 |
| claude-code | exact-stored-job | 179,875 | 150,941 | +28,934 | +19.17% | skill 1/1; base 1/1 |
| claude-code | explicit-fresh-window | 142,572 | 213,409 | -70,837 | -33.19% | skill 1/1; base 1/1 |
| claude-code | hot-context-sufficient | 66,294 | 28,983 | +37,311 | +128.73% | skill 1/1; base 1/1 |
| claude-code | introspection-disabled | 105,660 | 29,725 | +75,935 | +255.46% | skill 1/1; base 1/1 |
| claude-code | introspection-partial | 66,614 | 121,053 | -54,439 | -44.97% | skill 1/1; base 1/1 |
| claude-code | introspection-unconfigured | 180,515 | 182,393 | -1,878 | -1.03% | skill 1/1; base 1/1 |
| claude-code | invalid-child-identity | 141,632 | 280,301 | -138,669 | -49.47% | skill 1/1; base 1/1 |
| claude-code | markdown-pointer-introspection-enabled | 260,518 | 29,682 | +230,836 | +777.70% | skill 1/1; base 1/1 |
| claude-code | markdown-sufficient | 66,976 | 29,669 | +37,307 | +125.74% | skill 1/1; base 1/1 |
| claude-code | no-markdown-introspection-enabled | 219,392 | 90,253 | +129,139 | +143.09% | skill 1/1; base 1/1 |
| claude-code | no-memory-grounded-window | 145,271 | 121,261 | +24,010 | +19.80% | skill 1/1; base 1/1 |
| claude-code | no-memory-without-scope | 29,695 | 29,309 | +386 | +1.32% | skill 1/1; base 1/1 |
| claude-code | separate-shell-cli | 66,902 | 89,599 | -22,697 | -25.33% | skill 1/1; base 1/1 |
| claude-code | whole-recording-long | 67,351 | 120,632 | -53,281 | -44.17% | skill 1/1; base 1/1 |
| claude-code | whole-recording-short | 217,405 | 180,873 | +36,532 | +20.20% | skill 1/1; base 1/1 |
| codex | All cases | 1,450,276 | 3,845,752 | -2,395,476 | -62.29% | skill 17/17; base 17/17 |
| codex | archive-search-non-activation | 124,608 | 85,282 | +39,326 | +46.11% | skill 1/1; base 1/1 |
| codex | direct-file-vlm | 138,607 | 223,827 | -85,220 | -38.07% | skill 1/1; base 1/1 |
| codex | exact-stored-job | 46,894 | 455,349 | -408,455 | -89.70% | skill 1/1; base 1/1 |
| codex | explicit-fresh-window | 47,169 | 71,289 | -24,120 | -33.83% | skill 1/1; base 1/1 |
| codex | hot-context-sufficient | 29,987 | 13,273 | +16,714 | +125.92% | skill 1/1; base 1/1 |
| codex | introspection-disabled | 133,594 | 134,065 | -471 | -0.35% | skill 1/1; base 1/1 |
| codex | introspection-partial | 30,117 | 13,281 | +16,836 | +126.77% | skill 1/1; base 1/1 |
| codex | introspection-unconfigured | 91,058 | 307,048 | -215,990 | -70.34% | skill 1/1; base 1/1 |
| codex | invalid-child-identity | 87,924 | 631,789 | -543,865 | -86.08% | skill 1/1; base 1/1 |
| codex | markdown-pointer-introspection-enabled | 182,747 | 1,201,424 | -1,018,677 | -84.79% | skill 1/1; base 1/1 |
| codex | markdown-sufficient | 30,192 | 13,323 | +16,869 | +126.62% | skill 1/1; base 1/1 |
| codex | no-markdown-introspection-enabled | 181,632 | 419,454 | -237,822 | -56.70% | skill 1/1; base 1/1 |
| codex | no-memory-grounded-window | 47,697 | 13,492 | +34,205 | +253.52% | skill 1/1; base 1/1 |
| codex | no-memory-without-scope | 48,766 | 13,324 | +35,442 | +266.00% | skill 1/1; base 1/1 |
| codex | separate-shell-cli | 81,871 | 57,447 | +24,424 | +42.52% | skill 1/1; base 1/1 |
| codex | whole-recording-long | 100,504 | 91,418 | +9,086 | +9.94% | skill 1/1; base 1/1 |
| codex | whole-recording-short | 46,909 | 100,667 | -53,758 | -53.40% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,132,059 | 6,050,344 | -1,918,285 | -31.71% | skill 34/34; base 34/34 |

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
