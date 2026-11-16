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
| Overall | 83.0% — baseline ran, but no comparable score was available; uplift unavailable | 68.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 90.9% → 86.4% (-4.5 points) | 54.6% → 72.7% (+18.1 points) |
| Correctness | 25.5% → 92.7% (+67.2 points) | 10.9% → 52.7% (+41.8 points) |
| Discoverability | 97.2% — baseline ran, but no comparable score was available; uplift unavailable | 87.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 22.4% → 55.4% (+33.0 points) | 21.2% → 39.5% (+18.3 points) |
| Efficiency | 83.4% — baseline ran, but no comparable score was available; uplift unavailable | 91.8% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

### Comparison details

Shared-source comparisons use the same signals and weights on both conditions. They do not replace the full dimension scores above or change the publication verdict.

| Agent | Dimension | Basis | Baseline → With skill (uplift) | Explanation |
| --- | --- | --- | --- | --- |
| claude-code | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| claude-code | Efficiency | Token-only comparison | 98.1% → 89.6% (-8.5 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |
| codex | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| codex | Efficiency | Token-only comparison | 92.5% → 88.2% (-4.3 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 2,672,515 | 1,510,345 | +1,162,170 | +76.95% | skill 11/11; base 11/11 |
| claude-code | alerts-always-on-status-routing | 263,490 | 106,639 | +156,851 | +147.09% | skill 1/1; base 1/1 |
| claude-code | alerts-consolidated-event-count | 348,264 | 179,552 | +168,712 | +93.96% | skill 1/1; base 1/1 |
| claude-code | alerts-create-realtime-rule | 250,762 | 106,912 | +143,850 | +134.55% | skill 1/1; base 1/1 |
| claude-code | alerts-cv-mode-subscription-refusal | 88,620 | 143,459 | -54,839 | -38.23% | skill 1/1; base 1/1 |
| claude-code | alerts-incident-query-routing | 216,148 | 106,701 | +109,447 | +102.57% | skill 1/1; base 1/1 |
| claude-code | alerts-list-active-rules | 262,487 | 144,007 | +118,480 | +82.27% | skill 1/1; base 1/1 |
| claude-code | alerts-negative-non-alert-analytics | 230,890 | 108,287 | +122,603 | +113.22% | skill 1/1; base 1/1 |
| claude-code | alerts-ondemand-verify-clip-routing | 256,548 | 143,429 | +113,119 | +78.87% | skill 1/1; base 1/1 |
| claude-code | alerts-slack-webhook-status | 252,719 | 184,263 | +68,456 | +37.15% | skill 1/1; base 1/1 |
| claude-code | alerts-stop-rule-confirmation | 250,818 | 179,925 | +70,893 | +39.40% | skill 1/1; base 1/1 |
| claude-code | alerts-verification-verdict-routing | 251,769 | 107,171 | +144,598 | +134.92% | skill 1/1; base 1/1 |
| codex | All cases | 1,581,482 | 644,969 | +936,513 | +145.20% | skill 11/11; base 11/11 |
| codex | alerts-always-on-status-routing | 135,560 | 49,018 | +86,542 | +176.55% | skill 1/1; base 1/1 |
| codex | alerts-consolidated-event-count | 171,622 | 40,540 | +131,082 | +323.34% | skill 1/1; base 1/1 |
| codex | alerts-create-realtime-rule | 200,322 | 71,316 | +129,006 | +180.89% | skill 1/1; base 1/1 |
| codex | alerts-cv-mode-subscription-refusal | 84,918 | 126,252 | -41,334 | -32.74% | skill 1/1; base 1/1 |
| codex | alerts-incident-query-routing | 146,959 | 40,551 | +106,408 | +262.41% | skill 1/1; base 1/1 |
| codex | alerts-list-active-rules | 145,778 | 41,349 | +104,429 | +252.56% | skill 1/1; base 1/1 |
| codex | alerts-negative-non-alert-analytics | 65,447 | 59,683 | +5,764 | +9.66% | skill 1/1; base 1/1 |
| codex | alerts-ondemand-verify-clip-routing | 164,903 | 13,557 | +151,346 | +1116.37% | skill 1/1; base 1/1 |
| codex | alerts-slack-webhook-status | 94,881 | 56,898 | +37,983 | +66.76% | skill 1/1; base 1/1 |
| codex | alerts-stop-rule-confirmation | 228,060 | 89,961 | +138,099 | +153.51% | skill 1/1; base 1/1 |
| codex | alerts-verification-verdict-routing | 143,032 | 55,844 | +87,188 | +156.13% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 4,253,997 | 2,155,314 | +2,098,683 | +97.37% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. Change is `100 * (with - without) / without`; negative means fewer tokens. N/A comparisons include their reason in the coverage column; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 43 finding(s) |
| Tier 2 | Semantic deduplication | **INCOMPLETE** | Tier 2 scan incomplete: a content cluster exceeds the LLM prompt character limit. |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

## Incomplete Coverage

- **Context Deduplication:** Tier 2 scan incomplete: a content cluster exceeds the LLM prompt character limit.

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_reliability: MCP skill lacks connection/error guidance (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (11139 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Deeply nested references in deploy-alerts.md (`skills/operations/vss-manage-alerts/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-manage-alerts`)
- **MEDIUM** SECURITY/skillspector_execution_failure: skillspector scan did not execute reliably (agent-control analysis limitation): SKILL.md [static; obfuscated_instruction_text; analyzers=static_patterns_agent_snooping,static_patterns_anti_refusal,static_patterns_deserialization,static_patterns_excessive_agency,static_patterns_harmful_content,static_patterns_memory_poisoning,static_patterns_privilege_escalation,static_patterns_prompt_injection,static_patterns_rogue_agent,static_patterns_ssrf,static_patterns_supply_chain,static_patterns_system_prompt_leakage]: Obfuscated instruction text could not be fully evaluated by the deterministic layer.; SKILL.md [static; static_parse_limit; analyzers=static_patterns_tool_misuse]: A security-relevant expression could not be fully resolved by the bounded static parser. SkillSpector could not determine this command or code fragment's boundaries. Check: expansion boundary; location: reported source range; unchecked scope: expression. Relevant parser input not fully checked.; references/alert-notify.md [static; static_parse_limit; analyzers=static_patterns_tool_misuse]: A security-relevant expression could not be fully resolved by the bounded static parser. SkillSpector could not determine this command or code fragment's boundaries. Check: expansion boundary; location: reported source range; unchecked scope: expression. Relevant parser input not fully checked.; and 5 more (`.`)
- 38 additional finding(s) are available in the full evaluation artifacts.

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
