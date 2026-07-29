# AGENTS.md

Repository-wide guidance for coding agents working in the NVIDIA AI
Blueprint: Video Search and Summarization repository.

## Scope And Precedence

- This file applies to the whole repository. More specific `AGENTS.md` files
  take precedence inside their directories.
- Keep this root file high-level. Put workflow details, path-specific commands,
  deployment caveats, and skill-specific operating notes in the nearest nested
  guide.
- Treat `README.md`, `CONTRIBUTING.md`, and nearby component docs as the source
  of truth for product behavior and contribution rules.

## First Reads

Start broad, then move to the nearest nested guide:

1. `README.md` for the VSS architecture and directory map.
2. `CONTRIBUTING.md` for DCO, license headers, branch naming, and PR rules.
3. `skills/README.md` before choosing or editing a VSS skill.
4. A nested guide when the task enters one of these areas:
   - `skills/AGENTS.md`
   - `deploy/AGENTS.md`
   - `deploy/docker/AGENTS.md`
   - `.github/skill-eval/AGENTS.md`
   - `.github/helm-sync/AGENTS.md`
   - `.openclaw/workspace/AGENTS.md`
   - service-specific guides under `services/**/AGENTS.md`
5. The target `SKILL.md` and only the references named by that skill.

## Repository Rules

- Start from `develop` unless the task or issue says otherwise.
- Preserve SPDX headers and existing license notices.
- Do not commit secrets, local `.env` files, runtime logs, generated datasets,
  or scratch artifacts.
- Prefer existing scripts, adapters, and profile conventions over new one-off
  shell fragments.
- Keep VSS skills host-agnostic: describe the user task, prerequisites, APIs,
  and verification path rather than one coding agent's control flow.
- Put CI runner behavior in `.github/skill-eval/`; put skill behavior in
  `skills/`; put deployment config parity in `deploy/`.

## Validation

Run checks that match the files changed and report what was skipped.

- Universal:
  ```bash
  git status --short
  git diff --check
  ```
- For skills, follow `skills/AGENTS.md`.
- For deployment config, follow `deploy/AGENTS.md`.
- For skill-eval harness changes, follow `.github/skill-eval/AGENTS.md`.

## Review Checklist

- The diff matches the requested scope.
- The nearest nested guide was read before changing behavior.
- Commands and references use current paths.
- Runtime prerequisites are explicit and verifiable.
- Destructive operations are gated or pre-authorized by the eval prompt.
- Validation results are summarized with exact commands.
