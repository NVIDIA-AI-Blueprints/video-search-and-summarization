# AGENTS.md

## Scope

This guide applies to every file under `skills/`. It is intentionally
harness-agnostic: write and maintain skills so they work for any
agentskills.io-compatible host or coding agent, not only one CI runner,
chat tool, or evaluation harness.

The `skills/` directory is the canonical source for VSS skills. Each
subdirectory is one self-contained skill with a `SKILL.md`, optional
`references/`, `scripts/`, `assets/`, and `evals/` directories.

## First Reads

Before editing a skill:

1. Read `skills/README.md` to understand routing and adjacent skills.
2. Read the target skill's `SKILL.md` completely.
3. Read only the referenced files needed for the task, usually under that
   skill's `references/` directory.
4. If the change affects tests or eval specs, read the relevant
   `skills/<skill>/evals/*.json` file and the harness notes in
   `.github/skill-eval/AGENTS.md`.

## Skill Authoring

- Keep `SKILL.md` usable as standalone operational guidance. A host should be
  able to load one skill directory and follow the instructions without hidden
  repo-global assumptions beyond paths named in the skill.
- Preserve the agentskills.io frontmatter fields: `name`, `description`,
  `license`, and `metadata`. Keep descriptions action-oriented and specific
  enough for routing.
- Prefer durable instructions over transcript-specific fixes. Do not mention a
  single failing CI run, local machine, or temporary workaround unless the file
  is explicitly a dated incident note.
- Make commands copy-pasteable, idempotent where practical, and explicit about
  working directories and environment variables.
- Put long procedures, API surfaces, platform matrices, and troubleshooting
  trees in `references/`; keep `SKILL.md` as the routing and workflow entry
  point.
- Reuse existing scripts in `scripts/` for non-trivial logic. If a command
  sequence becomes stateful, error-prone, or repeated, add or update a script
  instead of embedding fragile shell in prose.
- Keep skill names and slash-command examples aligned with `skills/README.md`.
  When renaming or adding a skill, update the catalog and any cross-references.

## Harness-Agnostic Boundaries

- Do not require a particular host such as Claude Code, Codex, Cursor,
  NemoClaw, or a GitHub Actions job in the skill instructions.
- It is fine to document host-specific install paths in `skills/README.md`,
  but the skills themselves should describe the VSS task, prerequisites, and
  APIs rather than a host's control flow.
- Treat CI/eval harness behavior as an external caller. Eval specs may exercise
  a skill, but the skill should remain correct when a human or a different
  agent host invokes it directly.
- Avoid references to runner-local paths such as `/tmp/skill-eval/...` inside
  skill instructions. Keep those details in `.github/skill-eval/`.
- Do not depend on hidden harness pre-deploys. If a workflow needs a profile,
  endpoint, sample video, credential, or running service, state that
  prerequisite in the skill workflow and describe how to verify or create it.

## Deployment And Runtime Safety

- Never commit secrets, API keys, tokens, generated env files, resolved compose
  files, or runtime artifacts.
- Do not mutate checked-in `.env` defaults. Deployment skills should copy to a
  working file such as `generated.env` and write overrides there.
- Gate destructive actions. Before deleting data, tearing down services, or
  replacing user resources, make the action explicit and confirm when running
  interactively. In non-interactive evals, the prompt/spec must authorize the
  action.
- Prefer readiness probes that verify the actual service needed by the next
  step, not only that Docker containers exist.
- Use environment variables for hostnames, ports, model IDs, and credentials.
  Do not hardcode deployment-specific IPs or private endpoints.
- For VSS profile deployment, route through `vss-deploy-profile` unless the
  skill is explicitly about a standalone microservice.

## Eval Specs

Eval specs live in `skills/<skill>/evals/*.json`; legacy
`skills/<skill>/eval/*.json` files may still exist in older skills.

- Keep specs portable. They should describe user-facing tasks and observable
  checks, not assumptions about one harness implementation.
- Include platform and prerequisite context in the task query when an agent
  needs it to act correctly.
- If the first step must deploy a VSS profile or standalone service, say so in
  the query. Do not rely on the harness to predeploy it.
- Keep placeholders such as `{{platform}}`, `{{repo_root}}`, or `{{mode}}`
  limited to values that the matching adapter intentionally renders.
- When changing a spec's shape, update the corresponding adapter under
  `.github/skill-eval/adapters/<skill>/` and add focused coverage for the
  generated task output.

## Validation

For documentation-only skill changes:

```bash
git diff --check -- skills/
```

For eval or adapter changes, also run the relevant adapter generation command
or targeted skill-eval tests. Prefer the narrowest validation that proves the
changed skill renders the intended task, then broaden if shared behavior was
modified.

For scripts under `skills/**/scripts/`, run the script's own tests when
available; otherwise run a syntax check and one dry-run or help command if the
script supports it.

## Review Checklist

- The changed skill still names the right VSS profile or standalone service.
- Cross-references point to existing files and current skill names.
- Commands specify their working directory or define the variables they use.
- Destructive operations are gated.
- Runtime prerequisites are explicit and verifiable.
- Eval specs and adapters stay in sync.
- The guidance would make sense outside the current agent host or CI harness.
