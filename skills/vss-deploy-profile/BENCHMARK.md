# Evaluation Report

Evaluation of the `vss-deploy-profile` skill before publication through NVSkills-Eval.

This benchmark summarizes 3-Tier Evaluation from NVSkills-Eval results for the skill. The goal is to document whether the skill is safe, discoverable, effective, and useful for agents before it is published for broader workflow use.

## Evaluation Summary

- Skill: `vss-deploy-profile`
- Evaluation date: 2026-06-04
- NVSkills-Eval profile: `external`
- Overall verdict: FAIL
- Tier 3 live agent evaluation: not available in this report

## Agents Used

- Tier 3 agent details were not available in this report.

## Metrics Used

Reported benchmark dimensions:

- Security: checks whether skill-assisted execution avoids unsafe behavior such as secret leakage, destructive commands, or unauthorized access.
- Correctness: checks whether the agent follows the expected workflow and produces the correct final output.
- Discoverability: checks whether the agent loads the skill when relevant and avoids using it when irrelevant.
- Effectiveness: checks whether the agent performs measurably better with the skill than without it.
- Efficiency: checks whether the agent uses fewer tokens and avoids redundant work.

Underlying evaluation signals used in this run:

- No Tier 3 evaluation signal details were available in this report.

## Test Tasks

Tier 3 evaluation task details were not available in this report.

## Results

Tier 3 dimension rollup was not available in this report.

## Tier 1: Static Validation Summary

Tier 1 validation passed with observations. NVSkills-Eval ran 9 checks and found 5 total findings.

Top findings:

- MEDIUM QUALITY/quality_correctness: Instructions don't mention 'run_script' (`skills/vss-deploy-profile/SKILL.md`)
- MEDIUM QUALITY/quality_correctness: SKILL_SPEC recommended field missing: 'metadata.author' (`skills/vss-deploy-profile/SKILL.md`)
- MEDIUM QUALITY/quality_efficiency: Deeply nested references in edge.md (`skills/vss-deploy-profile/SKILL.md`)
- MEDIUM SCHEMA/author_missing: Author not specified in metadata (`skills/vss-deploy-profile/SKILL.md`)
- LOW QUALITY/quality_efficiency: Non-descriptive filename: ngc.md (`skills/vss-deploy-profile/SKILL.md`)

## Tier 2: Deduplication Summary

Tier 2 validation reported findings. NVSkills-Eval ran 2 checks and found 2 total findings.

Top findings:

- HIGH DUPLICATE/duplicate: Duplicate content found across SKILL.md and references/alerts.md and references/base.md and references/lvs-profile.md and references/search.md:
  "# 1. cp dev-profile-<profile>/.env dev-profile-<profile>/generated.env  (clean copy)" in SKILL.md (lines 41-41)
  vs "# 5. docker compose --env-file generated.env -f resolved.yml up -d" in SKILL.md (lines 45-49)
  vs "### Step 1c — Initialize `generated.env`" in SKILL.md (lines 165-178)
  vs "### Step 3 — Apply overrides + dry-run" in SKILL.md (lines 199-205)
  vs "## Env file location" in references/alerts.md (lines 279-285)
  vs "## Env File Location" in references/base.md (lines 453-459)
  vs "## Env file location" in references/lvs-profile.md (lines 205-211)
  vs "## Env file location" in references/search.md (lines 278-284) (`SKILL.md:41`)
- HIGH DUPLICATE/duplicate: Duplicate content found across references/prerequisites.md and references/warehouse.md:
  "### 2. Docker" in references/prerequisites.md (lines 167-186)
  vs "#### 2.2 Docker" in references/warehouse.md (lines 476-490) (`references/prerequisites.md:167`)

## Publication Recommendation

The skill should be reviewed before NVSkills-Eval publication. Skill owners should address the findings above and rerun NVSkills-Eval to refresh this benchmark.
