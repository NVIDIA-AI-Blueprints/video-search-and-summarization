# AGENTS.md

Router for Docker Compose deployment assets.

## Scope

- Applies to `deploy/docker/**`: root Compose, shared services, developer
  profiles, industry profiles, helper scripts, and Docker deployment docs.
- Profile-specific details live in the relevant profile files and README
  sections.

## First Reads

| Task area | Read next |
|---|---|
| General Docker workflow | `deploy/docker/README.md` |
| Top-level include behavior | `deploy/docker/compose.yml` |
| Developer profile | Target profile `.env`, `overrides.env`, `compose.yml`, and README |
| Industry profile | Target profile `.env`, `compose.yml`, and README |
| Helper script | Script help text and nearest README |

## Rules

- Run Compose from `deploy/docker` unless a script documents another working
  directory.
- Prefer `deploy/docker/scripts/dev-profile.sh` for developer-profile flows.
- Treat checked-in profile defaults as source and generated/runtime env files
  as local state.
- Do not edit generated resolved Compose output as source.
- Keep service profile keys canonical; do not invent aggregate profile names.
- If a service image, port, env var, volume, readiness endpoint, or dependency
  changes, check whether the matching Helm chart also needs an update.

## Validation

- Always run `git diff --check -- deploy/docker`.
- For Compose edits, render the affected stack with the documented env layers
  and run `docker compose config --quiet` when Docker Compose is available.
- For helper-script edits, run `bash -n` and any documented dry-run/help path.
- Do not start GPU-heavy or credentialed stacks unless explicitly asked.
