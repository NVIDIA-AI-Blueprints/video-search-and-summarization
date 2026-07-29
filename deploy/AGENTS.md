# AGENTS.md

## Scope

Applies to deployment assets under `deploy/`, including Docker Compose,
Helm charts, developer profiles, industry profiles, and deployment docs.

## First Reads

- For Docker Compose changes, read `deploy/docker/AGENTS.md` and
  `deploy/docker/README.md`.
- For Helm changes, read the nearest chart `Chart.yaml`, `values*.yaml`, and
  templates before editing.
- For profile behavior, read the matching skill reference under
  `skills/vss-deploy-profile/references/`.

## Rules

- Keep Docker and Helm behavior in sync when a deployment-facing setting,
  image, environment variable, port, volume, service dependency, or profile
  topology changes.
- Do not edit generated deployment output or runtime data directories as if
  they were source.
- Do not commit secrets, resolved `.env` files, logs, downloaded models, or
  local data.
- Prefer profile-local overrides and documented env substitution over hardcoded
  hosts, ports, models, credentials, or instance names.
- Preserve profile boundaries: developer profiles, industry profiles, and
  shared service definitions have different ownership and blast radius.

## Validation

- Run `git diff --check -- deploy/`.
- Validate changed Compose files with the commands documented next to the
  profile.
- Validate changed Helm charts with `helm lint` or `helm template` when Helm is
  available.
- For Docker-side changes under `deploy/docker/`, check whether matching
  `deploy/helm/` updates are required.
