# AGENTS.md

## Scope

Applies to `vss-build-vision-agent`, the skill that composes VSS deployments
from natural-language capability requests.

## First Reads

- Read `SKILL.md` first, then only the references needed for the request.
- Use `references/composition.md` for foundation and delta-profile rules.
- Use `references/profiles/` and `references/sizing.md` before choosing a
  profile or hardware placement.
- Use `references/deployment.md`, `references/readiness.md`, and
  `references/troubleshooting.md` only after a deploy is actually requested.

## Rules

- Pick exactly one current developer profile as the Foundation. Ask only when
  two foundations have the same smallest delta.
- Do not route warehouse or industry-profile requests through this skill unless
  the request is explicitly for a developer-profile-derived composition.
- In delta mode, add or remove only canonical Compose profile keys and only the
  environment knobs requested or required by the selected references.
- Generate and validate `_builds/<name>/override.env`, `compose.yml`, and
  `resolved.yml` as a unit. Never treat the label `<name>` as a Compose profile.
- Present the architecture and data-flow summary before writing or deploying
  generated artifacts.

## Eval Behavior

- In non-interactive evals, follow the prompt's pre-authorization for deploy or
  teardown steps.
- Keep proof concrete: selected Foundation, effective service set, changed env
  values, readiness checks, and browser/API endpoints.
