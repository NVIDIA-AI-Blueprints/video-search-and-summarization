# Skill Benchmark: vss-manage-alerts

> ❌ **Overall verdict: FAIL — Publication blocked**

The skill should be reviewed before publication. Address the blocking findings below, then rerun Skill Evaluator.

## Evaluation Metadata

- Skill: `vss-manage-alerts`
- Evaluation date: 2026-10-07
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
| Overall | Not available | 73.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | Not available | 40.9% → 50.0% (+9.1 points) |
| Correctness | Not available | 34.6% → 90.9% (+56.3 points) |
| Discoverability | Not available | 82.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | Not available | 28.6% → 56.5% (+27.9 points) |
| Efficiency | Not available | 88.5% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 3,511,824 | 2,207,234 | +1,304,590 | +59.11% | skill 11/11; base 11/11 |
| claude-code | alerts-always-on-status-routing | 338,878 | 121,737 | +217,141 | +178.37% | skill 1/1; base 1/1 |
| claude-code | alerts-consolidated-event-count | 369,244 | 153,905 | +215,339 | +139.92% | skill 1/1; base 1/1 |
| claude-code | alerts-create-realtime-rule | 579,855 | 215,201 | +364,654 | +169.45% | skill 1/1; base 1/1 |
| claude-code | alerts-cv-mode-subscription-refusal | 77,109 | 315,934 | -238,825 | -75.59% | skill 1/1; base 1/1 |
| claude-code | alerts-incident-query-routing | 221,389 | 89,466 | +131,923 | +147.46% | skill 1/1; base 1/1 |
| claude-code | alerts-list-active-rules | 221,500 | 182,942 | +38,558 | +21.08% | skill 1/1; base 1/1 |
| claude-code | alerts-negative-non-alert-analytics | 410,714 | 223,093 | +187,621 | +84.10% | skill 1/1; base 1/1 |
| claude-code | alerts-ondemand-verify-clip-routing | 221,764 | 30,195 | +191,569 | +634.44% | skill 1/1; base 1/1 |
| claude-code | alerts-slack-webhook-status | 251,100 | 212,342 | +38,758 | +18.25% | skill 1/1; base 1/1 |
| claude-code | alerts-stop-rule-confirmation | 371,377 | 447,301 | -75,924 | -16.97% | skill 1/1; base 1/1 |
| claude-code | alerts-verification-verdict-routing | 448,894 | 215,118 | +233,776 | +108.67% | skill 1/1; base 1/1 |
| codex | All cases | 4,592,904 | 3,974,674 | +618,230 | +15.55% | skill 11/11; base 11/11 |
| codex | alerts-always-on-status-routing | 404,857 | 88,211 | +316,646 | +358.96% | skill 1/1; base 1/1 |
| codex | alerts-consolidated-event-count | 917,155 | 894,265 | +22,890 | +2.56% | skill 1/1; base 1/1 |
| codex | alerts-create-realtime-rule | 257,536 | 41,506 | +216,030 | +520.48% | skill 1/1; base 1/1 |
| codex | alerts-cv-mode-subscription-refusal | 115,090 | 285,979 | -170,889 | -59.76% | skill 1/1; base 1/1 |
| codex | alerts-incident-query-routing | 438,434 | 69,724 | +368,710 | +528.81% | skill 1/1; base 1/1 |
| codex | alerts-list-active-rules | 185,166 | 645,977 | -460,811 | -71.34% | skill 1/1; base 1/1 |
| codex | alerts-negative-non-alert-analytics | 140,269 | 70,546 | +69,723 | +98.83% | skill 1/1; base 1/1 |
| codex | alerts-ondemand-verify-clip-routing | 292,398 | 116,303 | +176,095 | +151.41% | skill 1/1; base 1/1 |
| codex | alerts-slack-webhook-status | 92,316 | 79,109 | +13,207 | +16.69% | skill 1/1; base 1/1 |
| codex | alerts-stop-rule-confirmation | 705,212 | 530,123 | +175,089 | +33.03% | skill 1/1; base 1/1 |
| codex | alerts-verification-verdict-routing | 1,044,471 | 1,152,931 | -108,460 | -9.41% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 8,104,728 | 6,181,908 | +1,922,820 | +31.10% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **FAILED** | 11 validator(s); 43 finding(s) |
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
- 39 additional finding(s) are available in the full evaluation artifacts.

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
