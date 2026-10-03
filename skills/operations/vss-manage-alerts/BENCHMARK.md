# Skill Benchmark: vss-manage-alerts

> ❌ **Overall verdict: FAIL — Publication blocked**

The skill should be reviewed before publication. Address the blocking findings below, then rerun Skill Evaluator.

## Evaluation Metadata

- Skill: `vss-manage-alerts`
- Evaluation date: 2026-10-02
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
| Overall | 87.8% — baseline ran, but no comparable score was available; uplift unavailable | 74.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 90.9% → 95.5% (+4.6 points) | 59.1% → 68.2% (+9.1 points) |
| Correctness | 14.6% → 90.9% (+76.3 points) | 25.5% → 78.2% (+52.7 points) |
| Discoverability | 98.5% — baseline ran, but no comparable score was available; uplift unavailable | 84.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 26.7% → 66.3% (+39.6 points) | 26.8% → 54.6% (+27.8 points) |
| Efficiency | 88.0% — baseline ran, but no comparable score was available; uplift unavailable | 86.9% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,777,438 | 2,177,415 | +1,600,023 | +73.48% | skill 11/11; base 11/11 |
| claude-code | alerts-always-on-status-routing | 340,230 | 119,955 | +220,275 | +183.63% | skill 1/1; base 1/1 |
| claude-code | alerts-consolidated-event-count | 320,911 | 152,213 | +168,698 | +110.83% | skill 1/1; base 1/1 |
| claude-code | alerts-create-realtime-rule | 428,678 | 216,201 | +212,477 | +98.28% | skill 1/1; base 1/1 |
| claude-code | alerts-cv-mode-subscription-refusal | 76,986 | 391,728 | -314,742 | -80.35% | skill 1/1; base 1/1 |
| claude-code | alerts-incident-query-routing | 221,721 | 151,600 | +70,121 | +46.25% | skill 1/1; base 1/1 |
| claude-code | alerts-list-active-rules | 370,957 | 247,109 | +123,848 | +50.12% | skill 1/1; base 1/1 |
| claude-code | alerts-negative-non-alert-analytics | 494,509 | 213,945 | +280,564 | +131.14% | skill 1/1; base 1/1 |
| claude-code | alerts-ondemand-verify-clip-routing | 274,439 | 60,292 | +214,147 | +355.18% | skill 1/1; base 1/1 |
| claude-code | alerts-slack-webhook-status | 250,969 | 150,424 | +100,545 | +66.84% | skill 1/1; base 1/1 |
| claude-code | alerts-stop-rule-confirmation | 488,157 | 155,804 | +332,353 | +213.31% | skill 1/1; base 1/1 |
| claude-code | alerts-verification-verdict-routing | 509,881 | 318,144 | +191,737 | +60.27% | skill 1/1; base 1/1 |
| codex | All cases | 3,926,025 | 3,562,425 | +363,600 | +10.21% | skill 11/11; base 11/11 |
| codex | alerts-always-on-status-routing | 258,132 | 160,379 | +97,753 | +60.95% | skill 1/1; base 1/1 |
| codex | alerts-consolidated-event-count | 724,075 | 1,642,564 | -918,489 | -55.92% | skill 1/1; base 1/1 |
| codex | alerts-create-realtime-rule | 406,406 | 54,979 | +351,427 | +639.20% | skill 1/1; base 1/1 |
| codex | alerts-cv-mode-subscription-refusal | 73,332 | 302,525 | -229,193 | -75.76% | skill 1/1; base 1/1 |
| codex | alerts-incident-query-routing | 399,768 | 55,886 | +343,882 | +615.33% | skill 1/1; base 1/1 |
| codex | alerts-list-active-rules | 439,135 | 306,967 | +132,168 | +43.06% | skill 1/1; base 1/1 |
| codex | alerts-negative-non-alert-analytics | 127,108 | 145,283 | -18,175 | -12.51% | skill 1/1; base 1/1 |
| codex | alerts-ondemand-verify-clip-routing | 455,477 | 72,263 | +383,214 | +530.30% | skill 1/1; base 1/1 |
| codex | alerts-slack-webhook-status | 98,149 | 60,564 | +37,585 | +62.06% | skill 1/1; base 1/1 |
| codex | alerts-stop-rule-confirmation | 346,540 | 129,776 | +216,764 | +167.03% | skill 1/1; base 1/1 |
| codex | alerts-verification-verdict-routing | 597,903 | 631,239 | -33,336 | -5.28% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 7,703,463 | 5,739,840 | +1,963,623 | +34.21% | skill 22/22; base 22/22 |

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
