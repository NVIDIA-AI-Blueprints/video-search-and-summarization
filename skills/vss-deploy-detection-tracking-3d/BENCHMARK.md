# Evaluation Report

Evaluation of the `vss-deploy-detection-tracking-3d` skill before publication through NVSkills-Eval.

This benchmark summarizes 3-Tier Evaluation from NVSkills-Eval results for the skill. The goal is to document whether the skill is safe, discoverable, effective, and useful for agents before it is published for broader workflow use.

## Evaluation Summary

- Skill: `vss-deploy-detection-tracking-3d`
- Evaluation date: 2026-06-01
- NVSkills-Eval profile: `external`
- Environment: `local`
- Dataset: 4 evaluation tasks
- Attempts per task: 2
- Pass threshold: 50%
- Overall verdict: FAIL

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

The benchmark dataset contained 4 evaluation tasks:

- Positive tasks: 4 tasks where the skill was expected to activate.
- Negative tasks: 0 tasks where no skill was expected.
- Unlabeled tasks: 0 tasks where positive/negative intent could not be inferred.

Task composition is derived from the evaluation dataset when possible. Entries with `expected_skill` set are treated as positive skill-activation cases, while entries with `expected_skill: null` are treated as negative activation cases.

## Results

| Dimension | Num | `claude-code` | `codex` |
|---|---:|---:|---:|
| Security | 8 | 75% (+38%) | 75% (+25%) |
| Correctness | 8 | 82% (+14%) | 73% (+18%) |
| Discoverability | 8 | 86% (+15%) | 80% (+17%) |
| Effectiveness | 8 | 47% (-1%) | 45% (+15%) |
| Efficiency | 8 | 63% (+11%) | 70% (+26%) |

Score values show skill-assisted performance. Values in parentheses show uplift versus the no-skill baseline when baseline data is available.

## Tier 1: Static Validation Summary

Tier 1 validation passed with observations. NVSkills-Eval ran 9 checks and found 18 total findings.

Top findings:

- MEDIUM PII/gps_coordinates: GPS coordinates (location information) (`references/calibration-workflow.md:220`)
- MEDIUM PII/gps_coordinates: GPS coordinates (location information) (`references/calibration-workflow.md:225`)
- MEDIUM QUALITY/quality_correctness: SKILL_SPEC recommended field missing: 'metadata.author' (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)
- MEDIUM QUALITY/quality_discoverability: Description uses first/second person (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)
- MEDIUM QUALITY/quality_efficiency: Deeply nested references in troubleshooting.md (`skills/vss-deploy-detection-tracking-3d/SKILL.md`)

## Tier 2: Deduplication Summary

Tier 2 validation reported findings. NVSkills-Eval ran 2 checks and found 1 total findings.

Top findings:

- HIGH DUPLICATE/duplicate: Duplicate content found across SKILL.md and references/configure-cameras.md and references/deploy-rtvi-cv-3d-stack.md:
  "### Q2 — Calibration coverage (skip for `sample`)" in SKILL.md (lines 31-38)
  vs "## Step 1 — Count cameras from `calibration.json`" in references/configure-cameras.md (lines 103-108)
  vs "# 3. Calibration mount" in references/deploy-rtvi-cv-3d-stack.md (lines 65-70) (`SKILL.md:31`)

## Publication Recommendation

The skill should be reviewed before NVSkills-Eval publication. Skill owners should address the findings above and rerun NVSkills-Eval to refresh this benchmark.
