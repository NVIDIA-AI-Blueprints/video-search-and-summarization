# AGENTS.md

## Scope

Applies to Docker Compose deployment assets under `deploy/docker/`.

## First Reads

- `README.md` for the current deployment layout.
- The profile's `compose.yml`, `overrides.env`, and local config files before
  changing that profile.
- `skills/vss-deploy-profile/references/<profile>.md` for profile-specific
  sizing, services, readiness, and troubleshooting.

## Rules

- Treat `.env` as the stable default layer and `overrides.env` as the checked-in
  profile override layer. Per-deploy mutations belong in `generated.env` or
  an untracked working file.
- Use `docker compose config` to render the exact effective stack before
  reasoning about service names, ports, env vars, and dependencies.
- Do not hand-edit generated `resolved.yml` except when a documented normalizer
  is part of the workflow.
- Keep compose profile keys canonical. Do not invent profile labels that are
  only convenient for one eval.
- Preserve public URL contracts consumed by operate skills:
  `VSS_PUBLIC_URL`, `VSS_AGENT_EXTERNAL_URL`, `VSS_PUBLIC_HOST`, and
  `VSS_PUBLIC_PORT` must describe browser-reachable endpoints, not container
  internals.

## Validation

- Render the changed profile with the documented env layers.
- Run normalizers or validators named by the profile reference.
- Check readiness probes for the specific service the next workflow needs, not
  only that containers are up.
