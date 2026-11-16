# Skill Benchmark: vss-manage-video-io-storage

> ⚠️ **Overall verdict: INCOMPLETE — Required evidence is missing**

One or more required evaluation tiers did not complete, so this benchmark is not publication-complete.

## Evaluation Metadata

- Skill: `vss-manage-video-io-storage`
- Validation status: `incomplete`
- Evaluation date: 2026-11-16
- Evaluator version: `1.6.9`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-5-5`), Codex (`openai/openai/gpt-6.1-sol`)
- Tasks: 9 evaluation tasks (7 positive, 2 negative)
- Dataset digest: `sha256:8030d95b726d43132c18791ea680c15abd71a503eaf71342860ee75fd5719423` (skill-evaluator-dataset-snapshot/1)
- Attempts per task: 1
- Environment: `k8s-sandbox`
- Tier 2 evidence: required for publication
- Tier 3 evidence: required for publication

Each task attempt ran in its own isolated sandbox pod.

## Tier 1 LLM Provenance

SkillSpector provider, model, analyzer revision, and determinism evidence retained for this scan.

| Skill | Provider | Adapter | Analyzer | Model | Revision | Determinism |
| --- | --- | --- | --- | --- | --- | --- |
| vss-manage-video-io-storage | nv_inference | openai | mcp_tool_poisoning | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-video-io-storage | nv_inference | openai | semantic_developer_intent | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-video-io-storage | nv_inference | openai | semantic_quality_policy | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-video-io-storage | nv_inference | openai | semantic_security_discovery | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |
| vss-manage-video-io-storage | nv_inference | openai | meta_analyzer | `aws/anthropic/bedrock-claude-opus-5-5` | `2.13.4` | nondeterministic / provider_defaults |

- Controls for `vss-manage-video-io-storage`: temperature=not set (source=provider_default, forwarded=no); seed=not set (source=unset, forwarded=no); reasoning_effort=not set (source=provider_default, forwarded=no)

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
| Overall | 93.9% — baseline ran, but no comparable score was available; uplift unavailable | 92.8% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 100.0% → 100.0% (±0.0 points) |
| Correctness | 66.7% → 100.0% (+33.3 points) | 97.8% → 100.0% (+2.2 points) |
| Discoverability | 100.0% — baseline ran, but no comparable score was available; uplift unavailable | 95.0% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 38.0% → 75.9% (+37.9 points) | 74.1% → 75.0% (+0.9 points) |
| Efficiency | 93.4% — baseline ran, but no comparable score was available; uplift unavailable | 94.0% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

### Comparison details

Shared-source comparisons use the same signals and weights on both conditions. They do not replace the full dimension scores above or change the publication verdict.

| Agent | Dimension | Basis | Baseline → With skill (uplift) | Explanation |
| --- | --- | --- | --- | --- |
| claude-code | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| claude-code | Efficiency | Token-only comparison | 99.3% → 95.2% (-4.1 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |
| codex | Discoverability | Dimension comparison | N/A | Discoverability is not applicable to the no-skill baseline; there is no installed skill to discover. |
| codex | Efficiency | Token-only comparison | 88.5% → 91.6% (+3.1 points) | Only Token Efficiency is comparable; the full dimension scores use different evaluator sources. |

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 843,700 | 747,983 | +95,717 | +12.80% | skill 9/9; base 9/9 |
| claude-code | archive-search-non-activation | 35,618 | 70,857 | -35,239 | -49.73% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-delete-stream | 81,380 | 106,233 | -24,853 | -23.39% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-libav-failure | 178,632 | 108,226 | +70,406 | +65.05% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-not-configured | 81,826 | 70,956 | +10,870 | +15.32% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-nvstreamer-missing-url | 82,223 | 106,810 | -24,587 | -23.02% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-nvstreamer-upload | 183,922 | 71,899 | +112,023 | +155.81% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-plain-upload | 81,652 | 70,717 | +10,935 | +15.46% | skill 1/1; base 1/1 |
| claude-code | video-io-storage-vios-routing | 82,892 | 71,394 | +11,498 | +16.10% | skill 1/1; base 1/1 |
| claude-code | video-question-non-activation | 35,555 | 70,891 | -35,336 | -49.85% | skill 1/1; base 1/1 |
| codex | All cases | 411,107 | 450,731 | -39,624 | -8.79% | skill 9/9; base 9/9 |
| codex | archive-search-non-activation | 13,421 | 58,905 | -45,484 | -77.22% | skill 1/1; base 1/1 |
| codex | video-io-storage-delete-stream | 29,862 | 42,321 | -12,459 | -29.44% | skill 1/1; base 1/1 |
| codex | video-io-storage-libav-failure | 70,970 | 74,279 | -3,309 | -4.45% | skill 1/1; base 1/1 |
| codex | video-io-storage-not-configured | 46,473 | 52,704 | -6,231 | -11.82% | skill 1/1; base 1/1 |
| codex | video-io-storage-nvstreamer-missing-url | 66,136 | 45,275 | +20,861 | +46.08% | skill 1/1; base 1/1 |
| codex | video-io-storage-nvstreamer-upload | 73,690 | 81,431 | -7,741 | -9.51% | skill 1/1; base 1/1 |
| codex | video-io-storage-plain-upload | 29,959 | 13,349 | +16,610 | +124.43% | skill 1/1; base 1/1 |
| codex | video-io-storage-vios-routing | 67,201 | 43,297 | +23,904 | +55.21% | skill 1/1; base 1/1 |
| codex | video-question-non-activation | 13,395 | 39,170 | -25,775 | -65.80% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 1,254,807 | 1,198,714 | +56,093 | +4.68% | skill 18/18; base 18/18 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. Change is `100 * (with - without) / without`; negative means fewer tokens. N/A comparisons include their reason in the coverage column; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 46 finding(s) |
| Tier 2 | Semantic deduplication | **INCOMPLETE** | Tier 2 scan incomplete: a content cluster has more than 64 members. |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 9 task(s) |

## Incomplete Coverage

- **Context Deduplication:** Tier 2 scan incomplete: a content cluster has more than 64 members.

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **MEDIUM** PII/ip_addresses: Public IP address (`references/deploy-vios-service.md:141`)
- **MEDIUM** PII/ip_addresses: Public IP address (`references/deploy-vios-service.md:144`)
- **MEDIUM** QUALITY/quality_correctness: SKILL_SPEC recommended field missing: 'metadata.author' (`skills/operations/vss-manage-video-io-storage/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (6988 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-manage-video-io-storage/SKILL.md`)
- **MEDIUM** QUALITY/quality_efficiency: Deeply nested references in provision-vios-source.md (`skills/operations/vss-manage-video-io-storage/SKILL.md`)
- 41 additional finding(s) are available in the full evaluation artifacts.

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
