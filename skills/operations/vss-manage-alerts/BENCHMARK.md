# Skill Benchmark: vss-manage-alerts

> ❌ **Overall verdict: FAIL — Publication blocked**

The skill should be reviewed before publication. Address the blocking findings below, then rerun Skill Evaluator.

## Evaluation Metadata

- Skill: `vss-manage-alerts`
- Evaluation date: 2026-10-15
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 11 evaluation tasks (10 positive, 1 negative)
- Dataset digest: `sha256:2b5bf7deee2ba610968002c68c2365d6b4caef09ec9d3c359932dc32a2a099bb` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 89.1% — baseline ran, but no comparable score was available; uplift unavailable | 69.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 95.5% (-4.5 points) | 45.5% → 45.5% (±0.0 points) |
| Correctness | 9.1% → 96.4% (+87.3 points) | 36.4% → 83.6% (+47.2 points) |
| Discoverability | 98.5% — baseline ran, but no comparable score was available; uplift unavailable | 80.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 23.2% → 66.3% (+43.1 points) | 28.8% → 55.2% (+26.4 points) |
| Efficiency | 89.0% — baseline ran, but no comparable score was available; uplift unavailable | 83.7% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,493,802 | 1,805,708 | +1,688,094 | +93.49% | skill 11/11; base 11/11 |
| claude-code | alerts-always-on-status-routing | 235,916 | 120,528 | +115,388 | +95.74% | skill 1/1; base 1/1 |
| claude-code | alerts-consolidated-event-count | 368,496 | 182,148 | +186,348 | +102.31% | skill 1/1; base 1/1 |
| claude-code | alerts-create-realtime-rule | 372,758 | 183,981 | +188,777 | +102.61% | skill 1/1; base 1/1 |
| claude-code | alerts-cv-mode-subscription-refusal | 77,197 | 216,544 | -139,347 | -64.35% | skill 1/1; base 1/1 |
| claude-code | alerts-incident-query-routing | 310,211 | 150,467 | +159,744 | +106.17% | skill 1/1; base 1/1 |
| claude-code | alerts-list-active-rules | 372,918 | 120,803 | +252,115 | +208.70% | skill 1/1; base 1/1 |
| claude-code | alerts-negative-non-alert-analytics | 333,939 | 156,222 | +177,717 | +113.76% | skill 1/1; base 1/1 |
| claude-code | alerts-ondemand-verify-clip-routing | 322,022 | 30,085 | +291,937 | +970.37% | skill 1/1; base 1/1 |
| claude-code | alerts-slack-webhook-status | 251,300 | 119,150 | +132,150 | +110.91% | skill 1/1; base 1/1 |
| claude-code | alerts-stop-rule-confirmation | 424,641 | 184,827 | +239,814 | +129.75% | skill 1/1; base 1/1 |
| claude-code | alerts-verification-verdict-routing | 424,404 | 340,953 | +83,451 | +24.48% | skill 1/1; base 1/1 |
| codex | All cases | 6,148,994 | 3,974,843 | +2,174,151 | +54.70% | skill 11/11; base 11/11 |
| codex | alerts-always-on-status-routing | 447,016 | 110,328 | +336,688 | +305.17% | skill 1/1; base 1/1 |
| codex | alerts-consolidated-event-count | 729,906 | 626,297 | +103,609 | +16.54% | skill 1/1; base 1/1 |
| codex | alerts-create-realtime-rule | 1,143,464 | 41,298 | +1,102,166 | +2668.81% | skill 1/1; base 1/1 |
| codex | alerts-cv-mode-subscription-refusal | 97,348 | 565,447 | -468,099 | -82.78% | skill 1/1; base 1/1 |
| codex | alerts-incident-query-routing | 306,367 | 69,735 | +236,632 | +339.33% | skill 1/1; base 1/1 |
| codex | alerts-list-active-rules | 1,017,415 | 577,964 | +439,451 | +76.03% | skill 1/1; base 1/1 |
| codex | alerts-negative-non-alert-analytics | 388,300 | 84,838 | +303,462 | +357.70% | skill 1/1; base 1/1 |
| codex | alerts-ondemand-verify-clip-routing | 296,786 | 71,977 | +224,809 | +312.33% | skill 1/1; base 1/1 |
| codex | alerts-slack-webhook-status | 114,185 | 75,600 | +38,585 | +51.04% | skill 1/1; base 1/1 |
| codex | alerts-stop-rule-confirmation | 654,405 | 988,970 | -334,565 | -33.83% | skill 1/1; base 1/1 |
| codex | alerts-verification-verdict-routing | 953,802 | 762,389 | +191,413 | +25.11% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 9,642,796 | 5,780,551 | +3,862,245 | +66.81% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **FAILED** | 11 validator(s); 44 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED WITH OBSERVATIONS** | 2 validator(s); 1 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

## Blocking Findings

- **MEDIUM** BANDIT/B104:hardcoded_bind_all_interfaces: Possible binding to all interfaces. (CWE-605) (`skills/operations/vss-manage-alerts/scripts/alert-notify/server.py:485`)

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **CRITICAL** CONTENT_DEDUP/llm_prompt_size_limit: A Tier 2 cluster exceeds the LLM prompt character limit. (`skills/operations/vss-manage-alerts`)
- **MEDIUM** BANDIT/B104:hardcoded_bind_all_interfaces: Possible binding to all interfaces. (CWE-605) (`skills/operations/vss-manage-alerts/scripts/alert-notify/server.py:485`)
- **MEDIUM** QUALITY/quality_reliability: MCP skill lacks connection/error guidance (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (11044 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-manage-alerts`)
- 40 additional finding(s) are available in the full evaluation artifacts.

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
