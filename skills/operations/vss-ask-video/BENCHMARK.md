# Skill Benchmark: vss-ask-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-ask-video`
- Evaluation date: 2026-10-09
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
| Overall | 85.6% — baseline ran, but no comparable score was available; uplift unavailable | 87.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 94.1% (-5.9 points) | 52.9% → 100.0% (+47.1 points) |
| Correctness | 21.2% → 92.9% (+71.7 points) | 43.5% → 81.2% (+37.7 points) |
| Discoverability | 79.7% — baseline ran, but no comparable score was available; uplift unavailable | 94.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 36.3% → 71.8% (+35.5 points) | 46.0% → 63.7% (+17.7 points) |
| Efficiency | 89.5% — baseline ran, but no comparable score was available; uplift unavailable | 96.9% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,131,591 | 2,478,156 | +653,435 | +26.37% | skill 17/17; base 17/17 |
| claude-code | archive-search-non-activation | 200,386 | 150,451 | +49,935 | +33.19% | skill 1/1; base 1/1 |
| claude-code | direct-file-vlm | 535,347 | 359,084 | +176,263 | +49.09% | skill 1/1; base 1/1 |
| claude-code | exact-stored-job | 255,727 | 153,271 | +102,456 | +66.85% | skill 1/1; base 1/1 |
| claude-code | explicit-fresh-window | 219,923 | 254,699 | -34,776 | -13.65% | skill 1/1; base 1/1 |
| claude-code | hot-context-sufficient | 29,149 | 28,983 | +166 | +0.57% | skill 1/1; base 1/1 |
| claude-code | introspection-disabled | 143,370 | 29,811 | +113,559 | +380.93% | skill 1/1; base 1/1 |
| claude-code | introspection-partial | 66,528 | 151,066 | -84,538 | -55.96% | skill 1/1; base 1/1 |
| claude-code | introspection-unconfigured | 386,321 | 189,715 | +196,606 | +103.63% | skill 1/1; base 1/1 |
| claude-code | invalid-child-identity | 104,983 | 183,819 | -78,836 | -42.89% | skill 1/1; base 1/1 |
| claude-code | markdown-pointer-introspection-enabled | 143,441 | 29,623 | +113,818 | +384.22% | skill 1/1; base 1/1 |
| claude-code | markdown-sufficient | 30,097 | 29,573 | +524 | +1.77% | skill 1/1; base 1/1 |
| claude-code | no-markdown-introspection-enabled | 219,166 | 185,241 | +33,925 | +18.31% | skill 1/1; base 1/1 |
| claude-code | no-memory-grounded-window | 252,086 | 154,495 | +97,591 | +63.17% | skill 1/1; base 1/1 |
| claude-code | no-memory-without-scope | 29,715 | 29,231 | +484 | +1.66% | skill 1/1; base 1/1 |
| claude-code | separate-shell-cli | 66,892 | 89,921 | -23,029 | -25.61% | skill 1/1; base 1/1 |
| claude-code | whole-recording-long | 67,094 | 339,241 | -272,147 | -80.22% | skill 1/1; base 1/1 |
| claude-code | whole-recording-short | 381,366 | 119,932 | +261,434 | +217.99% | skill 1/1; base 1/1 |
| codex | All cases | 1,058,120 | 8,109,835 | -7,051,715 | -86.95% | skill 17/17; base 17/17 |
| codex | archive-search-non-activation | 161,528 | 114,751 | +46,777 | +40.76% | skill 1/1; base 1/1 |
| codex | direct-file-vlm | 47,360 | 869,299 | -821,939 | -94.55% | skill 1/1; base 1/1 |
| codex | exact-stored-job | 47,234 | 1,836,480 | -1,789,246 | -97.43% | skill 1/1; base 1/1 |
| codex | explicit-fresh-window | 47,249 | 978,730 | -931,481 | -95.17% | skill 1/1; base 1/1 |
| codex | hot-context-sufficient | 30,182 | 13,292 | +16,890 | +127.07% | skill 1/1; base 1/1 |
| codex | introspection-disabled | 47,063 | 290,496 | -243,433 | -83.80% | skill 1/1; base 1/1 |
| codex | introspection-partial | 30,166 | 13,368 | +16,798 | +125.66% | skill 1/1; base 1/1 |
| codex | introspection-unconfigured | 81,949 | 160,080 | -78,131 | -48.81% | skill 1/1; base 1/1 |
| codex | invalid-child-identity | 86,054 | 1,733,921 | -1,647,867 | -95.04% | skill 1/1; base 1/1 |
| codex | markdown-pointer-introspection-enabled | 47,214 | 1,401,523 | -1,354,309 | -96.63% | skill 1/1; base 1/1 |
| codex | markdown-sufficient | 30,088 | 13,276 | +16,812 | +126.63% | skill 1/1; base 1/1 |
| codex | no-markdown-introspection-enabled | 88,390 | 406,811 | -318,421 | -78.27% | skill 1/1; base 1/1 |
| codex | no-memory-grounded-window | 48,231 | 13,395 | +34,836 | +260.07% | skill 1/1; base 1/1 |
| codex | no-memory-without-scope | 30,077 | 13,311 | +16,766 | +125.96% | skill 1/1; base 1/1 |
| codex | separate-shell-cli | 29,557 | 51,381 | -21,824 | -42.47% | skill 1/1; base 1/1 |
| codex | whole-recording-long | 124,824 | 130,081 | -5,257 | -4.04% | skill 1/1; base 1/1 |
| codex | whole-recording-short | 80,954 | 69,640 | +11,314 | +16.25% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,189,711 | 10,587,991 | -6,398,280 | -60.43% | skill 34/34; base 34/34 |

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
