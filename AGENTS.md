# AGENTS.md

Repository-wide guidance for coding agents working in NVIDIA VSS.

## Always On

- Start with `README.md` for the blueprint overview and public documentation
  links.
- Follow `CONTRIBUTING.md` for licensing, DCO sign-off, branch, PR, and test
  expectations.
- Keep this file as a router. Put source, Docker, Kubernetes, and component
  specifics in the nearest nested guide or README.
- Do not commit secrets, generated runtime data, local `.env` files, logs, model
  downloads, or scratch artifacts.
- Preserve SPDX headers and existing third-party license notices.

## Scoped Routing

- Source code changes: read `services/AGENTS.md`, then the nearest service
  `README.md` or existing service-specific `AGENTS.md`.
- Docker Compose deployment changes: read `deploy/AGENTS.md` and
  `deploy/docker/AGENTS.md`.
- Kubernetes or Helm deployment changes: read `deploy/AGENTS.md` and
  `deploy/helm/AGENTS.md`.
- Shared libraries: start with the nearest `README.md` under `libs/`.
- Utility scripts or data tools: start with the nearest `README.md` under
  `tools/`.
- Agent skills under `skills/` are product artifacts; read the target
  `SKILL.md` and only the references it names.

## Validation

- Always run `git diff --check` before committing.
- Choose focused validation for the paths changed; do not run deployment or
  hardware-heavy checks unless the task explicitly requires them.
- Report commands run and any checks skipped because prerequisites were not
  available.
