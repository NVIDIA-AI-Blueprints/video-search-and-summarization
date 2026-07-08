# AGENTS.md

Repository-wide guidance for coding agents and contributors working in the
NVIDIA AI Blueprint: Video Search and Summarization repository.

## Scope and Precedence

- This file applies to the whole repository. More specific `AGENTS.md` files,
  and the local guides they point to, take precedence inside their directories.
- Keep this root file focused on durable, repo-wide behavior. Put component
  quirks, long command recipes, and harness-specific workflows in the nearest
  component guide instead.
- Treat `README.md` as the product overview and `CONTRIBUTING.md` as the
  source of truth for contribution workflow, DCO, licensing, and PR checklist
  requirements.

## First Reads

Before editing, read the smallest useful set of docs:

1. `README.md` for the VSS architecture and top-level directory map.
2. `CONTRIBUTING.md` for branch naming, DCO sign-off, license headers, and PR
   expectations.
3. The nearest component README and local agent guide for touched files:
   - `services/agent/AGENTS.md`
   - `services/analytics/behavior-analytics/AGENTS.md`
   - `services/analytics/video-analytics-api/AGENTS.md`
   - `.github/helm-sync/AGENTS.md`
   - `.github/skill-eval/AGENTS.md`
   - `.openclaw/workspace/AGENTS.md` only for `.openclaw/workspace/**`
4. Relevant workflow, deployment, or skill docs only after the component guide
   points there or the task touches that area.

## Repository Map

- `services/agent/`: Python VSS agent, APIs, tools, evaluators, embeddings, and
  unit tests. Uses `uv`, Ruff, Mypy, pytest, and NVIDIA Agent Toolkit patterns.
- `services/ui/`: Next.js/Turbo UI monorepo. Uses Node from `.nvmrc`, npm
  workspaces, Turbo tasks, Jest, lint, typecheck, build, and bundle steps.
- `services/analytics/`: Downstream analytics services. Behavior analytics is
  Python/Pipenv; video analytics API is Node.js/Express with Elasticsearch.
- `deploy/`: Docker Compose and Helm deployment configurations. Keep Docker and
  Helm parity in mind for deployment-affecting changes.
- `skills/`: agentskills.io-compatible VSS skills. Each skill owns a
  `SKILL.md`, references, and optional eval specs.
- `.github/`: CI workflows and automation harnesses. Hidden directories can
  contain important local guidance; search with `rg --hidden` when relevant.
- `libs/` and `tools/`: shared libraries and utility packages. Follow the local
  README and surrounding style for each package.

## Working Rules

- Start branches from `develop` unless the user or issue says otherwise. Use the
  `<type>/<name>` branch format from `CONTRIBUTING.md`.
- Keep changes narrow and reviewable. Do not reformat, rename, regenerate, or
  vendor unrelated files while solving a focused task.
- Preserve SPDX headers and existing license notices. Add the appropriate SPDX
  header to new source files, and call out third-party or generated content in
  the PR description.
- Do not commit secrets, API keys, tokens, local `.env` files, runtime logs,
  datasets, or generated scratch artifacts.
- Prefer environment variables and existing config substitution over hardcoded
  hosts, ports, credentials, model names, or deployment URLs.
- Avoid editing generated files directly unless the task is explicitly to
  regenerate them and the source plus generator command are clear.
- When dependency manifests, lockfiles, Dockerfiles, or third-party license
  inventories change, expect OSRB review and run the relevant license checks.
- For deployment changes under `deploy/docker/`, check whether matching
  `deploy/helm/` values/templates must change as well.
- For `skills/**`, keep skill behavior host-agnostic. Put host-specific install
  details in docs or references, not in the core skill contract.

## Validation

Run checks that match the files changed. Always report what passed and what was
not run.

- Universal hygiene:
  ```bash
  git status --short
  git diff --check
  ```
- Pre-commit, when the environment is available:
  ```bash
  cd services/agent
  uv run pre-commit run --all-files
  ```
- Python agent changes:
  ```bash
  cd services/agent
  uv run ruff check src/
  uv run ruff format --check src/
  uv run mypy src/vss_agents/
  uv run pytest tests/unit_test/ -v
  ```
- UI changes:
  ```bash
  cd services/ui
  npm install
  npm run lint
  npm run typecheck
  npm test
  npm run build
  ```
- Behavior analytics changes: follow
  `services/analytics/behavior-analytics/AGENTS.md` and run the closest
  `pipenv run pytest ...` target for touched modules.
- Video analytics API changes: follow
  `services/analytics/video-analytics-api/AGENTS.md`; syntax-check touched JS
  with `node -c`, then run the relevant npm tests from `test/`.
- Skill changes: validate JSON/YAML, run `git diff --check -- skills/`, and read
  `.github/skill-eval/AGENTS.md` before changing eval specs or adapters.
- Deployment changes: validate the edited Compose or Helm artifacts with the
  local commands documented beside them. Do not run destructive deploy or
  teardown commands unless the task explicitly calls for it.

## Review Checklist

Before opening or updating a PR:

- Confirm the diff matches the requested scope.
- Confirm the nearest local agent guide was followed.
- Confirm docs, examples, and tests were updated with behavior changes.
- Confirm all commits are signed off with `git commit -s`.
- Summarize validation results, including skipped checks and why they were
  skipped.
