# Skill Benchmark: vss-manage-alerts

> ❌ **Overall verdict: FAIL — Publication blocked**

The skill should be reviewed before publication. Address the blocking findings below, then rerun Skill Evaluator.

## Evaluation Metadata

- Skill: `vss-manage-alerts`
- Evaluation date: 2026-10-05
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
| Overall | 88.6% — baseline ran, but no comparable score was available; uplift unavailable | 74.8% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 54.6% → 59.1% (+4.5 points) |
| Correctness | 9.1% → 89.1% (+80.0 points) | 41.8% → 85.5% (+43.7 points) |
| Discoverability | 99.8% — baseline ran, but no comparable score was available; uplift unavailable | 85.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 23.0% → 64.5% (+41.5 points) | 34.7% → 60.2% (+25.5 points) |
| Efficiency | 89.3% — baseline ran, but no comparable score was available; uplift unavailable | 83.6% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,052,096 | 1,912,024 | +1,140,072 | +59.63% | skill 11/11; base 11/11 |
| claude-code | alerts-always-on-status-routing | 286,317 | 120,679 | +165,638 | +137.26% | skill 1/1; base 1/1 |
| claude-code | alerts-consolidated-event-count | 271,051 | 283,719 | -12,668 | -4.46% | skill 1/1; base 1/1 |
| claude-code | alerts-create-realtime-rule | 271,502 | 183,962 | +87,540 | +47.59% | skill 1/1; base 1/1 |
| claude-code | alerts-cv-mode-subscription-refusal | 77,187 | 280,717 | -203,530 | -72.50% | skill 1/1; base 1/1 |
| claude-code | alerts-incident-query-routing | 253,887 | 120,017 | +133,870 | +111.54% | skill 1/1; base 1/1 |
| claude-code | alerts-list-active-rules | 221,859 | 121,752 | +100,107 | +82.22% | skill 1/1; base 1/1 |
| claude-code | alerts-negative-non-alert-analytics | 309,391 | 218,849 | +90,542 | +41.37% | skill 1/1; base 1/1 |
| claude-code | alerts-ondemand-verify-clip-routing | 420,507 | 30,189 | +390,318 | +1292.91% | skill 1/1; base 1/1 |
| claude-code | alerts-slack-webhook-status | 299,713 | 119,911 | +179,802 | +149.95% | skill 1/1; base 1/1 |
| claude-code | alerts-stop-rule-confirmation | 221,139 | 218,063 | +3,076 | +1.41% | skill 1/1; base 1/1 |
| claude-code | alerts-verification-verdict-routing | 419,543 | 214,166 | +205,377 | +95.90% | skill 1/1; base 1/1 |
| codex | All cases | 5,276,478 | 4,020,706 | +1,255,772 | +31.23% | skill 11/11; base 11/11 |
| codex | alerts-always-on-status-routing | 282,677 | 162,122 | +120,555 | +74.36% | skill 1/1; base 1/1 |
| codex | alerts-consolidated-event-count | 1,150,930 | 840,452 | +310,478 | +36.94% | skill 1/1; base 1/1 |
| codex | alerts-create-realtime-rule | 545,288 | 70,113 | +475,175 | +677.73% | skill 1/1; base 1/1 |
| codex | alerts-cv-mode-subscription-refusal | 113,861 | 604,092 | -490,231 | -81.15% | skill 1/1; base 1/1 |
| codex | alerts-incident-query-routing | 478,577 | 55,463 | +423,114 | +762.88% | skill 1/1; base 1/1 |
| codex | alerts-list-active-rules | 239,861 | 611,886 | -372,025 | -60.80% | skill 1/1; base 1/1 |
| codex | alerts-negative-non-alert-analytics | 721,754 | 112,336 | +609,418 | +542.50% | skill 1/1; base 1/1 |
| codex | alerts-ondemand-verify-clip-routing | 304,269 | 41,535 | +262,734 | +632.56% | skill 1/1; base 1/1 |
| codex | alerts-slack-webhook-status | 117,108 | 99,704 | +17,404 | +17.46% | skill 1/1; base 1/1 |
| codex | alerts-stop-rule-confirmation | 412,292 | 602,482 | -190,190 | -31.57% | skill 1/1; base 1/1 |
| codex | alerts-verification-verdict-routing | 909,861 | 820,521 | +89,340 | +10.89% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 8,328,574 | 5,932,730 | +2,395,844 | +40.38% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **FAILED** | 11 validator(s); 72 finding(s) |
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
- 68 additional finding(s) are available in the full evaluation artifacts.

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
