---
name: bdd-container-update
description: >-
  Update the BDD test container image when its dependencies or runtime change.
  Determines version bump (major vs minor vs patch), rebuilds the Docker image,
  pushes to GitLab registry, and updates the image tag in start_test.sh.
  Test source (tests/, features/, scripts/, data/, conftest.py) is bind-mounted
  from the host repo at runtime, so pure test code changes do NOT require a
  container rebuild. Use only when files that the container bakes in are
  modified: Dockerfile, docker-entrypoint.sh, pyproject.toml, poetry.lock.
---

# BDD Test Container Update

The BDD test container runs `bdd_test` from
`<INTERNAL_REGISTRY>/bdd_tests:<TAG>`. Since the CI
runner bind-mounts the host repo's test source into `/app/`, the image only
needs to be rebuilt when the things it bakes in change.

## When to rebuild

**Rebuild required** -- files baked into the image:

- `test/bdd_tests/Dockerfile`
- `test/bdd_tests/docker-entrypoint.sh`
- `test/bdd_tests/pyproject.toml` (any change -- deps or pytest config)
- `test/bdd_tests/poetry.lock`

**Rebuild NOT required** -- bind-mounted at runtime by
`cicd_files/docker-compose-test/start_test.sh`:

- `test/bdd_tests/tests/**`
- `test/bdd_tests/features/**`
- `test/bdd_tests/scripts/**`
- `test/bdd_tests/data/**`
- `test/bdd_tests/conftest.py`
- `test/bdd_tests/config.json`

If a change touches only files in the second list, stop here -- no container
work is needed and `start_test.sh` is unchanged.

## 1. Identify the current image tag

The image tag lives in `cicd_files/docker-compose-test/start_test.sh` inside
the `update_docker_compose()` function, on the line that sets the `test`
service image:

```
image: <INTERNAL_REGISTRY>/bdd_tests:v<MAJOR>.<MINOR>.<PATCH>_x86
```

Extract the current `MAJOR`, `MINOR`, and `PATCH` numbers.

## 2. Determine version bump

Classify the changes made under `test/bdd_tests/`:

| Change type | Bump | Examples |
|---|---|---|
| **Major** | Increment MAJOR, reset MINOR and PATCH to 0 | Dockerfile base image change, entrypoint rewrite, Python major version bump, dependency major version upgrade (e.g. poetry major), new system-level package added to Dockerfile |
| **Minor** | Increment MINOR, reset PATCH to 0 | New runtime dependency in pyproject.toml, dependency minor version updates in pyproject.toml/poetry.lock, new pytest plugin in deps, additions to the Dockerfile that do not break existing tests |
| **Patch** | Increment PATCH | Dependency patch upgrades, minor Dockerfile cleanup, comment-only edits to Dockerfile/entrypoint, pinning a transitive dependency in poetry.lock |

When in doubt between major and minor, prefer minor. When in doubt between
minor and patch, prefer patch.

## 3. Build the new container image

```bash
cd test/bdd_tests
docker build -t <INTERNAL_REGISTRY>/bdd_tests:v<NEW_VERSION>_x86 .
```

Replace `<NEW_VERSION>` with the computed `MAJOR.MINOR.PATCH`.

## 4. Push to GitLab registry

```bash
docker push <INTERNAL_REGISTRY>/bdd_tests:v<NEW_VERSION>_x86
```

Ensure you are logged in to the GitLab registry first:

```bash
docker login <INTERNAL_REGISTRY>
```

## 5. Update the image tag in start_test.sh

In `cicd_files/docker-compose-test/start_test.sh`, locate the line inside the
`update_docker_compose()` function:

```
image: <INTERNAL_REGISTRY>/bdd_tests:v<OLD_VERSION>_x86
```

Replace `<OLD_VERSION>` with `<NEW_VERSION>`.

Only this one line needs to change. Do not modify any other part of the file.

## 6. Summary

After completing the update, report:

- Previous tag: `v<OLD_VERSION>_x86`
- New tag: `v<NEW_VERSION>_x86`
- Bump type: major / minor / patch
- Reason: one-line description of why that bump level was chosen
- Files changed:
  - `cicd_files/docker-compose-test/start_test.sh` (image tag)

## Quick reference

| Item | Value |
|---|---|
| Registry | `<INTERNAL_REGISTRY>/bdd_tests` |
| Tag format | `v<MAJOR>.<MINOR>.<PATCH>_x86` |
| Dockerfile | `test/bdd_tests/Dockerfile` |
| Tag location | `cicd_files/docker-compose-test/start_test.sh` -- `update_docker_compose()` function |
| Container name | `bdd_test` |
| Bind-mount source dir | `test/bdd_tests/` -> `/app/` (subdirs mounted, `.venv` and deps preserved) |
