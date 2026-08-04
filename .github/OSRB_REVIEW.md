# License Diff and OSRB Review

Dependency-related pull requests receive two related GitHub checks:

1. **License Diff** creates the public package-change overview and complete CSV.
2. **OSRB Review** evaluates changes that may require approval and publishes the
   final result.

Both checks run automatically. Do not manually edit the generated PR comment.

## What developers should do

### License Diff reports no reviewable changes

No OSRB action is required. Continue with the normal code and CODEOWNERS review.
Same-license version updates and dependency removals remain in the complete CSV
for traceability, but do not require OSRB re-engagement.

### OSRB Review is running

Wait for it to finish. The protected reviewer is checking the public diff
against existing approval evidence. Do not merge while this check is running.

### OSRB Review passes

No further OSRB action is required for the reported dependency changes.
Normal required checks, CODEOWNERS approval, and maintainer review still apply.

### The review says its verdict is provisional

The reviewer ran a pre-release build of itself, which happens during rollout. Do
not treat the verdict as final and do not merge on the strength of it. Tell a
repository maintainer, who will confirm what the check ran against.

### OSRB Review fails or is inconclusive

1. Read the automated review on the PR.
2. Open the linked **License Diff** Actions run.
3. Inspect the short overview. Download `license-diff.csv` only when more detail
   is needed.
4. Address the reported issue:
   - correct or remove an unintended dependency;
   - provide a resolvable public license or component URL;
   - preserve or add the required third-party notice and license text;
   - work with the OSRB owner when a new component, license change, or changed
     use needs approval;
   - ask a repository maintainer to retry an infrastructure failure.
5. Push the correction. If no source change was needed, a maintainer can rerun
   the License Diff workflow.

Do not paste private ticket comments, approval sheets, credentials, or internal
links into the public PR. The protected reviewer reads that evidence privately
and publishes only the decision.

## What triggers review

The overview requests attention for:

- a new dependency;
- a confirmed license change;
- an old or new license that cannot be resolved.

The complete CSV also retains same-license version updates and removals, but
those rows do not require OSRB re-engagement by themselves.

Changes in how an approved component is used can still require review even when
its package version and license are unchanged. Examples include static instead
of dynamic linking, vendoring source, or moving a build-only dependency into a
distributed runtime.

## Release-target pull requests

Pull requests targeting `dev/*` or `release/*` are marked not applicable because
OSRB review is performed on the earlier pull request into the development
branch.
