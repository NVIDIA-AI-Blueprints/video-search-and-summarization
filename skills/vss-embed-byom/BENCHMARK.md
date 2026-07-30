# Evaluation Report

Measured NVSkills-Eval results for the `vss-embed-byom` skill.

## Evaluation Summary

- Skill: `vss-embed-byom`
- Evaluation date: 2026-07-30
- NVSkills-Eval profile: `external`
- Environment: GitHub Actions Skills Eval on `RTXPRO6000BW`
- Dataset: 1 routing evaluation spec with 2 queries
- Attempts per task: 1
- Pass threshold: 50%
- Overall verdict: PASS

## Agents Used

- `claude-code`
- `codex`

## Metrics Used

Reported benchmark dimensions:

- Security: checks whether skill-assisted execution avoids unsafe behavior such as secret leakage, destructive commands, or unauthorized access.
- Correctness: checks whether the agent follows the expected workflow and produces the correct final output.
- Discoverability: checks whether the agent loads the skill when relevant and avoids using it when irrelevant.
- Effectiveness: checks whether the agent performs measurably better with the skill than without it.
- Efficiency: checks whether the agent uses fewer tokens and avoids redundant work.

Underlying evaluation signals used in this run:

- `security`: checks for unsafe operations, secret leakage, and unauthorized access.
- `skill_execution`: verifies that the agent loaded the expected skill and workflow.
- `skill_efficiency`: checks routing quality, decoy avoidance, and redundant tool usage.
- `accuracy`: grades final-answer correctness against the reference answer.
- `goal_accuracy`: checks whether the overall user task completed successfully.
- `behavior_check`: verifies expected behavior steps, including safety expectations.
- `token_efficiency`: compares token usage with and without the skill.

## Test Tasks

The benchmark dataset contains 1 routing spec with 2 independent queries:

- Positive task: route VideoPrism RT-Embed BYOM integration to `vss-embed-byom`.
- Negative routing task: route default Cosmos-Embed1 RT-Embed deployment to `vss-deploy-video-embedding`, not `vss-embed-byom`.

## Results

| Platform | Step | Query | Result | Reward | Duration | Turns |
|---|---|---|---|---|---|---|
| `RTXPRO6000BW` | `step-1` | Integrate VideoPrism as custom BYOM backend | PASS `(5/5)` | `1.0` | `2m 48s` | `2` |
| `RTXPRO6000BW` | `step-2` | Deploy RT-Embed with default Cosmos-Embed1 | PASS `(3/3)` | `1.0` | `2m 01s` | `1` |

Both routing tasks exceeded the pass threshold with full reward.

## Tier 1: Static Validation Summary

Static skill checks completed with no blocking playbook errors. The compliance
checker reported one naming-convention warning for `vss-embed-byom`.

## Tier 2: Deduplication Summary

No duplicate-content failure was reported by the evaluated routing workflow.

## Publication Recommendation

Recommended for publication after the placeholder `skill.oms.sig` is replaced
with a valid NVIDIA OMS signature bundle.
