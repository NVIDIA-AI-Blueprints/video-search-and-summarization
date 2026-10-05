# Skill Benchmark: vss-generate-video-report

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-generate-video-report`
- Evaluation date: 2026-10-05
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
| Overall | 90.8% — baseline ran, but no comparable score was available; uplift unavailable | 88.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 12.9% → 94.3% (+81.4 points) | 38.6% → 91.4% (+52.8 points) |
| Discoverability | 84.6% — baseline ran, but no comparable score was available; uplift unavailable | 71.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 3.4% → 83.5% (+80.1 points) | 12.7% → 81.7% (+69.0 points) |
| Efficiency | 91.6% — baseline ran, but no comparable score was available; uplift unavailable | 97.3% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 1,359,955 | 2,269,385 | -909,430 | -40.07% | skill 14/14; base 14/14 |
| claude-code | summarize-without-report-non-activation | 219,548 | 88,945 | +130,603 | +146.84% | skill 1/1; base 1/1 |
| claude-code | video-report-empty-range-policy | 122,224 | 120,121 | +2,103 | +1.75% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-harness-fallback | 74,862 | 149,992 | -75,130 | -50.09% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-multi-edit-loop | 74,621 | 149,188 | -74,567 | -49.98% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-off-policy | 75,042 | 246,076 | -171,034 | -69.50% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-on-policy | 74,462 | 150,630 | -76,168 | -50.57% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-stall-guard | 74,469 | 209,163 | -134,694 | -64.40% | skill 1/1; base 1/1 |
| claude-code | video-report-local-or-base64-path | 135,473 | 150,803 | -15,330 | -10.17% | skill 1/1; base 1/1 |
| claude-code | video-report-long-video-lvs-handoff | 74,570 | 401,857 | -327,287 | -81.44% | skill 1/1; base 1/1 |
| claude-code | video-report-profile-agnostic-mode-a | 74,377 | 181,372 | -106,995 | -58.99% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-a | 29,377 | 29,495 | -118 | -0.40% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-b | 29,329 | 29,570 | -241 | -0.82% | skill 1/1; base 1/1 |
| claude-code | video-report-step3-no-vss-agent-fallback | 168,893 | 150,808 | +18,085 | +11.99% | skill 1/1; base 1/1 |
| claude-code | video-report-vlm-unclear-options | 132,708 | 211,365 | -78,657 | -37.21% | skill 1/1; base 1/1 |
| codex | All cases | 1,043,213 | 825,356 | +217,857 | +26.40% | skill 14/14; base 14/14 |
| codex | summarize-without-report-non-activation | 40,944 | 26,768 | +14,176 | +52.96% | skill 1/1; base 1/1 |
| codex | video-report-empty-range-policy | 68,031 | 54,881 | +13,150 | +23.96% | skill 1/1; base 1/1 |
| codex | video-report-hitl-harness-fallback | 61,504 | 83,598 | -22,094 | -26.43% | skill 1/1; base 1/1 |
| codex | video-report-hitl-multi-edit-loop | 62,040 | 40,599 | +21,441 | +52.81% | skill 1/1; base 1/1 |
| codex | video-report-hitl-off-policy | 138,651 | 83,428 | +55,223 | +66.19% | skill 1/1; base 1/1 |
| codex | video-report-hitl-on-policy | 86,437 | 102,290 | -15,853 | -15.50% | skill 1/1; base 1/1 |
| codex | video-report-hitl-stall-guard | 49,943 | 40,732 | +9,211 | +22.61% | skill 1/1; base 1/1 |
| codex | video-report-local-or-base64-path | 131,633 | 69,495 | +62,138 | +89.41% | skill 1/1; base 1/1 |
| codex | video-report-long-video-lvs-handoff | 50,419 | 88,494 | -38,075 | -43.03% | skill 1/1; base 1/1 |
| codex | video-report-profile-agnostic-mode-a | 94,867 | 80,018 | +14,849 | +18.56% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-a | 13,385 | 13,455 | -70 | -0.52% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-b | 13,359 | 13,368 | -9 | -0.07% | skill 1/1; base 1/1 |
| codex | video-report-step3-no-vss-agent-fallback | 152,023 | 54,551 | +97,472 | +178.68% | skill 1/1; base 1/1 |
| codex | video-report-vlm-unclear-options | 79,977 | 73,679 | +6,298 | +8.55% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 2,403,168 | 3,094,741 | -691,573 | -22.35% | skill 28/28; base 28/28 |

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
