# Skill Benchmark: vss-search-archive

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-search-archive`
- Evaluation date: 2026-10-09
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 18 evaluation tasks (14 positive, 4 negative)
- Dataset digest: `sha256:012255238e0a0bfbd38be26e18659636ecf25d22d8d4fb51e274d34957eeb855` (skill-evaluator-dataset-snapshot/1)
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
| Overall | Not available | 66.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | Not available | 63.9% → 88.9% (+25.0 points) |
| Correctness | Not available | 31.1% → 48.9% (+17.8 points) |
| Discoverability | Not available | 76.4% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | Not available | 30.5% → 25.2% (-5.3 points) |
| Efficiency | Not available | 91.8% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 5,033,888 | 4,023,004 | +1,010,884 | +25.13% | skill 18/18; base 18/18 |
| claude-code | search-archive | 363,010 | 735,715 | -372,705 | -50.66% | skill 1/1; base 1/1 |
| claude-code | search-archive-attribute-flags | 443,721 | 696,363 | -252,642 | -36.28% | skill 1/1; base 1/1 |
| claude-code | search-archive-confirm-verification | 299,876 | 168,969 | +130,907 | +77.47% | skill 1/1; base 1/1 |
| claude-code | search-archive-delete-only | 346,235 | 180,845 | +165,390 | +91.45% | skill 1/1; base 1/1 |
| claude-code | search-archive-exit2-help | 155,971 | 59,935 | +96,036 | +160.23% | skill 1/1; base 1/1 |
| claude-code | search-archive-exit6-partial | 130,673 | 150,525 | -19,852 | -13.19% | skill 1/1; base 1/1 |
| claude-code | search-archive-exit6-without-data | 124,652 | 181,353 | -56,701 | -31.27% | skill 1/1; base 1/1 |
| claude-code | search-archive-fusion-decompose | 513,558 | 384,935 | +128,623 | +33.41% | skill 1/1; base 1/1 |
| claude-code | search-archive-implicit | 556,208 | 244,895 | +311,313 | +127.12% | skill 1/1; base 1/1 |
| claude-code | search-archive-ingest-only | 29,773 | 183,837 | -154,064 | -83.80% | skill 1/1; base 1/1 |
| claude-code | search-archive-missing-source | 431,075 | 153,477 | +277,598 | +180.87% | skill 1/1; base 1/1 |
| claude-code | search-archive-mixed-upload-and-stream | 436,031 | 215,029 | +221,002 | +102.78% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-direct-video-qa | 214,468 | 119,101 | +95,367 | +80.07% | skill 1/1; base 1/1 |
| claude-code | search-archive-negative-summary | 29,981 | 121,392 | -91,411 | -75.30% | skill 1/1; base 1/1 |
| claude-code | search-archive-object-flags | 472,249 | 183,314 | +288,935 | +157.62% | skill 1/1; base 1/1 |
| claude-code | search-archive-partially-verified | 29,800 | 29,580 | +220 | +0.74% | skill 1/1; base 1/1 |
| claude-code | search-archive-results-first | 396,337 | 153,871 | +242,466 | +157.58% | skill 1/1; base 1/1 |
| claude-code | search-archive-rtsp-live-stream | 60,270 | 59,868 | +402 | +0.67% | skill 1/1; base 1/1 |
| codex | All cases | 4,899,836 | 5,588,585 | -688,749 | -12.32% | skill 18/18; base 18/18 |
| codex | search-archive | 240,005 | 753,896 | -513,891 | -68.16% | skill 1/1; base 1/1 |
| codex | search-archive-attribute-flags | 113,859 | 325,562 | -211,703 | -65.03% | skill 1/1; base 1/1 |
| codex | search-archive-confirm-verification | 105,570 | 362,579 | -257,009 | -70.88% | skill 1/1; base 1/1 |
| codex | search-archive-delete-only | 1,971,004 | 614,993 | +1,356,011 | +220.49% | skill 1/1; base 1/1 |
| codex | search-archive-exit2-help | 13,964 | 13,781 | +183 | +1.33% | skill 1/1; base 1/1 |
| codex | search-archive-exit6-partial | 47,147 | 91,890 | -44,743 | -48.69% | skill 1/1; base 1/1 |
| codex | search-archive-exit6-without-data | 30,087 | 105,673 | -75,586 | -71.53% | skill 1/1; base 1/1 |
| codex | search-archive-fusion-decompose | 175,941 | 106,566 | +69,375 | +65.10% | skill 1/1; base 1/1 |
| codex | search-archive-implicit | 79,895 | 70,517 | +9,378 | +13.30% | skill 1/1; base 1/1 |
| codex | search-archive-ingest-only | 1,621,927 | 679,112 | +942,815 | +138.83% | skill 1/1; base 1/1 |
| codex | search-archive-missing-source | 46,254 | 55,406 | -9,152 | -16.52% | skill 1/1; base 1/1 |
| codex | search-archive-mixed-upload-and-stream | 46,775 | 500,431 | -453,656 | -90.65% | skill 1/1; base 1/1 |
| codex | search-archive-negative-direct-video-qa | 55,110 | 54,918 | +192 | +0.35% | skill 1/1; base 1/1 |
| codex | search-archive-negative-summary | 132,696 | 87,487 | +45,209 | +51.68% | skill 1/1; base 1/1 |
| codex | search-archive-object-flags | 63,041 | 1,655,204 | -1,592,163 | -96.19% | skill 1/1; base 1/1 |
| codex | search-archive-partially-verified | 13,466 | 13,325 | +141 | +1.06% | skill 1/1; base 1/1 |
| codex | search-archive-results-first | 96,517 | 70,078 | +26,439 | +37.73% | skill 1/1; base 1/1 |
| codex | search-archive-rtsp-live-stream | 46,578 | 27,167 | +19,411 | +71.45% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 9,933,724 | 9,611,589 | +322,135 | +3.35% | skill 36/36; base 36/36 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 15 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED** | 2 validator(s); 0 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 18 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** QUALITY/quality_correctness: No documented scripts in table format (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** QUALITY/quality_correctness: Instructions don't mention 'run_script' (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Deeply nested references in cli_usage.md (`skills/operations/vss-search-archive/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-search-archive`)
- **MEDIUM** SCHEMA/body_recommended_section: Missing recommended section: '## Instructions' (`skills/operations/vss-search-archive/SKILL.md`)
- 10 additional finding(s) are available in the full evaluation artifacts.

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
