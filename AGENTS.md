# AGENTS.md

Always-on router for coding agents working in NVIDIA VSS.

## Always On

- Start with `README.md` for the blueprint overview and public documentation
  links.
- Follow `CONTRIBUTING.md` for licensing, DCO sign-off, branch, PR, and test
  expectations.
- Load only the scoped guide that matches the task; avoid reading unrelated
  deployment, service, skill, or tooling trees.
- Do not commit secrets, generated runtime data, local `.env` files, logs, model
  downloads, or scratch artifacts.
- Preserve SPDX headers and existing third-party license notices.

## Scoped Routing

| Task area | Read next |
|---|---|
| Source services | `services/AGENTS.md`, then the nearest service guide |
| Docker Compose deployment | `deploy/AGENTS.md`, then `deploy/docker/AGENTS.md` |
| Kubernetes or Helm deployment | `deploy/AGENTS.md`, then `deploy/helm/AGENTS.md` |
| Shared libraries | Nearest `README.md` under `libs/` |
| Utility scripts or data tools | Nearest `README.md` under `tools/` |
| Agent skills | Target `SKILL.md` and only the references it names |

## Validation

- Always run `git diff --check` before committing.
- Choose focused validation for the paths changed; do not run deployment or
  hardware-heavy checks unless the task explicitly requires them.
- Report commands run and any checks skipped because prerequisites were not
  available.
