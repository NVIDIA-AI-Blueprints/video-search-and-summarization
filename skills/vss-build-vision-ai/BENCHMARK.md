# Skill Benchmark: vss-build-vision-ai

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-build-vision-ai`
- Evaluation date: 2026-10-02
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
| Overall | 67.8% — baseline ran, but no comparable score was available; uplift unavailable | 61.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 77.3% → 68.2% (-9.1 points) | 36.4% → 54.6% (+18.2 points) |
| Correctness | 21.8% → 69.1% (+47.3 points) | 60.0% → 70.9% (+10.9 points) |
| Discoverability | 99.1% — baseline ran, but no comparable score was available; uplift unavailable | 78.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 10.3% → 22.2% (+11.9 points) | 21.4% → 29.1% (+7.7 points) |
| Efficiency | 80.4% — baseline ran, but no comparable score was available; uplift unavailable | 74.4% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 8,083,494 | 7,300,078 | +783,416 | +10.73% | skill 11/11; base 11/11 |
| claude-code | build-alerts-gb300 | 281,029 | 216,231 | +64,798 | +29.97% | skill 1/1; base 1/1 |
| claude-code | build-base-default-tag | 708,157 | 184,749 | +523,408 | +283.31% | skill 1/1; base 1/1 |
| claude-code | build-base-gb300 | 782,252 | 299,995 | +482,257 | +160.76% | skill 1/1; base 1/1 |
| claude-code | build-base-gb300-release-tag | 868,509 | 293,412 | +575,097 | +196.00% | skill 1/1; base 1/1 |
| claude-code | build-base-harness-default-ref | 516,926 | 149,554 | +367,372 | +245.65% | skill 1/1; base 1/1 |
| claude-code | build-base-harness-ref | 677,869 | 157,611 | +520,258 | +330.09% | skill 1/1; base 1/1 |
| claude-code | build-base-release-tag | 708,030 | 292,874 | +415,156 | +141.75% | skill 1/1; base 1/1 |
| claude-code | build-lvs-gb300 | 609,666 | 3,911,872 | -3,302,206 | -84.41% | skill 1/1; base 1/1 |
| claude-code | build-search-gb300 | 1,593,435 | 153,350 | +1,440,085 | +939.08% | skill 1/1; base 1/1 |
| claude-code | build-search-profile | 1,026,232 | 1,425,672 | -399,440 | -28.02% | skill 1/1; base 1/1 |
| claude-code | search-running-archive | 311,389 | 214,758 | +96,631 | +45.00% | skill 1/1; base 1/1 |
| codex | All cases | 8,377,138 | 9,814,179 | -1,437,041 | -14.64% | skill 11/11; base 11/11 |
| codex | build-alerts-gb300 | 313,421 | 1,807,057 | -1,493,636 | -82.66% | skill 1/1; base 1/1 |
| codex | build-base-default-tag | 564,319 | 1,089,830 | -525,511 | -48.22% | skill 1/1; base 1/1 |
| codex | build-base-gb300 | 315,325 | 665,672 | -350,347 | -52.63% | skill 1/1; base 1/1 |
| codex | build-base-gb300-release-tag | 298,409 | 1,366,973 | -1,068,564 | -78.17% | skill 1/1; base 1/1 |
| codex | build-base-harness-default-ref | 140,384 | 161,159 | -20,775 | -12.89% | skill 1/1; base 1/1 |
| codex | build-base-harness-ref | 1,458,600 | 1,495,484 | -36,884 | -2.47% | skill 1/1; base 1/1 |
| codex | build-base-release-tag | 1,426,863 | 1,091,714 | +335,149 | +30.70% | skill 1/1; base 1/1 |
| codex | build-lvs-gb300 | 1,231,393 | 495,356 | +736,037 | +148.59% | skill 1/1; base 1/1 |
| codex | build-search-gb300 | 416,753 | 98,213 | +318,540 | +324.34% | skill 1/1; base 1/1 |
| codex | build-search-profile | 1,484,978 | 1,472,175 | +12,803 | +0.87% | skill 1/1; base 1/1 |
| codex | search-running-archive | 726,693 | 70,546 | +656,147 | +930.10% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 16,460,632 | 17,114,257 | -653,625 | -3.82% | skill 22/22; base 22/22 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 277 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED WITH OBSERVATIONS** | 2 validator(s); 1 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 11 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **CRITICAL** CONTENT_DEDUP/chunk_count_limit: Tier 2 produced more than 512 content chunks. (`references/services/sop/integrate-ds-sop.md`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/prerequisites.md:213`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/prerequisites.md:220`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/prerequisites.md:221`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/troubleshooting.md:130`)
- 273 additional finding(s) are available in the full evaluation artifacts.

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
