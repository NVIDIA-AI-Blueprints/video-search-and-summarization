# AGENTS.md

Scoped guidance for Kubernetes and Helm deployment assets.

## Scope

- Applies to `deploy/helm/**`: service charts, developer-profile charts,
  industry-profile charts, values files, templates, and chart docs.
- More specific chart READMEs and values files are the source of truth for a
  chart's public configuration.

## First Reads

- The nearest `Chart.yaml`, `values.yaml`, and chart `README.md`.
- Parent profile chart values when editing a subchart consumed by a profile.
- Matching Docker Compose service/profile files when changing shared deployment
  behavior.

## Rules

- Keep values user-facing and stable. Prefer adding documented values over
  hardcoding deployment-specific names, hosts, image tags, or credentials.
- Do not commit rendered manifests, live-cluster state, kubeconfigs, secrets, or
  generated values files.
- Preserve chart boundaries: shared service charts should not depend on a
  single developer or industry profile unless the chart already does so.
- Keep Docker and Helm behavior aligned for service contracts: images, ports,
  env vars, probes, volumes, dependencies, and public endpoints.

## Validation

- Always run `git diff --check -- deploy/helm`.
- Run `helm lint` or `helm template` for each affected chart when Helm is
  available.
- If a profile chart consumes a changed service chart, validate the profile
  chart too.
