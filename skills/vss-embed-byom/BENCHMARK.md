# Evaluation Report

Evaluation plan for the `vss-embed-byom` skill before publication through
NVSkills-Eval.

This benchmark documents the intended validation coverage for the VideoPrism
BYOM skill. It should be refreshed with measured NVSkills-Eval results after the
new routing spec runs in CI.

## Evaluation Summary

- Skill: `vss-embed-byom`
- Evaluation date: 2026-07-29
- NVSkills-Eval profile: `external`
- Environment: pending CI execution
- Dataset: 1 routing evaluation spec
- Attempts per task: 1
- Pass threshold: 50%
- Overall verdict: PENDING

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

Underlying evaluation signals expected for this run:

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

Pending CI execution.

## Tier 1: Static Validation Summary

Pending NVSkills-Eval execution.

## Tier 2: Deduplication Summary

Pending NVSkills-Eval execution.

## Publication Recommendation

Pending. Refresh this file with measured results after CI evaluates
`skills/vss-embed-byom/evals/routing.json`.
