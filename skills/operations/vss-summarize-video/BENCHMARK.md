# Skill Benchmark: vss-summarize-video

> ✅ **Overall verdict: PASS — Recommended for publication**

## Publication Recommendation

Recommended for publication based on the completed evaluation evidence in this report.

## Evaluation Metadata

- Skill: `vss-summarize-video`
- Evaluation date: 2026-10-05
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
| Overall | Not available | 78.2% — baseline ran, but no comparable score was available; uplift unavailable |
| Security | Not available | 33.3% → 100.0% (+66.7 points) |
| Correctness | Not available | 46.7% → 53.3% (+6.6 points) |
| Discoverability | Not available | 92.5% — baseline ran, but no comparable score was available; uplift unavailable |
| Effectiveness | Not available | 38.6% → 48.6% (+10.0 points) |
| Efficiency | Not available | 96.6% — baseline ran, but no comparable score was available; uplift unavailable |

**How to read this table:** baseline is the same task attempted without the target skill. Scores are rounded to one decimal; threshold-adjacent values use additional precision so their displayed band matches the verdict. Uplift is derived from those displayed scores and shown in percentage points.

Example: `47.0% → 92.0% (+45.0 points)` means the skill-assisted run scored 92.0%, 45.0 percentage points above its 47.0% no-skill baseline.

A partial dimension was calculated from only the available configured signals; review the detailed report before relying on it.

## Token Usage

Actual Tier 3 execution usage is reported for every observed agent/case pair and both conditions.

| Agent | Dataset case | With skill | Without skill | Delta | Change | Coverage |
|---|---|---:|---:|---:|---:|---|
| claude-code | All cases | 1,336,244 | 1,952,919 | -616,675 | -31.58% | skill 3/3; base 3/3 |
| claude-code | summarize-video | 499,762 | 1,646,537 | -1,146,775 | -69.65% | skill 1/1; base 1/1 |
| claude-code | summarize-video-negative-archive-search | 406,496 | 122,179 | +284,317 | +232.71% | skill 1/1; base 1/1 |
| claude-code | summarize-video-no-lvs-vlm-fallback | 429,986 | 184,203 | +245,783 | +133.43% | skill 1/1; base 1/1 |
| codex | All cases | 384,277 | 908,141 | -523,864 | -57.69% | skill 3/3; base 3/3 |
| codex | summarize-video | 46,571 | 292,192 | -245,621 | -84.06% | skill 1/1; base 1/1 |
| codex | summarize-video-negative-archive-search | 160,426 | 86,335 | +74,091 | +85.82% | skill 1/1; base 1/1 |
| codex | summarize-video-no-lvs-vlm-fallback | 177,280 | 529,614 | -352,334 | -66.53% | skill 1/1; base 1/1 |
| ALL AGENTS | Dataset aggregate | 1,720,521 | 2,861,060 | -1,140,539 | -39.86% | skill 6/6; base 6/6 |

Prompt tokens include cached reads, so total tokens are `prompt + completion` (cached is not added twice). The Efficiency score uses `(prompt - cached) + completion`. N/A means the relevant trajectory counters were not available; coverage is never estimated.

## Tier Status

| Tier | Purpose | Status | Evidence |
|---|---|---|---|
| Tier 1 | Static validation | **PASSED WITH OBSERVATIONS** | 11 validator(s); 13 finding(s) |
| Tier 2 | Semantic deduplication | **PASSED WITH OBSERVATIONS** | 2 validator(s); 2 finding(s) |
| Tier 3 | Live agent evaluation | **PASS** | 2 agent(s); 3 task(s) |

## Findings and Observations

<details>
<summary>Show detailed findings and successful checks</summary>

- **CRITICAL** CONTENT_DEDUP/llm_error: LLM analysis failed for a content cluster (`skills/operations/vss-summarize-video`)
- **HIGH** DUPLICATE/duplicate: Duplicate content found across references/hitl-prompts.md and references/video-summarization-api.md:
  "### HITL: collect scenario and events first (REQUIRED — do not skip)" in references/hitl-prompts.md (lines 88-91)
  vs "## File Summarization" in references/video-summarization-api.md (lines 196-198) (`references/hitl-prompts.md:88`)
- **MEDIUM** QUALITY/quality_efficiency: Large skill (5303 tokens, recommended max <5000). Per agentskills.io, SKILL.md should be concise (~500 lines) — large skill bodies increase token cost after invocation; long or unfocused top-level descriptions can degrade agent routing accuracy (`skills/operations/vss-summarize-video/SKILL.md`)
- **MEDIUM** SCHEMA/folder_hierarchy: Unexpected nesting depth for general skill (`skills/operations/vss-summarize-video`)
- **MEDIUM** SECURITY/Unknown (RP1): MCP Rug Pull: Docker image references without a specific tag (:latest is implicit) or digest (@sha256:...) can be silently replaced by (`references/deploy-lvs-service.md:56`)
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
