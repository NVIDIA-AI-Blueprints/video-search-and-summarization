# dev/

Code and tests for the VSS dev workstream. CI for this folder is wired
through the [`metromind/ci-vss-oss`](https://gitlab.com/metromind/ci-vss-oss)
parent pipeline, which dispatches a dedicated `dev` sub-pipeline whenever
files under `dev/` change.

## Running tests locally

```bash
bash dev/ci/run_dev_tests.sh
```

The script just calls `pytest dev/tests/` from the repo root — extend it
when this workstream needs more checks (linters, integration suites, etc.)
so the sub-pipeline contract stays a single command.

## How CI uses this

When files change under `dev/`, the ci-vss-oss `vss-diff-probe` step sets
`VSS_CHANGED_DEV=true`. The parent pipeline then triggers the `dev`
sub-pipeline, which checks out this repo at the tested SHA and runs
`bash dev/ci/run_dev_tests.sh`. No container builds happen on the dev
path — tests should depend only on upstream-pinned images.
