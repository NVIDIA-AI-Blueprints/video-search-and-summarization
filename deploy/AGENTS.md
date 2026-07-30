# AGENTS.md

Router for deployment assets under `deploy/`.

## Scope

- Read only the deployment surface that matches the task unless a service
  contract change requires both Docker and Helm.
- Keep guidance focused on external-user operation.

## Routing

| Task area | Read next |
|---|---|
| Docker Compose | `deploy/docker/AGENTS.md` |
| Kubernetes or Helm | `deploy/helm/AGENTS.md` |
| Service image, port, env, health, volume, or dependency contract | Matching Docker and Helm surfaces |
| Developer or industry profile behavior | The target profile README, values/env files, and compose/chart entrypoint |

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

- Validate only the affected deployment surface.
- If validation needs Docker, Helm, GPUs, credentials, or NGC access that are
  unavailable, say exactly which check was skipped.
