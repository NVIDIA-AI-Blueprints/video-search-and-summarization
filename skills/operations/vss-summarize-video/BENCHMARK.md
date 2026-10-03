# Skill Benchmark: vss-summarize-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-summarize-video`
- Evaluation date: 2026-10-02
- Evaluator version: `1.5.6`
- Agents: Claude Code (`aws/anthropic/bedrock-claude-opus-4-8`), Codex (`openai/openai/gpt-5.5`)
- Tasks: 3 evaluation tasks (2 positive, 1 negative)
- Dataset digest: `sha256:2d3aa44da1d687045c1aa735919e28dc2ffee16c19aa3676de506299a16876bc` (skill-evaluator-dataset-snapshot/1)
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
| Overall | 79.8% — baseline ran, but no comparable score was available; uplift unavailable | 69.1% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | 100.0% → 100.0% (±0.0 points) | 33.3% → 66.7% (+33.4 points) |
| Correctness | 13.3% → 60.0% (+46.7 points) | 40.0% → 66.7% (+26.7 points) |
| Discoverability | 97.5% — baseline ran, but no comparable score was available; uplift unavailable | 82.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | 32.0% → 52.8% (+20.8 points) | 39.4% → 47.0% (+7.6 points) |
| Efficiency | 88.8% — baseline ran, but no comparable score was available; uplift unavailable | 82.6% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 831,232 | 535,097 | +296,135 | +55.34% | skill 3/3; base 3/3 |
| claude-code | summarize-video | 364,406 | 259,710 | +104,696 | +40.31% | skill 1/1; base 1/1 |
| claude-code | summarize-video-negative-archive-search | 206,854 | 59,570 | +147,284 | +247.25% | skill 1/1; base 1/1 |
| claude-code | summarize-video-no-lvs-vlm-fallback | 259,972 | 215,817 | +44,155 | +20.46% | skill 1/1; base 1/1 |
| codex | All cases | 1,272,672 | 706,920 | +565,752 | +80.03% | skill 3/3; base 3/3 |
| codex | summarize-video | 90,631 | 232,880 | -142,249 | -61.08% | skill 1/1; base 1/1 |
| codex | summarize-video-negative-archive-search | 117,297 | 70,253 | +47,044 | +66.96% | skill 1/1; base 1/1 |
| codex | summarize-video-no-lvs-vlm-fallback | 1,064,744 | 403,787 | +660,957 | +163.69% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 2,103,904 | 1,242,017 | +861,887 | +69.39% | skill 6/6; base 6/6 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 16 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED WITH OBSERVATIONS** | 2 validator(s); 4 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 3 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **CRITICAL** CONTENT_DEDUP/llm_error: LLM analysis failed for a content cluster (`skills/operations/vss-summarize-video`)
- **HIGH** DUPLICATE/duplicate: Duplicate content found across SKILL.md and references/end-to-end-example.md:
  "## Endpoint resolution (Kubernetes vs Docker)" in SKILL.md (lines 130-135)
  vs "# Prefer VSS_PUBLIC_URL; accept legacy VSS_ENDPOINT as the same public origin." in SKILL.md (lines 136-165)
  vs "### Resolve endpoints" in references/end-to-end-example.md (lines 14-48) (`SKILL.md:130`)
- **HIGH** DUPLICATE/duplicate: Duplicate content found across references/video-summarization-api.md and references/video-summarization-debugging.md and references/video-summarization-deployment.md:
  "## Models" in references/video-summarization-api.md (lines 122-131)
  vs "## Model Id Mismatch" in references/video-summarization-debugging.md (lines 40-51)
  vs "## Model Id Rule" in references/video-summarization-deployment.md (lines 265-275)
  vs "# RT-VLM model id" in references/video-summarization-deployment.md (lines 301-303) (`references/video-summarization-api.md:122`)
- **HIGH** DUPLICATE/duplicate: Duplicate content found across references/hitl-prompts.md and references/video-summarization-api.md:
  "### HITL: collect scenario and events first (REQUIRED — do not skip)" in references/hitl-prompts.md (lines 88-91)
  vs "## File Summarization" in references/video-summarization-api.md (lines 200-202) (`references/hitl-prompts.md:88`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (5346 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-summarize-video/SKILL.md`)
- 15 additional finding(s) are available in the full evaluation artifacts.

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
