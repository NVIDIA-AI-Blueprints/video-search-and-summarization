# AGENTS.md

Scoped guidance for deployment assets under `deploy/`.

## Scope

- This file routes deployment changes. Read the Docker or Helm nested guide
  before editing those trees.
- Keep deployment guidance focused on external-user operation, not internal
  automation behavior.

## Routing

- Docker Compose: read `deploy/docker/AGENTS.md` and `deploy/docker/README.md`.
- Kubernetes or Helm: read `deploy/helm/AGENTS.md` and the nearest chart
  `README.md`.
- Service image, port, environment, health, volume, or dependency changes often
  require both Docker and Helm updates.
- Developer profiles and industry profiles are user-facing bundles. Preserve
  their documented intent and avoid moving settings across profile boundaries
  unless the task asks for it.

## Deployment Rules

- Prefer existing profile helpers, values files, and documented environment
  variables over ad hoc one-off commands.
- Do not commit generated values, rendered manifests, local data directories,
  logs, downloaded models, resolved secrets, or `.env` files containing secrets.
- Keep public endpoints and host-facing variables browser- or client-reachable;
  do not replace them with container-internal names unless the setting is
  explicitly internal.
- When a source service contract changes, update deployment wiring and docs in
  the same change.

## Validation

- Validate only the affected deployment surface:
  - Docker Compose changes: follow `deploy/docker/AGENTS.md`.
  - Helm changes: follow `deploy/helm/AGENTS.md`.
- If validation needs Docker, Helm, GPUs, credentials, or NGC access that are
  unavailable, say exactly which check was skipped.
