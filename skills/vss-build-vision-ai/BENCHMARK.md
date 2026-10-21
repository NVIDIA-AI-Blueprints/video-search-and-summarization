# Skill Benchmark: vss-build-vision-ai

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-build-vision-ai`
- Evaluation date: 2026-10-21
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 11 evaluation tasks (10 positive, 1 negative)
- Dataset digest: `sha256:726fa1cf2863b647abfdc7459b34909f535f7558068c245123a9a1ce9f1bf28e` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 65.2% — baseline ran, but no comparable score was available; uplift unavailable | 67.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 77.3% → 54.6% (-22.7 points) | 40.9% → 68.2% (+27.3 points) |
| Correctness | 27.3% → 67.3% (+40.0 points) | 52.7% → 78.2% (+25.5 points) |
| Discoverability | 98.0% — baseline ran, but no comparable score was available; uplift unavailable | 76.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 10.8% → 23.7% (+12.9 points) | 17.6% → 33.3% (+15.7 points) |
| Efficiency | 82.5% — baseline ran, but no comparable score was available; uplift unavailable | 79.7% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 10,019,554 | 9,463,564 | +555,990 | +5.88% | skill 11/11; base 11/11 |
| claude-code | build-alerts-gb300 | 219,731 | 216,506 | +3,225 | +1.49% | skill 1/1; base 1/1 |
| claude-code | build-base-default-tag | 658,323 | 182,143 | +476,180 | +261.43% | skill 1/1; base 1/1 |
| claude-code | build-base-gb300 | 817,475 | 1,049,947 | -232,472 | -22.14% | skill 1/1; base 1/1 |
| claude-code | build-base-gb300-release-tag | 590,649 | 2,398,736 | -1,808,087 | -75.38% | skill 1/1; base 1/1 |
| claude-code | build-base-harness-default-ref | 745,123 | 149,395 | +595,728 | +398.76% | skill 1/1; base 1/1 |
| claude-code | build-base-harness-ref | 807,429 | 370,231 | +437,198 | +118.09% | skill 1/1; base 1/1 |
| claude-code | build-base-release-tag | 758,178 | 279,142 | +479,036 | +171.61% | skill 1/1; base 1/1 |
| claude-code | build-lvs-gb300 | 1,948,696 | 1,692,276 | +256,420 | +15.15% | skill 1/1; base 1/1 |
| claude-code | build-search-gb300 | 2,256,097 | 183,966 | +2,072,131 | +1126.37% | skill 1/1; base 1/1 |
| claude-code | build-search-profile | 493,256 | 2,755,797 | -2,262,541 | -82.10% | skill 1/1; base 1/1 |
| claude-code | search-running-archive | 724,597 | 185,425 | +539,172 | +290.78% | skill 1/1; base 1/1 |
| codex | All cases | 5,837,280 | 8,239,065 | -2,401,785 | -29.15% | skill 11/11; base 11/11 |
| codex | build-alerts-gb300 | 230,189 | 634,075 | -403,886 | -63.70% | skill 1/1; base 1/1 |
| codex | build-base-default-tag | 228,970 | 616,108 | -387,138 | -62.84% | skill 1/1; base 1/1 |
| codex | build-base-gb300 | 347,362 | 120,936 | +226,426 | +187.23% | skill 1/1; base 1/1 |
| codex | build-base-gb300-release-tag | 592,785 | 2,042,654 | -1,449,869 | -70.98% | skill 1/1; base 1/1 |
| codex | build-base-harness-default-ref | 490,228 | 405,865 | +84,363 | +20.79% | skill 1/1; base 1/1 |
| codex | build-base-harness-ref | 436,005 | 159,815 | +276,190 | +172.82% | skill 1/1; base 1/1 |
| codex | build-base-release-tag | 190,881 | 904,706 | -713,825 | -78.90% | skill 1/1; base 1/1 |
| codex | build-lvs-gb300 | 609,308 | 1,459,372 | -850,064 | -58.25% | skill 1/1; base 1/1 |
| codex | build-search-gb300 | 397,459 | 146,589 | +250,870 | +171.14% | skill 1/1; base 1/1 |
| codex | build-search-profile | 1,522,438 | 1,679,552 | -157,114 | -9.35% | skill 1/1; base 1/1 |
| codex | search-running-archive | 791,655 | 69,393 | +722,262 | +1040.83% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 15,856,834 | 17,702,629 | -1,845,795 | -10.43% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 171 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED WITH OBSERVATIONS** | 2 validator(s); 1 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **CRITICAL** CONTENT_DEDUP/chunk_count_limit: Tier 2 produced more than 512 content chunks. (`references/services/sop/build-ds-sop.md`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/prerequisites.md:213`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/prerequisites.md:220`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/prerequisites.md:221`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/troubleshooting.md:130`)
- 167 additional finding(s) are available in the full evaluation artifacts.

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
