# Skill Benchmark: vss-generate-video-report

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-generate-video-report`
- Evaluation date: 2026-10-07
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
| Overall | 87.5% — baseline ran, but no comparable score was available; uplift unavailable | 89.7% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 96.4% → 100.0% (+3.6 points) |
| Correctness | 11.4% → 91.4% (+80.0 points) | 38.6% → 94.3% (+55.7 points) |
| Discoverability | 80.0% — baseline ran, but no comparable score was available; uplift unavailable | 74.6% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 8.7% → 77.6% (+68.9 points) | 14.5% → 82.1% (+67.6 points) |
| Efficiency | 88.4% — baseline ran, but no comparable score was available; uplift unavailable | 97.3% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 1,234,432 | 2,091,782 | -857,350 | -40.99% | skill 14/14; base 14/14 |
| claude-code | summarize-without-report-non-activation | 60,123 | 88,809 | -28,686 | -32.30% | skill 1/1; base 1/1 |
| claude-code | video-report-empty-range-policy | 122,097 | 89,866 | +32,231 | +35.87% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-harness-fallback | 122,097 | 181,615 | -59,518 | -32.77% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-multi-edit-loop | 74,785 | 120,179 | -45,394 | -37.77% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-off-policy | 74,652 | 214,422 | -139,770 | -65.18% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-on-policy | 74,557 | 150,308 | -75,751 | -50.40% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-stall-guard | 74,566 | 272,359 | -197,793 | -72.62% | skill 1/1; base 1/1 |
| claude-code | video-report-local-or-base64-path | 136,077 | 185,522 | -49,445 | -26.65% | skill 1/1; base 1/1 |
| claude-code | video-report-long-video-lvs-handoff | 121,692 | 245,011 | -123,319 | -50.33% | skill 1/1; base 1/1 |
| claude-code | video-report-profile-agnostic-mode-a | 74,328 | 119,884 | -45,556 | -38.00% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-a | 29,343 | 29,359 | -16 | -0.05% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-b | 29,358 | 29,430 | -72 | -0.24% | skill 1/1; base 1/1 |
| claude-code | video-report-step3-no-vss-agent-fallback | 134,574 | 213,083 | -78,509 | -36.84% | skill 1/1; base 1/1 |
| claude-code | video-report-vlm-unclear-options | 106,183 | 151,935 | -45,752 | -30.11% | skill 1/1; base 1/1 |
| codex | All cases | 1,125,779 | 1,446,959 | -321,180 | -22.20% | skill 14/14; base 14/14 |
| codex | summarize-without-report-non-activation | 40,841 | 26,772 | +14,069 | +52.55% | skill 1/1; base 1/1 |
| codex | video-report-empty-range-policy | 48,513 | 68,686 | -20,173 | -29.37% | skill 1/1; base 1/1 |
| codex | video-report-hitl-harness-fallback | 59,949 | 70,213 | -10,264 | -14.62% | skill 1/1; base 1/1 |
| codex | video-report-hitl-multi-edit-loop | 62,216 | 54,524 | +7,692 | +14.11% | skill 1/1; base 1/1 |
| codex | video-report-hitl-off-policy | 140,402 | 693,826 | -553,424 | -79.76% | skill 1/1; base 1/1 |
| codex | video-report-hitl-on-policy | 112,998 | 83,923 | +29,075 | +34.64% | skill 1/1; base 1/1 |
| codex | video-report-hitl-stall-guard | 66,626 | 45,311 | +21,315 | +47.04% | skill 1/1; base 1/1 |
| codex | video-report-local-or-base64-path | 74,453 | 55,421 | +19,032 | +34.34% | skill 1/1; base 1/1 |
| codex | video-report-long-video-lvs-handoff | 50,524 | 88,402 | -37,878 | -42.85% | skill 1/1; base 1/1 |
| codex | video-report-profile-agnostic-mode-a | 111,940 | 108,897 | +3,043 | +2.79% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-a | 13,427 | 13,358 | +69 | +0.52% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-b | 13,384 | 13,386 | -2 | -0.01% | skill 1/1; base 1/1 |
| codex | video-report-step3-no-vss-agent-fallback | 192,019 | 54,669 | +137,350 | +251.24% | skill 1/1; base 1/1 |
| codex | video-report-vlm-unclear-options | 138,487 | 69,571 | +68,916 | +99.06% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 2,360,211 | 3,538,741 | -1,178,530 | -33.30% | skill 28/28; base 28/28 |

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
