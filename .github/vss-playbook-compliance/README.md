# vss-playbook-compliance

Tier-1 static skill gate that runs on every PR touching `skills/` or this
harness. It fails the check when the playbook compliance script reports
an ERROR-level finding, then posts or updates a sticky PR comment with
the findings summary.

## Files

| File | Role |
|---|---|
| [`../workflows/vss-playbook-compliance.yml`](../workflows/vss-playbook-compliance.yml) | GitHub Actions workflow definition |
| [`skill_compliance_check.py`](skill_compliance_check.py) | Vendored playbook compliance checker for NAM / FM / STR / SEC rules. `--json-out` dumps findings for the comment poster |
| [`post_comment.py`](post_comment.py) | Composes a markdown summary from the playbook JSON output and posts or updates a sticky PR comment |
| `README.md` | This file |

## What It Covers

`skill_compliance_check.py` is vendored from `agent_skills_playbook` with
VSS-specific modifications:

- **NAM-001..007**: kebab-case, generic-name guard, approved verbs and team prefixes, token/char limits, reserved bare names, cross-skill collision.
- **FM-001..011**: frontmatter required fields, optional field formats, description-quality heuristics, and implementation-led description checks.
- **STR-001..003**: `SKILL.md` required, `SKILL.md` line limit, and `evals/` directory presence as WARN only.
- **SEC-001..003**: static credential scanning, PII detection, and Unicode smuggling checks.

Gate behavior: any ERROR-level finding exits non-zero. WARN findings are
reported but do not block unless the workflow is later changed to pass
`--strict`.

## Modifications vs Upstream Playbook Script

| Upstream rule | Action | Reason |
|---|---|---|
| `STR-003` evals/evals.json required (ERROR) | Softened to WARN, no filename restriction | `evals/` is not part of [agentskills.io spec](https://agentskills.io/specification); the `evals.json` shape is one community runner's convention, not a standard |
| `STR-004` references/README.md required (WARN) | Dropped | Not in spec; not used by Anthropic's reference skills |
| `EVAL-001..005` | Dropped entire family | The repo's existing `skills-eval` workflow owns runtime eval execution |
| `REQUIRED_FM_FIELDS = [name, description, owner, service, version, reviewed]` | Trimmed to `[name, description]` | Match [agentskills.io spec](https://agentskills.io/specification) |

`APPROVED_TEAM_PREFIXES` and `APPROVED_VERBS` are kept in
`skill_compliance_check.py`. Update them by PR review when the playbook
standard changes.

## Where It Runs

The workflow runs on `ubuntu-latest` and uses only Python stdlib code.
There is no self-hosted runner or pre-installed binary requirement.

## Required Status Check

For exit-1 to block merging, add `vss-playbook-compliance / skills-check`
as a required status check on `develop` and `main` under Settings ->
Branches / Rulesets.

## PR Comment Poster

`post_comment.py` reads `$PLAYBOOK_FINDINGS_JSON`, builds a markdown
summary, and posts a sticky PR comment. Subsequent runs update the same
comment in place using the hidden marker
`<!-- vss-playbook-compliance-bot:v1 -->`.

In `workflow_dispatch` mode there is no PR number, so the same markdown
is appended to `$GITHUB_STEP_SUMMARY`.
