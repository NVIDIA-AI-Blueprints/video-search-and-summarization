# Evaluation Report

Evaluation of the `vss-deploy-detection-tracking-3d` skill before publication through NVSkills-Eval.

This benchmark summarizes 3-Tier Evaluation from NVSkills-Eval results for the skill. The goal is to document whether the skill is safe, discoverable, effective, and useful for agents before it is published for broader workflow use.

## Evaluation Summary

- Skill: `vss-deploy-detection-tracking-3d`
- Evaluation date: 2026-06-07
- NVSkills-Eval profile: `external`
- Environment: `astra-sandbox`
- Dataset: 6 evaluation tasks
- Attempts per task: 2
- Pass threshold: 50%
- Overall verdict: FAIL
The skill should be reviewed before NVSkills-Eval publication. **Skill owners should address the applicable findings below and rerun NVSkills-Eval to refresh this benchmark.**

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

- `security` (Security): checks for unsafe operations, secret leakage, and unauthorized access.
- `skill_execution` (Skill Execution): verifies that the agent loaded the expected skill and workflow.
- `skill_efficiency` (Efficiency): checks routing quality, decoy avoidance, and redundant tool usage.
- `accuracy` (Accuracy): grades final-answer correctness against the reference answer.
- `goal_accuracy` (Goal Accuracy): checks whether the overall user task completed successfully.
- `behavior_check` (Behavior Check): verifies expected behavior steps, including safety expectations.
- `token_efficiency` (Token Efficiency): compares token usage with and without the skill.

## Test Tasks

The benchmark dataset contained 6 evaluation tasks:

- Positive tasks: 6 tasks where the skill was expected to activate.
- Negative tasks: 0 tasks where no skill was expected.
- Unlabeled tasks: 0 tasks where positive/negative intent could not be inferred.

Task composition is derived from the evaluation dataset when possible. Entries with `expected_skill` set are treated as positive skill-activation cases, while entries with `expected_skill: null` are treated as negative activation cases.

## Results

| Dimension | Num | `claude-code` | `codex` |
|---|---:|---:|---:|
| Security | 8 | 100% (+0%) | 100% (+0%) |
| Correctness | 8 | 84% (+40%) | 73% (+33%) |
| Discoverability | 8 | 63% (+19%) | 55% (+11%) |
| Effectiveness | 8 | 79% (+58%) | 61% (+40%) |
| Efficiency | 8 | 58% (+22%) | 43% (+4%) |

Score values show skill-assisted performance. Values in parentheses show uplift versus the no-skill baseline when baseline data is available.

## Tier 1: Static Validation Summary

Tier 1 validation passed with observations. NVSkills-Eval ran 9 checks and found 9 total findings.

Top findings:

- MEDIUM QUALITY/quality_correctness: SKILL_SPEC recommended field missing: 'metadata.author' (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)
- MEDIUM QUALITY/quality_efficiency: Deeply nested references in teardown.md (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)
- MEDIUM SCHEMA/body_recommended_section: Missing recommended section: '## Examples' (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)
- MEDIUM SCHEMA/author_missing: Author not specified in metadata (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)
- LOW QUALITY/quality_discoverability: Description very long (417 chars, recommend 50-150) (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)

## Tier 2: Deduplication Summary

Tier 2 validation reported findings. NVSkills-Eval ran 2 checks and found 1 total findings.

Top findings:

- HIGH DUPLICATE/duplicate: Duplicate content found across SKILL.md and references/configure-cameras.md and references/deploy-rtvi-cv-3d-stack.md:
  "### Q2 — Calibration coverage (skip for `sample`)" in SKILL.md (lines 39-46)
  vs "## Step 1 — Count cameras from `calibration.json`" in references/configure-cameras.md (lines 103-108)
  vs "# 3. Calibration mount" in references/deploy-rtvi-cv-3d-stack.md (lines 65-70) (`SKILL.md:39`)
