# Skill Benchmark: vss-generate-video-report

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-generate-video-report`
- Evaluation date: 2026-10-02
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 14 evaluation tasks (13 positive, 1 negative)
- Dataset digest: `sha256:f38f843e066015d7616f96094958f1afaecdfbffd0c34a876021607b7efc0a54` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 89.8% — baseline ran, but no comparable score was available; uplift unavailable | 90.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 12.9% → 94.3% (+81.4 points) | 41.4% → 95.7% (+54.3 points) |
| Discoverability | 84.6% — baseline ran, but no comparable score was available; uplift unavailable | 81.9% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 5.5% → 84.7% (+79.2 points) | 14.4% → 79.6% (+65.2 points) |
| Efficiency | 85.6% — baseline ran, but no comparable score was available; uplift unavailable | 97.3% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 1,411,124 | 1,936,697 | -525,573 | -27.14% | skill 14/14; base 14/14 |
| claude-code | summarize-without-report-non-activation | 170,412 | 88,909 | +81,503 | +91.67% | skill 1/1; base 1/1 |
| claude-code | video-report-empty-range-policy | 121,973 | 119,466 | +2,507 | +2.10% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-harness-fallback | 91,789 | 121,823 | -30,034 | -24.65% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-multi-edit-loop | 134,670 | 149,975 | -15,305 | -10.21% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-off-policy | 74,636 | 153,157 | -78,521 | -51.27% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-on-policy | 74,612 | 150,772 | -76,160 | -50.51% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-stall-guard | 74,497 | 272,592 | -198,095 | -72.67% | skill 1/1; base 1/1 |
| claude-code | video-report-local-or-base64-path | 135,158 | 185,524 | -50,366 | -27.15% | skill 1/1; base 1/1 |
| claude-code | video-report-long-video-lvs-handoff | 159,269 | 180,471 | -21,202 | -11.75% | skill 1/1; base 1/1 |
| claude-code | video-report-profile-agnostic-mode-a | 74,389 | 120,349 | -45,960 | -38.19% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-a | 29,342 | 29,412 | -70 | -0.24% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-b | 29,315 | 29,463 | -148 | -0.50% | skill 1/1; base 1/1 |
| claude-code | video-report-step3-no-vss-agent-fallback | 134,664 | 182,504 | -47,840 | -26.21% | skill 1/1; base 1/1 |
| claude-code | video-report-vlm-unclear-options | 106,398 | 152,280 | -45,882 | -30.13% | skill 1/1; base 1/1 |
| codex | All cases | 1,070,957 | 838,004 | +232,953 | +27.80% | skill 14/14; base 14/14 |
| codex | summarize-without-report-non-activation | 40,890 | 26,771 | +14,119 | +52.74% | skill 1/1; base 1/1 |
| codex | video-report-empty-range-policy | 91,314 | 83,033 | +8,281 | +9.97% | skill 1/1; base 1/1 |
| codex | video-report-hitl-harness-fallback | 60,515 | 69,971 | -9,456 | -13.51% | skill 1/1; base 1/1 |
| codex | video-report-hitl-multi-edit-loop | 61,905 | 40,577 | +21,328 | +52.56% | skill 1/1; base 1/1 |
| codex | video-report-hitl-off-policy | 170,408 | 98,624 | +71,784 | +72.79% | skill 1/1; base 1/1 |
| codex | video-report-hitl-on-policy | 125,261 | 112,770 | +12,491 | +11.08% | skill 1/1; base 1/1 |
| codex | video-report-hitl-stall-guard | 66,374 | 40,779 | +25,595 | +62.77% | skill 1/1; base 1/1 |
| codex | video-report-local-or-base64-path | 74,487 | 54,936 | +19,551 | +35.59% | skill 1/1; base 1/1 |
| codex | video-report-long-video-lvs-handoff | 50,439 | 69,443 | -19,004 | -27.37% | skill 1/1; base 1/1 |
| codex | video-report-profile-agnostic-mode-a | 73,827 | 65,205 | +8,622 | +13.22% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-a | 13,384 | 13,417 | -33 | -0.25% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-b | 30,173 | 13,364 | +16,809 | +125.78% | skill 1/1; base 1/1 |
| codex | video-report-step3-no-vss-agent-fallback | 75,110 | 79,802 | -4,692 | -5.88% | skill 1/1; base 1/1 |
| codex | video-report-vlm-unclear-options | 136,870 | 69,312 | +67,558 | +97.47% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 2,482,081 | 2,774,701 | -292,620 | -10.55% | skill 28/28; base 28/28 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 15 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED WITH OBSERVATIONS** | 2 validator(s); 2 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 14 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **HIGH** DUPLICATE/duplicate: Duplicate content found within references/report-types/sop-compliance.md:
  "# consumes the VA_MCP_URL it validated (no re-derivation, no Docker fallback)." in references/report-types/sop-compliance.md (lines 25-32)
  vs "# Fresh shell: paste the Endpoint resolution hand-off here too — this block requires its VA_MCP_URL." in references/report-types/sop-compliance.md (lines 55-62) (`references/report-types/sop-compliance.md:25`)
- **MEDIUM** QUALITY/quality_reliability: MCP skill lacks connection/error guidance (`skills/operations/vss-generate-video-report/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (9005 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-generate-video-report/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-generate-video-report`)
- **MEDIUM** SECURITY/External Transmission (E1): Data Exfiltration: curl -si --max-time 10 -X POST "$MCP" -H "$CT" -H "$AC" \
  -d  (`references/report-types/sop-compliance.md:29`)
- 12 additional finding(s) are available in the full evaluation artifacts.

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
