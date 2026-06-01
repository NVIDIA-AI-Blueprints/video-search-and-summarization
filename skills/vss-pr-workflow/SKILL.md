---
name: vss-pr-workflow
description: Create VSS GitHub PRs using the required branch naming, signed-off commits, and normal pre-commit hooks. Use when opening or updating VSS PRs, when the user mentions feat/* branch names, sign-off/DCO, pre-commit hooks, or /ok to test comments.
disable-model-invocation: true
---

# VSS PR Workflow

Use this workflow for changes to `NVIDIA-AI-Blueprints/video-search-and-summarization`.

## Required Rules

- Base work on the requested upstream branch, usually `origin/develop`.
- Use a branch name that starts with `feat/` unless the user explicitly requests another accepted prefix.
- Create signed-off commits with `git commit -s`.
- Never skip hooks. Do not use `--no-verify`.
- Let pre-commit hooks run normally and fix failures before pushing.
- After opening the PR, comment `/ok to test <sha>` where `<sha>` is the latest commit SHA on the PR branch.

## Start From A Clean Base

Prefer a separate worktree if the main checkout has unrelated local changes:

```bash
git fetch origin develop
git worktree add ../vss-pr-worktree -b feat/<short-topic> origin/develop
cd ../vss-pr-worktree
```

If already in a clean checkout:

```bash
git fetch origin develop
git switch --create feat/<short-topic> origin/develop
```

Check state before editing:

```bash
git status --short --branch
```

## Commit Correctly

Stage only relevant files:

```bash
git add <paths>
```

Commit with sign-off and normal hooks:

```bash
git commit -s -m "Concise imperative commit message"
```

If hooks fail, fix the issue and commit again. Do not bypass hooks.

Verify the commit:

```bash
git log -1 --format='%H%n%B'
```

The message must include a `Signed-off-by:` trailer.

## Push And Open PR

Push the `feat/*` branch:

```bash
git push -u origin HEAD
```

Open a PR against the requested base branch, usually `develop`. The PR body should include:

- Summary of user-facing or release-facing changes.
- Test plan with commands actually run.
- Any known tests not run and why.

If the GitHub CLI is unavailable, use the GitHub REST API with an existing local credential. Never print token values.

## Request CI

After the PR is open, get the latest commit SHA:

```bash
sha=$(git rev-parse HEAD)
```

Comment exactly:

```text
/ok to test <sha>
```

If another commit is pushed after the comment, comment again with the new latest SHA.
