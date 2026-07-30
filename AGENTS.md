# AGENTS.md

High-level guidance for coding agents working in NVIDIA VSS.

## Always On

- Start with `README.md` for the blueprint overview and public documentation
  links.
- Follow `CONTRIBUTING.md` for licensing, DCO sign-off, branch, PR, and test
  expectations.
- Prefer existing scripts, Compose files, Helm charts, and service-local
  conventions over inventing new workflows.
- Do not commit secrets, generated runtime data, local `.env` files, logs, model
  downloads, or scratch artifacts.
- Preserve SPDX headers and existing third-party license notices.

## Navigation

| Task area | Read next |
|---|---|
| Source services | The nearest service `README.md` or existing service-local guidance |
| Docker Compose deployment | `deploy/README.md` and relevant files under `deploy/docker/` |
| Kubernetes or Helm deployment | `deploy/README.md` and relevant files under `deploy/helm/` |
| Shared libraries | Nearest `README.md` under `libs/` |
| Utility scripts or data tools | Nearest `README.md` under `tools/` |
| Agent skills | Target `SKILL.md` and only the references it names |

## Validation

- Always run `git diff --check` before committing.
- Choose focused validation for the paths changed; do not run deployment or
  hardware-heavy checks unless the task explicitly requires them.
- Report commands run and any checks skipped because prerequisites were not
  available.
