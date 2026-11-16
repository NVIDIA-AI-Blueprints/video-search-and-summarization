# Skill Benchmark: vss-manage-alerts

> ⚠️ **Overall verdict: INCOMPLETE — Required evidence is missing**

One or more required evaluation tiers did not complete, so this benchmark is not publication-complete.

## Evaluation Metadata

- Skill: `vss-manage-alerts`
- Validation status: `incomplete`
- Evaluation date: 2026-11-16
- Evaluator version: `1.6.9`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-5-5`), Codex (`openai/openai/gpt-6.1-sol`)
- Tasks: 11 evaluation tasks (10 positive, 1 negative)
- Dataset digest: `sha256:2b5bf7deee2ba610968002c68c2365d6b4caef09ec9d3c359932dc32a2a099bb` (skill-evaluator-dataset-snapshot/1)
- Attempts per task: 1
- Environment: `k8s-sandbox`
- Tier 2 evidence: required for publication
- Tier 3 evidence: required for publication

Each task attempt ran in its own isolated sandbox pod.

## Tier 1 LLM Provenance

SkillSpector provider, model, analyzer revision, and determinism evidence retained for this scan.

| Skill | Provider | Adapter | Analyzer | Model | Revision | Determinism |
| --- | --- | --- | --- | --- | --- | --- |
| vss-manage-alerts | nv_inference | openai | mcp_tool_poisoning | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-alerts | nv_inference | openai | semantic_developer_intent | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-alerts | nv_inference | openai | semantic_quality_policy | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-alerts | nv_inference | openai | semantic_security_discovery | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-alerts | nv_inference | openai | meta_analyzer | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |

- Controls for `vss-manage-alerts`: temperature=not set (source=provider_default, forwarded=no); seed=not set (source=unset, forwarded=no); reasoning_effort=not set (source=provider_default, forwarded=no)

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
| Overall | 82.3% — baseline ran, but no comparable score was available; uplift unavailable | 72.7% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 90.9% → 72.7% (-18.2 points) | 63.6% → 90.9% (+27.3 points) |
| Correctness | 21.8% → 98.2% (+76.4 points) | 18.2% → 52.7% (+34.5 points) |
| Discoverability | 98.1% — baseline ran, but no comparable score was available; uplift unavailable | 88.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 21.7% → 54.4% (+32.7 points) | 25.8% → 40.3% (+14.5 points) |
| Efficiency | 88.0% — baseline ran, but no comparable score was available; uplift unavailable | 91.8% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

### Comparison details

Shared-source comparisons use the same signals and weights on both conditions. They do not replace the full dimension scores above or change the publication verdict.

| Agent | Dimension | Basis | Baseline → With skill (uplift) | Explanation |
| --- | --- | --- | --- | --- |
| claude-code | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| claude-code | Efficiency | Token-only comparison | 98.1% → 89.9% (-8.2 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |
| codex | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| codex | Efficiency | Token-only comparison | 92.0% → 88.2% (-3.8 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,764,566 | 1,547,888 | +1,216,678 | +78.60% | skill 11/11; base 11/11 |
| claude-code | alerts-always-on-status-routing | 319,469 | 181,773 | +137,696 | +75.75% | skill 1/1; base 1/1 |
| claude-code | alerts-consolidated-event-count | 280,810 | 179,628 | +101,182 | +56.33% | skill 1/1; base 1/1 |
| claude-code | alerts-create-realtime-rule | 249,322 | 107,123 | +142,199 | +132.74% | skill 1/1; base 1/1 |
| claude-code | alerts-cv-mode-subscription-refusal | 88,302 | 143,996 | -55,694 | -38.68% | skill 1/1; base 1/1 |
| claude-code | alerts-incident-query-routing | 342,006 | 106,430 | +235,576 | +221.34% | skill 1/1; base 1/1 |
| claude-code | alerts-list-active-rules | 249,308 | 144,027 | +105,281 | +73.10% | skill 1/1; base 1/1 |
| claude-code | alerts-negative-non-alert-analytics | 230,406 | 144,440 | +85,966 | +59.52% | skill 1/1; base 1/1 |
| claude-code | alerts-ondemand-verify-clip-routing | 250,084 | 143,167 | +106,917 | +74.68% | skill 1/1; base 1/1 |
| claude-code | alerts-slack-webhook-status | 254,011 | 147,461 | +106,550 | +72.26% | skill 1/1; base 1/1 |
| claude-code | alerts-stop-rule-confirmation | 250,196 | 142,602 | +107,594 | +75.45% | skill 1/1; base 1/1 |
| claude-code | alerts-verification-verdict-routing | 250,652 | 107,241 | +143,411 | +133.73% | skill 1/1; base 1/1 |
| codex | All cases | 1,604,830 | 697,208 | +907,622 | +130.18% | skill 11/11; base 11/11 |
| codex | alerts-always-on-status-routing | 128,937 | 49,338 | +79,599 | +161.33% | skill 1/1; base 1/1 |
| codex | alerts-consolidated-event-count | 151,019 | 40,746 | +110,273 | +270.64% | skill 1/1; base 1/1 |
| codex | alerts-create-realtime-rule | 182,227 | 123,334 | +58,893 | +47.75% | skill 1/1; base 1/1 |
| codex | alerts-cv-mode-subscription-refusal | 86,076 | 88,972 | -2,896 | -3.25% | skill 1/1; base 1/1 |
| codex | alerts-incident-query-routing | 177,430 | 40,532 | +136,898 | +337.75% | skill 1/1; base 1/1 |
| codex | alerts-list-active-rules | 145,657 | 41,108 | +104,549 | +254.33% | skill 1/1; base 1/1 |
| codex | alerts-negative-non-alert-analytics | 85,533 | 111,186 | -25,653 | -23.07% | skill 1/1; base 1/1 |
| codex | alerts-ondemand-verify-clip-routing | 140,594 | 13,641 | +126,953 | +930.67% | skill 1/1; base 1/1 |
| codex | alerts-slack-webhook-status | 121,122 | 41,819 | +79,303 | +189.63% | skill 1/1; base 1/1 |
| codex | alerts-stop-rule-confirmation | 198,033 | 90,563 | +107,470 | +118.67% | skill 1/1; base 1/1 |
| codex | alerts-verification-verdict-routing | 188,202 | 55,969 | +132,233 | +236.26% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,369,396 | 2,245,096 | +2,124,300 | +94.62% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. Change is `100 * (with - without) / without`; negative means fewer tokens. N/A comparisons include their reason in the coverage column; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 45 finding(s) |
| Tier 2 | Semantic deduplication | **INCOMPLETE** | Tier 2 scan incomplete: a content cluster exceeds the LLM prompt character limit. |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

## Incomplete Coverage

- **Context Deduplication:** Tier 2 scan incomplete: a content cluster exceeds the LLM prompt character limit.

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_reliability: MCP skill lacks connection/error guidance (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (11043 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Deeply nested references in deploy-alerts.md (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-manage-alerts`)
- **MEDIUM** SECURITY/skillspector_execution_failure: skillspector scan did not execute reliably (agent-control analysis limitation): SKILL.md [static; obfuscated_instruction_text; analyzers=static_patterns_agent_snooping,static_patterns_anti_refusal,static_patterns_deserialization,static_patterns_excessive_agency,static_patterns_harmful_content,static_patterns_memory_poisoning,static_patterns_privilege_escalation,static_patterns_prompt_injection,static_patterns_rogue_agent,static_patterns_ssrf,static_patterns_supply_chain,static_patterns_system_prompt_leakage]: Obfuscated instruction text could not be fully evaluated by the deterministic layer.; SKILL.md [static; static_parse_limit; analyzers=static_patterns_tool_misuse]: A security-relevant expression could not be fully resolved by the bounded static parser. SkillSpector could not determine this command or code fragment's boundaries. Check: expansion boundary; location: reported source range; unchecked scope: expression. Relevant parser input not fully checked.; references/alert-notify.md [static; static_parse_limit; analyzers=static_patterns_tool_misuse]: A security-relevant expression could not be fully resolved by the bounded static parser. SkillSpector could not determine this command or code fragment's boundaries. Check: expansion boundary; location: reported source range; unchecked scope: expression. Relevant parser input not fully checked.; and 5 more (`.`)
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
