# AGENTS.md

Repository-wide guidance for coding agents working in the NVIDIA AI
Blueprint: Video Search and Summarization repository.

## Scope

- This file applies to the whole repository unless a more specific
  `AGENTS.md` is present in the working path.
- Keep changes narrow and grounded in the existing VSS skill, deployment, and
  service contracts.
- Treat `README.md`, `CONTRIBUTING.md`, and nearby component docs as the source
  of truth for product behavior and contribution rules.

## First Reads

Before changing or operating an area, read the smallest relevant context:

1. `README.md` for the VSS architecture and directory map.
2. `CONTRIBUTING.md` for DCO, license headers, branch naming, and PR rules.
3. `skills/README.md` before choosing or editing a VSS skill.
4. The target skill's `SKILL.md` and only the references named by that skill.
5. The relevant workflow or deployment docs when touching CI, Docker, Helm, or
   eval specs.

## Working Rules

- Start from `develop` unless the task or issue says otherwise.
- Preserve SPDX headers and existing license notices.
- Do not commit secrets, local `.env` files, runtime logs, generated datasets,
  or scratch artifacts.
- Prefer existing scripts, adapters, and profile conventions over new one-off
  shell fragments.
- Keep VSS skills host-agnostic: describe the user task, prerequisites, APIs,
  and verification path rather than one coding agent's control flow.
- Put host-specific CI and runner behavior in `.github/skill-eval/`, not in
  individual skill contracts.
- For deployment-affecting Docker changes, check whether Helm parity is also
  required.

## Validation

Run checks that match the files changed and report what was skipped.

- Universal:
  ```bash
  git status --short
  git diff --check
  ```
- Skill changes:
  ```bash
  git diff --check -- skills/
  python3 -m json.tool skills/<skill>/evals/<spec>.json >/dev/null
  ```
- Skill-eval harness changes:
  ```bash
  python3 .github/skill-eval/plan_matrix.py
  pytest .github/skill-eval/tests -q
  ```
- Python service changes:
  ```bash
  cd services/agent
  uv run ruff check src/
  uv run ruff format --check src/
  uv run mypy src/vss_agents/
  uv run pytest tests/unit_test/ -v
  ```

## Review Checklist

- The diff matches the requested scope.
- The nearest relevant docs were read before changing behavior.
- Commands and references use current paths.
- Runtime prerequisites are explicit and verifiable.
- Destructive operations are gated or pre-authorized by the eval prompt.
- Validation results are summarized with exact commands.
