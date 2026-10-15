# Skill Benchmark: vss-generate-video-report

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-generate-video-report`
- Evaluation date: 2026-10-15
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
| Overall | 89.7% — baseline ran, but no comparable score was available; uplift unavailable | 89.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 14.3% → 92.9% (+78.6 points) | 52.9% → 90.0% (+37.1 points) |
| Discoverability | 83.1% — baseline ran, but no comparable score was available; uplift unavailable | 81.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 12.3% → 81.0% (+68.7 points) | 23.3% → 78.4% (+55.1 points) |
| Efficiency | 91.6% — baseline ran, but no comparable score was available; uplift unavailable | 97.2% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 1,357,944 | 1,930,352 | -572,408 | -29.65% | skill 14/14; base 14/14 |
| claude-code | summarize-without-report-non-activation | 121,751 | 88,637 | +33,114 | +37.36% | skill 1/1; base 1/1 |
| claude-code | video-report-empty-range-policy | 122,426 | 89,976 | +32,450 | +36.07% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-harness-fallback | 106,826 | 149,801 | -42,975 | -28.69% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-multi-edit-loop | 199,827 | 150,423 | +49,404 | +32.84% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-off-policy | 74,745 | 120,429 | -45,684 | -37.93% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-on-policy | 74,629 | 279,837 | -205,208 | -73.33% | skill 1/1; base 1/1 |
| claude-code | video-report-hitl-stall-guard | 74,497 | 210,164 | -135,667 | -64.55% | skill 1/1; base 1/1 |
| claude-code | video-report-local-or-base64-path | 135,064 | 152,009 | -16,945 | -11.15% | skill 1/1; base 1/1 |
| claude-code | video-report-long-video-lvs-handoff | 74,491 | 209,477 | -134,986 | -64.44% | skill 1/1; base 1/1 |
| claude-code | video-report-profile-agnostic-mode-a | 74,488 | 119,605 | -45,117 | -37.72% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-a | 29,441 | 29,363 | +78 | +0.27% | skill 1/1; base 1/1 |
| claude-code | video-report-routing-mode-b | 29,263 | 29,442 | -179 | -0.61% | skill 1/1; base 1/1 |
| claude-code | video-report-step3-no-vss-agent-fallback | 134,622 | 149,854 | -15,232 | -10.16% | skill 1/1; base 1/1 |
| claude-code | video-report-vlm-unclear-options | 105,874 | 151,335 | -45,461 | -30.04% | skill 1/1; base 1/1 |
| codex | All cases | 1,174,210 | 882,268 | +291,942 | +33.09% | skill 14/14; base 14/14 |
| codex | summarize-without-report-non-activation | 27,151 | 26,781 | +370 | +1.38% | skill 1/1; base 1/1 |
| codex | video-report-empty-range-policy | 79,752 | 82,937 | -3,185 | -3.84% | skill 1/1; base 1/1 |
| codex | video-report-hitl-harness-fallback | 61,390 | 87,641 | -26,251 | -29.95% | skill 1/1; base 1/1 |
| codex | video-report-hitl-multi-edit-loop | 133,983 | 40,777 | +93,206 | +228.57% | skill 1/1; base 1/1 |
| codex | video-report-hitl-off-policy | 191,056 | 72,830 | +118,226 | +162.33% | skill 1/1; base 1/1 |
| codex | video-report-hitl-on-policy | 111,237 | 73,315 | +37,922 | +51.72% | skill 1/1; base 1/1 |
| codex | video-report-hitl-stall-guard | 69,244 | 54,402 | +14,842 | +27.28% | skill 1/1; base 1/1 |
| codex | video-report-local-or-base64-path | 74,432 | 54,927 | +19,505 | +35.51% | skill 1/1; base 1/1 |
| codex | video-report-long-video-lvs-handoff | 50,496 | 87,380 | -36,884 | -42.21% | skill 1/1; base 1/1 |
| codex | video-report-profile-agnostic-mode-a | 124,150 | 65,873 | +58,277 | +88.47% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-a | 13,356 | 13,430 | -74 | -0.55% | skill 1/1; base 1/1 |
| codex | video-report-routing-mode-b | 30,046 | 40,649 | -10,603 | -26.08% | skill 1/1; base 1/1 |
| codex | video-report-step3-no-vss-agent-fallback | 69,756 | 83,036 | -13,280 | -15.99% | skill 1/1; base 1/1 |
| codex | video-report-vlm-unclear-options | 138,161 | 98,290 | +39,871 | +40.56% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 2,532,154 | 2,812,620 | -280,466 | -9.97% | skill 28/28; base 28/28 |

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
